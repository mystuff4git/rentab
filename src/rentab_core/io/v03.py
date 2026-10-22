"""Импорт профиля фирмы v0.3 (`data/firm_profile.json`, L-10, L-16).

Профиль v0.3 — плоский срез `session_state`: параметры юрисдикции, команда,
накладные, биллируемые часы фирмы, курс тенге, модель цены (русской подписью)
и целевая маржа (целыми процентами). Пример — `tests/fixtures/legacy/
v03_firm_profile.json`. Это единственное место ядра, которое знает формат v0.3.

Правила перевода — как у characterization-тестов (ADR 0004, `FINAL`):

- себестоимость часа v0.3 — стоимость контрактного часа; утилизация — 65%;
- если `cost_rate` совпадает с калькулятором ФОТ v0.3 (оклад × (1 + взносы) /
  контрактные часы), сотрудник переносится с окладом, иначе — суммой в месяц
  `cost_rate × часы`;
- плоская ставка взносов v0.3 по умолчанию (30% и т. п., L-08) пересчитывается
  по реестру 2026 года; ставку, заданную вручную, импорт не меняет;
- биллируемые часы фирмы не переносятся: ёмкость считается по команде;
- модель цены и целевая маржа становятся настройками пустого проекта.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

from pydantic import ValidationError

from rentab_core.costing import billable_capacity
from rentab_core.errors import ProfileImportError
from rentab_core.io.migrations import ImportNote, info, warning
from rentab_core.io.schema import ProjectRecord, Workspace
from rentab_core.models import (
    DEFAULT_CONTRACT_HOURS_PER_MONTH,
    DEFAULT_TARGET_MARGIN,
    DEFAULT_TARGET_UTILIZATION,
    ROLE_LABELS,
    AusnProfile,
    Compensation,
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
    Salary,
    TaxProfile,
    UsnProfile,
    employment_country,
)
from rentab_core.money import ONE, PERCENT, ZERO, Currency, format_number, round_to, to_decimal
from rentab_core.tax import employer_contribution_rate

LegacyDict = Mapping[str, Any]

# Версия «0» в цепочке версий: файл — профиль v0.3.
LEGACY_V03_VERSION: Final = 0
# Ключи профиля v0.3 (modules/profile.py:28–37).
V03_PROFILE_KEYS: tuple[str, ...] = (
    "jurisdiction_params",
    "team",
    "firm_expenses",
    "billable_hours_per_month",
    "contract_hours_total",
    "exchange_rate_rub_per_kzt",
    "pricing_model",
    "target_margin",
)
# По этим ключам файл узнаётся как профиль v0.3.
V03_MARKER_KEYS = frozenset({"jurisdiction_params", "team", "firm_expenses"})

MODE_BY_V03: Mapping[str, JurisdictionMode] = {
    "RF": JurisdictionMode.RF,
    "KZ": JurisdictionMode.KZ,
    "Both": JurisdictionMode.BOTH,
}
# Валюта окладов и ставок: v0.3 её не хранил, фирма «Оба» считалась рублёвой.
HOME_CURRENCY: Mapping[JurisdictionMode, Currency] = {
    JurisdictionMode.RF: Currency.RUB,
    JurisdictionMode.KZ: Currency.KZT,
    JurisdictionMode.BOTH: Currency.RUB,
}
ROLE_BY_LABEL: Mapping[str, Role] = {label: role for role, label in ROLE_LABELS.items()}
PERIOD_BY_V03: Mapping[str, Period] = {
    "monthly": Period.MONTHLY,
    "annual": Period.ANNUAL,
    "one-time": Period.ONE_TIME,
}
V03_OVERHEAD = "overhead"
# Модель цены v0.3: подпись радиокнопки (как хранил профиль) или внутренний ключ.
PRICING_BY_V03: Mapping[str, str] = {
    "Биллинговая (часы × ставка)": "billing",
    "billing": "billing",
    "Фиксированная (издержки + маржа)": "fixed",
    "fixed": "fixed",
}
# Ставки взносов v0.3 по умолчанию (legacy/rentab_v03/modules/jurisdiction.py:
# SOCIAL_CONTRIBUTIONS, PAYROLL_EMPLOYER, get_employer_contribution_rate). Только
# чтобы узнать ставку, которую пользователь не менял; в расчёт они не идут.
V03_RF_CONTRIBUTION_RATES: Mapping[str, Decimal] = {"standard": Decimal("0.30"), "zero": ZERO}
V03_RF_DEFAULT_CONTRIBUTION_RATE = Decimal("0.30")
V03_KZ_CONTRIBUTION_RATE = Decimal("0.115")
# Расхождение cost_rate с калькулятором ФОТ v0.3, которое считается совпадением:
# копейка на час (v0.3 хранил float).
COST_RATE_TOLERANCE = Decimal("0.01")


def is_v03_profile(raw: LegacyDict) -> bool:
    """Похож ли объект JSON на профиль v0.3."""
    return "format" not in raw and bool(V03_MARKER_KEYS & raw.keys())


def _number(value: object, what: str) -> Decimal:
    """Число из профиля v0.3 как Decimal."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal | str):
        raise ProfileImportError(f"Профиль v0.3: {what} — не число ({value!r})")
    try:
        result = to_decimal(value)
    except (ArithmeticError, ValueError) as exc:
        raise ProfileImportError(f"Профиль v0.3: {what} — не число ({value!r})") from exc
    if not result.is_finite():
        raise ProfileImportError(f"Профиль v0.3: {what} — не конечное число ({value!r})")
    return result


def _pct(value: Decimal) -> str:
    """Доля как процент для замечаний: 0.2455 → «24,55%»."""
    return f"{format_number(round_to(value * PERCENT))}%"


# ---------------------------------------------------------------------------
# Юрисдикция
# ---------------------------------------------------------------------------
def _tax_profile(params: LegacyDict | None, where: str) -> TaxProfile | None:
    """Параметры режима v0.3 (`jurisdiction_params["rf"|"kz"]`) → профиль ядра."""
    if not params:
        return None
    country, regime = params.get("country"), params.get("regime")
    try:
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
    except ValidationError as exc:
        raise ProfileImportError(f"Профиль v0.3: неверные параметры режима {where}: {exc}") from exc
    raise ProfileImportError(f"Профиль v0.3: неизвестный режим {where}: {country}/{regime}")


def _jurisdiction(params: LegacyDict) -> Jurisdiction:
    """`jurisdiction_params` v0.3 → Jurisdiction."""
    mode_key = params.get("jurisdiction", "RF")
    if mode_key not in MODE_BY_V03:
        raise ProfileImportError(f"Профиль v0.3: неизвестная юрисдикция {mode_key!r}")
    rf = _tax_profile(params.get("rf"), "РФ")
    kz = _tax_profile(params.get("kz"), "РК")
    if rf is not None and isinstance(rf, KzOurProfile):
        raise ProfileImportError("Профиль v0.3: в параметрах РФ указан режим РК")
    if kz is not None and not isinstance(kz, KzOurProfile):
        raise ProfileImportError("Профиль v0.3: в параметрах РК указан режим РФ")
    return Jurisdiction(mode=MODE_BY_V03[mode_key], rf=rf, kz=kz)


def v03_default_contribution_rate(params: LegacyDict) -> Decimal:
    """Ставка взносов, которую v0.3 подставлял по умолчанию для юрисдикции.

    Повторяет `get_employer_contribution_rate` v0.3: РФ и «Оба» — по тарифу
    режима (АУСН и НПД — 0), РК — ООСМС + СО + ОПВР.
    """
    if params.get("jurisdiction", "RF") == "KZ":
        return V03_KZ_CONTRIBUTION_RATE
    rf = params.get("rf") or {}
    if rf.get("regime", "USN") in {"AUSN", "NPD"}:
        return ZERO
    return V03_RF_CONTRIBUTION_RATES.get(
        rf.get("social_contributions", "standard"), V03_RF_DEFAULT_CONTRIBUTION_RATE
    )


# ---------------------------------------------------------------------------
# Команда
# ---------------------------------------------------------------------------
def _compensation(
    member: LegacyDict,
    hours: Decimal,
    *,
    jurisdiction: Jurisdiction,
    currency: Currency,
    default_rate: Decimal,
    notes: list[ImportNote],
) -> Compensation:
    """Полная стоимость сотрудника: оклад и взносы или сумма cost_rate × часы."""
    name = member["name"]
    cost_rate = _number(member.get("cost_rate", 0), f"cost_rate сотрудника «{name}»")
    gross = _number(member.get("gross_salary", 0), f"оклад сотрудника «{name}»")
    raw_rate = member.get("employer_contribution_rate")
    rate = None if raw_rate is None else _number(raw_rate, f"ставка взносов «{name}»")
    if gross > 0 and rate is not None and rate >= 0:
        fot_rate = gross * (ONE + rate) / hours
        if abs(fot_rate - cost_rate) <= COST_RATE_TOLERANCE:
            if rate == default_rate:
                current = employer_contribution_rate(
                    jurisdiction, employment_country(currency), gross
                )
                if current != rate:
                    notes.append(
                        info(
                            f"«{name}»: ставка взносов {_pct(rate)} — плоская ставка v0.3 "
                            f"(L-08); по реестру 2026 года для этого оклада — {_pct(current)}."
                        )
                    )
                rate = current
            else:
                notes.append(
                    info(f"«{name}»: ставка взносов {_pct(rate)} задана вручную — оставлена.")
                )
            return Salary(gross_salary=gross, employer_contribution_rate=rate)
        notes.append(
            warning(
                f"«{name}»: себестоимость часа {format_number(cost_rate)} введена вручную и "
                "расходится с окладом — стоимость в месяц = себестоимость часа × контрактные часы."
            )
        )
    return MonthlyCost(amount=cost_rate * hours)


def _employee(
    member: object,
    index: int,
    *,
    jurisdiction: Jurisdiction,
    params: LegacyDict,
    notes: list[ImportNote],
) -> Employee:
    """Член команды v0.3 → Employee."""
    if not isinstance(member, Mapping) or not isinstance(member.get("name"), str):
        raise ProfileImportError(f"Профиль v0.3: сотрудник № {index + 1} без имени")
    name = member["name"]
    role_label = member.get("role")
    if not isinstance(role_label, str) or role_label not in ROLE_BY_LABEL:
        raise ProfileImportError(f"Профиль v0.3: у «{name}» неизвестная роль {role_label!r}")
    hours = _number(
        member.get("contract_hours_per_month", DEFAULT_CONTRACT_HOURS_PER_MONTH),
        f"контрактные часы «{name}»",
    )
    if hours <= 0:
        raise ProfileImportError(f"Профиль v0.3: у «{name}» контрактные часы должны быть > 0")
    currency = HOME_CURRENCY[jurisdiction.mode]
    compensation = _compensation(
        member,
        hours,
        jurisdiction=jurisdiction,
        currency=currency,
        default_rate=v03_default_contribution_rate(params),
        notes=notes,
    )
    try:
        return Employee(
            name=name,
            role=ROLE_BY_LABEL[role_label],
            compensation=compensation,
            currency=currency,
            billing_rate=_number(member.get("billing_rate", 0), f"ставка для клиента «{name}»"),
            contract_hours_per_month=hours,
            target_utilization=DEFAULT_TARGET_UTILIZATION,
        )
    except ValidationError as exc:
        raise ProfileImportError(
            f"Профиль v0.3: неверные данные сотрудника «{name}»: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Накладные, курс, проект
# ---------------------------------------------------------------------------
def _overheads(items: object, notes: list[ImportNote]) -> list[Overhead]:
    """Накладные фирмы v0.3; расходы других категорий пропускаются."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise ProfileImportError("Профиль v0.3: firm_expenses — не список")
    result = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ProfileImportError(f"Профиль v0.3: расход № {index + 1} — не объект")
        name = str(item.get("name", ""))
        category = item.get("category", V03_OVERHEAD)
        if category != V03_OVERHEAD:
            notes.append(
                warning(f"Расход «{name}» (категория {category!r}) — не накладной, пропущен.")
            )
            continue
        period = item.get("period", "monthly")
        if period not in PERIOD_BY_V03:
            raise ProfileImportError(
                f"Профиль v0.3: у расхода «{name}» неизвестный период {period!r}"
            )
        try:
            result.append(
                Overhead(
                    name=name,
                    amount=_number(item.get("amount", 0), f"сумма расхода «{name}»"),
                    currency=item.get("currency", Currency.RUB),
                    period=PERIOD_BY_V03[period],
                )
            )
        except ValidationError as exc:
            raise ProfileImportError(f"Профиль v0.3: неверный расход «{name}»: {exc}") from exc
    return result


def _exchange_rates(raw: LegacyDict, params: LegacyDict) -> ExchangeRates:
    """Курс ₽ за 1 ₸: из корня профиля или из параметров юрисдикции."""
    value = raw.get("exchange_rate_rub_per_kzt")
    if value is None:
        value = params.get("exchange_rate_rub_per_kzt")
    if value is None:
        return ExchangeRates()
    rate = _number(value, "курс ₽ за 1 ₸")
    return ExchangeRates(rub_per_kzt=rate if rate > 0 else None)


def _project(raw: LegacyDict, notes: list[ImportNote]) -> Project:
    """Пустой проект с моделью цены и целевой маржой профиля (L-16)."""
    label = raw.get("pricing_model")
    key = PRICING_BY_V03.get(label, "billing") if isinstance(label, str) else "billing"
    if label is not None and label not in PRICING_BY_V03:
        notes.append(warning(f"Неизвестная модель цены v0.3 {label!r} — выбрана почасовая."))
    pricing: PricingModel = FixedPricing() if key == "fixed" else HourlyPricing()
    margin = DEFAULT_TARGET_MARGIN
    if raw.get("target_margin") is not None:
        margin = _number(raw["target_margin"], "целевая маржа") / PERCENT
        if not ZERO <= margin < ONE:
            notes.append(
                warning(
                    f"Целевая маржа {format_number(margin * PERCENT)}% вне диапазона — "
                    f"взята {format_number(DEFAULT_TARGET_MARGIN * PERCENT)}%."
                )
            )
            margin = DEFAULT_TARGET_MARGIN
    return Project(pricing=pricing, target_margin=margin)


def import_v03(raw: LegacyDict) -> tuple[Workspace, list[ImportNote]]:
    """Профиль фирмы v0.3 → данные текущей схемы и замечания перевода.

    Raises:
        ProfileImportError: Значения профиля неверны (неизвестный режим, роль,
            период, отрицательная сумма).
    """
    notes = [info("Файл — профиль фирмы Rentab v0.3: данные переведены в текущий формат.")]
    params = raw.get("jurisdiction_params") or {}
    if not isinstance(params, Mapping):
        raise ProfileImportError("Профиль v0.3: jurisdiction_params — не объект")
    jurisdiction = _jurisdiction(params)
    members = raw.get("team")
    if members is None:
        members = []
    if not isinstance(members, list):
        raise ProfileImportError("Профиль v0.3: team — не список")
    team = [
        _employee(member, index, jurisdiction=jurisdiction, params=params, notes=notes)
        for index, member in enumerate(members)
    ]
    if team:
        notes.append(
            info(
                "Утилизация в v0.3 не задавалась: у всех сотрудников "
                f"{_pct(DEFAULT_TARGET_UTILIZATION)}. Себестоимость биллируемого часа = "
                "стоимость контрактного часа / утилизация (L-01) — проверьте утилизацию."
            )
        )
    firm_hours = raw.get("billable_hours_per_month")
    if firm_hours is not None:
        notes.append(
            info(
                f"В v0.3 накладные делились на {format_number(_number(firm_hours, 'часы фирмы'))} "
                "биллируемых ч в месяц. Теперь — на ёмкость команды (контрактные часы × "
                f"утилизация): {format_number(billable_capacity(team))} ч."
            )
        )
    unknown = sorted(set(raw) - set(V03_PROFILE_KEYS))
    if unknown:
        notes.append(info(f"Не перенесены неизвестные поля: {', '.join(unknown)}."))
    firm = Firm(
        jurisdiction=jurisdiction,
        team=tuple(team),
        overheads=tuple(_overheads(raw.get("firm_expenses"), notes)),
        exchange_rates=_exchange_rates(raw, params),
    )
    workspace = Workspace(firm=firm, projects=(ProjectRecord(project=_project(raw, notes)),))
    return workspace, notes
