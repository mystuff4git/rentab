"""Светофор этапов и показатели дашборда."""

from __future__ import annotations

import pytest

from rentab_core.analysis.dashboard import (
    FixedDashboard,
    HourlyDashboard,
    Profitability,
    dashboard,
    profitability,
)
from rentab_core.analysis.stages import StageLines, classify, stage_results
from rentab_core.estimate import calculate_estimate
from rentab_core.models import DisbursementKind
from rentab_core.results import FixedPriceEstimate, StageFlag
from tests.core.factories import basic_scenario, d, disbursement, fixed, line, stage


@pytest.mark.parametrize(
    ("margin", "flag"),
    [
        ("-0.1", StageFlag.RED),
        ("0", StageFlag.RED),
        ("0.01", StageFlag.YELLOW),
        ("0.3", StageFlag.GREEN),
    ],
)
def test_classify(margin: str, flag: StageFlag) -> None:
    assert classify(d(margin), d("0.3")) == flag


def test_stage_results_split_by_hours() -> None:
    # KNOWN-BUG L-04 → фаза 2: выручка и накладные делятся по часам, маржа до налога.
    stages = [
        StageLines(name="Подготовка", lines=(line(10, billing=0, cost=5000),)),
        StageLines(name="Регистрация", lines=(line(15, billing=0, cost=2000),)),
    ]
    results = stage_results(
        stages, fixed_price=d(100_000), target_margin=d("0.3"), overheads_alloc=d(12_500)
    )
    assert [r.revenue_share for r in results] == [d(40_000), d(60_000)]
    assert [r.costs for r in results] == [d(55_000), d(37_500)]
    assert [r.flag for r in results] == [StageFlag.RED, StageFlag.GREEN]


def test_stage_without_hours_is_red() -> None:
    (result,) = stage_results(
        [StageLines(name="Пусто", lines=())],
        fixed_price=d(0),
        target_margin=d("0.3"),
        overheads_alloc=d(0),
    )
    assert result.margin == 0
    assert result.flag == StageFlag.RED


@pytest.mark.parametrize(
    ("nne", "expected"),
    [("1", Profitability.PROFITABLE), ("0", Profitability.BREAKEVEN), ("-1", Profitability.LOSS)],
)
def test_profitability(nne: str, expected: Profitability) -> None:
    assert profitability(d(nne)) == expected


def test_hourly_dashboard() -> None:
    scenario = basic_scenario(disbursements=(disbursement(DisbursementKind.BILLABLE, 35_000),))
    view = dashboard(calculate_estimate(scenario))
    assert isinstance(view, HourlyDashboard)
    # R = 165 000; NNE = 165 000 − 92 500 − 10 000 − 9 900 = 52 600.
    assert view.nne_margin_pct == d(52_600) / d(165_000) * 100
    # KNOWN-BUG L-15 → фаза 2: налог делится на услуги + пошлины.
    assert view.effective_tax_rate_pct == d(9_900) / d(200_000) * 100
    # KNOWN-BUG L-19 → фаза 2: маржа строки без накладных и налога.
    assert [row.margin_pct for row in view.stage_rows] == [
        d(7000) / d(15000) * 100,
        d(2500) / d(6000) * 100,
    ]
    assert view.stage_totals.hours == d(20)
    assert view.stage_totals.revenue == d(165_000)
    assert view.profitability == Profitability.PROFITABLE


def test_hourly_dashboard_without_revenue() -> None:
    scenario = basic_scenario(stages=(stage("Пусто", "Нет такого", 5),))
    view = dashboard(calculate_estimate(scenario))
    assert isinstance(view, HourlyDashboard)
    assert view.nne_margin_pct is None
    assert view.effective_tax_rate_pct == 0
    assert view.stage_totals.margin_pct == 0
    assert view.profitability == Profitability.BREAKEVEN


def test_fixed_dashboard_sums_hours_by_stage_name() -> None:
    # KNOWN-BUG L-05 → фаза 2: одноимённые этапы получают общие часы.
    scenario = basic_scenario(
        fixed(),
        stages=(stage("Ответ", "Петрова", 10), stage("Ответ", "Иванов", 2)),
    )
    estimate = calculate_estimate(scenario)
    assert isinstance(estimate, FixedPriceEstimate)
    view = dashboard(estimate)
    assert isinstance(view, FixedDashboard)
    assert [row.hours for row in view.stage_rows] == [d(12), d(12)]
    structure = view.price_structure
    total = structure.direct_labor + structure.indirect + structure.tax + structure.profit
    assert total == estimate.fixed_price
    assert view.target_margin_pct == d(30)
