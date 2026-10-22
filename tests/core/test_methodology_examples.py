"""Разобранные примеры docs/methodology.md (разделы 9–11, 11.6) — golden-тесты.

Если тест падает, расходятся код и документ: сначала правится документ
(CLAUDE.md), потом код.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.costing import employee_cost
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    CappedPricing,
    Disbursement,
    DisbursementKind,
    Employee,
    Firm,
    HourlyPricing,
    Jurisdiction,
    MonthlyCost,
    Overhead,
    Period,
    PricingModel,
    Project,
    Role,
    Salary,
    Scenario,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import round_to
from rentab_core.results import Estimate, StageFlag
from tests.core.factories import d, fixed, stage

USN_6_VAT_5 = UsnProfile(vat=UsnVat.VAT_5)
USN_15_VAT_5 = UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES, vat=UsnVat.VAT_5)
EXAMPLE_HOURS = d(160)
EXAMPLE_UTILIZATION = d("0.625")


def example_employee(name: str, role: Role, monthly: int, billing: int) -> Employee:
    """Сотрудник примера 10.1: H = 160 ч, u = 62,5%, K = 100 ч."""
    return Employee(
        name=name,
        role=role,
        compensation=MonthlyCost(amount=d(monthly)),
        billing_rate=d(billing),
        contract_hours_per_month=EXAMPLE_HOURS,
        target_utilization=EXAMPLE_UTILIZATION,
    )


TEAM = (
    example_employee("Партнёр", Role.PARTNER, 400_000, 10_000),
    example_employee("Юрист", Role.ASSOCIATE, 150_000, 5_000),
    example_employee("Стажёр", Role.INTERN, 50_000, 2_000),
)


def example_firm(profile: UsnProfile) -> Firm:
    """Фирма раздела 10.1: накладные 30 000 ₽ в месяц на ёмкость 300 ч."""
    return Firm(
        jurisdiction=Jurisdiction(rf=profile),
        team=TEAM,
        overheads=(Overhead(name="Накладные", amount=d(30_000), period=Period.MONTHLY),),
    )


def example_project(pricing: PricingModel, target_margin: str = "0.30") -> Project:
    """Проект раздела 10.2 «Регистрация товарного знака»."""
    return Project(
        name="Регистрация товарного знака",
        pricing=pricing,
        target_margin=d(target_margin),
        stages=(
            stage("Анализ охраноспособности", ("Партнёр", 2), ("Юрист", 8)),
            stage("Подготовка и подача заявки", ("Юрист", 4), ("Стажёр", 10)),
        ),
        disbursements=(
            Disbursement(
                name="Пошлина (условно)", kind=DisbursementKind.BILLABLE, amount=d(15_000)
            ),
            Disbursement(
                name="Нотариальный перевод доверенности",
                kind=DisbursementKind.OWN,
                amount=d(1_800),
                stage="Подготовка и подача заявки",
            ),
        ),
    )


def run(profile: UsnProfile, pricing: PricingModel, target_margin: str = "0.30") -> Estimate:
    return calculate_estimate(
        Scenario(firm=example_firm(profile), project=example_project(pricing, target_margin))
    )


def money(value: Decimal) -> Decimal:
    return round_to(value)


def pct(value: Decimal) -> Decimal:
    return round_to(value * 100)


def test_team_cost_rates_10_1() -> None:
    rates = [(employee_cost(e).contract_cost_rate, employee_cost(e).cost_rate) for e in TEAM]
    assert rates == [(d(2_500), d(4_000)), (d("937.5"), d(1_500)), (d("312.5"), d(500))]
    estimate = run(USN_6_VAT_5, HourlyPricing())
    assert estimate.overhead_rate == d(100)


def test_project_costs_10_2() -> None:
    estimate = run(USN_6_VAT_5, HourlyPricing())
    assert estimate.direct_labor == d(31_000)
    assert estimate.overheads_alloc == d(2_400)
    assert estimate.disbursements_own == d(1_800)
    assert estimate.total_costs == d(35_200)
    assert estimate.underutilization_cost == d(11_625)
    assert money(estimate.blended_rate) == d("4166.67")
    assert estimate.leverage == d(11)
    assert [s.shadow_value for s in estimate.stages] == [d(60_000), d(40_000)]


@pytest.mark.parametrize(
    ("profile", "tax", "nne", "margin_pct", "stage_taxes", "stage_nne", "stage_margins"),
    [
        (
            USN_6_VAT_5,
            "6000",
            "58800",
            "58.80",
            ("3600", "2400"),
            ("35400", "23400"),
            ("59.00", "58.50"),
        ),
        (
            USN_15_VAT_5,
            "9720",
            "55080",
            "55.08",
            ("5850", "3870"),
            ("33150", "21930"),
            ("55.25", "54.83"),
        ),
    ],
)
def test_hourly_example_10_3_to_10_5(
    profile: UsnProfile,
    tax: str,
    nne: str,
    margin_pct: str,
    stage_taxes: tuple[str, str],
    stage_nne: tuple[str, str],
    stage_margins: tuple[str, str],
) -> None:
    estimate = run(profile, HourlyPricing())
    assert estimate.revenue == d(100_000)
    assert estimate.vat == d(5_000)
    assert estimate.total_client == d(120_000)
    assert estimate.tax.tax == Decimal(tax)
    assert estimate.nne == Decimal(nne)
    assert pct(estimate.margin) == Decimal(margin_pct)
    assert [s.revenue for s in estimate.stages] == [d(60_000), d(40_000)]
    assert [s.costs for s in estimate.stages] == [d(21_000), d(14_200)]
    assert [s.tax for s in estimate.stages] == [Decimal(t) for t in stage_taxes]
    assert [s.nne for s in estimate.stages] == [Decimal(n) for n in stage_nne]
    assert [pct(s.margin) for s in estimate.stages] == [Decimal(m) for m in stage_margins]
    assert [s.flag for s in estimate.stages] == [StageFlag.GREEN, StageFlag.GREEN]


def test_hourly_example_prices_including_vat_10_3() -> None:
    estimate = run(USN_6_VAT_5, HourlyPricing())
    included = calculate_estimate(
        Scenario(
            firm=example_firm(USN_6_VAT_5),
            project=example_project(HourlyPricing()).model_copy(update={"vat_mode": "included"}),
        )
    )
    assert estimate.revenue == d(100_000)
    assert money(included.revenue) == d("95238.10")
    assert money(included.vat) == d("4761.90")


def test_capped_example_10_6() -> None:
    estimate = run(USN_6_VAT_5, CappedPricing(cap=d(90_000)))
    assert estimate.revenue == d(90_000)
    assert estimate.write_off == d(10_000)
    assert estimate.vat == d(4_500)
    assert estimate.total_client == d(109_500)
    assert estimate.tax.tax == d(5_400)
    assert estimate.nne == d(49_400)
    assert pct(estimate.margin) == d("54.89")
    assert [s.revenue for s in estimate.stages] == [d(54_000), d(36_000)]


@pytest.mark.parametrize(
    ("profile", "price", "tax", "nne", "vat", "total", "stage_revenue", "stage_tax", "margins"),
    [
        (
            USN_6_VAT_5,
            "55000",
            "3300",
            "16500",
            "2750",
            "72750",
            ("33000", "22000"),
            ("1980", "1320"),
            ("30.36", "29.45"),
        ),
        (
            USN_15_VAT_5,
            "54400",
            "2880",
            "16320",
            "2720",
            "72120",
            ("32640", "21760"),
            ("1746", "1134"),
            ("30.31", "29.53"),
        ),
    ],
)
def test_fixed_example_11_1_11_2_11_4(
    profile: UsnProfile,
    price: str,
    tax: str,
    nne: str,
    vat: str,
    total: str,
    stage_revenue: tuple[str, str],
    stage_tax: tuple[str, str],
    margins: tuple[str, str],
) -> None:
    estimate = run(profile, fixed())
    assert estimate.revenue == Decimal(price)
    assert estimate.tax.tax == Decimal(tax)
    assert estimate.nne == Decimal(nne)
    assert estimate.margin == d("0.30")
    assert estimate.vat == Decimal(vat)
    assert estimate.total_client == Decimal(total)
    assert [s.revenue for s in estimate.stages] == [Decimal(r) for r in stage_revenue]
    assert [s.tax for s in estimate.stages] == [Decimal(t) for t in stage_tax]
    assert [pct(s.margin) for s in estimate.stages] == [Decimal(m) for m in margins]
    assert [s.flag for s in estimate.stages] == [StageFlag.GREEN, StageFlag.YELLOW]


def test_break_even_price_with_minimum_tax_11_3() -> None:
    usn_15 = run(USN_15_VAT_5, fixed(), target_margin="0")
    assert money(usn_15.revenue) == d("35555.56")
    assert usn_15.tax.min_tax_applied
    assert money(usn_15.tax.tax) == d("355.56")
    assert money(usn_15.tax.regular_tax) == d("53.33")
    assert money(usn_15.nne) == 0
    usn_6 = run(USN_6_VAT_5, fixed(), target_margin="0")
    assert money(usn_6.revenue) == d("37446.81")


def test_v03_comparison_11_5() -> None:
    # В v0.3 C = 23 575 (себестоимость контрактного часа) и цена C / 0,59.
    assert money(d(23_575) / d("0.59")) == d("39957.63")
    assert money(d(23_575) / d("0.64")) == d("36835.94")
    assert run(USN_6_VAT_5, fixed()).revenue == d(55_000)


# ---------------------------------------------------------------------------
# Раздел 11.6: взносы и вознаграждение из прибыли (фаза 3)
# ---------------------------------------------------------------------------
USN_6 = UsnProfile()
USN_15 = UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES)


def salaried_lawyer_firm(profile: UsnProfile) -> Firm:
    """Фирма 10.1, у юриста W = 150 000 ₽ — оклад 120 000 ₽ и взносы 25%."""
    lawyer = TEAM[1].model_copy(
        update={
            "compensation": Salary(gross_salary=d(120_000), employer_contribution_rate=d("0.25"))
        }
    )
    return example_firm(profile).model_copy(update={"team": (TEAM[0], lawyer, TEAM[2])})


def profit_paid_partner_firm(profile: UsnProfile) -> Firm:
    """Фирма 10.1, вознаграждение партнёра выплачивается из прибыли."""
    partner = TEAM[0].model_copy(update={"paid_from_profit": True})
    return example_firm(profile).model_copy(update={"team": (partner, *TEAM[1:])})


def run_firm(firm: Firm, pricing: PricingModel) -> Estimate:
    return calculate_estimate(Scenario(firm=firm, project=example_project(pricing)))


def test_contributions_reduce_usn_income_tax_11_6() -> None:
    estimate = run_firm(salaried_lawyer_firm(USN_6), HourlyPricing())
    assert employee_cost(salaried_lawyer_firm(USN_6).team[1]).cost_rate == d(1_500)
    assert estimate.total_costs == d(35_200)
    assert estimate.tax.contributions == d(3_600)
    # max(6 000 − 3 600; 0,5 × 6 000) = 3 000: работает предел 50%.
    assert estimate.tax.tax == d(3_000)
    assert estimate.tax.deduction_capped
    assert estimate.nne == d(61_800)
    assert [s.tax for s in estimate.stages] == [d(1_800), d(1_200)]


def test_contributions_below_cap_reduce_stage_taxes_by_their_hours() -> None:
    # Взносы 2 000 < 3 000: налог 4 000, этапы — t × R_s − S_s.
    firm = salaried_lawyer_firm(USN_6)
    lawyer = firm.team[1].model_copy(
        update={
            "compensation": Salary(
                gross_salary=d(150_000) / d("1.125"), employer_contribution_rate=d("0.125")
            )
        }
    )
    firm = firm.model_copy(update={"team": (firm.team[0], lawyer, firm.team[2])})
    estimate = run_firm(firm, HourlyPricing())
    contributions_per_hour = d(150_000) / d("1.125") * d("0.125") / d(100)
    assert estimate.tax.contributions == contributions_per_hour * 12
    assert not estimate.tax.deduction_capped
    assert estimate.tax.tax == d(6_000) - estimate.tax.contributions
    s1, s2 = (contributions_per_hour * 8, contributions_per_hour * 4)
    assert [s.tax for s in estimate.stages] == [d(3_600) - s1, d(2_400) - s2]
    assert sum((s.tax for s in estimate.stages), d(0)) == estimate.tax.tax


def test_contributions_fixed_price_11_6() -> None:
    estimate = run_firm(salaried_lawyer_firm(USN_6), fixed())
    assert money(estimate.revenue) == d("52537.31")
    assert money(estimate.tax.tax) == d("1576.12")
    assert money(estimate.nne) == d("15761.19")
    assert abs(estimate.margin - d("0.30")) < d("1e-20")


def test_profit_paid_partner_hourly_11_6() -> None:
    estimate = run_firm(profit_paid_partner_firm(USN_15), HourlyPricing())
    assert estimate.tax.nondeductible == d(8_000)
    assert estimate.tax.tax == d(10_920)
    assert estimate.nne == d(53_880)
    assert [s.tax for s in estimate.stages] == [d(7_050), d(3_870)]


def test_profit_paid_partner_fixed_price_11_6() -> None:
    estimate = run_firm(profit_paid_partner_firm(USN_15), fixed())
    assert money(estimate.revenue) == d("56581.82")
    assert money(estimate.tax.tax) == d("4407.27")
    assert money(estimate.nne) == d("16974.55")
    assert abs(estimate.margin - d("0.30")) < d("1e-20")


def test_profit_paid_partner_does_not_change_income_base_tax() -> None:
    plain = run(USN_6_VAT_5, HourlyPricing())
    flagged = run_firm(profit_paid_partner_firm(USN_6_VAT_5), HourlyPricing())
    assert flagged.tax.tax == plain.tax.tax == d(6_000)
    assert flagged.tax.nondeductible == 0
