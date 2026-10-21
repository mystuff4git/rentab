"""Светофор этапов фикс-прайса (перенос `FixedPriceCalculator.get_stage_flags` v0.3).

KNOWN-BUG L-04 → фаза 2:
- выручка и накладные делятся между этапами пропорционально часам, а не
  теневой стоимости Σ ставка × часы;
- маржа этапа считается до налога, а сравнивается с целевой маржой после налога;
- собственные расходы проекта по этапам не распределяются.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from rentab_core.models import CoreModel
from rentab_core.money import ZERO, decimal_sum
from rentab_core.results import EstimateLine, StageFlag, StageResult

# Этап убыточен, если его маржа не больше этой границы.
RED_MARGIN_THRESHOLD = ZERO


class StageLines(CoreModel):
    """Этап и его строки сметы (исполнители с часами)."""

    name: str
    lines: tuple[EstimateLine, ...]

    @property
    def hours(self) -> Decimal:
        """Часы этапа."""
        return decimal_sum(line.hours for line in self.lines)

    @property
    def labor_cost(self) -> Decimal:
        """Трудозатраты этапа."""
        return decimal_sum(line.labor_cost for line in self.lines)


def classify(margin: Decimal, target_margin: Decimal) -> StageFlag:
    """Цвет этапа: red — маржа ≤ 0, green — маржа ≥ цели, иначе yellow."""
    if margin <= RED_MARGIN_THRESHOLD:
        return StageFlag.RED
    if margin >= target_margin:
        return StageFlag.GREEN
    return StageFlag.YELLOW


def stage_results(
    stages: Sequence[StageLines],
    *,
    fixed_price: Decimal,
    target_margin: Decimal,
    overheads_alloc: Decimal,
) -> tuple[StageResult, ...]:
    """Маржа и цвет каждого этапа.

    Args:
        stages: Этапы в порядке проекта; одноимённые этапы считаются раздельно.
        fixed_price: Фиксированная цена проекта.
        target_margin: Целевая маржа, доля.
        overheads_alloc: Накладные, отнесённые на проект.

    Returns:
        По одному результату на этап, в том же порядке.
    """
    total_hours = decimal_sum(stage.hours for stage in stages)
    results: list[StageResult] = []
    for stage in stages:
        hour_share = stage.hours / total_hours if total_hours > 0 else ZERO
        revenue_share = fixed_price * hour_share
        costs = stage.labor_cost + overheads_alloc * hour_share
        margin = (revenue_share - costs) / revenue_share if revenue_share > 0 else ZERO
        results.append(
            StageResult(
                stage_name=stage.name,
                costs=costs,
                revenue_share=revenue_share,
                margin=margin,
                flag=classify(margin, target_margin),
            )
        )
    return tuple(results)
