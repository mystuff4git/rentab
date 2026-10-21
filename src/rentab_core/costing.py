"""Себестоимость: стоимость часа сотрудника, ставка накладных, издержки проекта.

Формулы — docs/methodology.md, раздел 3:

    K_i = H_i × u_i                  биллируемая ёмкость сотрудника
    c_i = W_i / K_i                  себестоимость биллируемого часа (L-01)
    ĉ_i = W_i / H_i                  себестоимость контрактного часа
    ρ   = Σ OH(мес) / Σ_i K_i        ставка накладных на биллируемый час
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import assert_never

from rentab_core.fx import convert
from rentab_core.models import CoreModel, Employee, ExchangeRates, Firm, Overhead, Period
from rentab_core.money import ZERO, Currency, decimal_sum

MONTHS_PER_YEAR = Decimal(12)


class EmployeeCost(CoreModel):
    """Стоимость сотрудника и его часа в валюте сотрудника.

    Attributes:
        monthly_cost: W — полная стоимость в месяц.
        contract_hours: H — контрактные часы в месяц.
        billable_capacity: K = H × u — биллируемые часы в месяц.
        contract_cost_rate: ĉ = W / H.
        cost_rate: c = W / K — себестоимость биллируемого часа.
        underutilization_rate: c − ĉ — стоимость недозагрузки на час.
    """

    monthly_cost: Decimal
    contract_hours: Decimal
    billable_capacity: Decimal
    contract_cost_rate: Decimal
    cost_rate: Decimal

    @property
    def underutilization_rate(self) -> Decimal:
        """Стоимость недозагрузки на биллируемый час: c − ĉ."""
        return self.cost_rate - self.contract_cost_rate


def employee_cost(employee: Employee) -> EmployeeCost:
    """Себестоимость контрактного и биллируемого часа сотрудника."""
    monthly = employee.compensation.monthly_cost
    hours = employee.contract_hours_per_month
    capacity = hours * employee.target_utilization
    return EmployeeCost(
        monthly_cost=monthly,
        contract_hours=hours,
        billable_capacity=capacity,
        contract_cost_rate=monthly / hours,
        cost_rate=monthly / capacity,
    )


def billable_capacity(team: Iterable[Employee]) -> Decimal:
    """Биллируемая ёмкость фирмы в месяц: Σ H_i × u_i."""
    return decimal_sum(
        employee.contract_hours_per_month * employee.target_utilization for employee in team
    )


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


def total_overheads_monthly(
    overheads: Iterable[Overhead], currency: Currency, rates: ExchangeRates
) -> Decimal:
    """Накладные фирмы за месяц в валюте currency.

    Raises:
        CurrencyConversionError: Накладной расход в валюте, для которой нет курса.
    """
    return decimal_sum(
        convert(monthly_amount(item), item.currency, currency, rates) for item in overheads
    )


def overhead_rate(firm: Firm, currency: Currency) -> Decimal:
    """Ставка накладных ρ на биллируемый час в валюте currency; 0, если ёмкости нет.

    Raises:
        CurrencyConversionError: Накладной расход в валюте, для которой нет курса.
    """
    capacity = billable_capacity(firm.team)
    if capacity <= 0:
        return ZERO
    return total_overheads_monthly(firm.overheads, currency, firm.exchange_rates) / capacity
