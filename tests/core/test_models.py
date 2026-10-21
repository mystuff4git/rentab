"""Модели ядра: проверка входа, дефолты v0.3, юрисдикция."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from rentab_core.models import (
    DEFAULT_KZ_TAX_PROFILE,
    DEFAULT_RF_TAX_PROFILE,
    AusnProfile,
    Country,
    Disbursement,
    DisbursementKind,
    Employee,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    KzVat,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Payroll,
    PricingModel,
    Role,
    Scenario,
    TaxProfile,
    UsnProfile,
)
from rentab_core.money import Currency
from tests.core.factories import basic_scenario, d


def test_float_input_becomes_exact_decimal() -> None:
    # Так приходят данные из JSON и виджетов Streamlit.
    employee = Employee.model_validate(
        {"name": "x", "role": "partner", "billing_rate": 0.1, "cost_rate": 15000.0}
    )
    assert employee.billing_rate == Decimal("0.1")
    assert employee.cost_rate == Decimal("15000.0")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"billing_rate": -1},
        {"cost_rate": "NaN"},
        {"cost_rate": "Infinity"},
        {"role": "Партнёр"},  # подпись, а не значение перечисления
        {"unknown_field": 1},
    ],
)
def test_employee_rejects_invalid_input(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Employee.model_validate({"name": "x", "role": "partner", **kwargs})


def test_models_are_frozen() -> None:
    employee = Employee(name="x", role=Role.PARTNER)
    with pytest.raises(ValidationError):
        employee.name = "y"


def test_payroll_defaults_and_bounds() -> None:
    payroll = Payroll(gross_salary=d(100_000), employer_contribution_rate=d("0.3"))
    assert payroll.contract_hours_per_month == Decimal(168)
    with pytest.raises(ValidationError):
        Payroll(gross_salary=d(1), contract_hours_per_month=d(0), employer_contribution_rate=d(0))


def test_overhead_and_disbursement_validation() -> None:
    with pytest.raises(ValidationError):
        Overhead.model_validate({"name": "x", "amount": -1, "period": "monthly"})
    with pytest.raises(ValidationError):
        Overhead.model_validate({"name": "x", "amount": 1, "period": "weekly"})
    with pytest.raises(ValidationError):
        Overhead.model_validate({"name": "x", "amount": 1, "period": "monthly", "currency": "EUR"})
    item = Disbursement(name="Пошлина", kind=DisbursementKind.BILLABLE, amount=d(100))
    assert item.billable
    assert item.currency == Currency.RUB
    assert not Disbursement(name="Курьер", kind=DisbursementKind.OWN, amount=d(1)).billable


@pytest.mark.parametrize("margin", ["-0.01", "1", "1.5"])
def test_fixed_pricing_margin_must_be_in_unit_interval(margin: str) -> None:
    with pytest.raises(ValidationError):
        FixedPricing(target_margin=Decimal(margin))


def test_pricing_model_is_discriminated_by_kind() -> None:
    adapter: TypeAdapter[PricingModel] = TypeAdapter(PricingModel)
    assert adapter.validate_python({"kind": "hourly"}) == HourlyPricing()
    fixed = adapter.validate_python({"kind": "fixed", "target_margin": "0.25"})
    assert fixed == FixedPricing(target_margin=Decimal("0.25"))
    assert FixedPricing().target_margin == Decimal("0.30")


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"regime": "usn", "tax_object": "income_minus_expenses", "vat": "5%"}, UsnProfile),
        ({"regime": "ausn"}, AusnProfile),
        ({"regime": "osno"}, OsnoProfile),
        ({"regime": "npd", "client_type": "individual"}, NpdProfile),
        ({"regime": "our", "form": "ip", "vat": "16%"}, KzOurProfile),
    ],
)
def test_tax_profile_is_discriminated_by_regime(
    data: dict[str, str], expected: type[object]
) -> None:
    profile = TypeAdapter(TaxProfile).validate_python(data)
    assert isinstance(profile, expected)
    assert profile.country == (Country.KZ if data["regime"] == "our" else Country.RF)


def test_tax_profile_rejects_option_of_other_regime() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(TaxProfile).validate_python({"regime": "usn", "vat": "16%"})
    with pytest.raises(ValidationError):
        TypeAdapter(TaxProfile).validate_python({"regime": "psn"})


def test_country_currency() -> None:
    assert Country.RF.currency == Currency.RUB
    assert Country.KZ.currency == Currency.KZT


def test_jurisdiction_defaults_match_v03() -> None:
    jurisdiction = Jurisdiction()
    assert jurisdiction.mode == JurisdictionMode.RF
    assert jurisdiction.countries == (Country.RF,)
    assert jurisdiction.tax_profile(Country.RF) == DEFAULT_RF_TAX_PROFILE == UsnProfile()
    assert jurisdiction.tax_profile(Country.KZ) == DEFAULT_KZ_TAX_PROFILE == KzOurProfile()


def test_jurisdiction_calc_country() -> None:
    assert Jurisdiction(mode=JurisdictionMode.KZ).calc_country(Country.RF) == Country.KZ
    both = Jurisdiction(mode=JurisdictionMode.BOTH)
    assert both.countries == (Country.RF, Country.KZ)
    assert both.calc_country() == Country.RF  # первая кнопка в v0.3
    assert both.calc_country(Country.KZ) == Country.KZ


def test_jurisdiction_uses_configured_profiles() -> None:
    jurisdiction = Jurisdiction(
        mode=JurisdictionMode.BOTH, rf=OsnoProfile(), kz=KzOurProfile(vat=KzVat.VAT_16)
    )
    assert jurisdiction.tax_profile(Country.RF) == OsnoProfile()
    assert jurisdiction.tax_profile(Country.KZ) == KzOurProfile(vat=KzVat.VAT_16)
    with pytest.raises(ValidationError):
        Jurisdiction.model_validate({"rf": {"regime": "our"}})
    with pytest.raises(ValidationError):
        Jurisdiction(exchange_rate_rub_per_kzt=d(0))


def test_team_by_name_last_duplicate_wins() -> None:
    first = Employee(name="Иванов", role=Role.PARTNER)
    second = Employee(name="Иванов", role=Role.INTERN)
    assert Firm(team=(first, second)).team_by_name() == {"Иванов": second}


def test_firm_billable_hours_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Firm(billable_hours_per_month=d(0))


def test_scenario_round_trips_through_json() -> None:
    scenario = basic_scenario()
    restored = Scenario.model_validate_json(scenario.model_dump_json())
    assert restored == scenario
