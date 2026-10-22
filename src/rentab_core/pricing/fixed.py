"""Фиксированная цена (docs/methodology.md, разделы 7.3–7.4).

Цена «издержки + маржа» — наименьшая выручка без НДС P, при которой
NNE = m* × P. Закрытые формулы (L-03; в v0.3 цена искалась итерациями):

    база «доходы»:            P = max((C − S) / (1 − t − m*), C / (1 − (1 − κ)t − m*))
    база «доходы − расходы»:  P_A = (C(1 − t) + tN) / (1 − t − m*)
    минимальный налог:        P_B = C / (1 − t_min − m*)
                              P = max(P_A, P_B)

S — взносы на часах проекта (уменьшают налог УСН «доходы» не более чем на κ),
N — вознаграждение из прибыли (не уменьшает базу «доходы − расходы»).

НДС в цену не закладывается: он начисляется сверху (L-02).
"""

from __future__ import annotations

from decimal import Decimal

from rentab_core.errors import FixedPriceError
from rentab_core.models import VatMode
from rentab_core.money import ONE, PERCENT, ZERO
from rentab_core.pricing import net_of_vat
from rentab_core.results import TaxBase, TaxTerms


def _unreachable(terms: TaxTerms, target_margin: Decimal, rate: Decimal) -> FixedPriceError:
    total = (target_margin + rate) * PERCENT
    return FixedPriceError(
        f"Целевая маржа {target_margin * PERCENT:.1f}% недостижима при режиме "
        f"«{terms.regime_label}»: маржа + ставка налога = {total:.1f}% ≥ 100%."
    )


def price_without_min_tax(
    terms: TaxTerms,
    costs: Decimal,
    target_margin: Decimal,
    *,
    contributions: Decimal = ZERO,
    nondeductible: Decimal = ZERO,
) -> Decimal:
    """Цена «издержки + маржа» без учёта минимального налога.

    «доходы»: max((C − S) / (1 − m* − t), C / (1 − (1 − κ)t − m*));
    «доходы − расходы»: (C(1 − t) + tN) / (1 − t − m*).

    Raises:
        FixedPriceError: 1 − m* − t ≤ 0.
    """
    denominator = ONE - target_margin - terms.rate
    if denominator <= 0:
        raise _unreachable(terms, target_margin, terms.rate)
    if terms.base == TaxBase.PROFIT:
        return (costs * (ONE - terms.rate) + terms.rate * nondeductible) / denominator
    price = (costs - contributions) / denominator
    cap = terms.contributions_deduction_cap
    if cap > 0:
        # Знаменатель не меньше 1 − m* − t > 0: предел κ только уменьшает налог.
        price = max(price, costs / (ONE - target_margin - (ONE - cap) * terms.rate))
    return price


def cost_plus_price(
    terms: TaxTerms,
    costs: Decimal,
    target_margin: Decimal,
    *,
    contributions: Decimal = ZERO,
    nondeductible: Decimal = ZERO,
) -> Decimal:
    """Цена «издержки + маржа» без НДС.

    Args:
        terms: Параметры налогового режима.
        costs: Издержки проекта C = Labor + OH + Own.
        target_margin: Целевая маржа NNE m*, доля в [0; 1).
        contributions: Взносы на часах проекта S (база «доходы» с κ > 0).
        nondeductible: Вознаграждение из прибыли N (база «доходы − расходы»).

    Returns:
        Наименьшая P, при которой NNE = m* × P; при C = 0 — 0.

    Raises:
        FixedPriceError: Цель недостижима: 1 − m* − t ≤ 0 или 1 − m* − t_min ≤ 0.
    """
    if not ZERO <= target_margin < ONE:
        raise FixedPriceError(f"Целевая маржа должна быть в [0; 1), получено {target_margin}")
    price = price_without_min_tax(
        terms, costs, target_margin, contributions=contributions, nondeductible=nondeductible
    )
    if terms.min_rate > 0:
        denominator = ONE - terms.min_rate - target_margin
        if denominator <= 0:
            raise _unreachable(terms, target_margin, terms.min_rate)
        price = max(price, costs / denominator)
    return price


def agreed_revenue(price: Decimal, vat_mode: VatMode, vat_rate: Decimal) -> Decimal:
    """Выручка без НДС при согласованной фиксированной цене."""
    return net_of_vat(price, vat_mode, vat_rate)
