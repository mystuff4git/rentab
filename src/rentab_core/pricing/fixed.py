"""Фиксированная цена «издержки + маржа» (перенос `FixedPriceCalculator` v0.3).

Цена — решение уравнения P = C / (1 − m − t_eff(P)), где t_eff = налог / P.
Как и v0.3, ядро ищет его итерациями неподвижной точки (KNOWN-BUG L-03 →
фаза 2, там их заменят закрытые формулы). Итерации совпадают с закрытыми
формулами с точностью до копейки (property-тесты ядра и v0.3):

- база «доходы»: P = C / (1 − m − t − vat);
- база «доходы − расходы»: P = C(1 − t) / (1 − t − vat − m).

НДС здесь заложен в цену как налог фирмы (KNOWN-BUG L-02 → фаза 2).
"""

from __future__ import annotations

from decimal import Decimal

from rentab_core.errors import FixedPriceError
from rentab_core.models import CoreModel, TaxProfile
from rentab_core.money import ONE, ZERO
from rentab_core.results import TaxResult
from rentab_core.tax import calculate_tax

# Предел числа итераций: для базы «доходы» хватает одной-двух, для базы
# «доходы − расходы» — нескольких; запас как в v0.3.
MAX_PRICE_ITERATIONS = 50
# Точность сходимости цены, валюта проекта: одна копейка.
PRICE_CONVERGENCE_EPS = Decimal("0.01")


class FixedPriceQuote(CoreModel):
    """Фиксированная цена и показатели при ней."""

    fixed_price: Decimal
    tax: TaxResult
    nne: Decimal
    actual_margin: Decimal


def _tax_at(profile: TaxProfile, price: Decimal, total_costs: Decimal) -> TaxResult:
    """Налог при цене price: пошлины в фикс-цену не входят, их база нулевая."""
    return calculate_tax(profile, price, expenses=total_costs, disbursements_billed=ZERO)


def solve_fixed_price(profile: TaxProfile, total_costs: Decimal, target_margin: Decimal) -> Decimal:
    """Ищет фиксированную цену итерациями (v0.3: `_solve_fixed_price`).

    Args:
        profile: Налоговый режим.
        total_costs: Все издержки проекта: трудозатраты + накладные + собственные расходы.
        target_margin: Целевая маржа после налога, доля в [0; 1).

    Returns:
        Цена, при которой маржа после налога равна целевой (с точностью
        сходимости), или последнее приближение после MAX_PRICE_ITERATIONS.

    Raises:
        FixedPriceError: Маржа вне [0; 1) или маржа + эффективная ставка
            налога ≥ 1 — проект не может быть прибыльным при такой цели.
    """
    if not ZERO <= target_margin < ONE:
        raise FixedPriceError(f"target_margin должна быть в [0; 1), получено {target_margin}")

    # Стартовое приближение — цена при нулевом налоге.
    price = total_costs / (ONE - target_margin)
    for _ in range(MAX_PRICE_ITERATIONS):
        tax_amount = _tax_at(profile, price, total_costs).total_tax
        effective_tax_rate = tax_amount / price if price > 0 else ZERO
        denominator = ONE - target_margin - effective_tax_rate
        if denominator <= 0:
            raise FixedPriceError(
                "Сумма target_margin + effective_tax_rate "
                f"= {target_margin + effective_tax_rate:.4f} ≥ 1: "
                "проект не может быть прибыльным при такой цели."
            )
        new_price = total_costs / denominator
        if abs(new_price - price) < PRICE_CONVERGENCE_EPS:
            return new_price
        price = new_price
    return price


def quote_fixed_price(
    profile: TaxProfile, total_costs: Decimal, target_margin: Decimal
) -> FixedPriceQuote:
    """Фиксированная цена, налог при ней, NNE и фактическая маржа.

    Raises:
        FixedPriceError: См. `solve_fixed_price`.
    """
    price = solve_fixed_price(profile, total_costs, target_margin)
    tax = _tax_at(profile, price, total_costs)
    nne = price - total_costs - tax.total_tax
    actual_margin = nne / price if price > 0 else ZERO
    return FixedPriceQuote(fixed_price=price, tax=tax, nne=nne, actual_margin=actual_margin)
