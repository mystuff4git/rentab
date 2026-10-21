"""Адаптер «сценарий v0.3 → модели ядра» для characterization-тестов фазы 2.

Ядро о формате v0.3 ничего не знает: всё соответствие собрано здесь. Формат
сценария — `scenarios/README.md`; правила перевода — docs/methodology.md,
раздел 13.

Перевод настраивается `LegacyMapping`, чтобы разложить расхождение v0.3 и
новой методологии по шагам (мост «было/стало», `bridge.py`):

- `NEUTRAL` — входы v0.3 переводятся так, чтобы ядро видело ровно те же
  числа, что и v0.3: себестоимость часа берётся как есть, ёмкость фирмы равна
  биллируемым часам v0.3, «Прочее» отбрасывается, валюты складываются 1 : 1,
  одноимённые этапы остаются разными. Разница с v0.3 — только методология
  налога и этапов: НДС сверху (L-02), минимальный налог (L-03), этапы по
  теневой стоимости (L-04).
- `FINAL` — перевод фазы 2: себестоимость часа v0.3 — это себестоимость
  контрактного часа (W = cost_rate × H, u = 0,65), «Прочее» — собственный
  расход, суммы пересчитываются по курсу, одноимённые этапы сливаются в один
  этап с несколькими назначениями.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from rentab_core.duties import duty_disbursements
from rentab_core.models import (
    DEFAULT_CONTRACT_HOURS_PER_MONTH,
    DEFAULT_TARGET_MARGIN,
    DEFAULT_TARGET_UTILIZATION,
    ROLE_LABELS,
    Assignment,
    AusnProfile,
    Country,
    Disbursement,
    DisbursementKind,
    Employee,
    ExchangeRates,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    MonthlyCost,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Period,
    PricingModel,
    Project,
    Role,
    Scenario,
    Stage,
    TaxProfile,
    UsnProfile,
)
from rentab_core.money import ONE, Currency, to_decimal

LegacyDict = Mapping[str, Any]

ROLE_BY_LABEL: Mapping[str, Role] = {label: role for role, label in ROLE_LABELS.items()}
MODE_BY_LEGACY: Mapping[str, JurisdictionMode] = {
    "RF": JurisdictionMode.RF,
    "KZ": JurisdictionMode.KZ,
    "Both": JurisdictionMode.BOTH,
}
PERIOD_BY_LEGACY: Mapping[str, Period] = {
    "monthly": Period.MONTHLY,
    "annual": Period.ANNUAL,
    "one-time": Period.ONE_TIME,
}
LEGACY_EXTRA = "project_extra"
KIND_BY_CATEGORY: Mapping[str, DisbursementKind] = {
    "disbursement_billable": DisbursementKind.BILLABLE,
    "disbursement_own": DisbursementKind.OWN,
}
# Пункт 02_Проект.py:67–69: целевая маржа хранится целыми процентами.
TARGET_MARGIN_DEFAULT_PCT = 30
LEGACY_DEFAULT_BILLABLE_HOURS = 160.0
# Курс ₽ за 1 ₸ по умолчанию в v0.3 (pages/01_Настройка.py:97).
LEGACY_DEFAULT_RUB_PER_KZT = Decimal("0.18")
# Курсы 1 : 1 — так v0.3 складывал суммы в разных валютах (L-06).
ONE_TO_ONE = ExchangeRates(rub_per_kzt=ONE, rub_per_usd=ONE)
# Валюта ставок и окладов команды: v0.3 их валюту не хранил, фирма в режиме
# «Оба» считается рублёвой.
HOME_CURRENCY: Mapping[JurisdictionMode, Currency] = {
    JurisdictionMode.RF: Currency.RUB,
    JurisdictionMode.KZ: Currency.KZT,
    JurisdictionMode.BOTH: Currency.RUB,
}


class LegacyInputError(ValueError):
    """Запись v0.3, которую страница «Проект» пропустила бы с предупреждением."""


@dataclass(frozen=True)
class LegacyMapping:
    """Как переводить входы v0.3 в модели ядра.

    Attributes:
        utilization: Утилизация сотрудника; себестоимость часа v0.3 считается
            себестоимостью контрактного часа. None — себестоимость часа v0.3
            берётся как себестоимость биллируемого часа, а ёмкость фирмы равна
            биллируемым часам v0.3 (как считал v0.3).
        extra_as_own: «Прочее» — собственный расход (L-12); иначе отбрасывается.
        convert_currency: Пересчитывать суммы по курсу (L-06); иначе 1 : 1, а
            ставки команды — в валюте страны расчёта, как в v0.3.
        merge_stage_names: Сливать одноимённые этапы в один (L-05); иначе
            одноимённые этапы получают суффиксы « #2», « #3».
    """

    utilization: Decimal | None
    extra_as_own: bool
    convert_currency: bool
    merge_stage_names: bool


NEUTRAL = LegacyMapping(
    utilization=None, extra_as_own=False, convert_currency=False, merge_stage_names=False
)
FINAL = LegacyMapping(
    utilization=DEFAULT_TARGET_UTILIZATION,
    extra_as_own=True,
    convert_currency=True,
    merge_stage_names=True,
)
# Шаги моста «было/стало»: каждый включает одно исправление поверх предыдущего.
BRIDGE_STEPS: tuple[tuple[str, LegacyMapping], ...] = (
    ("L-02, L-03, L-04", NEUTRAL),
    ("L-12", replace(NEUTRAL, extra_as_own=True)),
    ("L-06", replace(NEUTRAL, extra_as_own=True, convert_currency=True)),
    ("L-05", replace(FINAL, utilization=None)),
    ("L-01", FINAL),
)


# ---------------------------------------------------------------------------
# Юрисдикция
# ---------------------------------------------------------------------------
def tax_profile_from_params(params: LegacyDict | None) -> TaxProfile | None:
    """Параметры режима v0.3 (`jurisdiction_params["rf"|"kz"]`) → профиль ядра."""
    if not params:
        return None
    country, regime = params["country"], params["regime"]
    if country == "KZ" and regime == "OUR":
        return KzOurProfile(form=params.get("form", "too"), vat=params.get("vat", "none"))
    if country == "RF":
        social = params.get("social_contributions", "standard")
        if regime == "USN":
            return UsnProfile(
                tax_object=params.get("object", "income"),
                vat=params.get("vat", "none"),
                social_contributions=social,
            )
        if regime == "AUSN":
            return AusnProfile()
        if regime == "OSNO":
            return OsnoProfile(social_contributions=social)
        if regime == "NPD":
            return NpdProfile(client_type=params.get("client_type", "legal_entity"))
    raise LegacyInputError(f"Неизвестный режим v0.3: {country}/{regime}")


def jurisdiction_from_params(params: LegacyDict) -> Jurisdiction:
    """`session_state["jurisdiction_params"]` v0.3 → Jurisdiction."""
    rf = tax_profile_from_params(params.get("rf"))
    kz = tax_profile_from_params(params.get("kz"))
    if isinstance(rf, KzOurProfile):
        raise LegacyInputError("В параметрах РФ указан режим РК")
    if kz is not None and not isinstance(kz, KzOurProfile):
        raise LegacyInputError("В параметрах РК указан режим РФ")
    return Jurisdiction(mode=MODE_BY_LEGACY[params.get("jurisdiction", "RF")], rf=rf, kz=kz)


def exchange_rates(params: LegacyDict, mapping: LegacyMapping) -> ExchangeRates:
    """Курсы фирмы: 1 : 1 (как v0.3) или курс тенге из сценария."""
    if not mapping.convert_currency:
        return ONE_TO_ONE
    rate = params.get("exchange_rate_rub_per_kzt")
    return ExchangeRates(
        rub_per_kzt=LEGACY_DEFAULT_RUB_PER_KZT if rate is None else to_decimal(rate)
    )


# ---------------------------------------------------------------------------
# Команда
# ---------------------------------------------------------------------------
def employee_from_member(
    member: LegacyDict, *, currency: Currency, mapping: LegacyMapping, neutral_hours: Decimal
) -> Employee:
    """Член команды v0.3 → Employee.

    Args:
        member: `{name, role, billing_rate, cost_rate}` v0.3.
        currency: Валюта ставок сотрудника.
        mapping: Правила перевода.
        neutral_hours: Контрактные часы для нейтрального перевода — доля
            биллируемых часов фирмы v0.3 на одного сотрудника.
    """
    cost_rate = to_decimal(member["cost_rate"])
    if mapping.utilization is None:
        hours, utilization = neutral_hours, ONE
    else:
        hours = to_decimal(member.get("contract_hours_per_month", DEFAULT_CONTRACT_HOURS_PER_MONTH))
        utilization = mapping.utilization
    return Employee(
        name=member["name"],
        role=ROLE_BY_LABEL[member["role"]],
        currency=currency,
        billing_rate=to_decimal(member["billing_rate"]),
        # W = cost_rate × H: нейтрально u = 1 и c = cost_rate; в фазе 2 c = cost_rate / u.
        compensation=MonthlyCost(amount=cost_rate * hours),
        contract_hours_per_month=hours,
        target_utilization=utilization,
    )


# ---------------------------------------------------------------------------
# Расходы
# ---------------------------------------------------------------------------
def _expense_fields(item: LegacyDict) -> tuple[str, str, Decimal, Currency, str]:
    """Поля расхода с дефолтами и проверками `Expense.from_dict` v0.3."""
    name = str(item.get("name", ""))
    category = str(item.get("category", "overhead"))
    period = str(item.get("period", "one-time"))
    try:
        amount = to_decimal(float(item.get("amount", 0.0)))
        currency = Currency(str(item.get("currency", "RUB")))
    except ValueError as exc:
        raise LegacyInputError(str(exc)) from exc
    if category not in {"overhead", LEGACY_EXTRA, *KIND_BY_CATEGORY}:
        raise LegacyInputError(f"category={category!r}")
    if period not in PERIOD_BY_LEGACY:
        raise LegacyInputError(f"period={period!r}")
    if amount < 0:
        raise LegacyInputError(f"amount не может быть отрицательной: {amount}")
    return name, category, amount, currency, period


def overhead_from_item(item: LegacyDict) -> Overhead:
    """Накладной расход v0.3 → Overhead (`ExpenseManager.add_firm_overhead`)."""
    name, category, amount, currency, period = _expense_fields(item)
    if category != "overhead":
        raise LegacyInputError(f"Ожидалась category='overhead', получено {category!r}")
    return Overhead(name=name, amount=amount, currency=currency, period=PERIOD_BY_LEGACY[period])


def disbursement_from_item(item: LegacyDict, mapping: LegacyMapping) -> Disbursement | None:
    """Произвольный расход проекта v0.3 → Disbursement; None — «Прочее», которое v0.3 терял."""
    name, category, amount, currency, _ = _expense_fields(item)
    if category == "overhead":
        raise LegacyInputError("Для накладных фирмы используйте add_firm_overhead().")
    if category == LEGACY_EXTRA:
        if not mapping.extra_as_own:
            return None
        kind = DisbursementKind.OWN
    else:
        kind = KIND_BY_CATEGORY[category]
    return Disbursement(name=name, kind=kind, amount=amount, currency=currency)


# ---------------------------------------------------------------------------
# Проект
# ---------------------------------------------------------------------------
def pricing_from_scenario(scenario: LegacyDict) -> tuple[PricingModel, Decimal]:
    """Модель ценообразования и целевая маржа; маржа в v0.3 — целые проценты."""
    if scenario["pricing_model"] == "fixed":
        pct = int(scenario.get("target_margin_pct", TARGET_MARGIN_DEFAULT_PCT))
        return FixedPricing(), Decimal(pct) / Decimal(100)
    return HourlyPricing(), DEFAULT_TARGET_MARGIN


def stages_from_scenario(scenario: LegacyDict, mapping: LegacyMapping) -> tuple[Stage, ...]:
    """Этапы v0.3 `{name, executor, hours}` → этапы ядра.

    Одноимённые этапы v0.3 — это пирамида, которую иначе было не задать
    (L-05): при `merge_stage_names` они сливаются в один этап, иначе
    получают суффиксы.
    """
    assignments: dict[str, list[Assignment]] = {}
    for item in scenario["stages"]:
        name = item["name"]
        if not mapping.merge_stage_names:
            base, index = name, 1
            while name in assignments:
                index += 1
                name = f"{base} #{index}"
        assignments.setdefault(name, []).append(
            Assignment(employee=item["executor"], hours=to_decimal(item["hours"]))
        )
    return tuple(Stage(name=name, assignments=tuple(items)) for name, items in assignments.items())


def scenario_to_core(
    scenario: LegacyDict, mapping: LegacyMapping = FINAL
) -> tuple[Scenario, list[str]]:
    """JSON-сценарий v0.3 → Scenario ядра и предупреждения о пропущенных записях.

    Тексты предупреждений повторяют префиксы v0.3; причина — своя.
    """
    warnings: list[str] = []
    params = scenario["jurisdiction_params"]
    jurisdiction = jurisdiction_from_params(params)
    calc_country = scenario.get("calc_country")
    project_country = jurisdiction.calc_country(
        None if calc_country is None else Country(calc_country)
    )

    overheads: list[Overhead] = []
    for item in scenario.get("firm_expenses", []):
        try:
            overheads.append(overhead_from_item(item))
        except (LegacyInputError, ValidationError) as exc:
            warnings.append(f"Пропущен накладной расход: {exc}")

    disbursements: list[Disbursement] = []
    selected_duties = scenario.get("selected_duties", {})
    for country in jurisdiction.countries:
        disbursements.extend(duty_disbursements(country, selected_duties.get(country.value, [])))
    for item in scenario.get("custom_expenses", []):
        try:
            converted = disbursement_from_item(item, mapping)
        except (LegacyInputError, ValidationError) as exc:
            warnings.append(f"Пропущен произвольный расход: {exc}")
            continue
        if converted is not None:
            disbursements.append(converted)

    team = scenario["team"]
    billable_hours = to_decimal(
        float(scenario.get("billable_hours_per_month", LEGACY_DEFAULT_BILLABLE_HOURS))
    )
    currency = (
        HOME_CURRENCY[jurisdiction.mode] if mapping.convert_currency else project_country.currency
    )
    firm = Firm(
        jurisdiction=jurisdiction,
        team=tuple(
            employee_from_member(
                member,
                currency=currency,
                mapping=mapping,
                neutral_hours=billable_hours / max(len(team), 1),
            )
            for member in team
        ),
        overheads=tuple(overheads),
        exchange_rates=exchange_rates(params, mapping),
    )
    project_info = scenario.get("project", {})
    pricing, target_margin = pricing_from_scenario(scenario)
    project = Project(
        name=project_info.get("name", ""),
        client=project_info.get("client", ""),
        description=project_info.get("description", ""),
        pricing=pricing,
        target_margin=target_margin,
        stages=stages_from_scenario(scenario, mapping),
        disbursements=tuple(disbursements),
        calc_country=None if calc_country is None else Country(calc_country),
    )
    return Scenario(firm=firm, project=project), warnings
