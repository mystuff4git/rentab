"""Единый налоговый модуль ядра.

В v0.3 налоговая логика была продублирована (`calculator.calculate_tax` и
`jurisdiction.TaxCalculator`, L-07). Здесь одна точка входа —
`calculate_tax`; расчёт по странам — в `tax.ru` и `tax.kz`.

Налоговая база считается по агентской схеме: перевыставленные клиенту
пошлины вычитаются из полученной суммы и не облагаются.
"""

from __future__ import annotations

from decimal import Decimal

from rentab_core.models import KzOurProfile, TaxProfile
from rentab_core.money import ZERO
from rentab_core.results import TaxResult
from rentab_core.tax import kz, ru
from rentab_core.tax.payroll import employer_contribution_rate

__all__ = ["calculate_tax", "employer_contribution_rate"]


def calculate_tax(
    profile: TaxProfile,
    revenue: Decimal,
    *,
    expenses: Decimal = ZERO,
    disbursements_billed: Decimal = ZERO,
) -> TaxResult:
    """Рассчитывает налоги проекта по режиму (v0.3: `TaxCalculator.calculate_tax`).

    Args:
        profile: Налоговый режим.
        revenue: Сумма, полученная от клиента (услуги + пошлины).
        expenses: Подтверждённые расходы; используются только в режимах с
            базой «доходы − расходы».
        disbursements_billed: Перевыставленные клиенту пошлины; вычитаются из базы.

    Returns:
        Налог на доход или прибыль, НДС и их сумма. НДС входит в total_tax
        как расход фирмы (KNOWN-BUG L-02 → фаза 2).
    """
    taxable_base = max(revenue - disbursements_billed, ZERO)
    if isinstance(profile, KzOurProfile):
        return kz.calculate(profile, revenue=revenue, taxable_base=taxable_base, expenses=expenses)
    return ru.calculate(profile, revenue=revenue, taxable_base=taxable_base, expenses=expenses)
