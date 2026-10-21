"""Себестоимость биллируемого часа, ставка накладных и показатели команды."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rentab_core.analysis.team import blended_rate, leverage
from rentab_core.costing import (
    billable_capacity,
    employee_cost,
    monthly_amount,
    overhead_rate,
    total_overheads_monthly,
)
from rentab_core.errors import CurrencyConversionError
from rentab_core.models import (
    Employee,
    ExchangeRates,
    Firm,
    MonthlyCost,
    Overhead,
    Period,
    Role,
    Salary,
)
from rentab_core.money import Currency
from tests.core.factories import d, line


def test_cost_rate_is_per_billable_hour() -> None:
    # L-01: c = W / (H × u). Калькулятор v0.3 давал W / H = 260 000 / 168.
    employee = Employee(
        name="Иванов",
        role=Role.PARTNER,
        compensation=Salary(gross_salary=d(200_000), employer_contribution_rate=d("0.30")),
        contract_hours_per_month=d(168),
        target_utilization=d("0.65"),
    )
    cost = employee_cost(employee)
    assert cost.monthly_cost == d(260_000)
    assert cost.contract_hours == d(168)
    assert cost.billable_capacity == d("109.2")
    assert cost.contract_cost_rate == d(260_000) / d(168)
    assert cost.cost_rate == d(260_000) / d("109.2")
    assert cost.underutilization_rate == cost.cost_rate - cost.contract_cost_rate


def test_methodology_partner_example() -> None:
    # docs/methodology.md, 10.1: W = 400 000, H = 160, u = 62,5%.
    partner = Employee(
        name="Партнёр",
        role=Role.PARTNER,
        compensation=MonthlyCost(amount=d(400_000)),
        contract_hours_per_month=d(160),
        target_utilization=d("0.625"),
    )
    cost = employee_cost(partner)
    assert cost.billable_capacity == d(100)
    assert cost.contract_cost_rate == d(2_500)
    assert cost.cost_rate == d(4_000)
    assert cost.underutilization_rate == d(1_500)


@given(
    monthly=st.integers(min_value=0, max_value=10_000_000),
    hours=st.integers(min_value=1, max_value=300),
    utilization_pct=st.integers(min_value=1, max_value=100),
)
def test_underutilization_share_is_one_minus_u(
    monthly: int, hours: int, utilization_pct: int
) -> None:
    u = Decimal(utilization_pct) / 100
    cost = employee_cost(
        Employee(
            name="x",
            role=Role.ASSOCIATE,
            compensation=MonthlyCost(amount=Decimal(monthly)),
            contract_hours_per_month=Decimal(hours),
            target_utilization=u,
        )
    )
    # c − ĉ = c × (1 − u); при u = 1 недозагрузки нет.
    assert abs(cost.underutilization_rate - cost.cost_rate * (1 - u)) < Decimal("1e-18")
    assert cost.cost_rate >= cost.contract_cost_rate


@pytest.mark.parametrize(
    ("period", "expected"),
    [(Period.MONTHLY, "12000"), (Period.ANNUAL, "1000"), (Period.ONE_TIME, "0")],
)
def test_monthly_amount(period: Period, expected: str) -> None:
    assert monthly_amount(Overhead(name="x", amount=d(12_000), period=period)) == Decimal(expected)


def _member(capacity: int, u: str = "0.5") -> Employee:
    return Employee(
        name="x",
        role=Role.ASSOCIATE,
        compensation=MonthlyCost(amount=d(0)),
        contract_hours_per_month=d(capacity) / d(u),
        target_utilization=d(u),
    )


def test_overhead_rate_uses_team_capacity() -> None:
    # ρ = Σ OH / Σ H·u: 80 000 / (100 + 60) = 500.
    firm = Firm(
        team=(_member(100), _member(60, "0.75")),
        overheads=(Overhead(name="Аренда", amount=d(80_000), period=Period.MONTHLY),),
    )
    assert billable_capacity(firm.team) == d(160)
    assert overhead_rate(firm, Currency.RUB) == d(500)
    assert overhead_rate(firm.model_copy(update={"team": ()}), Currency.RUB) == 0


def test_overheads_are_converted_to_project_currency() -> None:
    # L-06: 120 000 ₽ + 300 000 ₸ × 0,18 + 100 $ × 90 = 183 000 ₽.
    overheads = [
        Overhead(name="Москва", amount=d(120_000), period=Period.MONTHLY),
        Overhead(name="Алматы", amount=d(300_000), currency=Currency.KZT, period=Period.MONTHLY),
        Overhead(name="SaaS", amount=d(1_200), currency=Currency.USD, period=Period.ANNUAL),
    ]
    rates = ExchangeRates(rub_per_kzt=d("0.18"), rub_per_usd=d(90))
    assert total_overheads_monthly(overheads, Currency.RUB, rates) == d(183_000)
    in_kzt = total_overheads_monthly(overheads, Currency.KZT, rates)
    assert in_kzt == d(183_000) / d("0.18")


def test_overheads_without_rate_raise() -> None:
    overheads = [
        Overhead(name="Алматы", amount=d(300_000), currency=Currency.KZT, period=Period.MONTHLY)
    ]
    with pytest.raises(CurrencyConversionError, match="₽ за 1 ₸"):
        total_overheads_monthly(overheads, Currency.RUB, ExchangeRates())


def test_blended_rate() -> None:
    assert blended_rate([line(10, billing=15000), line(40, billing=6000)]) == d(7800)
    assert blended_rate([]) == 0


def test_leverage_counts_senior_lawyer_as_senior() -> None:
    lines = [
        line(4, role=Role.PARTNER),
        line(6, role=Role.SENIOR),
        line(30, role=Role.ASSOCIATE),
        line(10, role=Role.INTERN),
    ]
    assert leverage(lines) == d(4)
    assert leverage([line(10, role=Role.INTERN)]) == 0


def test_line_economics() -> None:
    item = line(10, billing=5000, cost=1500, contract_cost="937.5")
    assert item.shadow_value == d(50_000)
    assert item.labor_cost == d(15_000)
    assert item.underutilization_cost == d(5_625)
