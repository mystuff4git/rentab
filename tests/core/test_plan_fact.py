"""План–факт (docs/methodology.md, раздел 18).

Golden-тесты: слайд 3 как один проект «план → факт» (раздел 18.5) и пример 1
по факту в трёх моделях цены (раздел 18.6); property-тесты — тождества
перерасхода и «факт = план».
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentab_core.analysis.plan_fact import (
    BillingRule,
    actuals_from_plan,
    billing_rule,
    fact_project,
    plan_fact,
    plan_pairs,
)
from rentab_core.errors import PlanFactError
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    ActualHours,
    CappedPricing,
    HourlyPricing,
    PricingModel,
    Project,
    ProjectActuals,
    Role,
    Scenario,
    VatMode,
)
from rentab_core.money import round_to
from rentab_core.results import StageFlag
from tests.core.factories import actuals, d, fixed, stage
from tests.core.strategies import scenarios
from tests.core.test_methodology_examples import USN_6_VAT_5, example_firm, example_project

ANALYSIS = "Анализ охраноспособности"
FILING = "Подготовка и подача заявки"
PCT = d("0.01")


def slide3_plan_fact() -> Scenario:
    """Слайд 3 одним проектом: план 5 / 15 / 20, факт 62 / 15 / 10."""
    return Scenario(
        firm=example_firm(USN_6_VAT_5),
        project=Project(
            name="Слайд 3: план и факт",
            pricing=fixed(300_000),
            stages=(stage("Проект", ("Партнёр", 5), ("Юрист", 15), ("Стажёр", 20)),),
            actuals=actuals(
                ("Проект", "Партнёр", 62), ("Проект", "Юрист", 15), ("Проект", "Стажёр", 10)
            ),
        ),
    )


EXAMPLE1_FACT = actuals(
    (ANALYSIS, "Партнёр", 3),
    (ANALYSIS, "Юрист", 10),
    (FILING, "Юрист", 4),
    (FILING, "Стажёр", 12),
    (FILING, "Партнёр", 1),
)


def example1_plan_fact(pricing: PricingModel, fact: ProjectActuals = EXAMPLE1_FACT) -> Scenario:
    project = example_project(pricing).model_copy(update={"actuals": fact})
    return Scenario(firm=example_firm(USN_6_VAT_5), project=project)


def pct(value: Decimal | None) -> Decimal:
    assert value is not None
    return round_to(value * 100, PCT)


def test_slide3_plan_to_fact() -> None:
    # Раздел 18.5: NNE 225 500 → −2 200 ₽.
    result = plan_fact(slide3_plan_fact())
    assert result.rule == BillingRule.AGREED
    plan, fact = result.plan, result.fact
    assert (plan.total_hours, fact.total_hours) == (d(40), d(87))
    assert (plan.revenue, fact.revenue) == (d(300_000), d(300_000))
    assert (plan.direct_labor, fact.direct_labor) == (d(52_500), d(275_500))
    assert (plan.overheads_alloc, fact.overheads_alloc) == (d(4_000), d(8_700))
    assert (plan.tax.tax, fact.tax.tax) == (d(18_000), d(18_000))
    assert (plan.nne, fact.nne) == (d(225_500), d(-2_200))
    assert result.delta_nne == d(-227_700)
    assert (pct(plan.margin), pct(fact.margin)) == (d("75.17"), d("-0.73"))
    assert (plan.total_client, fact.total_client) == (d(315_000), d(315_000))
    assert pct(result.plan_realization.rate) == d("181.82")
    assert pct(result.fact_realization.rate) == d("41.96")
    assert result.plan_realization.premium == d(135_000)
    assert result.plan_realization.write_off == 0
    assert result.fact_realization.write_off == d(415_000)
    assert result.fact_realization.premium == 0
    roles = {item.role: (item.delta_hours, item.delta_cost) for item in result.roles}
    assert roles == {
        Role.PARTNER: (d(57), d(233_700)),
        Role.ASSOCIATE: (d(0), d(0)),
        Role.INTERN: (d(-10), d(-6_000)),
    }
    (only,) = result.stages
    assert only.delta_hours == d(47)
    assert only.plan is not None
    assert only.fact is not None
    assert (only.plan.flag, only.fact.flag) == (StageFlag.GREEN, StageFlag.RED)
    assert only.delta_cost == d(227_700)
    assert result.missing == ()
    assert result.unplanned == ()


@pytest.mark.parametrize(
    ("pricing", "rule", "revenue", "realization", "write_off", "tax", "nne", "margin"),
    [
        (HourlyPricing(), BillingRule.BY_FACT, 134_000, "100.00", 0, 8_040, 78_160, "58.33"),
        (
            CappedPricing(cap=d(90_000)),
            BillingRule.CAPPED,
            90_000,
            "67.16",
            44_000,
            5_400,
            36_800,
            "40.89",
        ),
        (fixed(), BillingRule.PLAN_PRICE, 55_000, "41.04", 79_000, 3_300, 3_900, "7.09"),
    ],
)
def test_example1_fact_by_pricing_model(
    pricing: PricingModel,
    rule: BillingRule,
    revenue: int,
    realization: str,
    write_off: int,
    tax: int,
    nne: int,
    margin: str,
) -> None:
    # Раздел 18.6.
    result = plan_fact(example1_plan_fact(pricing))
    assert result.rule == rule
    fact = result.fact
    assert fact.total_costs == d(47_800)
    assert fact.shadow_value == d(134_000)
    assert fact.revenue == d(revenue)
    assert pct(result.fact_realization.rate) == d(realization)
    assert round_to(result.fact_realization.write_off) == d(write_off)
    assert round_to(fact.tax.tax) == d(tax)
    assert round_to(fact.nne) == d(nne)
    assert pct(fact.margin) == d(margin)


def test_example1_overrun_by_pair_stage_employee() -> None:
    result = plan_fact(example1_plan_fact(HourlyPricing()))
    pairs = [
        (p.stage_name, p.employee, p.plan_hours, p.fact_hours, p.unplanned) for p in result.pairs
    ]
    assert pairs == [
        (ANALYSIS, "Партнёр", d(2), d(3), False),
        (ANALYSIS, "Юрист", d(8), d(10), False),
        (FILING, "Юрист", d(4), d(4), False),
        (FILING, "Стажёр", d(10), d(12), False),
        (FILING, "Партнёр", d(0), d(1), True),
    ]
    assert result.unplanned[0].delta_share is None
    assert result.pairs[0].delta_share == d("0.5")
    employees = {
        e.employee: (e.delta_hours, e.delta_cost, e.unplanned_hours) for e in result.employees
    }
    assert employees == {
        "Партнёр": (d(2), d(8_200), d(1)),
        "Юрист": (d(2), d(3_200), d(0)),
        "Стажёр": (d(2), d(1_200), d(0)),
    }
    stages = {
        s.stage_name: (s.delta_hours, round_to(s.delta_share or d(0), d("0.001")), s.delta_cost)
        for s in result.stages
    }
    assert stages == {ANALYSIS: (d(3), d("0.300"), d(7_300)), FILING: (d(3), d("0.214"), d(5_300))}
    assert all(s.fact is not None and s.fact.flag == StageFlag.GREEN for s in result.stages)
    cost_plus = plan_fact(example1_plan_fact(fixed()))
    assert [s.fact.flag for s in cost_plus.stages if s.fact] == [StageFlag.YELLOW, StageFlag.YELLOW]


def test_manual_billed_amount() -> None:
    # Раздел 18.6: фирма выставила 120 000 ₽ вместо 134 000.
    fact = EXAMPLE1_FACT.model_copy(update={"billed": d(120_000)})
    result = plan_fact(example1_plan_fact(HourlyPricing(), fact))
    assert result.rule == BillingRule.MANUAL
    assert pct(result.fact_realization.rate) == d("89.55")
    assert result.fact_realization.write_off == d(14_000)
    assert result.fact.tax.tax == d(7_200)
    assert result.fact.nne == d(65_000)
    # Цена «включая НДС»: выставленная сумма содержит НДС 5%.
    included = example1_plan_fact(HourlyPricing(), fact.model_copy(update={"billed": d(126_000)}))
    included = included.model_copy(
        update={"project": included.project.model_copy(update={"vat_mode": VatMode.INCLUDED})}
    )
    assert plan_fact(included).fact.revenue == d(120_000)


def test_missing_fact_unresolved_employee_and_no_fact() -> None:
    fact = actuals((ANALYSIS, "Партнёр", 2), (FILING, "Консультант", 5))
    result = plan_fact(example1_plan_fact(HourlyPricing(), fact))
    assert [(p.stage_name, p.employee) for p in result.missing] == [
        (ANALYSIS, "Юрист"),
        (FILING, "Юрист"),
        (FILING, "Стажёр"),
    ]
    stranger = next(p for p in result.pairs if p.employee == "Консультант")
    assert not stranger.resolved
    assert stranger.role is None
    assert stranger.unplanned
    assert [e.employee for e in result.employees] == ["Партнёр", "Юрист", "Стажёр"]
    assert len(result.fact.unresolved_assignments) == 1
    # Этап без часов по факту: его экономики по факту нет.
    filing = next(s for s in result.stages if s.stage_name == FILING)
    assert filing.fact is None
    assert filing.fact_hours == 0
    with pytest.raises(PlanFactError, match="не введён"):
        plan_fact(
            Scenario(firm=example_firm(USN_6_VAT_5), project=example_project(HourlyPricing()))
        )


def test_actuals_from_plan_sum_repeated_assignments() -> None:
    project = Project(stages=(stage("Этап", ("Юрист", 2), ("Юрист", 3), ("Партнёр", 1)),))
    assert plan_pairs(project) == {("Этап", "Юрист"): d(5), ("Этап", "Партнёр"): d(1)}
    assert actuals_from_plan(project) == (
        ActualHours(stage="Этап", employee="Юрист", hours=d(5)),
        ActualHours(stage="Этап", employee="Партнёр", hours=d(1)),
    )
    assert billing_rule(project) == BillingRule.BY_FACT
    with pytest.raises(PlanFactError):
        fact_project(project)


# ---------------------------------------------------------------------------
# Property-тесты
# ---------------------------------------------------------------------------
@st.composite
def with_actuals(draw: st.DrawFn) -> Scenario:
    """Сценарий с фактом: часы плана × случайный множитель и работа вне плана."""
    scenario = draw(scenarios())
    project = scenario.project
    rows = [
        ActualHours(
            stage=stage_,
            employee=employee,
            hours=hours * draw(st.sampled_from([0, 1, 2]).map(d)) / 2,
        )
        for (stage_, employee), hours in plan_pairs(project).items()
    ]
    names = [employee.name for employee in scenario.firm.team]
    planned = {(row.stage, row.employee) for row in rows}
    for stage_ in project.stages:
        for name in names:
            if (stage_.name, name) not in planned and draw(st.booleans()):
                rows.append(
                    ActualHours(stage=stage_.name, employee=name, hours=d(draw(st.integers(1, 30))))
                )
    billed = draw(st.none() | st.integers(1_000, 500_000).map(d))
    return scenario.model_copy(
        update={
            "project": project.model_copy(
                update={"actuals": ProjectActuals(hours=tuple(rows), billed=billed)}
            )
        }
    )


@settings(max_examples=100, deadline=None)
@given(scenario=with_actuals())
def test_overrun_identities(scenario: Scenario) -> None:
    result = plan_fact(scenario)
    plan, fact = result.plan, result.fact
    resolved = [pair for pair in result.pairs if pair.resolved]
    delta_hours = fact.total_hours - plan.total_hours
    assert sum((pair.delta_hours for pair in resolved), d(0)) == delta_hours
    assert sum((item.delta_hours for item in result.employees), d(0)) == delta_hours
    assert sum((item.delta_hours for item in result.roles), d(0)) == delta_hours
    hours_cost = (fact.direct_labor + fact.overheads_alloc) - (
        plan.direct_labor + plan.overheads_alloc
    )
    assert abs(sum((item.delta_cost for item in result.employees), d(0)) - hours_cost) < d("1e-9")
    assert abs(sum((item.delta_cost for item in result.stages), d(0)) - hours_cost) < d("1e-9")
    expected_delta = (
        (fact.revenue - plan.revenue)
        - (fact.total_costs - plan.total_costs)
        - (fact.tax.tax - plan.tax.tax)
    )
    assert abs(result.delta_nne - expected_delta) < d("1e-9")
    real = result.fact_realization
    if real.rate is not None:
        if result.rule == BillingRule.BY_FACT:
            assert abs(real.rate - 1) < d("1e-20")
        if result.rule == BillingRule.CAPPED:
            assert abs(real.rate - min(d(1), real.billed / real.shadow_value)) < d("1e-20")
            assert real.billed <= real.shadow_value + d("1e-20")
    assert real.write_off >= 0
    assert real.premium >= 0
    assert real.billed - real.shadow_value == real.premium - real.write_off


@settings(max_examples=100, deadline=None)
@given(scenario=scenarios())
def test_fact_equal_to_plan_changes_nothing(scenario: Scenario) -> None:
    project = scenario.project
    same = scenario.model_copy(
        update={
            "project": project.model_copy(
                update={"actuals": ProjectActuals(hours=actuals_from_plan(project))}
            )
        }
    )
    result = plan_fact(same)
    assert abs(result.fact.nne - result.plan.nne) < d("1e-9")
    assert result.fact.revenue == result.plan.revenue
    assert all(pair.delta_hours == 0 for pair in result.pairs)
    assert all(item.delta_cost == 0 for item in result.employees)
    assert result.plan_realization == result.fact_realization
    assert result.plan == calculate_estimate(scenario)
