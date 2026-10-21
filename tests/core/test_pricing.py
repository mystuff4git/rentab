"""Модели ценообразования: почасовая, фиксированная цена (итерации v0.3)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from rentab_core.errors import FixedPriceError
from rentab_core.models import (
    AusnProfile,
    KzForm,
    KzOurProfile,
    KzVat,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    TaxProfile,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.pricing import fixed
from rentab_core.pricing.fixed import quote_fixed_price, solve_fixed_price
from rentab_core.pricing.hourly import gross_revenue, net_net_earnings
from tests.core.factories import d, line

KOPECK = Decimal("0.01")
# Ниже этого знаменателя закрытая формула уходит в бесконечность.
MIN_DENOMINATOR = Decimal("0.02")

# (режим, ставка налога на доход/прибыль, ставка НДС, база «доходы − расходы»)
REGIMES: list[tuple[TaxProfile, Decimal, Decimal, bool]] = [
    (UsnProfile(), d("0.06"), d(0), False),
    (UsnProfile(vat=UsnVat.VAT_7), d("0.06"), d("0.07"), False),
    (AusnProfile(), d("0.08"), d(0), False),
    (NpdProfile(client_type=NpdClientType.INDIVIDUAL), d("0.04"), d(0), False),
    (
        UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES, vat=UsnVat.VAT_5),
        d("0.15"),
        d("0.05"),
        True,
    ),
    (OsnoProfile(), d("0.25"), d("0.22"), True),
    (KzOurProfile(vat=KzVat.VAT_16), d("0.20"), d("0.16"), True),
    (KzOurProfile(form=KzForm.IP), d("0.10"), d(0), True),
]


def test_gross_revenue_and_nne() -> None:
    # Контрольный сценарий v0.3: 15 000 × 5 + 6 000 × 15.
    assert gross_revenue([line(5, billing=15000), line(15, billing=6000)]) == d(165_000)
    assert net_net_earnings(d(165_000), d(92_500), d(10_000), d(0), d(9_900)) == d(52_600)
    assert net_net_earnings(d(100), d(80), d(10), d(5), d(20)) == d(-15)


@given(
    cost=st.integers(min_value=1_000, max_value=50_000_000),
    margin_pct=st.integers(min_value=0, max_value=60),
    regime=st.sampled_from(REGIMES),
)
def test_iterations_match_closed_form(
    cost: int, margin_pct: int, regime: tuple[TaxProfile, Decimal, Decimal, bool]
) -> None:
    # L-03: итерации совпадают с закрытыми формулами с точностью до копейки,
    # поэтому их замена в фазе 2 цифр не меняет (меняет исправление НДС, L-02).
    profile, t, vat, profit_base = regime
    margin = Decimal(margin_pct) / 100
    costs = Decimal(cost)
    if profit_base:
        denominator = 1 - t - vat - margin
        expected = costs * (1 - t) / denominator
    else:
        denominator = 1 - margin - t - vat
        expected = costs / denominator
    assume(denominator >= MIN_DENOMINATOR)

    quote = quote_fixed_price(profile, costs, margin)
    assert abs(quote.fixed_price - expected) <= KOPECK
    assert quote.nne == quote.fixed_price - costs - quote.tax.total_tax
    assert abs(quote.actual_margin - margin) < Decimal("1e-6")


def test_basic_fixed_price_matches_v03_hand_calculation() -> None:
    # legacy/rentab_v03/test_scenario.py, сценарий 2.
    quote = quote_fixed_price(UsnProfile(), d(92_500), d("0.30"))
    assert quote.fixed_price == d("144531.25")
    assert quote.tax.total_tax == d("8671.875")
    assert quote.nne == d("43359.375")
    assert quote.actual_margin == d("0.30")


def test_zero_costs_give_zero_price() -> None:
    quote = quote_fixed_price(OsnoProfile(), d(0), d("0.30"))
    assert quote.fixed_price == 0
    assert quote.actual_margin == 0


@pytest.mark.parametrize("margin", ["-0.1", "1", "1.5"])
def test_margin_outside_unit_interval(margin: str) -> None:
    with pytest.raises(FixedPriceError, match=r"\[0; 1\)"):
        solve_fixed_price(UsnProfile(), d(1000), Decimal(margin))


def test_unreachable_margin() -> None:
    with pytest.raises(FixedPriceError, match="не может быть прибыльным") as error:
        solve_fixed_price(UsnProfile(), d(1000), d("0.95"))
    assert isinstance(error.value, ValueError)  # совместимость с v0.3


def test_exact_boundary_is_always_unreachable() -> None:
    # L-20: при m + t = 1 знаменатель ровно 0. v0.3 из-за погрешности float то
    # отказывал, то выдавал цену порядка 10^21 ₽ (УСН 6% при марже 94%);
    # точная арифметика ядра отказывает всегда.
    with pytest.raises(FixedPriceError, match=r"= 1\.0000 ≥ 1"):
        solve_fixed_price(UsnProfile(), d(100_000), d("0.94"))
    with pytest.raises(FixedPriceError, match=r"= 1\.0000 ≥ 1"):
        solve_fixed_price(KzOurProfile(vat=KzVat.VAT_16), d(100_000), d("0.70"))


def test_iteration_limit_returns_last_approximation(monkeypatch: pytest.MonkeyPatch) -> None:
    # KNOWN-BUG L-03 → фаза 2: константы итераций v0.3.
    assert fixed.MAX_PRICE_ITERATIONS == 50
    assert fixed.PRICE_CONVERGENCE_EPS == KOPECK
    converged = solve_fixed_price(OsnoProfile(), d(100_000), d("0.30"))
    monkeypatch.setattr(fixed, "MAX_PRICE_ITERATIONS", 1)
    one_step = solve_fixed_price(OsnoProfile(), d(100_000), d("0.30"))
    assert one_step != converged
    assert one_step > d(100_000) / (1 - d("0.30"))
