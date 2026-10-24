"""Подсказки KPI: формула из docs/methodology.md и цифры проекта.

Каждая функция возвращает Markdown для подсказки «?» у `st.metric`: строка с
формулой, строка с подстановкой и ссылка на раздел методологии. Здесь нет
расчётов, только показ: значения берутся из сметы и отчётов ядра.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from rentab_core.analysis.plan_fact import BillingRule, PlanFact
from rentab_core.analysis.sensitivity import BreakEven, BreakEvenStatus
from rentab_core.costing import EmployeeCost
from rentab_core.models import JUNIOR_ROLES, SENIOR_ROLES, CappedPricing, FixedPricing, VatMode
from rentab_core.money import CURRENCY_SYMBOLS, decimal_sum
from rentab_core.results import Estimate, EstimateLine, TaxBase
from ui.labels import (
    DASH,
    hours_label,
    money,
    money_rate,
    number_label,
    pct_label,
    signed_money,
)

LEVERAGE_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.01")


def _text(formula: str, substitution: str, section: str, note: str = "") -> str:
    """Подсказка: формула, подстановка, пояснение и раздел методологии."""
    parts = [f"**{formula}**", substitution]
    if note:
        parts.append(note)
    parts.append(f"Методология, раздел {section}.")
    return "\n\n".join(part for part in parts if part)


def _sym(estimate: Estimate) -> str:
    return CURRENCY_SYMBOLS[estimate.currency]


# ---------------------------------------------------------------------------
# Смета
# ---------------------------------------------------------------------------
def revenue(estimate: Estimate) -> str:
    """Выручка без НДС R по модели цены (раздел 7)."""
    sym = _sym(estimate)
    pricing = estimate.pricing
    value = money(estimate.revenue, sym)
    included = estimate.vat_mode == VatMode.INCLUDED
    vat_note = (
        f"Цены «включая НДС»: R = цена / (1 + v), v = {pct_label(estimate.tax.terms.vat_rate)}."
        if included
        else "НДС начисляется сверху и в выручку не входит."
    )
    if isinstance(pricing, CappedPricing):
        return _text(
            "R = min(Σ ставка × часы; потолок)",
            f"min({money(estimate.shadow_value_net, sym)}; потолок) = {value}",
            "7.2",
            vat_note,
        )
    if isinstance(pricing, FixedPricing) and pricing.price is not None:
        return _text("R = согласованная цена", f"R = {value}", "7.4", vat_note)
    if isinstance(pricing, FixedPricing):
        tax = estimate.tax
        if tax.terms.base == TaxBase.INCOME:
            formula = "P = max((C − S) / (1 − t − m*); C / (1 − (1 − κ)·t − m*))"
        else:
            formula = "P = max((C·(1 − t) + t·N) / (1 − t − m*); C / (1 − t_min − m*))"
        return _text(
            formula,
            f"C = {money(estimate.total_costs, sym)}, t = {pct_label(tax.terms.rate)}, "
            f"m* = {pct_label(estimate.target_margin, Decimal(1))} → R = {value}",
            "7.3",
            "Цена «издержки + маржа»: NNE = m* × R. " + vat_note,
        )
    return _text(
        "R = Σ ставка × часы",
        f"R = {money(estimate.shadow_value_net, sym)} за {hours_label(estimate.total_hours)}",
        "7.1",
        vat_note,
    )


def vat_and_billed(estimate: Estimate) -> str:
    """НДС и возмещаемые расходы (раздел 4)."""
    sym = _sym(estimate)
    return _text(
        "НДС = v × R;  D — возмещаемые расходы",
        f"НДС = {pct_label(estimate.tax.terms.vat_rate)} × {money(estimate.revenue, sym)} = "
        f"{money(estimate.vat, sym)}; D = {money(estimate.disbursements_billed, sym)}",
        "4",
        "Транзит: НДС и пошлины не доход и не расход фирмы.",
    )


def total_client(estimate: Estimate) -> str:
    """К оплате клиентом (раздел 4)."""
    sym = _sym(estimate)
    return _text(
        "К оплате = R + НДС + D",
        f"{money(estimate.revenue, sym)} + {money(estimate.vat, sym)} + "
        f"{money(estimate.disbursements_billed, sym)} = {money(estimate.total_client, sym)}",
        "4",
    )


def costs(estimate: Estimate) -> str:
    """Издержки проекта C (раздел 3.4)."""
    sym = _sym(estimate)
    return _text(
        "C = Labor + ρ × Σ часов + Own",
        f"{money(estimate.direct_labor, sym)} + {money(estimate.overheads_alloc, sym)} + "
        f"{money(estimate.disbursements_own, sym)} = {money(estimate.total_costs, sym)}",
        "3.4",
        "Labor — себестоимость биллируемых часов, ρ — ставка накладных, Own — собственные "
        "расходы проекта.",
    )


def tax(estimate: Estimate) -> str:
    """Налог на доход или прибыль (раздел 5)."""
    sym = _sym(estimate)
    result = estimate.tax
    terms = result.terms
    value = money(result.tax, sym)
    if terms.base == TaxBase.INCOME:
        substitution = (
            f"{pct_label(terms.rate)} × {money(result.revenue, sym)}"
            f" − {money(result.deduction, sym)} = {value}"
            if result.deduction > 0
            else f"{pct_label(terms.rate)} × {money(result.revenue, sym)} = {value}"
        )
        return _text(
            "Tax = max(t × R − S; (1 − κ) × t × R)",
            substitution,
            "5.1",
            "S — взносы на часах проекта, κ — предел уменьшения налога на взносы.",
        )
    substitution = (
        f"max({pct_label(terms.rate)} × {money(result.taxable_base, sym)}; "
        f"{pct_label(terms.min_rate)} × {money(result.revenue, sym)}) = {value}"
    )
    return _text(
        "Tax = max(t × max(R − C + N; 0); t_min × R)",
        substitution,
        "5.1",
        "N — вознаграждение из прибыли; t_min — минимальный налог.",
    )


def nne(estimate: Estimate) -> str:
    """Чистая прибыль проекта (раздел 6)."""
    sym = _sym(estimate)
    return _text(
        "NNE = R − C − Tax",
        f"{money(estimate.revenue, sym)} − {money(estimate.total_costs, sym)} − "
        f"{money(estimate.tax.tax, sym)} = {money(estimate.nne, sym)}",
        "6",
    )


def margin(estimate: Estimate) -> str:
    """Маржа NNE и цель (разделы 6 и 8.5)."""
    sym = _sym(estimate)
    return _text(
        "m = NNE / R",
        f"{money(estimate.nne, sym)} / {money(estimate.revenue, sym)} = "
        f"{pct_label(estimate.margin)}; цель m* = {pct_label(estimate.target_margin, Decimal(1))}",
        "6, 8.5",
        "Зелёный — не ниже цели, жёлтый — ниже цели, красный — убыток.",
    )


def blended_rate(shadow: Decimal, hours: Decimal, value: Decimal, symbol: str) -> str:
    """Средневзвешенная ставка (раздел 6)."""
    return _text(
        "Σ ставка × часы / Σ часов",
        f"{money(shadow, symbol)} / {hours_label(hours)} = {money_rate(value, symbol)}",
        "6",
    )


def leverage(lines: Sequence[EstimateLine], value: Decimal) -> str:
    """Коэффициент рычага (раздел 6)."""
    senior = decimal_sum(line.hours for line in lines if line.role in SENIOR_ROLES)
    junior = decimal_sum(line.hours for line in lines if line.role in JUNIOR_ROLES)
    return _text(
        "часы младших / часы старших",
        f"{hours_label(junior)} / {hours_label(senior)} = {number_label(value, LEVERAGE_QUANTUM)}",
        "6",
        "Младшие — младший юрист и стажёр, старшие — партнёр и старший юрист. Выше рычаг — "
        "больше работы делают те, чей час дешевле.",
    )


def overhead_rate(value: Decimal, overheads: Decimal, capacity: Decimal, symbol: str) -> str:
    """Ставка накладных на биллируемый час фирмы (раздел 3.3)."""
    return _text(
        "ρ = Σ накладных в месяц / Σ биллируемой ёмкости",
        f"{money(overheads, symbol)} / {hours_label(capacity)} = "
        f"{money_rate(value, symbol, RATE_QUANTUM)}",
        "3.3",
        "Ёмкость сотрудника K = контрактные часы × утилизация.",
    )


def effective_tax_rate(estimate: Estimate) -> str:
    """Эффективная ставка налога (раздел 6)."""
    sym = _sym(estimate)
    share = estimate.tax.tax / estimate.revenue if estimate.revenue > 0 else None
    return _text(
        "Tax / R",
        f"{money(estimate.tax.tax, sym)} / {money(estimate.revenue, sym)} = {pct_label(share)}",
        "6",
    )


def underutilization(estimate: Estimate) -> str:
    """Стоимость недозагрузки (раздел 6)."""
    sym = _sym(estimate)
    return _text(
        "Σ (c − ĉ) × часы",
        f"= {money(estimate.underutilization_cost, sym)}",
        "3.2, 6",
        "c — себестоимость биллируемого часа, ĉ — контрактного. Разница оплачивает "
        "небиллируемое время команды.",
    )


# ---------------------------------------------------------------------------
# Сотрудник и фирма
# ---------------------------------------------------------------------------
def monthly_cost(cost: EmployeeCost, symbol: str) -> str:
    """Полная стоимость сотрудника в месяц (раздел 3.1)."""
    return _text(
        "W = оклад + взносы работодателя (или сумма в месяц)",
        f"W = {money(cost.monthly_cost, symbol)}",
        "3.1",
    )


def contract_rate(cost: EmployeeCost, symbol: str) -> str:
    """Себестоимость контрактного часа (раздел 3.2)."""
    return _text(
        "ĉ = W / H",
        f"{money(cost.monthly_cost, symbol)} / {hours_label(cost.contract_hours)} = "
        f"{money_rate(cost.contract_cost_rate, symbol)}",
        "3.2",
    )


def billable_rate(cost: EmployeeCost, symbol: str) -> str:
    """Себестоимость биллируемого часа (раздел 3.2)."""
    return _text(
        "c = W / (H × u)",
        f"{money(cost.monthly_cost, symbol)} / {hours_label(cost.billable_capacity)} = "
        f"{money_rate(cost.cost_rate, symbol)}",
        "3.2",
        "Эта себестоимость идёт в расчёт проекта.",
    )


def underutilization_rate(cost: EmployeeCost, symbol: str) -> str:
    """Недозагрузка на час (раздел 3.2)."""
    return _text(
        "c − ĉ",
        f"{money_rate(cost.cost_rate, symbol)} − {money_rate(cost.contract_cost_rate, symbol)} = "
        f"{money_rate(cost.underutilization_rate, symbol)}",
        "3.2",
        "Сколько каждый проданный час возвращает за небиллируемое время.",
    )


def contract_hours_total(total: Decimal) -> str:
    """Контрактные часы фирмы."""
    return _text("Σ H", f"= {hours_label(total)} в месяц", "3.2")


def capacity_total(total: Decimal) -> str:
    """Биллируемая ёмкость фирмы."""
    return _text("Σ K = Σ H × u", f"= {hours_label(total)} в месяц", "3.3")


def firm_utilization(capacity: Decimal, contract: Decimal) -> str:
    """Утилизация фирмы."""
    share = capacity / contract if contract > 0 else None
    return _text(
        "Σ K / Σ H",
        f"{hours_label(capacity)} / {hours_label(contract)} = {pct_label(share, Decimal('0.1'))}",
        "3.2",
    )


def project_hours(total: Decimal) -> str:
    """Часы проекта."""
    return _text("Σ часов назначений", f"= {hours_label(total)}", "8.1")


# ---------------------------------------------------------------------------
# Аналитика
# ---------------------------------------------------------------------------
def headroom(row: BreakEven, symbol: str) -> str:
    """Запас часов до безубыточности (раздел 16)."""
    binding = [line for line in row.lines if line.beta > 0]
    if row.status == BreakEvenStatus.HEADROOM and binding:
        line = min(binding, key=lambda item: item.alpha / item.beta)
        substitution = (
            f"x* = {money(line.alpha, symbol)} / {money_rate(line.beta, symbol, RATE_QUANTUM)} = "
            f"{hours_label(line.alpha / line.beta)}"
        )
    else:
        substitution = f"Каждый час сверх плана даёт {signed_money(row.nne_per_hour, symbol)} NNE."
    return _text(
        "x* = min(α / β) по ветвям налога",
        substitution,
        "16.5",
        "NNE(x) = α − β·x: α — NNE плана в этой ветви, β — потеря NNE на час сверх плана "
        "(себестоимость часа с накладными, налог, оплата часа клиентом).",
    )


def nne_per_hour(row: BreakEven, symbol: str) -> str:
    """Прибыль дополнительного часа — наклон NNE в точке плана (раздел 16.5)."""
    return _text(
        "dNNE / dx = −β линии, на которой лежит NNE плана",
        f"= {signed_money(row.nne_per_hour, symbol)} на час сверх плана",
        "16.5",
        "Минус — каждый лишний час уменьшает NNE; плюс — час оплачивается и окупается.",
    )


def billed(result: PlanFact, rule_text: str) -> str:
    """Выставлено по факту (раздел 18.2)."""
    sym = CURRENCY_SYMBOLS[result.fact.currency]
    formula = {
        BillingRule.BY_FACT: "R^f = Σ ставка × факт часов",
        BillingRule.CAPPED: "R^f = min(Σ ставка × факт часов; потолок)",
        BillingRule.AGREED: "R^f = согласованная цена",
        BillingRule.PLAN_PRICE: "R^f = R плана",
        BillingRule.MANUAL: "R^f = сумма, введённая вручную",
    }[result.rule]
    return _text(formula, f"R^f = {money(result.fact.revenue, sym)}", "18.2", rule_text)


def fact_costs(result: PlanFact) -> str:
    """Издержки по факту (разделы 3.4 и 18.4)."""
    sym = CURRENCY_SYMBOLS[result.fact.currency]
    fact = result.fact
    return _text(
        "C^f = Labor^f + ρ × Σ часов факта + Own",
        f"{money(fact.direct_labor, sym)} + {money(fact.overheads_alloc, sym)} + "
        f"{money(fact.disbursements_own, sym)} = {money(fact.total_costs, sym)}",
        "18.4",
    )


def fact_nne(result: PlanFact) -> str:
    """NNE по факту (раздел 18.4)."""
    sym = CURRENCY_SYMBOLS[result.fact.currency]
    fact = result.fact
    return _text(
        "NNE^f = R^f − C^f − Tax^f",
        f"{money(fact.revenue, sym)} − {money(fact.total_costs, sym)} − "
        f"{money(fact.tax.tax, sym)} = {money(fact.nne, sym)}",
        "18.4",
        f"Отклонение от плана ΔNNE = ΔR − ΔC − ΔTax = {signed_money(result.delta_nne, sym)}.",
    )


def fact_margin(result: PlanFact) -> str:
    """Маржа по факту."""
    sym = CURRENCY_SYMBOLS[result.fact.currency]
    fact = result.fact
    return _text(
        "m^f = NNE^f / R^f",
        f"{money(fact.nne, sym)} / {money(fact.revenue, sym)} = {pct_label(fact.margin)}",
        "18.2",
    )


def realization(value: Decimal | None, revenue: Decimal, shadow: Decimal, symbol: str) -> str:
    """Реализация (раздел 18.3)."""
    substitution = (
        f"{money(revenue, symbol)} / {money(shadow, symbol)} = {pct_label(value)}"
        if value is not None
        else f"Ставки не заданы: {DASH}"
    )
    return _text(
        "реализация = R / V",
        substitution,
        "18.3",
        "V — часы по ставкам без НДС. Выше 100% — премия к ставкам, ниже — списание.",
    )
