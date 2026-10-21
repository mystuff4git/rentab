"""Расчёт сметы проекта: точка входа ядра.

В v0.3 эта сборка жила в обработчике кнопки «Рассчитать смету» страницы
`pages/02_Проект.py:549–751` (L-13). Порядок шагов и формулы сохранены.
"""

from __future__ import annotations

from typing import Any, assert_never

from rentab_core.analysis.stages import StageLines, stage_results
from rentab_core.analysis.team import blended_rate, leverage
from rentab_core.costing import (
    allocate_overheads,
    direct_labor,
    disbursements_total,
    overhead_rate,
)
from rentab_core.models import (
    DisbursementKind,
    Firm,
    FixedPricing,
    HourlyPricing,
    Project,
    Scenario,
)
from rentab_core.money import decimal_sum
from rentab_core.pricing.fixed import quote_fixed_price
from rentab_core.pricing.hourly import gross_revenue, net_net_earnings
from rentab_core.results import (
    Estimate,
    EstimateLine,
    FixedPriceEstimate,
    HourlyEstimate,
    UnresolvedAssignment,
)
from rentab_core.tax import calculate_tax


def resolve_stages(
    firm: Firm, project: Project
) -> tuple[list[StageLines], list[UnresolvedAssignment]]:
    """Сопоставляет назначения этапов с командой фирмы.

    Назначение с исполнителем, которого нет в команде, пропускается, как в
    v0.3; этап без единого найденного исполнителя в расчёт не входит.

    Returns:
        Этапы со строками сметы и список пропущенных назначений.
    """
    team = firm.team_by_name()
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
            lines.append(
                EstimateLine(
                    stage_name=stage.name,
                    employee_name=employee.name,
                    role=employee.role,
                    billing_rate=employee.billing_rate,
                    cost_rate=employee.cost_rate,
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
        HourlyEstimate или FixedPriceEstimate — по модели ценообразования проекта.

    Raises:
        FixedPriceError: Фиксированную цену нельзя рассчитать при заданной
            марже и налоговом режиме.
    """
    firm, project = scenario.firm, scenario.project
    calc_country = firm.jurisdiction.calc_country(project.calc_country)
    profile = firm.jurisdiction.tax_profile(calc_country)

    stages, unresolved = resolve_stages(firm, project)
    lines = tuple(line for stage in stages for line in stage.lines)
    total_hours = decimal_sum(line.hours for line in lines)
    rate = overhead_rate(firm.overheads, firm.billable_hours_per_month)
    labor = direct_labor(lines)
    overheads_alloc = allocate_overheads(rate, total_hours)
    billed = disbursements_total(project.disbursements, DisbursementKind.BILLABLE)
    own = disbursements_total(project.disbursements, DisbursementKind.OWN)
    # KNOWN-BUG L-12 → фаза 2: расходы «Прочее» (EXTRA) не входят ни в NNE, ни в цену.

    common: dict[str, Any] = {
        "project_name": project.name,
        "client": project.client,
        "description": project.description,
        "calc_country": calc_country,
        "jurisdiction_mode": firm.jurisdiction.mode,
        "currency": calc_country.currency,
        "blended_rate": blended_rate(lines),
        "leverage": leverage(lines),
        "total_hours": total_hours,
        "overhead_rate": rate,
        "lines": lines,
        "disbursements": project.disbursements,
        "unresolved_assignments": tuple(unresolved),
        "direct_labor": labor,
        "overheads_alloc": overheads_alloc,
        "disbursements_billed": billed,
        "disbursements_own": own,
    }

    pricing = project.pricing
    match pricing:
        case HourlyPricing():
            gross = gross_revenue(lines)
            tax = calculate_tax(
                profile,
                gross + billed,
                expenses=labor + overheads_alloc + own,
                disbursements_billed=billed,
            )
            return HourlyEstimate(
                **common,
                regime_label=tax.regime_label,
                tax=tax,
                nne=net_net_earnings(gross, labor, overheads_alloc, own, tax.total_tax),
                gross_revenue=gross,
            )
        case FixedPricing():
            total_costs = labor + overheads_alloc + own
            quote = quote_fixed_price(profile, total_costs, pricing.target_margin)
            return FixedPriceEstimate(
                **common,
                regime_label=quote.tax.regime_label,
                tax=quote.tax,
                nne=quote.nne,
                fixed_price=quote.fixed_price,
                total_costs=total_costs,
                actual_margin=quote.actual_margin,
                target_margin=pricing.target_margin,
                stages=stage_results(
                    stages,
                    fixed_price=quote.fixed_price,
                    target_margin=pricing.target_margin,
                    overheads_alloc=overheads_alloc,
                ),
            )
        case _:
            assert_never(pricing)
