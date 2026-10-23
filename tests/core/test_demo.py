"""Демо-фирма: слайд 3 и пример 1 методологии воспроизводятся, все проекты считаются."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core import calculate_estimate
from rentab_core.analysis.plan_fact import plan_fact
from rentab_core.costing import employee_cost, overhead_rate
from rentab_core.demo import demo_firm, demo_workspace
from rentab_core.io import dumps, loads
from rentab_core.models import CappedPricing, FixedPricing, HourlyPricing
from rentab_core.money import Currency, round_to
from rentab_core.results import Estimate
from rentab_core.tax.eligibility import check_firm
from tests.core.factories import d
from tests.core.test_golden_slide3 import slide3


def estimate(index: int) -> Estimate:
    workspace = demo_workspace()
    return calculate_estimate(workspace.projects[index].scenario(workspace.firm))


def test_firm_has_slide3_rates() -> None:
    firm = demo_firm()
    assert [employee_cost(member).cost_rate for member in firm.team] == [4_000, 1_500, 500]
    assert overhead_rate(firm, Currency.RUB) == d(100)
    assert check_firm(firm) == []


def test_projects() -> None:
    names = [record.project.name for record in demo_workspace().projects]
    assert names == [
        "Слайд 3: план и факт",
        "Регистрация товарного знака",
        "Патентование изобретения",
        "Ответ на уведомление экспертизы",
    ]


@pytest.mark.parametrize("variant", ["plan", "fact"])
def test_slide3_plan_and_fact_match_golden(variant: str) -> None:
    workspace = demo_workspace()
    result = plan_fact(workspace.projects[0].scenario(workspace.firm))
    estimate_ = result.plan if variant == "plan" else result.fact
    golden = slide3(variant)
    assert estimate_.nne == golden.nne
    assert estimate_.total_client == golden.total_client == d(315_000)
    assert estimate_.direct_labor == golden.direct_labor
    assert estimate_.overheads_alloc == golden.overheads_alloc


def test_trademark_has_fact_with_unplanned_partner() -> None:
    # docs/methodology.md, раздел 18.6: почасовая по факту — NNE 58 800 → 78 160 ₽.
    workspace = demo_workspace()
    result = plan_fact(workspace.projects[1].scenario(workspace.firm))
    assert result.fact.nne == d(78_160)
    assert [(pair.stage_name, pair.employee) for pair in result.unplanned] == [
        ("Подготовка и подача заявки", "Партнёр")
    ]
    assert [record.project.actuals is None for record in workspace.projects] == [
        False,
        False,
        True,
        True,
    ]


def test_trademark_project_is_methodology_example_1() -> None:
    # docs/methodology.md, разделы 10.2–10.4: R = 100 000, C = 35 200, NNE = 58 800.
    result = estimate(1)
    assert result.revenue == d(100_000)
    assert result.total_costs == d(35_200)
    assert result.tax.tax == d(6_000)
    assert result.nne == d(58_800)
    # Пошлины Роспатента за три класса МКТУ: 4 000 + 2 × 1 000 и 13 000 + 2 × 2 500.
    assert result.disbursements_billed == d(24_000)
    assert result.total_client == d(129_000)


def test_invention_project_reaches_target_margin() -> None:
    result = estimate(2)
    assert isinstance(result.pricing, FixedPricing)
    assert round_to(result.margin) == Decimal("0.30")
    # 4 000 + 5 × 1 000 за 15 пунктов формулы; 14 000 + 10 000 за второй независимый пункт.
    assert result.disbursements_billed == d(33_000)


def test_office_action_project_is_capped() -> None:
    result = estimate(3)
    assert isinstance(result.pricing, CappedPricing)
    assert result.revenue == d(60_000)
    assert result.write_off == d(10_000)


def test_every_project_calculates_and_round_trips() -> None:
    workspace = demo_workspace()
    pricing_kinds = {type(record.project.pricing) for record in workspace.projects}
    assert pricing_kinds == {FixedPricing, HourlyPricing, CappedPricing}
    for record in workspace.projects:
        assert calculate_estimate(record.scenario(workspace.firm)).nne != 0
    assert loads(dumps(workspace)).workspace == workspace
