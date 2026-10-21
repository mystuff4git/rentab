"""Адаптер между форматом v0.3 и моделями ядра для проверки эквивалентности.

- `scenario_to_core`: JSON-сценарий characterization-тестов (формат
  `scenarios/README.md`) → `Scenario` ядра. Повторяет, как страница
  «Проект» v0.3 собирает входные данные: дефолты `Expense.from_dict`,
  пропуск некорректных расходов с предупреждением, пошлины из каталогов.
- `estimate_to_results` и `dashboard_to_legacy`: результат ядра → словари
  `results` и `dashboard_view` v0.3 (как в снимках).

Ядро о формате v0.3 ничего не знает: всё соответствие собрано здесь.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from rentab_core.analysis.dashboard import (
    Dashboard,
    FixedDashboard,
    HourlyDashboard,
    Profitability,
)
from rentab_core.duties import duty_disbursements
from rentab_core.models import (
    ROLE_LABELS,
    AusnProfile,
    Country,
    Disbursement,
    DisbursementKind,
    Employee,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Payroll,
    Period,
    PricingModel,
    Project,
    Role,
    Scenario,
    Stage,
    TaxProfile,
    UsnProfile,
)
from rentab_core.models import (
    Assignment as CoreAssignment,
)
from rentab_core.money import CURRENCY_SYMBOLS, Currency, to_decimal
from rentab_core.results import Estimate, FixedPriceEstimate, HourlyEstimate

LegacyDict = Mapping[str, Any]

ROLE_BY_LABEL: Mapping[str, Role] = {label: role for role, label in ROLE_LABELS.items()}
MODE_BY_LEGACY: Mapping[str, JurisdictionMode] = {
    "RF": JurisdictionMode.RF,
    "KZ": JurisdictionMode.KZ,
    "Both": JurisdictionMode.BOTH,
}
LEGACY_BY_MODE: Mapping[JurisdictionMode, str] = {v: k for k, v in MODE_BY_LEGACY.items()}
PERIOD_BY_LEGACY: Mapping[str, Period] = {
    "monthly": Period.MONTHLY,
    "annual": Period.ANNUAL,
    "one-time": Period.ONE_TIME,
}
KIND_BY_CATEGORY: Mapping[str, DisbursementKind] = {
    "disbursement_billable": DisbursementKind.BILLABLE,
    "disbursement_own": DisbursementKind.OWN,
    "project_extra": DisbursementKind.EXTRA,
}
CATEGORY_BY_KIND: Mapping[DisbursementKind, str] = {v: k for k, v in KIND_BY_CATEGORY.items()}
# Расходы проекта в v0.3 всегда разовые (форма и каталог ставят "one-time").
PROJECT_EXPENSE_PERIOD = "one-time"
PROFITABILITY_LABELS: Mapping[Profitability, str] = {
    Profitability.PROFITABLE: "рентабелен",
    Profitability.BREAKEVEN: "безубыточность",
    Profitability.LOSS: "убыточен",
}
# Пункт 02_Проект.py:67–69: целевая маржа хранится целыми процентами.
TARGET_MARGIN_DEFAULT_PCT = 30
LEGACY_DEFAULT_BILLABLE_HOURS = 160.0


class LegacyInputError(ValueError):
    """Запись v0.3, которую страница «Проект» пропустила бы с предупреждением."""


# ---------------------------------------------------------------------------
# v0.3 → ядро
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
    rate = params.get("exchange_rate_rub_per_kzt")
    return Jurisdiction(
        mode=MODE_BY_LEGACY[params.get("jurisdiction", "RF")],
        rf=rf,
        kz=kz,
        exchange_rate_rub_per_kzt=None if rate is None else to_decimal(rate),
    )


def employee_from_member(member: LegacyDict) -> Employee:
    """Член команды v0.3 (`session_state["team"]`) → Employee."""
    payroll = None
    if "gross_salary" in member:
        payroll = Payroll(
            gross_salary=to_decimal(member["gross_salary"]),
            contract_hours_per_month=to_decimal(member["contract_hours_per_month"]),
            employer_contribution_rate=to_decimal(member["employer_contribution_rate"]),
        )
    return Employee(
        name=member["name"],
        role=ROLE_BY_LABEL[member["role"]],
        billing_rate=to_decimal(member["billing_rate"]),
        cost_rate=to_decimal(member["cost_rate"]),
        payroll=payroll,
    )


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
    if category not in {"overhead", *KIND_BY_CATEGORY}:
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


def disbursement_from_item(item: LegacyDict) -> Disbursement:
    """Произвольный расход проекта v0.3 → Disbursement (`add_project_expense`)."""
    name, category, amount, currency, _ = _expense_fields(item)
    if category == "overhead":
        raise LegacyInputError("Для накладных фирмы используйте add_firm_overhead().")
    return Disbursement(
        name=name, kind=KIND_BY_CATEGORY[category], amount=amount, currency=currency
    )


def pricing_from_scenario(scenario: LegacyDict) -> PricingModel:
    """Модель ценообразования; маржа в v0.3 — целые проценты."""
    if scenario["pricing_model"] == "fixed":
        pct = int(scenario.get("target_margin_pct", TARGET_MARGIN_DEFAULT_PCT))
        return FixedPricing(target_margin=Decimal(pct) / Decimal(100))
    return HourlyPricing()


def scenario_to_core(scenario: LegacyDict) -> tuple[Scenario, list[str]]:
    """JSON-сценарий v0.3 → Scenario ядра и предупреждения о пропущенных записях.

    Тексты предупреждений повторяют префиксы v0.3; причина — своя.
    """
    warnings: list[str] = []
    jurisdiction = jurisdiction_from_params(scenario["jurisdiction_params"])

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
            disbursements.append(disbursement_from_item(item))
        except (LegacyInputError, ValidationError) as exc:
            warnings.append(f"Пропущен произвольный расход: {exc}")

    firm = Firm(
        jurisdiction=jurisdiction,
        team=tuple(employee_from_member(m) for m in scenario["team"]),
        overheads=tuple(overheads),
        billable_hours_per_month=to_decimal(
            float(scenario.get("billable_hours_per_month", LEGACY_DEFAULT_BILLABLE_HOURS))
        ),
    )
    project_info = scenario.get("project", {})
    calc_country = scenario.get("calc_country")
    project = Project(
        name=project_info.get("name", ""),
        client=project_info.get("client", ""),
        description=project_info.get("description", ""),
        pricing=pricing_from_scenario(scenario),
        stages=tuple(
            Stage(
                name=stage["name"],
                assignments=(
                    CoreAssignment(employee=stage["executor"], hours=to_decimal(stage["hours"])),
                ),
            )
            for stage in scenario["stages"]
        ),
        disbursements=tuple(disbursements),
        calc_country=None if calc_country is None else Country(calc_country),
    )
    return Scenario(firm=firm, project=project), warnings


# ---------------------------------------------------------------------------
# ядро → v0.3
# ---------------------------------------------------------------------------
def _f(value: Decimal) -> float:
    return float(value)


def _base_results(estimate: Estimate) -> dict[str, Any]:
    return {
        "project_name": estimate.project_name,
        "client": estimate.client,
        "description": estimate.description,
        "calc_country": estimate.calc_country.value,
        "jurisdiction_mode": LEGACY_BY_MODE[estimate.jurisdiction_mode],
        "currency": CURRENCY_SYMBOLS[estimate.currency],
        "currency_code": estimate.currency.value,
        "blended_rate": _f(estimate.blended_rate),
        "leverage": _f(estimate.leverage),
        "total_hours": _f(estimate.total_hours),
        "overhead_rate": _f(estimate.overhead_rate),
        "team_with_hours": [
            {
                "stage_name": line.stage_name,
                "name": line.employee_name,
                "role": ROLE_LABELS[line.role],
                "billing_rate": _f(line.billing_rate),
                "cost_rate": _f(line.cost_rate),
                "hours": _f(line.hours),
            }
            for line in estimate.lines
        ],
        "project_expenses": [
            {
                "name": item.name,
                "category": CATEGORY_BY_KIND[item.kind],
                "amount": _f(item.amount),
                "currency": item.currency.value,
                "period": PROJECT_EXPENSE_PERIOD,
                "billable": item.billable,
            }
            for item in estimate.disbursements
        ],
    }


def estimate_to_results(estimate: Estimate) -> dict[str, Any]:
    """Смета ядра → словарь `st.session_state["results"]` v0.3."""
    results = _base_results(estimate)
    if isinstance(estimate, FixedPriceEstimate):
        results.update(
            pricing_model="fixed",
            regime_label=estimate.regime_label,
            fixed_price=_f(estimate.fixed_price),
            total_costs=_f(estimate.total_costs),
            direct_labor=_f(estimate.direct_labor),
            overheads_alloc=_f(estimate.overheads_alloc),
            disbursements_own=_f(estimate.disbursements_own),
            tax=_f(estimate.tax.total_tax),
            nne=_f(estimate.nne),
            actual_margin=_f(estimate.actual_margin),
            target_margin=_f(estimate.target_margin),
            stage_flags=[
                {
                    "stage_name": stage.stage_name,
                    "stage_costs": _f(stage.costs),
                    "stage_revenue_share": _f(stage.revenue_share),
                    "stage_margin": _f(stage.margin),
                    "flag": stage.flag.value,
                }
                for stage in estimate.stages
            ],
            disbursements=_f(estimate.disbursements_billed),
        )
        return results
    assert isinstance(estimate, HourlyEstimate)
    results.update(
        pricing_model="billing",
        regime_label=estimate.regime_label,
        gross_revenue=_f(estimate.gross_revenue),
        direct_labor=_f(estimate.direct_labor),
        overheads_alloc=_f(estimate.overheads_alloc),
        disbursements=_f(estimate.disbursements_billed),
        disbursements_own=_f(estimate.disbursements_own),
        income_tax=_f(estimate.tax.income_tax),
        vat=_f(estimate.tax.vat),
        tax=_f(estimate.tax.total_tax),
        taxable_base=_f(estimate.tax.taxable_base),
        nne=_f(estimate.nne),
        total_client=_f(estimate.total_client),
    )
    return results


def dashboard_to_legacy(view: Dashboard) -> dict[str, Any]:
    """Показатели дашборда ядра → словарь `dashboard_view` legacy_runner."""
    if isinstance(view, FixedDashboard):
        return {
            "target_margin_pct": _f(view.target_margin_pct),
            "actual_margin_pct": _f(view.actual_margin_pct),
            "price_structure": {
                "direct_labor": _f(view.price_structure.direct_labor),
                "indirect": _f(view.price_structure.indirect),
                "tax": _f(view.price_structure.tax),
                "profit": _f(view.price_structure.profit),
            },
            "stage_rows": [
                {
                    "stage_name": row.stage_name,
                    "hours": _f(row.hours),
                    "stage_costs": _f(row.costs),
                    "stage_revenue_share": _f(row.revenue_share),
                    "stage_margin_pct": _f(row.margin_pct),
                    "flag": row.flag.value,
                }
                for row in view.stage_rows
            ],
            "profitability": PROFITABILITY_LABELS[view.profitability],
        }
    assert isinstance(view, HourlyDashboard)
    return {
        "nne_margin_pct": None if view.nne_margin_pct is None else _f(view.nne_margin_pct),
        "effective_tax_rate_pct": _f(view.effective_tax_rate_pct),
        "stage_rows": [
            {
                "stage_name": row.stage_name,
                "executor": f"{row.employee_name} ({ROLE_LABELS[row.role]})",
                "hours": _f(row.hours),
                "billing_rate": _f(row.billing_rate),
                "revenue": _f(row.revenue),
                "margin_pct": _f(row.margin_pct),
            }
            for row in view.stage_rows
        ],
        "stage_totals": {
            "hours": _f(view.stage_totals.hours),
            "revenue": _f(view.stage_totals.revenue),
            "margin_pct": _f(view.stage_totals.margin_pct),
        },
        "profitability": PROFITABILITY_LABELS[view.profitability],
    }
