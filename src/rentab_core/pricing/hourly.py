"""Почасовая модель: выручка = Σ ставка для клиента × часы."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from rentab_core.money import decimal_sum
from rentab_core.results import EstimateLine


def gross_revenue(lines: Iterable[EstimateLine]) -> Decimal:
    """Выручка за услуги без пошлин: Σ ставка × часы."""
    return decimal_sum(line.revenue for line in lines)


def net_net_earnings(
    revenue: Decimal,
    direct_labor: Decimal,
    overheads_alloc: Decimal,
    disbursements_own: Decimal,
    tax: Decimal,
) -> Decimal:
    """Чистая прибыль проекта (NNE) по Майстеру.

    NNE = выручка − трудозатраты − накладные − собственные расходы − налог.
    Может быть отрицательной. В v0.3 `tax` включает НДС (KNOWN-BUG L-02 → фаза 2).
    """
    return revenue - direct_labor - overheads_alloc - disbursements_own - tax
