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


# Команда контрольного сценария v0.3 (legacy/rentab_v03/test_scenario.py).
PARTNER = Employee(name="Иванов", role=Role.PARTNER, billing_rate=d(15000), cost_rate=d(8000))
ASSOCIATE = Employee(name="Петрова", role=Role.ASSOCIATE, billing_rate=d(6000), cost_rate=d(3500))
RENT = Overhead(name="Аренда + ПО", amount=d(80000), period=Period.MONTHLY)


def line(
    hours: str | int,
    *,
    billing: str | int = 0,
    cost: str | int = 0,
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
        hours=d(hours),
    )


def stage(name: str, employee: str, hours: str | int) -> Stage:
    """Этап с одним исполнителем, как в интерфейсе v0.3."""
    return Stage(name=name, assignments=(Assignment(employee=employee, hours=d(hours)),))


def disbursement(kind: DisbursementKind, amount: str | int, name: str = "Расход") -> Disbursement:
    """Прямой расход проекта в рублях."""
    return Disbursement(name=name, kind=kind, amount=d(amount))


def basic_scenario(
    pricing: PricingModel | None = None,
    *,
    jurisdiction: Jurisdiction | None = None,
    stages: tuple[Stage, ...] | None = None,
    disbursements: tuple[Disbursement, ...] = (),
) -> Scenario:
    """Контрольный сценарий v0.3: партнёр 5 ч, юрист 15 ч, аренда 80 000 ₽ при 160 ч."""
    return Scenario(
        firm=Firm(
            jurisdiction=jurisdiction or Jurisdiction(),
            team=(PARTNER, ASSOCIATE),
            overheads=(RENT,),
            billable_hours_per_month=d(160),
        ),
        project=Project(
            name="Контрольный проект",
            pricing=pricing or HourlyPricing(),
            stages=stages
            or (stage("Анализ", "Иванов", 5), stage("Подготовка документов", "Петрова", 15)),
            disbursements=disbursements,
        ),
    )


def fixed(margin: str = "0.30") -> FixedPricing:
    """Фикс-прайс с целевой маржой."""
    return FixedPricing(target_margin=d(margin))
