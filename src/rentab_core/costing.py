"""Себестоимость: стоимость сотрудника, трудозатраты, накладные, прямые расходы.

Перенос из v0.3: `team.calculate_employee_full_cost`, `team.total_direct_labor`
и расчёт накладных из `expenses.ExpenseManager`.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import assert_never

from rentab_core.models import CoreModel, Disbursement, DisbursementKind, Overhead, Payroll, Period
from rentab_core.money import ZERO, decimal_sum
from rentab_core.results import EstimateLine

MONTHS_PER_YEAR = Decimal(12)


class EmployeeCost(CoreModel):
    """Полная стоимость сотрудника в месяц и стоимость его часа."""

    gross_salary: Decimal
    employer_taxes: Decimal
    total_monthly_cost: Decimal
    cost_rate_per_hour: Decimal


def employee_full_cost(payroll: Payroll) -> EmployeeCost:
    """Калькулятор ФОТ: полная стоимость сотрудника и стоимость часа.

    employer_taxes = оклад × ставка взносов;
    total_monthly_cost = оклад + взносы;
    cost_rate_per_hour = total_monthly_cost / контрактные часы.

    KNOWN-BUG L-01 → фаза 2: знаменатель — контрактные, а не биллируемые часы,
    поэтому стоимость небиллируемого времени не возмещается.
    """
    employer_taxes = payroll.gross_salary * payroll.employer_contribution_rate
    total = payroll.gross_salary + employer_taxes
    return EmployeeCost(
        gross_salary=payroll.gross_salary,
        employer_taxes=employer_taxes,
        total_monthly_cost=total,
        cost_rate_per_hour=total / payroll.contract_hours_per_month,
    )


def direct_labor(lines: Iterable[EstimateLine]) -> Decimal:
    """Прямые трудозатраты: Σ себестоимость часа × часы."""
    return decimal_sum(line.labor_cost for line in lines)


def monthly_amount(overhead: Overhead) -> Decimal:
    """Накладной расход в пересчёте на месяц: годовой / 12, разовый — 0.

    Разовые расходы не входят в ежемесячные накладные.
    """
    match overhead.period:
        case Period.MONTHLY:
            return overhead.amount
        case Period.ANNUAL:
            return overhead.amount / MONTHS_PER_YEAR
        case Period.ONE_TIME:
            return ZERO
        case _:
            assert_never(overhead.period)


def total_overheads_monthly(overheads: Iterable[Overhead]) -> Decimal:
    """Накладные фирмы за месяц.

    KNOWN-BUG L-06 → фаза 2: суммы в RUB, KZT и USD складываются без конвертации.
    """
    return decimal_sum(monthly_amount(overhead) for overhead in overheads)


def overhead_rate(overheads: Iterable[Overhead], billable_hours_per_month: Decimal) -> Decimal:
    """Ставка накладных на биллируемый час: накладные за месяц / биллируемые часы.

    Возвращает 0, если часов нет.
    """
    if billable_hours_per_month <= 0:
        return ZERO
    return total_overheads_monthly(overheads) / billable_hours_per_month


def allocate_overheads(rate: Decimal, hours: Decimal) -> Decimal:
    """Накладные, относимые на проект: ставка накладных × часы проекта.

    В v0.3 метод назывался `get_overheads_allocated(direct_costs)`, хотя
    получал часы (L-07). Возвращает 0 при неположительных часах.
    """
    if hours <= 0:
        return ZERO
    return rate * hours


def disbursements_total(disbursements: Iterable[Disbursement], kind: DisbursementKind) -> Decimal:
    """Сумма прямых расходов проекта одного типа.

    KNOWN-BUG L-06 → фаза 2: валюты складываются без конвертации.
    """
    return decimal_sum(item.amount for item in disbursements if item.kind == kind)
