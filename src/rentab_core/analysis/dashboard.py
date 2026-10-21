"""Показатели дашборда сметы (docs/methodology.md, раздел 6).

Все относительные показатели считаются от одной базы — выручки без НДС R
(L-15); маржа этапов — полная, после накладных, собственных расходов и
налога (L-19). Интерфейс только рисует эти значения.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from rentab_core.models import CoreModel
from rentab_core.money import PERCENT, round_to
from rentab_core.results import Estimate, StageResult


class Profitability(StrEnum):
    """Итог проекта по знаку NNE."""

    PROFITABLE = "profitable"
    BREAKEVEN = "breakeven"
    LOSS = "loss"


def profitability(nne: Decimal) -> Profitability:
    """Рентабелен (NNE > 0), в точке безубыточности (= 0) или убыточен (< 0).

    NNE сравнивается с нулём с точностью до копейки: цена безубыточности,
    найденная делением, может дать NNE порядка ±1e-24.
    """
    nne = round_to(nne)
    if nne > 0:
        return Profitability.PROFITABLE
    if nne == 0:
        return Profitability.BREAKEVEN
    return Profitability.LOSS


class PriceStructure(CoreModel):
    """Слои выручки без НДС: в сумме дают R."""

    direct_labor: Decimal
    overheads: Decimal
    own: Decimal
    tax: Decimal
    profit: Decimal


class Dashboard(CoreModel):
    """Показатели сметы для дашборда.

    Attributes:
        margin_pct: NNE / R, %; None, если выручки нет.
        target_margin_pct: Целевая маржа, %.
        effective_tax_rate_pct: Налог / R, %; None, если выручки нет.
        underutilization_share_pct: Доля стоимости недозагрузки в трудозатратах, %;
            None, если трудозатрат нет.
        price_structure: Слои выручки.
        stages: Экономика этапов со светофором.
        profitability: Итог проекта.
    """

    margin_pct: Decimal | None
    target_margin_pct: Decimal
    effective_tax_rate_pct: Decimal | None
    underutilization_share_pct: Decimal | None
    price_structure: PriceStructure
    stages: tuple[StageResult, ...]
    profitability: Profitability


def dashboard(estimate: Estimate) -> Dashboard:
    """Производные показатели сметы для дашборда."""
    revenue = estimate.revenue
    has_revenue = revenue > 0
    labor = estimate.direct_labor
    return Dashboard(
        margin_pct=estimate.nne / revenue * PERCENT if has_revenue else None,
        target_margin_pct=estimate.target_margin * PERCENT,
        effective_tax_rate_pct=estimate.tax.tax / revenue * PERCENT if has_revenue else None,
        underutilization_share_pct=(
            estimate.underutilization_cost / labor * PERCENT if labor > 0 else None
        ),
        price_structure=PriceStructure(
            direct_labor=labor,
            overheads=estimate.overheads_alloc,
            own=estimate.disbursements_own,
            tax=estimate.tax.tax,
            profit=estimate.nne,
        ),
        stages=estimate.stages,
        profitability=profitability(estimate.nne),
    )
