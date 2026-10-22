"""Единый налоговый модуль ядра (docs/methodology.md, раздел 5).

`tax_terms` превращает налоговый режим в параметры расчёта (база, ставка,
минимальный налог, предел уменьшения на взносы, НДС); страновые правила — в
`tax.ru` и `tax.kz`. `calculate_tax` считает налог проекта и НДС:

    «доходы»:            Tax = max(t × R − S, (1 − κ) × t × R)
    «доходы − расходы»:  Tax = max(t × max(R − C + N, 0), t_min × R)
    VAT = v × R          — сверху, не расход фирмы

R — выручка за услуги без НДС; возмещаемые расходы (пошлины) в неё не входят.
S — взносы на часах проекта, N — вознаграждение из прибыли (раздел 5.1).
Налог проекта — аппроксимация годового налога фирмы (раздел 5.2).
"""

from __future__ import annotations

from decimal import Decimal

from rentab_core.models import KzOurProfile, TaxProfile
from rentab_core.money import ZERO
from rentab_core.results import TaxBase, TaxResult, TaxTerms
from rentab_core.tax import kz, ru
from rentab_core.tax.payroll import (
    ContributionBreakdown,
    employer_contribution_rate,
    employer_contributions,
)

__all__ = [
    "ContributionBreakdown",
    "calculate_tax",
    "employer_contribution_rate",
    "employer_contributions",
    "tax_terms",
]


def tax_terms(profile: TaxProfile) -> TaxTerms:
    """Параметры налогового режима: база, ставка, минимальный налог, НДС."""
    if isinstance(profile, KzOurProfile):
        return kz.terms(profile)
    return ru.terms(profile)


def calculate_tax(
    terms: TaxTerms,
    revenue: Decimal,
    costs: Decimal,
    *,
    contributions: Decimal = ZERO,
    nondeductible: Decimal = ZERO,
) -> TaxResult:
    """Налог проекта на доход или прибыль и НДС.

    Args:
        terms: Параметры режима (`tax_terms`).
        revenue: Выручка за услуги без НДС R.
        costs: Издержки проекта C; нужны только для базы «доходы − расходы».
        contributions: Взносы на часах проекта S; уменьшают налог на базе
            «доходы», если режим это допускает (κ > 0).
        nondeductible: Вознаграждение из прибыли N; не уменьшает базу
            «доходы − расходы».

    Returns:
        Налог проекта (с учётом уменьшения на взносы и минимального налога) и НДС сверху.
    """
    if terms.base == TaxBase.INCOME:
        taxable_base = revenue
        gross_tax = terms.rate * taxable_base
        deduction = min(contributions, terms.contributions_deduction_cap * gross_tax)
        regular_tax = gross_tax - deduction
    else:
        taxable_base = max(revenue - costs + nondeductible, ZERO)
        deduction = ZERO
        regular_tax = terms.rate * taxable_base
    min_tax = terms.min_rate * revenue
    return TaxResult(
        terms=terms,
        revenue=revenue,
        costs=costs,
        taxable_base=taxable_base,
        regular_tax=regular_tax,
        min_tax=min_tax,
        tax=max(regular_tax, min_tax),
        vat=terms.vat_rate * revenue,
        contributions=contributions if terms.contributions_deduction_cap > 0 else ZERO,
        deduction=deduction,
        nondeductible=nondeductible if terms.base == TaxBase.PROFIT else ZERO,
    )
