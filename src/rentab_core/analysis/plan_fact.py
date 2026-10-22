"""План–факт проекта (docs/methodology.md, раздел 18).

Смета по факту — тот же проект, в котором назначения этапов заменены
фактическими часами (`Project.actuals`); фирма, расходы, режим и НДС — те же.
Выручка по факту — по правилу выставления:

    почасовая                 V^f = Σ b × h^f — выставляется факт
    с потолком                min(V^f, cap)
    согласованная фикс-цена   price
    «издержки + маржа»        R плана — цена держится
    вручную                   фактически выставленная сумма

Реализация — выставлено / теневая стоимость без НДС; списание и премия —
разница в одну и в другую сторону. Перерасход — по парам «этап × сотрудник»,
этапам, сотрудникам и ролям: Δcost_i = (c_i + ρ) × Δh_i.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from enum import StrEnum

from rentab_core.errors import PlanFactError
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    ActualHours,
    Assignment,
    CappedPricing,
    CoreModel,
    FixedPricing,
    HourlyPricing,
    Project,
    Role,
    Scenario,
    Stage,
)
from rentab_core.money import ZERO, decimal_sum
from rentab_core.pricing import net_of_vat
from rentab_core.results import Estimate, EstimateLine, StageResult


class BillingRule(StrEnum):
    """Что выставляется клиенту по факту (раздел 18.2)."""

    BY_FACT = "by_fact"  # почасовая: факт по ставкам
    CAPPED = "capped"  # с потолком: факт по ставкам, не больше потолка
    AGREED = "agreed"  # согласованная фикс-цена
    PLAN_PRICE = "plan_price"  # «издержки + маржа»: цена плана держится
    MANUAL = "manual"  # фактически выставленная сумма введена вручную


def billing_rule(project: Project) -> BillingRule:
    """Правило выставления по факту для модели цены и факта проекта."""
    if project.actuals is not None and project.actuals.billed is not None:
        return BillingRule.MANUAL
    pricing = project.pricing
    if isinstance(pricing, HourlyPricing):
        return BillingRule.BY_FACT
    if isinstance(pricing, CappedPricing):
        return BillingRule.CAPPED
    assert isinstance(pricing, FixedPricing)
    return BillingRule.PLAN_PRICE if pricing.price is None else BillingRule.AGREED


class Realization(CoreModel):
    """Реализация сметы (раздел 18.3).

    Attributes:
        shadow_value: V — теневая стоимость часов по ставкам без НДС.
        billed: R — выставлено без НДС.
    """

    shadow_value: Decimal
    billed: Decimal

    @property
    def rate(self) -> Decimal | None:
        """R / V; None — ставки для клиента не заданы (V = 0)."""
        return self.billed / self.shadow_value if self.shadow_value > 0 else None

    @property
    def write_off(self) -> Decimal:
        """Списание: max(V − R, 0)."""
        return max(self.shadow_value - self.billed, ZERO)

    @property
    def premium(self) -> Decimal:
        """Премия к ставкам: max(R − V, 0)."""
        return max(self.billed - self.shadow_value, ZERO)


def realization(estimate: Estimate) -> Realization:
    """Реализация сметы: выставлено против теневой стоимости часов."""
    return Realization(shadow_value=estimate.shadow_value_net, billed=estimate.revenue)


class PairVariance(CoreModel):
    """Отклонение по паре «этап × сотрудник».

    Attributes:
        unplanned: Работа вне плана: в плане этой пары нет.
        missing: Пара плана без строки факта (считается нулём часов).
        resolved: Сотрудник есть в команде; иначе пара в расчёт не входит.
    """

    stage_name: str
    employee: str
    role: Role | None
    plan_hours: Decimal
    fact_hours: Decimal
    unplanned: bool = False
    missing: bool = False
    resolved: bool = True

    @property
    def delta_hours(self) -> Decimal:
        """Δh = факт − план."""
        return self.fact_hours - self.plan_hours

    @property
    def delta_share(self) -> Decimal | None:
        """Δh / план; None — вне плана."""
        return self.delta_hours / self.plan_hours if self.plan_hours > 0 else None


class EmployeeVariance(CoreModel):
    """Перерасход сотрудника.

    Attributes:
        cost_per_hour: c + ρ — себестоимость часа с накладными.
        unplanned_hours: Часы на этапах, где сотрудника в плане не было.
    """

    employee: str
    role: Role
    plan_hours: Decimal
    fact_hours: Decimal
    cost_per_hour: Decimal
    unplanned_hours: Decimal = ZERO

    @property
    def delta_hours(self) -> Decimal:
        """Δh = факт − план."""
        return self.fact_hours - self.plan_hours

    @property
    def delta_cost(self) -> Decimal:
        """Δcost = (c + ρ) × Δh."""
        return self.cost_per_hour * self.delta_hours


class RoleVariance(CoreModel):
    """Перерасход роли: сумма по её сотрудникам."""

    role: Role
    plan_hours: Decimal
    fact_hours: Decimal
    delta_cost: Decimal

    @property
    def delta_hours(self) -> Decimal:
        """Δh = факт − план."""
        return self.fact_hours - self.plan_hours


class StageVariance(CoreModel):
    """Перерасход этапа.

    Attributes:
        plan: Экономика этапа по плану; None — у этапа нет часов в плане.
        fact: Экономика этапа по факту; None — у этапа нет часов по факту.
    """

    stage_name: str
    plan: StageResult | None
    fact: StageResult | None

    @property
    def plan_hours(self) -> Decimal:
        """Часы этапа по плану."""
        return ZERO if self.plan is None else self.plan.hours

    @property
    def fact_hours(self) -> Decimal:
        """Часы этапа по факту."""
        return ZERO if self.fact is None else self.fact.hours

    @property
    def delta_hours(self) -> Decimal:
        """ΔH_s."""
        return self.fact_hours - self.plan_hours

    @property
    def delta_share(self) -> Decimal | None:
        """ΔH_s / H_s плана; None — у этапа не было часов в плане."""
        return self.delta_hours / self.plan_hours if self.plan_hours > 0 else None

    @property
    def delta_cost(self) -> Decimal:
        """ΔC_s = ΔLabor_s + ρ × ΔH_s (без собственных расходов)."""

        def hours_cost(result: StageResult | None) -> Decimal:
            return ZERO if result is None else result.labor + result.overheads

        return hours_cost(self.fact) - hours_cost(self.plan)


class PlanFact(CoreModel):
    """План–факт проекта (раздел 18).

    Attributes:
        plan: Смета по плану.
        fact: Смета по факту.
        rule: Правило выставления по факту.
        pairs: Пары «этап × сотрудник»: по этапам, сначала пары плана.
        stages: Этапы проекта в порядке плана.
        employees: Сотрудники команды с часами по плану или факту.
        roles: Роли от старшей к младшей.
    """

    plan: Estimate
    fact: Estimate
    rule: BillingRule
    pairs: tuple[PairVariance, ...]
    stages: tuple[StageVariance, ...]
    employees: tuple[EmployeeVariance, ...]
    roles: tuple[RoleVariance, ...]

    @property
    def plan_realization(self) -> Realization:
        """Реализация по плану."""
        return realization(self.plan)

    @property
    def fact_realization(self) -> Realization:
        """Реализация по факту."""
        return realization(self.fact)

    @property
    def delta_nne(self) -> Decimal:
        """ΔNNE = ΔR − ΔC − ΔTax."""
        return self.fact.nne - self.plan.nne

    @property
    def missing(self) -> tuple[PairVariance, ...]:
        """Пары плана без строки факта."""
        return tuple(pair for pair in self.pairs if pair.missing)

    @property
    def unplanned(self) -> tuple[PairVariance, ...]:
        """Работа вне плана."""
        return tuple(pair for pair in self.pairs if pair.unplanned)


def fact_project(project: Project) -> Project:
    """Проект по факту: назначения этапов заменены фактическими часами.

    Raises:
        PlanFactError: Факт проекта не введён.
    """
    if project.actuals is None:
        raise PlanFactError("Факт по проекту не введён")
    rows = project.actuals.hours
    stages = tuple(
        Stage(
            name=stage.name,
            assignments=tuple(
                Assignment(employee=row.employee, hours=row.hours)
                for row in rows
                if row.stage == stage.name
            ),
        )
        for stage in project.stages
    )
    return project.model_copy(update={"stages": stages})


def plan_pairs(project: Project) -> dict[tuple[str, str], Decimal]:
    """Часы плана по парам «этап × сотрудник» (повторы назначений складываются)."""
    pairs: dict[tuple[str, str], Decimal] = {}
    for stage in project.stages:
        for assignment in stage.assignments:
            key = (stage.name, assignment.employee)
            pairs[key] = pairs.get(key, ZERO) + assignment.hours
    return pairs


def actuals_from_plan(project: Project) -> tuple[ActualHours, ...]:
    """Факт, равный плану: с него начинается ввод факта."""
    return tuple(
        ActualHours(stage=stage, employee=employee, hours=hours)
        for (stage, employee), hours in plan_pairs(project).items()
    )


def _pairs(scenario: Scenario) -> tuple[PairVariance, ...]:
    project = scenario.project
    assert project.actuals is not None
    team = scenario.firm.team_by_name()
    plan = plan_pairs(project)
    fact = {(row.stage, row.employee): row.hours for row in project.actuals.hours}
    result = []
    for stage in project.stages:
        keys = [key for key in plan if key[0] == stage.name]
        keys += [key for key in fact if key[0] == stage.name and key not in plan]
        for key in keys:
            employee = team.get(key[1])
            result.append(
                PairVariance(
                    stage_name=key[0],
                    employee=key[1],
                    role=None if employee is None else employee.role,
                    plan_hours=plan.get(key, ZERO),
                    fact_hours=fact.get(key, ZERO),
                    unplanned=key not in plan,
                    missing=key not in fact,
                    resolved=employee is not None,
                )
            )
    return tuple(result)


def _cost_per_hour(lines: Iterable[EstimateLine], overhead_rate: Decimal) -> dict[str, Decimal]:
    return {line.employee_name: line.cost_rate + overhead_rate for line in lines}


def _employees(
    pairs: Sequence[PairVariance], plan: Estimate, fact: Estimate, scenario: Scenario
) -> tuple[EmployeeVariance, ...]:
    team = scenario.firm.team_by_name()
    costs = _cost_per_hour(plan.lines, plan.overhead_rate)
    costs |= _cost_per_hour(fact.lines, fact.overhead_rate)
    result = []
    for name in dict.fromkeys(pair.employee for pair in pairs if pair.resolved):
        own = [pair for pair in pairs if pair.employee == name]
        result.append(
            EmployeeVariance(
                employee=name,
                role=team[name].role,
                plan_hours=decimal_sum(pair.plan_hours for pair in own),
                fact_hours=decimal_sum(pair.fact_hours for pair in own),
                # Сотрудник без часов ни в плане, ни в факте перерасхода не даёт.
                cost_per_hour=costs.get(name, ZERO),
                unplanned_hours=decimal_sum(pair.fact_hours for pair in own if pair.unplanned),
            )
        )
    return tuple(result)


def _roles(employees: Sequence[EmployeeVariance]) -> tuple[RoleVariance, ...]:
    return tuple(
        RoleVariance(
            role=role,
            plan_hours=decimal_sum(item.plan_hours for item in members),
            fact_hours=decimal_sum(item.fact_hours for item in members),
            delta_cost=decimal_sum(item.delta_cost for item in members),
        )
        for role in Role
        if (members := [item for item in employees if item.role == role])
    )


def plan_fact(scenario: Scenario) -> PlanFact:
    """План–факт проекта (раздел 18).

    Raises:
        PlanFactError: Факт проекта не введён.
        FixedPriceError: Цена «издержки + маржа» недостижима при режиме.
        CurrencyConversionError: Для пересчёта суммы в валюту проекта нет курса.
    """
    project = scenario.project
    fact_scenario = Scenario(firm=scenario.firm, project=fact_project(project))
    plan = calculate_estimate(scenario)
    rule = billing_rule(project)
    revenue: Decimal | None = None
    billed = None if project.actuals is None else project.actuals.billed
    if billed is not None:
        revenue = net_of_vat(billed, project.vat_mode, plan.tax.terms.vat_rate)
    elif rule == BillingRule.PLAN_PRICE:
        revenue = plan.revenue
    fact = calculate_estimate(fact_scenario, revenue=revenue)
    pairs = _pairs(scenario)
    employees = _employees(pairs, plan, fact, scenario)
    plan_stages = {stage.stage_name: stage for stage in plan.stages}
    fact_stages = {stage.stage_name: stage for stage in fact.stages}
    return PlanFact(
        plan=plan,
        fact=fact,
        rule=rule,
        pairs=pairs,
        stages=tuple(
            StageVariance(
                stage_name=stage.name,
                plan=plan_stages.get(stage.name),
                fact=fact_stages.get(stage.name),
            )
            for stage in project.stages
        ),
        employees=employees,
        roles=_roles(employees),
    )
