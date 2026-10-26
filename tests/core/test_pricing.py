"""Модели ценообразования: почасовая, с потолком, фикс (закрытые формулы)."""

from __future__ import annotations

import re
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
    VatMode,
)
from rentab_core.money import ONE
from rentab_core.pricing import net_of_vat
from rentab_core.pricing.fixed import agreed_revenue, cost_plus_price, price_without_min_tax
from rentab_core.pricing.hourly import capped_revenue, hourly_revenue, shadow_value
from rentab_core.results import TaxBase
from rentab_core.tax import calculate_tax, tax_terms
from tests.core.factories import d, line

USN_6 = tax_terms(UsnProfile(vat=UsnVat.VAT_5))
USN_15 = tax_terms(UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES, vat=UsnVat.VAT_5))
PROFILES: list[TaxProfile] = [
    UsnProfile(),
    UsnProfile(vat=UsnVat.VAT_7),
    UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES, vat=UsnVat.VAT_5),
    AusnProfile(),
    NpdProfile(client_type=NpdClientType.INDIVIDUAL),
    OsnoProfile(),
    KzOurProfile(vat=KzVat.VAT_16),
    KzOurProfile(form=KzForm.IP),
]
# Точность сравнения NNE с m* × P: деление в Decimal (28 знаков).
EPS = Decimal("1e-15")


def test_shadow_value_and_hourly_revenue() -> None:
    # Контрольный сценарий v0.3: 15 000 × 5 + 6 000 × 15.
    lines = [line(5, billing=15000), line(15, billing=6000)]
    assert shadow_value(lines) == d(165_000)
    assert hourly_revenue(d(165_000), VatMode.ON_TOP, d("0.05")) == d(165_000)
    assert hourly_revenue(d(105_000), VatMode.INCLUDED, d("0.05")) == d(100_000)


def test_net_of_vat() -> None:
    assert net_of_vat(d(300_000), VatMode.ON_TOP, d("0.05")) == d(300_000)
    assert net_of_vat(d(120), VatMode.INCLUDED, d("0.2")) == d(100)
    assert net_of_vat(d(120), VatMode.INCLUDED, d(0)) == d(120)


def test_capped_revenue() -> None:
    assert capped_revenue(d(100_000), d(90_000), VatMode.ON_TOP, d("0.05")) == d(90_000)
    assert capped_revenue(d(80_000), d(90_000), VatMode.ON_TOP, d("0.05")) == d(80_000)
    assert capped_revenue(d(126_000), d(105_000), VatMode.INCLUDED, d("0.05")) == d(100_000)


def test_agreed_revenue() -> None:
    assert agreed_revenue(d(300_000), VatMode.ON_TOP, d("0.05")) == d(300_000)
    included = agreed_revenue(d(300_000), VatMode.INCLUDED, d("0.05"))
    assert included == d(300_000) / d("1.05")


def test_methodology_example_prices() -> None:
    # docs/methodology.md, раздел 11: C = 35 200, m* = 30%.
    assert cost_plus_price(USN_6, d(35_200), d("0.30")) == d(55_000)
    assert price_without_min_tax(USN_15, d(35_200), d("0.30")) == d(54_400)
    assert cost_plus_price(USN_15, d(35_200), d("0.30")) == d(54_400)
    # Цена безубыточности: работает минимальный налог, P = C / 0,99.
    assert cost_plus_price(USN_15, d(35_200), d(0)) == d(35_200) / d("0.99")
    assert cost_plus_price(USN_6, d(35_200), d(0)) == d(35_200) / d("0.94")


def test_vat_is_not_in_the_price() -> None:
    # L-02: в v0.3 УСН 6% + НДС 5% давал C / (1 − m − t − v) = 23 575 / 0,59.
    assert cost_plus_price(USN_6, d(23_575), d("0.30")) == d(23_575) / d("0.64")
    osno = tax_terms(OsnoProfile())
    expected = d(100_000) * d("0.75") / (d("0.75") - d("0.30"))
    assert cost_plus_price(osno, d(100_000), d("0.30")) == expected


@given(
    cost=st.integers(min_value=1, max_value=50_000_000),
    margin_pct=st.integers(min_value=0, max_value=80),
    profile=st.sampled_from(PROFILES),
)
def test_cost_plus_price_hits_target_margin_exactly(
    cost: int, margin_pct: int, profile: TaxProfile
) -> None:
    terms = tax_terms(profile)
    margin = Decimal(margin_pct) / 100
    assume(1 - margin - terms.rate > 0)
    costs = Decimal(cost)
    price = cost_plus_price(terms, costs, margin)
    tax = calculate_tax(terms, price, costs)
    nne = price - costs - tax.tax
    # NNE = m* × P, и это наименьшая такая цена: копейкой меньше маржа ниже цели.
    assert abs(nne - margin * price) <= EPS * price
    cheaper = price - Decimal("0.01")
    cheaper_nne = cheaper - costs - calculate_tax(terms, cheaper, costs).tax
    assert cheaper_nne < margin * cheaper
    # VAT в цену не закладывается.
    assert tax.vat == terms.vat_rate * price


@given(cost=st.integers(min_value=1, max_value=10_000_000), margin_pct=st.integers(0, 20))
def test_min_tax_decides_price_at_low_margin(cost: int, margin_pct: int) -> None:
    # Для УСН 15% / 1% минимальный налог определяет цену при m* < 0,85 × 0,01 / 0,15.
    margin = Decimal(margin_pct) / 100
    costs = Decimal(cost)
    price = cost_plus_price(USN_15, costs, margin)
    min_tax_binds = margin < d("0.0085") / d("0.15")
    assert (price == costs / (1 - d("0.01") - margin)) == min_tax_binds
    assert calculate_tax(USN_15, price, costs).min_tax_applied == min_tax_binds


def test_zero_costs_give_zero_price() -> None:
    assert cost_plus_price(tax_terms(OsnoProfile()), d(0), d("0.30")) == 0
    assert cost_plus_price(USN_15, d(0), d("0.30")) == 0


@pytest.mark.parametrize("margin", ["-0.1", "1", "1.5"])
def test_margin_outside_unit_interval(margin: str) -> None:
    with pytest.raises(FixedPriceError, match=r"\[0; 1\)"):
        cost_plus_price(USN_6, d(1000), Decimal(margin))


@pytest.mark.parametrize(
    ("terms", "margin", "message"),
    [
        (
            USN_6,
            "0.95",
            re.escape("Целевая маржа 95.0% недостижима при режиме «УСН income (6%) + НДС 5%»"),
        ),
        # L-20: точная граница m* + t = 1 — всегда отказ.
        (USN_6, "0.94", re.escape("маржа + ставка налога = 100.0% ≥ 100%")),
        (USN_15, "0.85", re.escape("маржа + ставка налога = 100.0% ≥ 100%")),
    ],
)
def test_unreachable_margin(terms: object, margin: str, message: str) -> None:
    with pytest.raises(FixedPriceError, match=message) as error:
        cost_plus_price(terms, d(1000), Decimal(margin))  # type: ignore[arg-type]
    assert isinstance(error.value, ValueError)  # совместимость с v0.3
    # По полям ошибки интерфейс пишет сообщение со своей подписью режима (L-18).
    assert error.value.target_margin == Decimal(margin)
    assert error.value.tax_rate is not None
    assert error.value.target_margin + error.value.tax_rate >= 1


def test_margin_outside_unit_interval_has_no_fields() -> None:
    with pytest.raises(FixedPriceError) as error:
        cost_plus_price(USN_6, d(1000), d(1))
    assert error.value.target_margin is None
    assert error.value.tax_rate is None


def test_min_tax_denominator_is_checked() -> None:
    # Искусственный режим, где минимальный налог выше ставки налога на прибыль.
    terms = USN_15.model_copy(update={"rate": d("0.10"), "min_rate": d("0.20")})
    with pytest.raises(FixedPriceError, match=re.escape("= 100.0%")):
        cost_plus_price(terms, d(1000), d("0.80"))


def test_profit_base_price_covers_costs() -> None:
    assert USN_15.base == TaxBase.PROFIT
    assert price_without_min_tax(USN_15, d(1000), d(0)) == d(1000)


def test_cost_plus_price_with_contributions_and_profit_reward() -> None:
    # Пример 11.6 методологии.
    with_contributions = cost_plus_price(USN_6, d(35_200), d("0.30"), contributions=d(3_600))
    assert with_contributions == d(35_200) / (1 - d("0.03") - d("0.30"))
    below_cap = cost_plus_price(USN_6, d(35_200), d("0.30"), contributions=d(500))
    assert below_cap == (d(35_200) - d(500)) / d("0.64")
    rewarded = cost_plus_price(USN_15, d(35_200), d("0.30"), nondeductible=d(8_000))
    assert rewarded == (d(35_200) * d("0.85") + d("0.15") * d(8_000)) / d("0.55")


@pytest.mark.parametrize("profile", [AusnProfile(), NpdProfile()], ids=["ausn", "npd"])
def test_contributions_do_not_lower_price_without_deduction(profile: TaxProfile) -> None:
    # L-23: при κ = 0 взносы налог не уменьшают — цена та же, что без них, и маржа = цели.
    terms = tax_terms(profile)
    costs, margin = d(11_904), d("0.30")
    price = cost_plus_price(terms, costs, margin, contributions=d(2_747))
    assert price == cost_plus_price(terms, costs, margin) == costs / (1 - margin - terms.rate)
    tax = calculate_tax(terms, price, costs, contributions=d(2_747))
    assert price - costs - tax.tax == margin * price


@given(
    cost=st.integers(min_value=1, max_value=10**8),
    share=st.integers(min_value=0, max_value=100),
    # Цель достижима при всех трёх режимах: m* + t < 1 и для ОСНО 25%.
    margin_pct=st.integers(min_value=0, max_value=70),
)
def test_price_with_contributions_and_reward_hits_target_margin(
    cost: int, share: int, margin_pct: int
) -> None:
    costs, margin = Decimal(cost), Decimal(margin_pct) / 100
    part = costs * share / 100  # S и N — часть трудозатрат, не больше C
    for terms, kwargs in (
        (USN_6, {"contributions": part}),
        (tax_terms(AusnProfile()), {"contributions": part}),
        (USN_15, {"nondeductible": part}),
        (tax_terms(OsnoProfile()), {"nondeductible": part}),
    ):
        price = cost_plus_price(terms, costs, margin, **kwargs)
        tax = calculate_tax(terms, price, costs, **kwargs)
        nne = price - costs - tax.tax
        assert abs(nne - margin * price) <= Decimal("1e-15") * max(price, ONE)
