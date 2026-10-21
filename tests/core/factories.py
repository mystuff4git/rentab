"""Построители входных данных для unit-тестов ядра."""

from __future__ import annotations

from decimal import Decimal

from rentab_core.models import (
    Assignment,
    Disbursement,
    DisbursementKind,
    Employee,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    MonthlyCost,
    Overhead,
    Period,
    PricingModel,
    Project,
    Role,
    Scenario,
    Stage,
)
from rentab_core.results import EstimateLine


def d(value: str | int) -> Decimal:
    """Короткая запись Decimal в тестах."""
    return Decimal(value)


def employee(
    name: str,
    role: Role,
    *,
    cost_rate: str | int,
    billing_rate: str | int = 0,
    capacity: str | int = 80,
) -> Employee:
    """Сотрудник с заданной себестоимостью биллируемого часа.

    Контрактные часы — 160, утилизация подбирается так, чтобы ёмкость была
    `capacity` ч/мес; W = cost_rate × capacity.
    """
    hours = d(160)
    return Employee(
        name=name,
        role=role,
        billing_rate=d(billing_rate),
        compensation=MonthlyCost(amount=d(cost_rate) * d(capacity)),
        contract_hours_per_month=hours,
        target_utilization=d(capacity) / hours,
    )


# Команда контрольного сценария v0.3 (legacy/rentab_v03/test_scenario.py), она
# же нижняя таблица слайда 3: себестоимость биллируемого часа 8 000 и 3 500 ₽,
# ёмкость фирмы 2 × 80 = 160 ч, аренда 80 000 ₽ → ставка накладных 500 ₽/ч.
PARTNER = employee("Иванов", Role.PARTNER, cost_rate=8000, billing_rate=15000)
ASSOCIATE = employee("Петрова", Role.ASSOCIATE, cost_rate=3500, billing_rate=6000)
RENT = Overhead(name="Аренда + ПО", amount=d(80000), period=Period.MONTHLY)


def line(
    hours: str | int,
    *,
    billing: str | int = 0,
    cost: str | int = 0,
    contract_cost: str | int | None = None,
    role: Role = Role.ASSOCIATE,
    stage: str = "Этап",
) -> EstimateLine:
    """Строка сметы с заданными ставками и часами."""
    return EstimateLine(
        stage_name=stage,
        employee_name="x",
        role=role,
        billing_rate=d(billing),
        cost_rate=d(cost),
        contract_cost_rate=d(cost if contract_cost is None else contract_cost),
        hours=d(hours),
    )


def stage(name: str, *assignments: tuple[str, str | int]) -> Stage:
    """Этап с назначениями (сотрудник, часы)."""
    return Stage(
        name=name,
        assignments=tuple(Assignment(employee=who, hours=d(hours)) for who, hours in assignments),
    )


def disbursement(
    kind: DisbursementKind, amount: str | int, name: str = "Расход", stage: str | None = None
) -> Disbursement:
    """Прямой расход проекта в рублях."""
    return Disbursement(name=name, kind=kind, amount=d(amount), stage=stage)


def basic_scenario(
    pricing: PricingModel | None = None,
    *,
    jurisdiction: Jurisdiction | None = None,
    stages: tuple[Stage, ...] | None = None,
    disbursements: tuple[Disbursement, ...] = (),
    target_margin: str = "0.30",
) -> Scenario:
    """Контрольный сценарий v0.3: партнёр 5 ч, юрист 15 ч, ставка накладных 500 ₽/ч."""
    return Scenario(
        firm=Firm(
            jurisdiction=jurisdiction or Jurisdiction(),
            team=(PARTNER, ASSOCIATE),
            overheads=(RENT,),
        ),
        project=Project(
            name="Контрольный проект",
            pricing=pricing or HourlyPricing(),
            target_margin=d(target_margin),
            stages=stages
            or (stage("Анализ", ("Иванов", 5)), stage("Подготовка документов", ("Петрова", 15))),
            disbursements=disbursements,
        ),
    )


def fixed(price: str | int | None = None) -> FixedPricing:
    """Фиксированная цена: «издержки + маржа» или согласованная."""
    return FixedPricing(price=None if price is None else d(price))
