"""Единый налоговый модуль: режимы РФ и РК, взносы работодателя."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.models import (
    AusnProfile,
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
from rentab_core.tax import calculate_tax, employer_contribution_rate
from tests.core.factories import d


def test_agency_scheme_excludes_billed_disbursements() -> None:
    # Пример из docstring TaxCalculator v0.3.
    result = calculate_tax(
        UsnProfile(), d(250_000), expenses=d(80_000), disbursements_billed=d(50_000)
    )
    assert result.revenue == d(250_000)
    assert result.taxable_base == d(200_000)
    assert result.income_tax == d(12_000)
    assert result.total_tax == d(12_000)


def test_taxable_base_is_never_negative() -> None:
    result = calculate_tax(UsnProfile(), d(10), disbursements_billed=d(50))
    assert result.taxable_base == 0
    assert result.total_tax == 0


@pytest.mark.parametrize(
    ("profile", "income_tax", "vat", "label"),
    [
        (UsnProfile(), "6000", "0", "УСН income (6%)"),
        (UsnProfile(vat=UsnVat.VAT_5), "6000", "5000", "УСН income (6%)"),
        (UsnProfile(vat=UsnVat.VAT_7), "6000", "7000", "УСН income (6%)"),
        (
            UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES),
            "6000",
            "0",
            "УСН income_minus_expenses (15%)",
        ),
        (OsnoProfile(), "10000", "22000", "ОСНО (прибыль 25% + НДС 22%)"),
        (AusnProfile(), "8000", "0", "АУСН — Автоматизированная УСН (8%, без страховых взносов)"),
        (NpdProfile(), "6000", "0", "НПД (6%)"),
        (NpdProfile(client_type=NpdClientType.INDIVIDUAL), "4000", "0", "НПД (4%)"),
        (KzOurProfile(), "8000", "0", "ОУР — ТОО (КПН) 20%"),
        (KzOurProfile(form=KzForm.IP), "4000", "0", "ОУР — ИП (ИПН) 10%"),
        (KzOurProfile(vat=KzVat.VAT_16), "8000", "16000", "ОУР — ТОО (КПН) 20% + НДС 16%"),
    ],
)
def test_regimes(profile: TaxProfile, income_tax: str, vat: str, label: str) -> None:
    # Выручка 100 000, расходы 60 000: база «доходы» = 100 000, «доходы − расходы» = 40 000.
    result = calculate_tax(profile, d(100_000), expenses=d(60_000))
    assert result.income_tax == Decimal(income_tax)
    assert result.vat == Decimal(vat)
    assert result.total_tax == result.income_tax + result.vat
    assert result.regime_label == label


def test_profit_base_tax_never_negative() -> None:
    # KNOWN-BUG L-08 → фаза 3: минимального налога 1% для УСН 15% нет.
    profile = UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES)
    result = calculate_tax(profile, d(100_000), expenses=d(150_000))
    assert result.income_tax_base == 0
    assert result.income_tax == 0
    kz = calculate_tax(KzOurProfile(), d(100_000), expenses=d(150_000))
    assert kz.income_tax == 0


@pytest.mark.parametrize(
    ("jurisdiction", "expected"),
    [
        (None, "0.30"),
        (Jurisdiction(), "0.30"),
        (Jurisdiction(rf=UsnProfile(social_contributions=SocialContributions.ZERO)), "0"),
        (Jurisdiction(rf=OsnoProfile()), "0.30"),
        (Jurisdiction(rf=AusnProfile()), "0"),
        (Jurisdiction(rf=NpdProfile()), "0"),
        (Jurisdiction(mode=JurisdictionMode.KZ), "0.115"),
        # KNOWN-BUG L-06 → фаза 2: в режиме «Оба» взносы берутся по РФ.
        (Jurisdiction(mode=JurisdictionMode.BOTH, rf=NpdProfile()), "0"),
        (Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile()), "0.30"),
    ],
)
def test_employer_contribution_rate(jurisdiction: Jurisdiction | None, expected: str) -> None:
    assert employer_contribution_rate(jurisdiction) == Decimal(expected)
