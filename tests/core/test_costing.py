"""Себестоимость и показатели команды."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.analysis.team import blended_rate, leverage
from rentab_core.costing import (
    allocate_overheads,
    direct_labor,
    disbursements_total,
    employee_full_cost,
    monthly_amount,
    overhead_rate,
    total_overheads_monthly,
)
from rentab_core.models import DisbursementKind, Overhead, Payroll, Period, Role
from rentab_core.money import Currency
from tests.core.factories import d, disbursement, line


def test_employee_full_cost() -> None:
    # KNOWN-BUG L-01 → фаза 2: делится на контрактные часы, а не на биллируемые.
    cost = employee_full_cost(
        Payroll(
            gross_salary=d(200_000),
            contract_hours_per_month=d(168),
            employer_contribution_rate=d("0.30"),
        )
    )
    assert cost.employer_taxes == d(60_000)
    assert cost.total_monthly_cost == d(260_000)
    assert cost.cost_rate_per_hour == d(260_000) / d(168)


@pytest.mark.parametrize(
    ("period", "expected"),
    [(Period.MONTHLY, "12000"), (Period.ANNUAL, "1000"), (Period.ONE_TIME, "0")],
)
def test_monthly_amount(period: Period, expected: str) -> None:
    assert monthly_amount(Overhead(name="x", amount=d(12_000), period=period)) == Decimal(expected)


def test_overheads_sum_currencies_without_conversion() -> None:
    # KNOWN-BUG L-06 → фаза 2.
    overheads = [
        Overhead(name="Москва", amount=d(100_000), period=Period.MONTHLY),
        Overhead(name="Алматы", amount=d(500_000), currency=Currency.KZT, period=Period.MONTHLY),
        Overhead(name="SaaS", amount=d(1_000), currency=Currency.USD, period=Period.MONTHLY),
    ]
    assert total_overheads_monthly(overheads) == d(601_000)
    assert overhead_rate(overheads, d(100)) == d(6_010)


def test_overhead_rate_and_allocation_by_hours() -> None:
    overheads = [Overhead(name="Аренда", amount=d(80_000), period=Period.MONTHLY)]
    rate = overhead_rate(overheads, d(160))
    assert rate == d(500)
    assert overhead_rate(overheads, d(0)) == 0
    assert allocate_overheads(rate, d(20)) == d(10_000)
    assert allocate_overheads(rate, d(0)) == 0
    assert allocate_overheads(rate, d(-5)) == 0


def test_direct_labor_and_disbursement_totals() -> None:
    assert direct_labor([line(10, cost=8000), line(40, cost=3500)]) == d(220_000)
    items = [
        disbursement(DisbursementKind.BILLABLE, 38_500),
        disbursement(DisbursementKind.BILLABLE, 1_500),
        disbursement(DisbursementKind.OWN, 3_000),
        disbursement(DisbursementKind.EXTRA, 7_000),
    ]
    assert disbursements_total(items, DisbursementKind.BILLABLE) == d(40_000)
    assert disbursements_total(items, DisbursementKind.OWN) == d(3_000)
    assert disbursements_total(items, DisbursementKind.EXTRA) == d(7_000)


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
