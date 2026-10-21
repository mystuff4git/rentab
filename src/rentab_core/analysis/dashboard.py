"""Показатели страницы «Дашборд» (перенос расчётной части `pages/03_Дашборд.py` v0.3).

Интерфейс только рисует эти значения; пересчёт валюты для показа —
`money.display_conversion_factor`.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, assert_never

from pydantic import Field

from rentab_core.models import CoreModel, Role
from rentab_core.money import PERCENT, ZERO, decimal_sum
from rentab_core.results import Estimate, FixedPriceEstimate, HourlyEstimate, StageFlag


class Profitability(StrEnum):
    """Итог проекта по знаку NNE."""

    PROFITABLE = "profitable"
    BREAKEVEN = "breakeven"
    LOSS = "loss"


def profitability(nne: Decimal) -> Profitability:
    """Рентабелен (NNE > 0), в точке безубыточности (= 0) или убыточен (< 0)."""
    if nne > 0:
        return Profitability.PROFITABLE
    if nne == 0:
        return Profitability.BREAKEVEN
    return Profitability.LOSS


class PriceStructure(CoreModel):
    """Слои фиксированной цены: в сумме дают цену."""

    direct_labor: Decimal
    indirect: Decimal  # накладные + собственные расходы
    tax: Decimal
    profit: Decimal


class FixedStageRow(CoreModel):
    """Строка таблицы «Анализ этапов»."""

    stage_name: str
    hours: Decimal
    costs: Decimal
    revenue_share: Decimal
    margin_pct: Decimal
    flag: StageFlag


class FixedDashboard(CoreModel):
    """Дашборд фикс-прайса."""

    pricing: Literal["fixed"] = "fixed"
    target_margin_pct: Decimal
    actual_margin_pct: Decimal
    price_structure: PriceStructure
    stage_rows: tuple[FixedStageRow, ...]
    profitability: Profitability


class HourlyStageRow(CoreModel):
    """Строка таблицы этапов почасовой модели."""

    stage_name: str
    employee_name: str
    role: Role
    hours: Decimal
    billing_rate: Decimal
    revenue: Decimal
    margin_pct: Decimal


class StageTotals(CoreModel):
    """Итоговая строка таблицы этапов."""

    hours: Decimal
    revenue: Decimal
    margin_pct: Decimal


class HourlyDashboard(CoreModel):
    """Дашборд почасовой модели."""

    pricing: Literal["hourly"] = "hourly"
    nne_margin_pct: Decimal | None  # None, если выручки нет
    effective_tax_rate_pct: Decimal
    stage_rows: tuple[HourlyStageRow, ...]
    stage_totals: StageTotals
    profitability: Profitability


Dashboard = Annotated[HourlyDashboard | FixedDashboard, Field(discriminator="pricing")]


def _labor_margin_pct(revenue: Decimal, labor_cost: Decimal) -> Decimal:
    """(выручка − трудозатраты) / выручка, %; 0, если выручки нет."""
    return (revenue - labor_cost) / revenue * PERCENT if revenue > 0 else ZERO


def _fixed_dashboard(estimate: FixedPriceEstimate) -> FixedDashboard:
    # KNOWN-BUG L-05 → фаза 2: часы этапа берутся суммой по НАЗВАНИЮ, поэтому
    # одноимённые этапы получают общие часы.
    hours_by_name: dict[str, Decimal] = {}
    for line in estimate.lines:
        hours_by_name[line.stage_name] = hours_by_name.get(line.stage_name, ZERO) + line.hours
    return FixedDashboard(
        target_margin_pct=estimate.target_margin * PERCENT,
        actual_margin_pct=estimate.actual_margin * PERCENT,
        price_structure=PriceStructure(
            direct_labor=estimate.direct_labor,
            indirect=estimate.overheads_alloc + estimate.disbursements_own,
            tax=estimate.tax.total_tax,
            profit=estimate.nne,
        ),
        stage_rows=tuple(
            FixedStageRow(
                stage_name=stage.stage_name,
                hours=hours_by_name.get(stage.stage_name, ZERO),
                costs=stage.costs,
                revenue_share=stage.revenue_share,
                margin_pct=stage.margin * PERCENT,
                flag=stage.flag,
            )
            for stage in estimate.stages
        ),
        profitability=profitability(estimate.nne),
    )


def _hourly_dashboard(estimate: HourlyEstimate) -> HourlyDashboard:
    gross = estimate.gross_revenue
    total_client = estimate.total_client
    # KNOWN-BUG L-15 → фаза 2: эффективная ставка налога делится на услуги +
    # пошлины, а маржа NNE — только на услуги.
    effective_rate = estimate.tax.total_tax / total_client * PERCENT if total_client > 0 else ZERO
    # KNOWN-BUG L-19 → фаза 2: маржа строки — (ставка − себестоимость) / ставка,
    # без накладных, налога и собственных расходов.
    rows = tuple(
        HourlyStageRow(
            stage_name=line.stage_name,
            employee_name=line.employee_name,
            role=line.role,
            hours=line.hours,
            billing_rate=line.billing_rate,
            revenue=line.revenue,
            margin_pct=_labor_margin_pct(line.revenue, line.labor_cost),
        )
        for line in estimate.lines
    )
    sum_revenue = decimal_sum(line.revenue for line in estimate.lines)
    sum_labor = decimal_sum(line.labor_cost for line in estimate.lines)
    return HourlyDashboard(
        nne_margin_pct=estimate.nne / gross * PERCENT if gross else None,
        effective_tax_rate_pct=effective_rate,
        stage_rows=rows,
        stage_totals=StageTotals(
            hours=decimal_sum(line.hours for line in estimate.lines),
            revenue=sum_revenue,
            margin_pct=_labor_margin_pct(sum_revenue, sum_labor),
        ),
        profitability=profitability(estimate.nne),
    )


def dashboard(estimate: Estimate) -> Dashboard:
    """Производные показатели сметы для дашборда."""
    match estimate:
        case FixedPriceEstimate():
            return _fixed_dashboard(estimate)
        case HourlyEstimate():
            return _hourly_dashboard(estimate)
        case _:
            assert_never(estimate)
