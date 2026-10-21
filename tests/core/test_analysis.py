"""Экономика этапов, светофор и показатели дашборда."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rentab_core.analysis.dashboard import Profitability, dashboard, profitability
from rentab_core.analysis.stages import StageLines, allocation_shares, classify, stage_results
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    CappedPricing,
    DisbursementKind,
    HourlyPricing,
    Jurisdiction,
    OsnoProfile,
    PricingModel,
    RfTaxProfile,
    UsnObject,
    UsnProfile,
)
from rentab_core.results import StageFlag
from rentab_core.tax import calculate_tax, tax_terms
from tests.core.factories import basic_scenario, d, disbursement, fixed, line, stage

USN_15 = UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES)
# Суммы по этапам сходятся с проектом с точностью деления в Decimal.
EPS = Decimal("1e-18")


@pytest.mark.parametrize(
    ("margin", "flag"),
    [
        ("-0.1", StageFlag.RED),
        ("0", StageFlag.RED),
        ("0.01", StageFlag.YELLOW),
        ("0.3", StageFlag.GREEN),
        # Погрешность деления в Decimal не перекрашивает этап с маржой, равной цели.
        ("0.2999999999999999999999999999", StageFlag.GREEN),
        ("0.0000000000000000000000000001", StageFlag.RED),
        ("0.29999", StageFlag.YELLOW),
    ],
)
def test_classify(margin: str, flag: StageFlag) -> None:
    assert classify(d(margin), d("0.3")) == flag


def _stage(name: str, *lines: object) -> StageLines:
    return StageLines(name=name, lines=tuple(lines))  # type: ignore[arg-type]


def test_allocation_by_shadow_value_not_hours() -> None:
    # L-04: партнёр 2 ч + юрист 8 ч = 60 000; юрист 4 ч + стажёр 10 ч = 40 000.
    stages = [
        _stage("Анализ", line(2, billing=10000), line(8, billing=5000)),
        _stage("Подача", line(4, billing=5000), line(10, billing=2000)),
    ]
    assert allocation_shares(stages) == [d("0.6"), d("0.4")]


def test_allocation_fallbacks() -> None:
    # Ставок нет — делим по трудозатратам; нет и их — по часам; нет ничего — нули.
    by_labor = [_stage("A", line(10, cost=5000)), _stage("B", line(15, cost=2000))]
    assert allocation_shares(by_labor) == [d(50_000) / d(80_000), d(30_000) / d(80_000)]
    by_hours = [_stage("A", line(10)), _stage("B", line(30))]
    assert allocation_shares(by_hours) == [d("0.25"), d("0.75")]
    assert allocation_shares([_stage("A")]) == [0]
    assert allocation_shares([]) == []


def test_stage_results_methodology_example() -> None:
    # docs/methodology.md, 11.4: P = 55 000, УСН 6%, ρ = 100, нотариус 1 800 — на этапе 2.
    stages = [
        _stage("Анализ", line(2, billing=10000, cost=4000), line(8, billing=5000, cost=1500)),
        _stage("Подача", line(4, billing=5000, cost=1500), line(10, billing=2000, cost=500)),
    ]
    tax = calculate_tax(tax_terms(UsnProfile()), d(55_000), d(35_200))
    first, second = stage_results(
        stages,
        revenue=d(55_000),
        overhead_rate=d(100),
        own_by_stage={"Подача": d(1_800)},
        unassigned_own=d(0),
        tax=tax,
        target_margin=d("0.30"),
    )
    assert (first.revenue, first.costs, first.tax, first.nne) == (
        d(33_000),
        d(21_000),
        d(1_980),
        d(10_020),
    )
    assert first.flag == StageFlag.GREEN
    assert (second.revenue, second.own, second.costs, second.tax, second.nne) == (
        d(22_000),
        d(1_800),
        d(14_200),
        d(1_320),
        d(6_480),
    )
    assert second.flag == StageFlag.YELLOW
    assert first.shadow_value == d(60_000)
    assert first.overheads == d(1_000)


def test_unassigned_own_expense_follows_revenue_key() -> None:
    stages = [_stage("A", line(1, billing=3000)), _stage("B", line(1, billing=1000))]
    tax = calculate_tax(tax_terms(UsnProfile()), d(4_000), d(800))
    first, second = stage_results(
        stages,
        revenue=d(4_000),
        overhead_rate=d(0),
        own_by_stage={},
        unassigned_own=d(800),
        tax=tax,
        target_margin=d("0.3"),
    )
    assert (first.own, second.own) == (d(600), d(200))


def test_loss_making_stage_gets_negative_profit_tax() -> None:
    # База «доходы − расходы»: убыточный этап уменьшает налог проекта.
    stages = [
        _stage("Прибыльный", line(10, billing=10000, cost=1000)),
        _stage("Убыточный", line(10, billing=1000, cost=3000)),
    ]
    tax = calculate_tax(tax_terms(OsnoProfile()), d(110_000), d(40_000))
    assert tax.profit_tax_applied
    good, bad = stage_results(
        stages,
        revenue=d(110_000),
        overhead_rate=d(0),
        own_by_stage={},
        unassigned_own=d(0),
        tax=tax,
        target_margin=d("0.3"),
    )
    assert good.tax == d("0.25") * (d(100_000) - d(10_000))
    assert bad.tax == d("0.25") * (d(10_000) - d(30_000))
    assert good.tax + bad.tax == tax.tax
    assert bad.flag == StageFlag.RED


def test_minimum_tax_is_split_by_revenue() -> None:
    stages = [_stage("A", line(1, billing=3000)), _stage("B", line(1, billing=1000))]
    tax = calculate_tax(tax_terms(USN_15), d(4_000), d(4_500))
    assert tax.min_tax_applied
    first, second = stage_results(
        stages,
        revenue=d(4_000),
        overhead_rate=d(0),
        own_by_stage={"A": d(4_500)},
        unassigned_own=d(0),
        tax=tax,
        target_margin=d("0.3"),
    )
    assert (first.tax, second.tax) == (d(30), d(10))


def test_stage_without_revenue_is_red() -> None:
    tax = calculate_tax(tax_terms(UsnProfile()), d(0), d(0))
    (result,) = stage_results(
        [_stage("Пусто")],
        revenue=d(0),
        overhead_rate=d(0),
        own_by_stage={},
        unassigned_own=d(0),
        tax=tax,
        target_margin=d("0.3"),
    )
    assert result.margin == 0
    assert result.flag == StageFlag.RED


PRICINGS: list[PricingModel] = [
    HourlyPricing(),
    CappedPricing(cap=d(120_000)),
    fixed(),
    fixed(90_000),
]
PROFILES: list[RfTaxProfile] = [UsnProfile(), USN_15, OsnoProfile()]


@given(
    hours=st.lists(st.integers(min_value=0, max_value=60), min_size=4, max_size=4),
    own=st.integers(min_value=0, max_value=50_000),
    bind_own=st.booleans(),
    pricing=st.sampled_from(PRICINGS),
    profile=st.sampled_from(PROFILES),
)
def test_stage_totals_add_up_to_project(
    hours: list[int], own: int, bind_own: bool, pricing: PricingModel, profile: RfTaxProfile
) -> None:
    # Σ R_s = R, Σ C_s = C, Σ Tax_s = Tax для любой модели и режима.
    a, b, c, e = hours
    stages = (
        stage("Анализ", ("Иванов", a), ("Петрова", b)),
        stage("Подготовка", ("Петрова", c), ("Иванов", e)),
    )
    scenario = basic_scenario(
        pricing,
        jurisdiction=Jurisdiction(rf=profile),
        stages=stages,
        disbursements=(
            disbursement(DisbursementKind.OWN, own, stage="Подготовка" if bind_own else None),
            disbursement(DisbursementKind.BILLABLE, 10_000),
        ),
    )
    estimate = calculate_estimate(scenario)
    if not estimate.stages or estimate.total_hours == 0:
        return
    assert abs(sum(s.revenue for s in estimate.stages) - estimate.revenue) <= EPS
    assert abs(sum(s.costs for s in estimate.stages) - estimate.total_costs) <= EPS
    assert abs(sum(s.tax for s in estimate.stages) - estimate.tax.tax) <= EPS
    assert abs(sum(s.nne for s in estimate.stages) - estimate.nne) <= EPS


@pytest.mark.parametrize(
    ("nne", "expected"),
    [
        ("1", Profitability.PROFITABLE),
        ("0", Profitability.BREAKEVEN),
        ("-1", Profitability.LOSS),
        # Цена безубыточности, найденная делением: NNE ≈ ±1e-24 — это ноль.
        ("-0.000000000000000000000002", Profitability.BREAKEVEN),
        ("0.004", Profitability.BREAKEVEN),
        ("0.005", Profitability.PROFITABLE),
    ],
)
def test_profitability(nne: str, expected: Profitability) -> None:
    assert profitability(d(nne)) == expected


def test_dashboard_bases_are_revenue_without_vat() -> None:
    # L-15: налог / R, а не налог / (услуги + пошлины). R = 165 000, налог 9 900.
    scenario = basic_scenario(
        jurisdiction=Jurisdiction(rf=OsnoProfile()),
        disbursements=(disbursement(DisbursementKind.BILLABLE, 35_000),),
    )
    estimate = calculate_estimate(scenario)
    view = dashboard(estimate)
    assert view.effective_tax_rate_pct == d(15_625) / d(165_000) * 100
    assert view.margin_pct == d(46_875) / d(165_000) * 100
    assert view.target_margin_pct == d(30)
    structure = view.price_structure
    total = structure.direct_labor + structure.overheads + structure.own + structure.tax
    assert total + structure.profit == estimate.revenue
    assert view.profitability == Profitability.PROFITABLE
    # L-19: строки этапов — полная маржа m_s, а не (ставка − себестоимость) / ставка.
    assert [s.margin for s in view.stages] == [s.margin for s in estimate.stages]
    assert view.stages[0].margin < (d(15_000) - d(8_000)) / d(15_000)


def test_dashboard_without_revenue() -> None:
    scenario = basic_scenario(stages=(stage("Пусто", ("Нет такого", 5)),))
    view = dashboard(calculate_estimate(scenario))
    assert view.margin_pct is None
    assert view.effective_tax_rate_pct is None
    assert view.underutilization_share_pct is None
    assert view.stages == ()
    assert view.profitability == Profitability.BREAKEVEN
