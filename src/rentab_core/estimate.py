"""Расчёт сметы проекта: точка входа ядра (docs/methodology.md).

Порядок: страна и валюта проекта → строки «этап × сотрудник» с себестоимостью
биллируемого часа → ставка накладных → прямые расходы в валюте проекта →
издержки C → выручка R по модели цены → налог и НДС → NNE → этапы.
"""

from __future__ import annotations

from typing import assert_never

from rentab_core.analysis.stages import StageLines, stage_results
from rentab_core.analysis.team import blended_rate, leverage
from rentab_core.costing import employee_cost, overhead_rate
from rentab_core.fx import convert
from rentab_core.models import (
    CappedPricing,
    DisbursementKind,
    Firm,
    FixedPricing,
    HourlyPricing,
    Project,
    Scenario,
)
from rentab_core.money import ZERO, Currency, decimal_sum
from rentab_core.pricing.fixed import agreed_revenue, cost_plus_price
from rentab_core.pricing.hourly import capped_revenue, hourly_revenue, shadow_value
from rentab_core.results import (
    DisbursementLine,
    Estimate,
    EstimateLine,
    UnresolvedAssignment,
)
from rentab_core.tax import calculate_tax, tax_terms


def resolve_stages(
    firm: Firm, project: Project, currency: Currency
) -> tuple[list[StageLines], list[UnresolvedAssignment]]:
    """Сопоставляет назначения этапов с командой и переводит ставки в валюту проекта.

    Назначение с исполнителем, которого нет в команде, пропускается (как в
    v0.3); этап без единого найденного исполнителя в расчёт не входит.

    Returns:
        Этапы со строками сметы и список пропущенных назначений.

    Raises:
        CurrencyConversionError: Ставки сотрудника в валюте, для которой нет курса.
    """
    team = firm.team_by_name()
    rates = firm.exchange_rates
    stages: list[StageLines] = []
    unresolved: list[UnresolvedAssignment] = []
    for stage in project.stages:
        lines: list[EstimateLine] = []
        for assignment in stage.assignments:
            employee = team.get(assignment.employee)
            if employee is None:
                unresolved.append(
                    UnresolvedAssignment(stage_name=stage.name, employee=assignment.employee)
                )
                continue
            cost = employee_cost(employee)
            lines.append(
                EstimateLine(
                    stage_name=stage.name,
                    employee_name=employee.name,
                    role=employee.role,
                    billing_rate=convert(employee.billing_rate, employee.currency, currency, rates),
                    cost_rate=convert(cost.cost_rate, employee.currency, currency, rates),
                    contract_cost_rate=convert(
                        cost.contract_cost_rate, employee.currency, currency, rates
                    ),
                    hours=assignment.hours,
                )
            )
        if lines:
            stages.append(StageLines(name=stage.name, lines=tuple(lines)))
    return stages, unresolved


def calculate_estimate(scenario: Scenario) -> Estimate:
    """Рассчитывает смету проекта.

    Args:
        scenario: Фирма и проект.

    Returns:
        Смета в валюте страны расчёта налога.

    Raises:
        FixedPriceError: Цена «издержки + маржа» недостижима при режиме.
        CurrencyConversionError: Для пересчёта суммы в валюту проекта нет курса.
    """
    firm, project = scenario.firm, scenario.project
    calc_country = firm.jurisdiction.calc_country(project.calc_country)
    currency = calc_country.currency
    terms = tax_terms(firm.jurisdiction.tax_profile(calc_country))
    rates = firm.exchange_rates

    stages, unresolved = resolve_stages(firm, project, currency)
    lines = tuple(line for stage in stages for line in stage.lines)
    total_hours = decimal_sum(line.hours for line in lines)
    rho = overhead_rate(firm, currency)
    disbursements = tuple(
        DisbursementLine(
            name=item.name,
            kind=item.kind,
            original_amount=item.amount,
            original_currency=item.currency,
            amount=convert(item.amount, item.currency, currency, rates),
            stage=item.stage,
        )
        for item in project.disbursements
    )
    billed = decimal_sum(item.amount for item in disbursements if item.billable)
    own = decimal_sum(item.amount for item in disbursements if not item.billable)
    labor = decimal_sum(line.labor_cost for line in lines)
    overheads_alloc = rho * total_hours
    total_costs = labor + overheads_alloc + own
    shadow = shadow_value(lines)

    pricing = project.pricing
    match pricing:
        case HourlyPricing():
            revenue = hourly_revenue(shadow, project.vat_mode, terms.vat_rate)
        case CappedPricing():
            revenue = capped_revenue(shadow, pricing.cap, project.vat_mode, terms.vat_rate)
        case FixedPricing():
            if pricing.price is None:
                revenue = cost_plus_price(terms, total_costs, project.target_margin)
            else:
                revenue = agreed_revenue(pricing.price, project.vat_mode, terms.vat_rate)
        case _:
            assert_never(pricing)

    tax = calculate_tax(terms, revenue, total_costs)
    nne = revenue - total_costs - tax.tax

    # Собственный расход привязан к этапу, только если этап вошёл в расчёт;
    # иначе он распределяется по этапам вместе с непривязанными.
    stage_names = {stage.name for stage in stages}
    own_items = [item for item in disbursements if item.kind == DisbursementKind.OWN]
    own_by_stage = {
        name: decimal_sum(item.amount for item in own_items if item.stage == name)
        for name in stage_names
    }
    unassigned_own = decimal_sum(item.amount for item in own_items if item.stage not in stage_names)

    return Estimate(
        project_name=project.name,
        client=project.client,
        description=project.description,
        calc_country=calc_country,
        jurisdiction_mode=firm.jurisdiction.mode,
        currency=currency,
        pricing=pricing,
        vat_mode=project.vat_mode,
        target_margin=project.target_margin,
        lines=lines,
        disbursements=disbursements,
        unresolved_assignments=tuple(unresolved),
        total_hours=total_hours,
        blended_rate=blended_rate(lines),
        leverage=leverage(lines),
        overhead_rate=rho,
        shadow_value=shadow,
        revenue=revenue,
        disbursements_billed=billed,
        disbursements_own=own,
        direct_labor=labor,
        overheads_alloc=overheads_alloc,
        total_costs=total_costs,
        underutilization_cost=decimal_sum(line.underutilization_cost for line in lines),
        tax=tax,
        nne=nne,
        margin=nne / revenue if revenue > 0 else ZERO,
        stages=stage_results(
            stages,
            revenue=revenue,
            overhead_rate=rho,
            own_by_stage=own_by_stage,
            unassigned_own=unassigned_own,
            tax=tax,
            target_margin=project.target_margin,
        ),
    )
