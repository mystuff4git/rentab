"""Экономика этапов и светофор (docs/methodology.md, раздел 8).

    V_s   = Σ_{a∈s} b × h                      теневая стоимость этапа
    R_s   = R × V_s / Σ V                      выручка — по теневой стоимости (L-04)
    C_s   = Labor_s + ρ × H_s + Own_s          собственные расходы — привязанные к этапу
                                               и доля нераспределённых по тому же ключу
    Tax_s — та же логика, что у налога проекта (с взносами S_s и
            вознаграждением из прибыли N_s этапа); Σ Tax_s = Tax
    m_s   = (R_s − C_s − Tax_s) / R_s          маржа после налога

Если ставки для клиента не заданы ни у кого (Σ V = 0), ключ — трудозатраты,
если нет и их — часы.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from rentab_core.models import CoreModel
from rentab_core.money import ZERO, decimal_sum, round_to
from rentab_core.results import EstimateLine, StageFlag, StageResult, TaxBase, TaxResult

# Пороги светофора: этап убыточен, если его маржа не больше RED_MARGIN_THRESHOLD;
# в норме — если маржа не ниже целевой.
RED_MARGIN_THRESHOLD = ZERO
# Точность сравнения маржи с порогами: 0,0001 п. п. Деление в Decimal оставляет
# погрешность порядка 1e-28, и без округления этап с маржой, ровно равной цели,
# мог бы получить жёлтый цвет вместо зелёного.
MARGIN_COMPARISON_QUANTUM = Decimal("0.000001")


class StageLines(CoreModel):
    """Этап и его строки сметы (исполнители с часами)."""

    name: str
    lines: tuple[EstimateLine, ...]

    @property
    def hours(self) -> Decimal:
        """Часы этапа H_s."""
        return decimal_sum(line.hours for line in self.lines)

    @property
    def labor_cost(self) -> Decimal:
        """Трудозатраты этапа."""
        return decimal_sum(line.labor_cost for line in self.lines)

    @property
    def shadow_value(self) -> Decimal:
        """Теневая стоимость этапа V_s."""
        return decimal_sum(line.shadow_value for line in self.lines)

    @property
    def contributions(self) -> Decimal:
        """Взносы на часах этапа S_s."""
        return decimal_sum(line.contributions for line in self.lines)

    @property
    def nondeductible_cost(self) -> Decimal:
        """Вознаграждение из прибыли на часах этапа N_s."""
        return decimal_sum(line.nondeductible_cost for line in self.lines)


def classify(margin: Decimal, target_margin: Decimal) -> StageFlag:
    """Цвет этапа: red — маржа ≤ 0, green — маржа ≥ цели, иначе yellow.

    Маржа сравнивается с точностью `MARGIN_COMPARISON_QUANTUM`.
    """
    margin = round_to(margin, MARGIN_COMPARISON_QUANTUM)
    if margin <= RED_MARGIN_THRESHOLD:
        return StageFlag.RED
    if margin >= target_margin:
        return StageFlag.GREEN
    return StageFlag.YELLOW


def allocation_shares(stages: Sequence[StageLines]) -> list[Decimal]:
    """Доли этапов в выручке: по теневой стоимости, иначе по трудозатратам, иначе по часам.

    Returns:
        Доли в том же порядке, в сумме 1; все нули, если у этапов нет ни
        ставок, ни затрат, ни часов.
    """
    for key in (
        [stage.shadow_value for stage in stages],
        [stage.labor_cost for stage in stages],
        [stage.hours for stage in stages],
    ):
        total = decimal_sum(key)
        if total > 0:
            return [value / total for value in key]
    return [ZERO for _ in stages]


def _stage_tax(tax: TaxResult, stage: StageLines, revenue: Decimal, costs: Decimal) -> Decimal:
    """Доля налога проекта, приходящаяся на этап."""
    terms = tax.terms
    if terms.base == TaxBase.INCOME and not tax.deduction_capped:
        contributions = stage.contributions if tax.contributions > 0 else ZERO
        return terms.rate * revenue - contributions
    if tax.profit_tax_applied:
        # Убыточный этап получает отрицательный налог: он уменьшает налог проекта.
        return terms.rate * (revenue - costs + stage.nondeductible_cost)
    # Предел уменьшения на взносы, минимальный или нулевой налог — пропорционально выручке.
    return tax.tax * revenue / tax.revenue if tax.revenue > 0 else ZERO


def stage_results(
    stages: Sequence[StageLines],
    *,
    revenue: Decimal,
    overhead_rate: Decimal,
    own_by_stage: Mapping[str, Decimal],
    unassigned_own: Decimal,
    tax: TaxResult,
    target_margin: Decimal,
) -> tuple[StageResult, ...]:
    """Выручка, издержки, налог, маржа и цвет каждого этапа.

    Args:
        stages: Этапы проекта в порядке выполнения.
        revenue: Выручка проекта без НДС R.
        overhead_rate: Ставка накладных ρ.
        own_by_stage: Собственные расходы, привязанные к этапам, по названию этапа.
        unassigned_own: Собственные расходы без привязки — делятся по ключу выручки.
        tax: Налог проекта.
        target_margin: Целевая маржа m*.

    Returns:
        По одному результату на этап, в том же порядке. Суммы R_s, C_s и
        Tax_s по этапам равны R, C и Tax проекта, если на проекте есть этапы.
    """
    results: list[StageResult] = []
    for stage, share in zip(stages, allocation_shares(stages), strict=True):
        stage_revenue = revenue * share
        overheads = overhead_rate * stage.hours
        own = own_by_stage.get(stage.name, ZERO) + unassigned_own * share
        costs = stage.labor_cost + overheads + own
        stage_tax = _stage_tax(tax, stage, stage_revenue, costs)
        nne = stage_revenue - costs - stage_tax
        margin = nne / stage_revenue if stage_revenue > 0 else ZERO
        results.append(
            StageResult(
                stage_name=stage.name,
                hours=stage.hours,
                shadow_value=stage.shadow_value,
                revenue=stage_revenue,
                labor=stage.labor_cost,
                overheads=overheads,
                own=own,
                costs=costs,
                tax=stage_tax,
                nne=nne,
                margin=margin,
                flag=classify(margin, target_margin),
            )
        )
    return tuple(results)
