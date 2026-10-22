"""Импорт профиля фирмы v0.3 `data/firm_profile.json` (L-10, L-16)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from rentab_core.costing import employee_cost
from rentab_core.errors import ProfileImportError
from rentab_core.io import NoteLevel, Workspace, dumps, loads
from rentab_core.io.v03 import LEGACY_V03_VERSION, v03_default_contribution_rate
from rentab_core.models import (
    DEFAULT_TARGET_MARGIN,
    AusnProfile,
    ExchangeRates,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    MonthlyCost,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Period,
    Role,
    Salary,
    SocialContributions,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import Currency, round_to
from rentab_core.tax import employer_contribution_rate
from tests.core.factories import d

FIXTURE = Path(__file__).parents[1] / "fixtures" / "legacy" / "v03_firm_profile.json"


def fixture() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return result


def load(raw: dict[str, Any]) -> tuple[Workspace, list[str]]:
    result = loads(json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    assert result.from_v03
    return result.workspace, [note.message for note in result.notes]


def test_fixture_profile() -> None:
    result = loads(FIXTURE.read_bytes())
    assert result.source_version == LEGACY_V03_VERSION
    firm = result.workspace.firm
    assert firm.jurisdiction == Jurisdiction(rf=UsnProfile())
    assert [(e.name, e.role) for e in firm.team] == [
        ("Иванов А.", Role.PARTNER),
        ("Петрова М.", Role.SENIOR),
        ("Сидоров К.", Role.ASSOCIATE),
        ("Кузнецова Е.", Role.INTERN),
    ]
    assert [e.contract_hours_per_month for e in firm.team] == [168, 168, 168, 120]
    assert {e.target_utilization for e in firm.team} == {Decimal("0.65")}
    assert {e.currency for e in firm.team} == {Currency.RUB}
    assert [e.billing_rate for e in firm.team] == [15_000, 10_000, 6_000, 2_500]
    assert firm.overheads == (
        Overhead(name="Аренда офиса", amount=d(120_000), period=Period.MONTHLY),
        Overhead(name="КонсультантПлюс", amount=d(180_000), period=Period.ANNUAL),
        Overhead(name="Облачные сервисы", amount=d(15_000), period=Period.MONTHLY),
    )
    assert firm.exchange_rates == ExchangeRates(rub_per_kzt=d("0.18"))
    assert result.workspace.rates_quote is None
    # L-16: «Фиксированная (издержки + маржа)» и 30 (целые проценты) — пустой проект.
    (record,) = result.workspace.projects
    assert record.project.pricing == FixedPricing()
    assert record.project.target_margin == d("0.30")
    assert record.project.stages == ()
    # Файл сохраняется уже в новом формате и читается без замечаний.
    assert loads(dumps(result.workspace)).workspace == result.workspace


def test_salary_and_recalculated_contribution_rate() -> None:
    firm = loads(FIXTURE.read_bytes()).workspace.firm
    for member in firm.team:
        assert isinstance(member.compensation, Salary)
        gross = member.compensation.gross_salary
        # Плоские 30% v0.3 — ставка по умолчанию: пересчитаны по реестру 2026 года.
        assert member.compensation.employer_contribution_rate == employer_contribution_rate(
            Jurisdiction(rf=UsnProfile()), gross_salary=gross
        )
    partner = firm.team[0].compensation
    assert isinstance(partner, Salary)
    assert partner.gross_salary == d(400_000)
    # 4,8 млн ₽ в год: 30% до базы 2 979 000, 15,1% сверх, 0,2% на травматизм.
    assert round_to(partner.employer_contribution_rate * 100) == d("24.55")


def test_notes_explain_every_change() -> None:
    result = loads(FIXTURE.read_bytes())
    assert {note.level for note in result.notes} == {NoteLevel.INFO}
    messages = [note.message for note in result.notes]
    assert messages[0] == "Файл — профиль фирмы Rentab v0.3: данные переведены в текущий формат."
    assert messages[1] == (
        "«Иванов А.»: ставка взносов 30% — плоская ставка v0.3 (L-08); по реестру 2026 "
        "года для этого оклада — 24,55%."
    )
    assert any("Утилизация в v0.3 не задавалась: у всех сотрудников 65%" in m for m in messages)
    # 3 × 168 × 0,65 + 120 × 0,65 = 405,6 ч.
    assert messages[-1] == (
        "В v0.3 накладные делились на 400 биллируемых ч в месяц. Теперь — на ёмкость "
        "команды (контрактные часы × утилизация): 405,6 ч."
    )


def test_contract_hour_cost_matches_v03_cost_rate() -> None:
    # Без пересчёта взносов себестоимость контрактного часа = cost_rate v0.3 (ADR 0004).
    raw = fixture()
    for member in raw["team"]:
        member["employer_contribution_rate"] = 0.25
        member["cost_rate"] = member["gross_salary"] * 1.25 / member["contract_hours_per_month"]
    workspace, messages = load(raw)
    for member, employee in zip(raw["team"], workspace.firm.team, strict=True):
        cost = employee_cost(employee)
        assert abs(cost.contract_cost_rate - Decimal(str(member["cost_rate"]))) < d("1e-9")
        # u = 0,65: себестоимость биллируемого часа в 1 / 0,65 раза выше.
        assert abs(cost.cost_rate - cost.contract_cost_rate / d("0.65")) < d("1e-20")
    assert "«Иванов А.»: ставка взносов 25% задана вручную — оставлена." in messages


def test_manual_cost_rate_becomes_monthly_cost() -> None:
    raw = fixture()
    raw["team"][0]["cost_rate"] = 5_000
    workspace, messages = load(raw)
    assert workspace.firm.team[0].compensation == MonthlyCost(amount=d(5_000 * 168))
    assert (
        "«Иванов А.»: себестоимость часа 5 000 введена вручную и расходится с окладом — "
        "стоимость в месяц = себестоимость часа × контрактные часы."
    ) in messages


def test_old_team_entry_without_salary() -> None:
    raw = fixture()
    raw["team"] = [{"name": "Старый", "role": "Партнёр", "billing_rate": 9000, "cost_rate": 2500}]
    workspace, _ = load(raw)
    (member,) = workspace.firm.team
    assert member.compensation == MonthlyCost(amount=d(2_500 * 168))
    assert member.contract_hours_per_month == 168


@pytest.mark.parametrize(
    ("params", "jurisdiction", "currency"),
    [
        (
            {
                "jurisdiction": "RF",
                "rf": {
                    "country": "RF",
                    "regime": "USN",
                    "object": "income_minus_expenses",
                    "vat": "5%",
                    "social_contributions": "zero",
                },
            },
            Jurisdiction(
                rf=UsnProfile(
                    tax_object=UsnObject.INCOME_MINUS_EXPENSES,
                    vat=UsnVat.VAT_5,
                    social_contributions=SocialContributions.ZERO,
                )
            ),
            Currency.RUB,
        ),
        (
            {"jurisdiction": "RF", "rf": {"country": "RF", "regime": "AUSN"}},
            Jurisdiction(rf=AusnProfile()),
            Currency.RUB,
        ),
        (
            {"jurisdiction": "RF", "rf": {"country": "RF", "regime": "NPD"}},
            Jurisdiction(rf=NpdProfile()),
            Currency.RUB,
        ),
        (
            {
                "jurisdiction": "KZ",
                "rf": None,
                "kz": {"country": "KZ", "regime": "OUR", "form": "ip", "vat": "16%"},
            },
            Jurisdiction(
                mode=JurisdictionMode.KZ, kz=KzOurProfile(form=KzForm.IP, vat=KzVat.VAT_16)
            ),
            Currency.KZT,
        ),
        (
            {
                "jurisdiction": "Both",
                "rf": {"country": "RF", "regime": "OSNO", "social_contributions": "standard"},
                "kz": {"country": "KZ", "regime": "OUR"},
            },
            Jurisdiction(mode=JurisdictionMode.BOTH, rf=OsnoProfile(), kz=KzOurProfile()),
            Currency.RUB,
        ),
    ],
)
def test_jurisdictions(
    params: dict[str, Any], jurisdiction: Jurisdiction, currency: Currency
) -> None:
    raw = fixture()
    raw["jurisdiction_params"] = params
    workspace, _ = load(raw)
    assert workspace.firm.jurisdiction == jurisdiction
    assert {member.currency for member in workspace.firm.team} == {currency}


@pytest.mark.parametrize(
    ("params", "rate"),
    [
        ({}, "0.30"),
        ({"jurisdiction": "RF", "rf": {"regime": "USN", "social_contributions": "zero"}}, "0"),
        ({"jurisdiction": "RF", "rf": {"regime": "AUSN"}}, "0"),
        ({"jurisdiction": "Both", "rf": {"regime": "NPD"}}, "0"),
        ({"jurisdiction": "KZ"}, "0.115"),
    ],
)
def test_v03_default_contribution_rates(params: dict[str, Any], rate: str) -> None:
    assert v03_default_contribution_rate(params) == Decimal(rate)


def test_hourly_pricing_label_and_defaults() -> None:
    raw = fixture()
    raw["pricing_model"] = "Биллинговая (часы × ставка)"
    del raw["target_margin"]
    workspace, _ = load(raw)
    assert workspace.projects[0].project.pricing == HourlyPricing()
    assert workspace.projects[0].project.target_margin == DEFAULT_TARGET_MARGIN


def test_minimal_profile() -> None:
    workspace, messages = load({"team": []})
    assert workspace.firm.team == ()
    assert workspace.firm.exchange_rates == ExchangeRates()
    assert workspace.projects[0].project.pricing == HourlyPricing()
    assert messages == ["Файл — профиль фирмы Rentab v0.3: данные переведены в текущий формат."]


def test_questionable_values_become_warnings() -> None:
    raw = fixture()
    raw["pricing_model"] = "Бартер"
    raw["target_margin"] = 150
    raw["firm_expenses"].append(
        {"name": "Курьер", "category": "disbursement_own", "amount": 500, "period": "one-time"}
    )
    raw["_profile_autoloaded"] = True
    raw["exchange_rate_rub_per_kzt"] = None
    raw["jurisdiction_params"]["exchange_rate_rub_per_kzt"] = 0
    workspace, messages = load(raw)
    assert workspace.projects[0].project.pricing == HourlyPricing()
    assert workspace.projects[0].project.target_margin == DEFAULT_TARGET_MARGIN
    assert len(workspace.firm.overheads) == 3
    assert workspace.firm.exchange_rates == ExchangeRates()
    assert "Неизвестная модель цены v0.3 'Бартер' — выбрана почасовая." in messages
    assert "Целевая маржа 150% вне диапазона — взята 30%." in messages
    assert "Расход «Курьер» (категория 'disbursement_own') — не накладной, пропущен." in messages
    assert "Не перенесены неизвестные поля: _profile_autoloaded." in messages


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda raw: raw.update(jurisdiction_params=[1]), "jurisdiction_params — не объект"),
        (
            lambda raw: raw["jurisdiction_params"].update(jurisdiction="Mars"),
            "неизвестная юрисдикция 'Mars'",
        ),
        (
            lambda raw: raw["jurisdiction_params"]["rf"].update(regime="PSN"),
            "неизвестный режим РФ: RF/PSN",
        ),
        (
            lambda raw: raw["jurisdiction_params"]["rf"].update(vat="50%"),
            "неверные параметры режима РФ",
        ),
        (
            lambda raw: raw["jurisdiction_params"].update(rf={"country": "KZ", "regime": "OUR"}),
            "в параметрах РФ указан режим РК",
        ),
        (
            lambda raw: raw["jurisdiction_params"].update(kz={"country": "RF", "regime": "OSNO"}),
            "в параметрах РК указан режим РФ",
        ),
        (lambda raw: raw.update(team={}), "team — не список"),
        (lambda raw: raw["team"].append("Иванов"), "сотрудник № 5 без имени"),
        (lambda raw: raw["team"][0].update(role="Босс"), "у «Иванов А.» неизвестная роль 'Босс'"),
        (lambda raw: raw["team"][0].update(cost_rate="много"), "cost_rate сотрудника «Иванов А.»"),
        (lambda raw: raw["team"][0].update(cost_rate=True), "— не число (True)"),
        (lambda raw: raw["team"][0].update(cost_rate=None), "— не число (None)"),
        (
            lambda raw: raw["team"][0].update(contract_hours_per_month=0),
            "у «Иванов А.» контрактные часы должны быть > 0",
        ),
        (
            lambda raw: raw["team"][0].update(billing_rate=-1),
            "неверные данные сотрудника «Иванов А.»",
        ),
        (lambda raw: raw.update(firm_expenses="аренда"), "firm_expenses — не список"),
        (lambda raw: raw["firm_expenses"].append(1), "расход № 4 — не объект"),
        (
            lambda raw: raw["firm_expenses"][0].update(period="weekly"),
            "у расхода «Аренда офиса» неизвестный период 'weekly'",
        ),
        (
            lambda raw: raw["firm_expenses"][0].update(amount=-5),
            "неверный расход «Аренда офиса»",
        ),
    ],
)
def test_invalid_profile(change: Any, message: str) -> None:
    raw = fixture()
    change(raw)
    with pytest.raises(ProfileImportError) as error:
        load(raw)
    assert str(error.value).startswith("Профиль v0.3: ")
    assert message in str(error.value)


def test_infinite_number_is_rejected() -> None:
    raw = fixture()
    raw["team"][0]["cost_rate"] = "Infinity"
    with pytest.raises(ProfileImportError, match="не конечное число"):
        load(raw)


def test_profile_without_team() -> None:
    workspace, _ = load({"jurisdiction_params": {"jurisdiction": "RF"}})
    assert workspace.firm.team == ()
    assert workspace.firm.jurisdiction == Jurisdiction()


def test_unknown_country_in_regime() -> None:
    raw = fixture()
    raw["jurisdiction_params"]["rf"]["country"] = "US"
    with pytest.raises(ProfileImportError, match="неизвестный режим РФ: US/USN"):
        load(raw)


def test_rate_equal_to_current_needs_no_note() -> None:
    # НПД: работодателя нет, ставка 0 и в v0.3, и в реестре 2026 года.
    raw = fixture()
    raw["jurisdiction_params"]["rf"] = {"country": "RF", "regime": "NPD"}
    member = raw["team"][3]
    member.update(employer_contribution_rate=0.0, cost_rate=60_000 / 120)
    raw["team"] = [member]
    workspace, messages = load(raw)
    assert workspace.firm.team[0].compensation == Salary(
        gross_salary=d(60_000), employer_contribution_rate=d(0)
    )
    assert not any("ставка взносов" in message for message in messages)
