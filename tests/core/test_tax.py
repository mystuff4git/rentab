"""Единый налоговый модуль: режимы РФ и РК, минимальный налог, НДС, взносы."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rentab_core.models import (
    AusnProfile,
    Country,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    SocialContributions,
    TaxProfile,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.results import TaxBase
from rentab_core.tax import calculate_tax, employer_contribution_rate, tax_terms
from tests.core.factories import d

USN_15 = UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES)


@pytest.mark.parametrize(
    ("profile", "base", "rate", "min_rate", "vat_rate", "label"),
    [
        (UsnProfile(), TaxBase.INCOME, "0.06", "0", "0", "УСН income (6%)"),
        (
            UsnProfile(vat=UsnVat.VAT_5),
            TaxBase.INCOME,
            "0.06",
            "0",
            "0.05",
            "УСН income (6%) + НДС 5%",
        ),
        (
            UsnProfile(vat=UsnVat.VAT_7),
            TaxBase.INCOME,
            "0.06",
            "0",
            "0.07",
            "УСН income (6%) + НДС 7%",
        ),
        (USN_15, TaxBase.PROFIT, "0.15", "0.01", "0", "УСН income_minus_expenses (15%)"),
        (OsnoProfile(), TaxBase.PROFIT, "0.25", "0", "0.22", "ОСНО (прибыль 25% + НДС 22%)"),
        (
            AusnProfile(),
            TaxBase.INCOME,
            "0.08",
            "0",
            "0",
            "АУСН — Автоматизированная УСН (8%, без страховых взносов)",
        ),
        (NpdProfile(), TaxBase.INCOME, "0.06", "0", "0", "НПД (6%)"),
        (
            NpdProfile(client_type=NpdClientType.INDIVIDUAL),
            TaxBase.INCOME,
            "0.04",
            "0",
            "0",
            "НПД (4%)",
        ),
        (KzOurProfile(), TaxBase.PROFIT, "0.20", "0", "0", "ОУР — ТОО (КПН) 20%"),
        (KzOurProfile(form=KzForm.IP), TaxBase.PROFIT, "0.10", "0", "0", "ОУР — ИП (ИПН) 10%"),
        (
            KzOurProfile(vat=KzVat.VAT_16),
            TaxBase.PROFIT,
            "0.20",
            "0",
            "0.16",
            "ОУР — ТОО (КПН) 20% + НДС 16%",
        ),
    ],
)
def test_tax_terms(
    profile: TaxProfile,
    base: TaxBase,
    rate: str,
    min_rate: str,
    vat_rate: str,
    label: str,
) -> None:
    terms = tax_terms(profile)
    assert terms.base == base
    assert terms.rate == Decimal(rate)
    assert terms.min_rate == Decimal(min_rate)
    assert terms.vat_rate == Decimal(vat_rate)
    assert terms.regime_label == label


@pytest.mark.parametrize(
    ("profile", "tax", "vat"),
    [
        # R = 100 000, C = 60 000: база «доходы» 100 000, «доходы − расходы» 40 000.
        (UsnProfile(vat=UsnVat.VAT_5), "6000", "5000"),
        (USN_15, "6000", "0"),
        (OsnoProfile(), "10000", "22000"),
        (AusnProfile(), "8000", "0"),
        (KzOurProfile(vat=KzVat.VAT_16), "8000", "16000"),
    ],
)
def test_vat_is_charged_on_top_and_is_not_firm_tax(profile: TaxProfile, tax: str, vat: str) -> None:
    # L-02: НДС = v × R — отдельно от налога фирмы.
    result = calculate_tax(tax_terms(profile), d(100_000), d(60_000))
    assert result.tax == Decimal(tax)
    assert result.vat == Decimal(vat)
    assert result.revenue == d(100_000)


def test_profit_base_tax_never_negative() -> None:
    kz = calculate_tax(tax_terms(KzOurProfile()), d(100_000), d(150_000))
    assert kz.taxable_base == 0
    assert kz.tax == 0
    assert not kz.profit_tax_applied


def test_usn_minimum_tax() -> None:
    # L-08: 1% выручки, если он больше 15% прибыли.
    terms = tax_terms(USN_15)
    loss = calculate_tax(terms, d(100_000), d(150_000))
    assert loss.regular_tax == 0
    assert loss.min_tax == d(1_000)
    assert loss.tax == d(1_000)
    assert loss.min_tax_applied
    thin = calculate_tax(terms, d(100_000), d(95_000))  # 15% × 5 000 = 750 < 1 000
    assert thin.tax == d(1_000)
    profit = calculate_tax(terms, d(100_000), d(35_200))  # пример 10.4 методологии
    assert profit.tax == d(9_720)
    assert profit.profit_tax_applied
    assert not profit.min_tax_applied


@given(
    revenue=st.integers(min_value=0, max_value=10**9),
    costs=st.integers(min_value=0, max_value=10**9),
    profile=st.sampled_from([UsnProfile(), USN_15, OsnoProfile(), KzOurProfile(form=KzForm.IP)]),
)
def test_tax_formula(revenue: int, costs: int, profile: TaxProfile) -> None:
    terms = tax_terms(profile)
    r, c = Decimal(revenue), Decimal(costs)
    result = calculate_tax(terms, r, c)
    if terms.base == TaxBase.INCOME:
        expected = terms.rate * r
    else:
        expected = max(terms.rate * max(r - c, Decimal(0)), terms.min_rate * r)
    assert result.tax == expected
    assert result.vat == terms.vat_rate * r


@pytest.mark.parametrize(
    ("jurisdiction", "country", "expected"),
    [
        (None, None, "0.30"),
        (Jurisdiction(), None, "0.30"),
        (Jurisdiction(rf=UsnProfile(social_contributions=SocialContributions.ZERO)), None, "0"),
        (Jurisdiction(rf=OsnoProfile()), None, "0.30"),
        (Jurisdiction(rf=AusnProfile()), None, "0"),
        (Jurisdiction(rf=NpdProfile()), None, "0"),
        (Jurisdiction(mode=JurisdictionMode.KZ), None, "0.115"),
        (Jurisdiction(mode=JurisdictionMode.KZ), Country.RF, "0.115"),
        # Режим «Оба»: взносы — по стране трудоустройства сотрудника (L-06).
        (Jurisdiction(mode=JurisdictionMode.BOTH, rf=NpdProfile()), None, "0"),
        (Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile()), Country.RF, "0.30"),
        (Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile()), Country.KZ, "0.115"),
    ],
)
def test_employer_contribution_rate(
    jurisdiction: Jurisdiction | None, country: Country | None, expected: str
) -> None:
    assert employer_contribution_rate(jurisdiction, country) == Decimal(expected)
