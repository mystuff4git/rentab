"""Модели ядра: проверка входа, значения по умолчанию, юрисдикция, проект."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from rentab_core.models import (
    DEFAULT_KZ_TAX_PROFILE,
    DEFAULT_RF_TAX_PROFILE,
    AusnProfile,
    CappedPricing,
    Country,
    Disbursement,
    DisbursementKind,
    Employee,
    ExchangeRates,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    KzVat,
    MonthlyCost,
    NpdProfile,
    OsnoProfile,
    Overhead,
    PricingModel,
    Project,
    Role,
    Salary,
    Scenario,
    TaxProfile,
    UsnProfile,
    VatMode,
)
from rentab_core.money import Currency
from tests.core.factories import actuals, basic_scenario, d, disbursement, stage

ZERO_COST = {"kind": "monthly", "amount": 0}


def test_float_input_becomes_exact_decimal() -> None:
    # Так приходят данные из JSON и виджетов Streamlit.
    employee = Employee.model_validate(
        {
            "name": "x",
            "role": "partner",
            "billing_rate": 0.1,
            "compensation": {
                "kind": "salary",
                "gross_salary": 15000.0,
                "employer_contribution_rate": 0.3,
            },
        }
    )
    assert employee.billing_rate == Decimal("0.1")
    assert isinstance(employee.compensation, Salary)
    assert employee.compensation.employer_contribution_rate == Decimal("0.3")


def test_employee_defaults() -> None:
    employee = Employee(name="x", role=Role.PARTNER, compensation=MonthlyCost(amount=d(1)))
    assert employee.currency == Currency.RUB
    assert employee.contract_hours_per_month == Decimal(168)
    assert employee.target_utilization == Decimal("0.65")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"billing_rate": -1},
        {"billing_rate": "NaN"},
        {"compensation": {"kind": "monthly", "amount": "Infinity"}},
        {"compensation": {"kind": "bonus", "amount": 1}},
        {"role": "Партнёр"},  # подпись, а не значение перечисления
        {"target_utilization": 0},
        {"target_utilization": "1.01"},
        {"contract_hours_per_month": 0},
        {"currency": "EUR"},
        {"cost_rate": 100},  # себестоимость часа не вводится, а выводится
        {"unknown_field": 1},
    ],
)
def test_employee_rejects_invalid_input(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Employee.model_validate(
            {"name": "x", "role": "partner", "compensation": ZERO_COST, **kwargs}
        )


def test_employee_requires_compensation() -> None:
    with pytest.raises(ValidationError):
        Employee.model_validate({"name": "x", "role": "partner"})


def test_compensation_monthly_cost() -> None:
    salary = Salary(gross_salary=d(100_000), employer_contribution_rate=d("0.3"))
    assert salary.employer_contributions == d(30_000)
    assert salary.monthly_cost == d(130_000)
    assert MonthlyCost(amount=d(400_000)).monthly_cost == d(400_000)


def test_models_are_frozen() -> None:
    employee = Employee(name="x", role=Role.PARTNER, compensation=MonthlyCost(amount=d(1)))
    with pytest.raises(ValidationError):
        employee.name = "y"


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
    assert item.stage is None
    assert not Disbursement(name="Курьер", kind=DisbursementKind.OWN, amount=d(1)).billable
    # «Прочее» v0.3 — собственный расход, отдельной категории больше нет (L-12).
    with pytest.raises(ValidationError):
        Disbursement.model_validate({"name": "x", "kind": "extra", "amount": 1})


def test_exchange_rates_must_be_positive() -> None:
    assert ExchangeRates() == ExchangeRates(rub_per_kzt=None, rub_per_usd=None)
    with pytest.raises(ValidationError):
        ExchangeRates(rub_per_kzt=d(0))
    with pytest.raises(ValidationError):
        ExchangeRates(rub_per_usd=d(-1))


@pytest.mark.parametrize("margin", ["-0.01", "1", "1.5"])
def test_target_margin_must_be_in_unit_interval(margin: str) -> None:
    with pytest.raises(ValidationError):
        Project(target_margin=Decimal(margin))


def test_project_defaults() -> None:
    project = Project()
    assert project.pricing == HourlyPricing()
    assert project.target_margin == Decimal("0.30")
    assert project.vat_mode == VatMode.ON_TOP


def test_pricing_model_is_discriminated_by_kind() -> None:
    adapter: TypeAdapter[PricingModel] = TypeAdapter(PricingModel)
    assert adapter.validate_python({"kind": "hourly"}) == HourlyPricing()
    assert adapter.validate_python({"kind": "capped", "cap": "90000"}) == CappedPricing(
        cap=d(90_000)
    )
    assert adapter.validate_python({"kind": "fixed"}) == FixedPricing()
    agreed = adapter.validate_python({"kind": "fixed", "price": 300000})
    assert agreed == FixedPricing(price=d(300_000))
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "capped", "cap": 0})
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "fixed", "price": 0})
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "fixed", "target_margin": "0.3"})


def test_stage_names_are_unique() -> None:
    # Пирамида — один этап с несколькими назначениями, а не одноимённые этапы (L-05).
    with pytest.raises(ValidationError, match="повторяются: Ответ"):
        Project(stages=(stage("Ответ", ("Петрова", 10)), stage("Ответ", ("Иванов", 2))))


def test_disbursement_stage_must_exist() -> None:
    Project(
        stages=(stage("Подача", ("Петрова", 1)),),
        disbursements=(disbursement(DisbursementKind.OWN, 1, stage="Подача"),),
    )
    with pytest.raises(ValidationError, match="неизвестному этапу «Ответ»"):
        Project(
            stages=(stage("Подача", ("Петрова", 1)),),
            disbursements=(disbursement(DisbursementKind.OWN, 1, stage="Ответ"),),
        )


def test_actuals_default_to_none_and_reference_stages() -> None:
    assert Project().actuals is None
    project = Project(
        stages=(stage("Подача", ("Петрова", 1)),),
        actuals=actuals(("Подача", "Петрова", 2), ("Подача", "Иванов", 1), billed=1000),
    )
    assert project.actuals is not None
    assert project.actuals.billed == d(1000)
    with pytest.raises(ValidationError, match="Факт «Петрова» относится к неизвестному этапу"):
        Project(stages=(stage("Подача", ("Петрова", 1)),), actuals=actuals(("Ответ", "Петрова", 1)))


def test_actuals_pairs_are_unique_and_hours_non_negative() -> None:
    with pytest.raises(ValidationError, match="на этапе «Подача» указан дважды"):
        Project(
            stages=(stage("Подача", ("Петрова", 1)),),
            actuals=actuals(("Подача", "Петрова", 1), ("Подача", "Петрова", 2)),
        )
    with pytest.raises(ValidationError):
        actuals(("Подача", "Петрова", -1))
    with pytest.raises(ValidationError):
        actuals(billed=0)


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
    # Курс переехал из юрисдикции в Firm.exchange_rates.
    with pytest.raises(ValidationError):
        Jurisdiction.model_validate({"exchange_rate_rub_per_kzt": "0.18"})


def test_team_by_name_last_duplicate_wins() -> None:
    first = Employee(name="Иванов", role=Role.PARTNER, compensation=MonthlyCost(amount=d(1)))
    second = Employee(name="Иванов", role=Role.INTERN, compensation=MonthlyCost(amount=d(2)))
    assert Firm(team=(first, second)).team_by_name() == {"Иванов": second}


def test_firm_has_no_separate_billable_hours() -> None:
    # Ёмкость фирмы выводится из команды (L-01).
    with pytest.raises(ValidationError):
        Firm.model_validate({"billable_hours_per_month": 160})


def test_scenario_round_trips_through_json() -> None:
    scenario = basic_scenario(
        CappedPricing(cap=d(100_000)),
        disbursements=(disbursement(DisbursementKind.OWN, 3_000, stage="Анализ"),),
    )
    restored = Scenario.model_validate_json(scenario.model_dump_json())
    assert restored == scenario
