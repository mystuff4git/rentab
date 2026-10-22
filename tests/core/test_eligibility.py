"""Валидатор применимости налоговых режимов (docs/methodology.md, раздел 15)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.models import (
    AusnProfile,
    Country,
    Firm,
    FirmFacts,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    LegalForm,
    NpdProfile,
    OsnoProfile,
    TaxProfile,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import Currency
from rentab_core.tax.eligibility import (
    EligibilityIssue,
    Severity,
    check_firm,
    check_profile,
    employee_headcount,
    is_available,
)
from tests.core.factories import ASSOCIATE, PARTNER

MLN = Decimal(1_000_000)


def facts(
    income_rf: Decimal | int | None = None,
    income_kz: Decimal | int | None = None,
    **kwargs: object,
) -> FirmFacts:
    return FirmFacts.model_validate(
        {
            "annual_income_rf": None if income_rf is None else Decimal(income_rf),
            "annual_income_kz": None if income_kz is None else Decimal(income_kz),
            **kwargs,
        }
    )


def blockers(issues: list[EligibilityIssue]) -> list[str]:
    return [issue.message for issue in issues if issue.severity == Severity.BLOCKER]


def test_osno_is_always_available() -> None:
    assert check_profile(OsnoProfile(), facts(), headcount=500) == []


def test_missing_income_is_reported_as_info() -> None:
    (issue,) = check_profile(UsnProfile(), facts(), headcount=3)
    assert issue.severity == Severity.INFO
    assert "доход" in issue.message
    assert issue.regime_name == "УСН"


@pytest.mark.parametrize(
    ("income", "headcount", "vat", "fragment"),
    [
        (500 * MLN, 10, UsnVat.VAT_7, "лимита УСН 490 500 000 ₽"),
        (100 * MLN, 131, UsnVat.VAT_5, "лимита УСН 130 чел."),
        (30 * MLN, 5, UsnVat.NONE, "нужен НДС 5%, 7% или 22%"),
        (15 * MLN, 5, UsnVat.VAT_5, "освобождён от НДС"),
        (300 * MLN, 5, UsnVat.VAT_5, "НДС 5% — только при доходе до 272 500 000 ₽"),
    ],
)
def test_usn_blockers(income: Decimal, headcount: int, vat: UsnVat, fragment: str) -> None:
    issues = check_profile(UsnProfile(vat=vat), facts(income), headcount)
    assert any(fragment in message for message in blockers(issues)), issues
    assert not is_available(issues)


@pytest.mark.parametrize(
    ("income", "vat"),
    [(15 * MLN, UsnVat.NONE), (30 * MLN, UsnVat.VAT_5), (300 * MLN, UsnVat.VAT_7)],
)
def test_usn_valid_settings(income: Decimal, vat: UsnVat) -> None:
    assert check_profile(UsnProfile(vat=vat), facts(income), headcount=10) == []


def test_usn_and_ausn_are_not_for_individuals() -> None:
    individual = facts(1 * MLN, rf_legal_form=LegalForm.INDIVIDUAL)
    for profile in (UsnProfile(), AusnProfile()):
        assert any(
            "физлица" in message for message in blockers(check_profile(profile, individual, 0))
        )


def test_ausn_limits_and_region_note() -> None:
    issues = check_profile(AusnProfile(), facts(70 * MLN), headcount=6)
    assert len(blockers(issues)) == 2
    (info,) = [issue for issue in issues if issue.severity == Severity.INFO]
    assert "регион" in info.message
    assert is_available(check_profile(AusnProfile(), facts(50 * MLN), headcount=5))


def test_npd_restrictions() -> None:
    organization = check_profile(NpdProfile(), facts(1 * MLN, agency_contracts=True), headcount=2)
    messages = blockers(organization)
    assert len(messages) == 3
    assert any("агентским" in message for message in messages)
    entrepreneur = facts(3 * MLN, rf_legal_form=LegalForm.ENTREPRENEUR)
    (limit,) = blockers(check_profile(NpdProfile(), entrepreneur, headcount=0))
    assert "2 400 000 ₽" in limit
    ok = facts(2 * MLN, rf_legal_form=LegalForm.INDIVIDUAL)
    assert is_available(check_profile(NpdProfile(), ok, headcount=0))


@pytest.mark.parametrize(
    ("profile", "income", "severity", "fragment"),
    [
        (KzOurProfile(), 50 * MLN, Severity.BLOCKER, "постановка на учёт по НДС обязательна"),
        (KzOurProfile(form=KzForm.IP, vat=KzVat.VAT_16), 1_000 * MLN, Severity.WARNING, "15%"),
    ],
)
def test_kz_checks(profile: TaxProfile, income: Decimal, severity: Severity, fragment: str) -> None:
    (issue,) = check_profile(profile, facts(income_kz=income), headcount=3)
    assert issue.severity == severity
    assert fragment in issue.message
    assert issue.country == Country.KZ


def test_kz_vat_registration_threshold_is_in_mrp() -> None:
    # 10 000 МРП × 4 325 ₸ = 43 250 000 ₸.
    below = check_profile(KzOurProfile(), facts(income_kz=43_250_000), headcount=3)
    above = check_profile(KzOurProfile(), facts(income_kz=43_250_001), headcount=3)
    assert below == []
    assert "43 250 000 ₸" in blockers(above)[0]


def test_headcount_defaults_to_team() -> None:
    kz_lawyer = ASSOCIATE.model_copy(update={"name": "Ахметов", "currency": Currency.KZT})
    firm = Firm(
        jurisdiction=Jurisdiction(mode=JurisdictionMode.BOTH),
        team=(PARTNER, ASSOCIATE, kz_lawyer),
    )
    assert employee_headcount(firm, Country.RF) == 2
    assert employee_headcount(firm, Country.KZ) == 1
    entrepreneur = firm.model_copy(update={"facts": facts(rf_legal_form=LegalForm.ENTREPRENEUR)})
    assert employee_headcount(entrepreneur, Country.RF) == 1
    explicit = firm.model_copy(update={"facts": facts(headcount=7)})
    assert employee_headcount(explicit, Country.RF) == 7


def test_check_firm_covers_every_country() -> None:
    firm = Firm(
        jurisdiction=Jurisdiction(
            mode=JurisdictionMode.BOTH, rf=NpdProfile(), kz=KzOurProfile(vat=KzVat.NONE)
        ),
        team=(PARTNER,),
        facts=facts(1 * MLN, 50 * MLN, rf_legal_form=LegalForm.ENTREPRENEUR, agency_contracts=True),
    )
    issues = check_firm(firm)
    assert {issue.country for issue in issues} == {Country.RF, Country.KZ}
    assert not is_available(issues)


def test_experiment_end_blocks_regime_after_its_term() -> None:
    issues = check_profile(AusnProfile(), facts(1 * MLN), headcount=1, year=2028)
    assert any("Эксперимент закончился 31.12.2027" in message for message in blockers(issues))
