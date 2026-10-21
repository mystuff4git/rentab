"""Property-тесты на инварианты v0.3, на которые опирается фаза 2.

Главное: итерационный поиск фикс-цены в v0.3 совпадает с закрытыми формулами
с точностью до копейки во всей области входных данных. Значит, замена итераций
формулами в фазе 2 (L-03) сама по себе цифры не меняет; меняет их только
исправление учёта НДС (L-02).
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from tests.characterization.legacy import expenses, fixed_price, jurisdiction

pytestmark = pytest.mark.characterization

# Допуск сравнения с закрытой формулой: одна копейка (точность сходимости v0.3).
KOPECK = 0.01
# Минимальный знаменатель закрытой формулы: ниже цена уходит в бесконечность.
MIN_DENOMINATOR = 0.02

# (параметры режима, ставка налога на доход/прибыль, ставка НДС, база «прибыль»)
REGIMES: list[tuple[dict[str, str], float, float, bool]] = [
    ({"country": "RF", "regime": "USN", "object": "income", "vat": "none"}, 0.06, 0.0, False),
    ({"country": "RF", "regime": "USN", "object": "income", "vat": "7%"}, 0.06, 0.07, False),
    ({"country": "RF", "regime": "AUSN"}, 0.08, 0.0, False),
    ({"country": "RF", "regime": "NPD", "client_type": "legal_entity"}, 0.06, 0.0, False),
    (
        {"country": "RF", "regime": "USN", "object": "income_minus_expenses", "vat": "5%"},
        0.15,
        0.05,
        True,
    ),
    ({"country": "RF", "regime": "OSNO"}, 0.25, 0.22, True),
    ({"country": "KZ", "regime": "OUR", "form": "too", "vat": "16%"}, 0.20, 0.16, True),
    ({"country": "KZ", "regime": "OUR", "form": "ip", "vat": "none"}, 0.10, 0.0, True),
]

costs = st.floats(min_value=1_000, max_value=50_000_000, allow_nan=False)
margins = st.floats(min_value=0.0, max_value=0.6, allow_nan=False)
regimes = st.sampled_from(REGIMES)


def legacy_fixed_price(cost: float, params: dict[str, str], margin: float) -> dict[str, Any]:
    """Фикс-цена v0.3 для проекта с издержками cost (без накладных и расходов)."""
    member = {"name": "x", "role": "Партнёр", "billing_rate": 0.0, "cost_rate": cost, "hours": 1.0}
    stages = [{"name": "Этап", "assigned_members": [member]}]
    result: dict[str, Any] = fixed_price.FixedPriceCalculator().calculate(
        stages, expenses.ExpenseManager(), params, margin
    )
    return result


@given(cost=costs, margin=margins, regime=regimes)
def test_fixed_price_matches_closed_form(
    cost: float, margin: float, regime: tuple[dict[str, str], float, float, bool]
) -> None:
    params, t, vat, profit_base = regime
    if profit_base:
        denominator = 1 - t - vat - margin
        expected = cost * (1 - t) / denominator
    else:
        denominator = 1 - margin - t - vat
        expected = cost / denominator
    assume(denominator >= MIN_DENOMINATOR)

    result = legacy_fixed_price(cost, params, margin)
    assert result["fixed_price"] == pytest.approx(expected, abs=KOPECK)
    assert result["nne"] == pytest.approx(
        result["fixed_price"] - result["total_costs"] - result["tax_amount"]
    )


@given(revenue=st.floats(min_value=0, max_value=1e9, allow_nan=False), regime=regimes)
def test_vat_is_always_part_of_total_tax(
    revenue: float, regime: tuple[dict[str, str], float, float, bool]
) -> None:
    # KNOWN-BUG L-02 → фаза 2: total_tax = налог на доход + НДС с той же базы.
    params, _, vat, _ = regime
    result = jurisdiction.TaxCalculator(params).calculate_tax(revenue=revenue)
    assert result["vat"] == pytest.approx(revenue * vat)
    assert result["total_tax"] == pytest.approx(result["income_tax"] + result["vat"])
