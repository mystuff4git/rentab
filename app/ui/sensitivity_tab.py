"""Вкладка «Чувствительность» шага «Результат» (docs/methodology.md, разделы 16–17).

Запас часов до безубыточности при зафиксированной цене (микс проекта и каждый
сотрудник), торнадо ±X% и ползунки «что если». Проект не меняется: ползунки
только пересчитывают смету; их значения у каждого проекта свои и живут в
сессии (в файл не попадают). Вся математика — в
`rentab_core.analysis.sensitivity`.
"""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from rentab_core.analysis.sensitivity import (
    BreakEven,
    BreakEvenStatus,
    Factor,
    PriceRule,
    TornadoBar,
    break_even,
    foreign_currencies,
    is_cost_plus,
    price_rule,
    tornado,
    what_if,
)
from rentab_core.models import CappedPricing, Scenario
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, Currency
from rentab_core.results import Estimate
from ui import charts, formulas, state
from ui.analytics import compute
from ui.components import Cell, Kpi, Table, kpi_grid, render_table
from ui.labels import (
    DASH,
    FACTOR_LABELS,
    TAX_BRANCH_LABELS,
    TORNADO_NOTE_LABELS,
    employee_title,
    hours_label,
    metric_delta,
    metric_points,
    money,
    money_rate,
    pct_label,
    signed_hours,
    signed_money,
    signed_pct,
)

# Торнадо: отклонение X, % (границы и шаг ползунка).
TORNADO_MIN_PCT = 5
TORNADO_MAX_PCT = 50
TORNADO_STEP_PCT = 5
# Ползунки «что если», %: (ключ, фактор, минимум, максимум).
WHATIF_STEP_PCT = 5
WHATIF_SLIDERS: tuple[tuple[str, Factor, int, int], ...] = (
    (state.WHATIF_HOURS, Factor.HOURS, -50, 200),
    (state.WHATIF_BILLING_RATES, Factor.BILLING_RATES, -50, 50),
    (state.WHATIF_UTILIZATION, Factor.UTILIZATION, -50, 50),
    (state.WHATIF_OVERHEADS, Factor.OVERHEADS, -50, 100),
)
WHATIF_FX_RANGE = (-50, 50)
WHATIF_PRICE_RANGE = (-50, 50)


def direction_label(row: BreakEven) -> str:
    """Микс проекта или сотрудник с ролью."""
    if row.employee is None:
        return "Микс проекта"
    assert row.role is not None
    return employee_title(row.employee, row.role)


def explanation(row: BreakEven, sym: str) -> str:
    """Что значит итог строки (методология, раздел 16.5)."""
    match row.status:
        case BreakEvenStatus.HEADROOM:
            assert row.branch is not None
            text = f"в точке — {TAX_BRANCH_LABELS[row.branch]}"
            return text + (", выручка упирается в потолок" if row.capped else "")
        case BreakEvenStatus.NOT_REACHED:
            return (
                f"не наступает: каждый оплаченный час даёт {signed_money(row.nne_per_hour, sym)} "
                "NNE — перерасход оплачивает клиент"
            )
        case BreakEvenStatus.LOSS:
            assert row.extra_hours is not None
            if row.extra_hours < 0:
                return f"убыток; в ноль — если часов на {hours_label(-row.extra_hours)} меньше"
            return (
                "убыток; часы прибыльны, в ноль — если оплачиваемых часов на "
                f"{hours_label(row.extra_hours)} больше"
            )
        case BreakEvenStatus.LOSS_ANY_HOURS:
            return "даже без этих часов проект убыточен: дело не в часах"
        case _:
            return "в проекте нет часов"


def headroom(row: BreakEven) -> str:
    """Запас часов строки."""
    if row.status == BreakEvenStatus.NOT_REACHED:
        return "не наступает"
    return DASH if row.extra_hours is None else signed_hours(row.extra_hours)


def break_even_table(rows: tuple[BreakEven, ...], sym: str, key: str) -> Table:
    """Таблица запаса часов: направление, запас и пояснение."""
    return Table(
        columns=[
            "Чем добираются часы",
            "Запас",
            "К плану",
            "Часов в точке",
            "Час с накладными",
            "Часов в плане",
        ],
        rows=[
            {
                "Чем добираются часы": Cell(direction_label(row), note=explanation(row, sym)),
                "Запас": headroom(row),
                "К плану": DASH if row.share_of_plan is None else signed_pct(row.share_of_plan),
                "Часов в точке": DASH if row.total_hours is None else hours_label(row.total_hours),
                "Час с накладными": money_rate(row.cost_per_hour, sym),
                "Часов в плане": hours_label(row.plan_hours),
            }
            for row in rows
        ],
        numeric=frozenset(
            {"Запас", "К плану", "Часов в точке", "Час с накладными", "Часов в плане"}
        ),
        key=key,
    )


def render_break_even(scenario: Scenario, sym: str) -> None:
    """Запас часов до безубыточности: карточка микса и таблицы направлений."""
    report = compute(scenario, lambda: break_even(scenario))
    rule = price_rule(scenario.project.pricing)
    st.markdown("#### Запас часов до безубыточности")
    mix = report.rows[0]
    kpi_grid(
        [
            Kpi(
                "headroom",
                "Запас часов (микс проекта)",
                headroom(mix),
                formulas.headroom(mix, sym),
                caption=explanation(mix, sym),
            ),
            Kpi(
                "nne-per-hour",
                "NNE на час сверх плана",
                signed_money(mix.nne_per_hour, sym),
                formulas.nne_per_hour(mix, sym),
            ),
        ],
        columns=2,
    )
    if rule == PriceRule.BILLED:
        st.markdown("**Если часы сверх плана не выставляются** (выручка остаётся суммой сметы)")
        render_table(break_even_table(report.written_off, sym, "break-even-written-off"))
        st.markdown("**Если часы выставляются по ставкам**")
    render_table(break_even_table(report.rows, sym, "break-even"))
    st.caption(
        "Запас — сколько часов можно потратить сверх плана, пока NNE не станет нулём при "
        "зафиксированной цене: x* = min(α / β) по ветвям налога (методология, раздел 16). "
        "Микс — все назначения растут в одной пропорции; сотрудник — растут только его часы."
    )
    if isinstance(scenario.project.pricing, CappedPricing):
        st.caption("С потолком часы оплачиваются, пока выручка не дойдёт до потолка.")


def factor_label(bar: TornadoBar, sym: str) -> str:
    """Подпись фактора торнадо."""
    if bar.factor == Factor.FX:
        assert bar.currency is not None
        return f"Курс {CURRENCY_SYMBOLS[bar.currency]} к {sym}"
    return FACTOR_LABELS[bar.factor]


def render_tornado(scenario: Scenario, base: Estimate, sym: str) -> None:
    """Торнадо ±X%: что сильнее всего двигает NNE."""
    st.markdown("#### Торнадо: что сильнее всего двигает NNE")
    st.slider(
        "Отклонение каждого фактора, ±%",
        min_value=TORNADO_MIN_PCT,
        max_value=TORNADO_MAX_PCT,
        step=TORNADO_STEP_PCT,
        key=state.TORNADO_PCT,
        format="%d%%",
    )
    deviation = state.tornado_deviation()
    chart = compute(scenario, lambda: tornado(scenario, deviation))
    pct = int(deviation * PERCENT)
    labels = [factor_label(bar, sym) for bar in chart.bars]
    charts.show(
        charts.paired_bars(
            labels,
            [
                (
                    f"−{pct}%",
                    [bar.low_delta for bar in chart.bars],
                    [signed_money(bar.low_delta, sym) for bar in chart.bars],
                ),
                (
                    f"+{pct}%",
                    [bar.high_delta for bar in chart.bars],
                    [signed_money(bar.high_delta, sym) for bar in chart.bars],
                ),
            ],
            horizontal=True,
            axis_title=f"Изменение NNE, {sym}",
        )
    )
    render_table(
        Table(
            columns=["Фактор", f"NNE при −{pct}%", f"NNE при +{pct}%"],
            rows=[
                {
                    "Фактор": Cell(
                        factor_label(bar, sym),
                        note="" if bar.note is None else TORNADO_NOTE_LABELS[bar.note],
                    ),
                    f"NNE при −{pct}%": Cell(
                        money(bar.low, sym), note=signed_money(bar.low_delta, sym)
                    ),
                    f"NNE при +{pct}%": Cell(
                        money(bar.high, sym), note=signed_money(bar.high_delta, sym)
                    ),
                }
                for bar in chart.bars
            ],
            numeric=frozenset({f"NNE при −{pct}%", f"NNE при +{pct}%"}),
            key="tornado",
        )
    )
    if not any(bar.factor == Factor.FX for bar in chart.bars):
        st.caption("Курс: в проекте нет сумм в других валютах.")
    st.caption(
        f"Базовое NNE — {money(base.nne, sym)}. Каждый фактор меняется отдельно, остальное — "
        "как в плане; цена — по правилу модели (методология, раздел 17.1)."
    )


def price_label(scenario: Scenario) -> str:
    """Подпись ползунка цены."""
    pricing = scenario.project.pricing
    if isinstance(pricing, CappedPricing):
        return "Потолок"
    return "Цена «издержки + маржа» (держится)" if is_cost_plus(pricing) else "Согласованная цена"


def render_what_if(scenario: Scenario, base: Estimate, sym: str) -> None:
    """Ползунки «что если» и показатели против плана."""
    st.markdown("#### Что если — проект не меняется")
    st.caption(
        "Ползунки только пересчитывают смету: этапы, ставки и цена проекта остаются прежними. "
        "Значения у каждого проекта свои и в файл не сохраняются."
    )
    rule = price_rule(scenario.project.pricing)
    currencies: list[Currency] = list(foreign_currencies(scenario, base))
    sliders: list[tuple[str, str, int, int]] = [
        (key, FACTOR_LABELS[factor], low, high) for key, factor, low, high in WHATIF_SLIDERS
    ]
    sliders += [
        (
            state.whatif_fx_key(currency),
            f"Курс {CURRENCY_SYMBOLS[currency]} к {sym}",
            *WHATIF_FX_RANGE,
        )
        for currency in currencies
    ]
    if rule != PriceRule.BILLED:
        sliders.append((state.WHATIF_PRICE, price_label(scenario), *WHATIF_PRICE_RANGE))
    slider_columns = st.columns(2)
    for index, (key, label, low, high) in enumerate(sliders):
        slider_columns[index % 2].slider(
            f"{label}, %",
            min_value=low,
            max_value=high,
            step=WHATIF_STEP_PCT,
            key=key,
            format="%d%%",
        )
    st.button("↺ Сбросить «что если»", on_click=state.reset_what_if)

    result = compute(scenario, lambda: what_if(scenario, state.what_if()))
    plan, changed = result.base, result.changed
    kpi_grid(
        [
            Kpi(
                "whatif-revenue",
                "Выручка без НДС",
                money(changed.revenue, sym),
                formulas.revenue(changed),
                delta=metric_delta(changed.revenue - plan.revenue, sym),
            ),
            Kpi(
                "whatif-costs",
                "Издержки",
                money(changed.total_costs, sym),
                formulas.costs(changed),
                delta=metric_delta(changed.total_costs - plan.total_costs, sym),
                delta_color="inverse",
            ),
            Kpi(
                "whatif-tax",
                "Налог",
                money(changed.tax.tax, sym),
                formulas.tax(changed),
                delta=metric_delta(changed.tax.tax - plan.tax.tax, sym),
                delta_color="inverse",
            ),
            Kpi(
                "whatif-nne",
                "NNE",
                money(changed.nne, sym),
                formulas.nne(changed),
                delta=metric_delta(changed.nne - plan.nne, sym),
            ),
            Kpi(
                "whatif-margin",
                "Маржа",
                pct_label(changed.margin),
                formulas.margin(changed),
                delta=metric_points(changed.margin - plan.margin),
            ),
            Kpi(
                "whatif-total",
                "К оплате",
                money(changed.total_client, sym),
                formulas.total_client(changed),
                delta=metric_delta(changed.total_client - plan.total_client, sym),
                delta_color="off",
            ),
        ],
        columns=3,
    )
    if result.repriced is not None:
        change = result.repriced / plan.revenue - 1 if plan.revenue > 0 else Decimal(0)
        st.caption(
            f"Пересчитанная цена «издержки + маржа»: {money(result.repriced, sym)} "
            f"({signed_pct(change, Decimal('0.1'))}) — если бы цена следовала за издержками."
        )
    if result.utilization_capped:
        st.caption(
            "Утилизация упирается в 100% у сотрудников: "
            + ", ".join(result.utilization_capped)
            + "."
        )
    if changed.stages:
        plan_stages = {stage.stage_name: stage for stage in plan.stages}
        render_table(
            Table(
                columns=["Этап", "Маржа по плану", "Маржа «что если»", "Статус «что если»"],
                rows=[
                    {
                        "Этап": stage.stage_name,
                        "Маржа по плану": pct_label(plan_stages[stage.stage_name].margin),
                        "Маржа «что если»": pct_label(stage.margin),
                        "Статус «что если»": Cell("", flag=stage.flag),
                    }
                    for stage in changed.stages
                    if stage.stage_name in plan_stages
                ],
                numeric=frozenset({"Маржа по плану", "Маржа «что если»"}),
                key="what-if-stages",
            )
        )


def render(scenario: Scenario, base: Estimate) -> None:
    """Вкладка «Чувствительность»."""
    sym = CURRENCY_SYMBOLS[base.currency]
    rule = price_rule(scenario.project.pricing)
    if is_cost_plus(scenario.project.pricing):
        st.info(
            "Цена «издержки + маржа» считается по плану и дальше держится: она уже предложена "
            "клиенту. Иначе NNE = маржа × цена росло бы вместе с часами (методология, "
            "раздел 16.6).",
            icon="📌",
        )
    elif rule == PriceRule.BILLED:
        st.info(
            "В почасовой модели часы сверх плана оплачивает клиент. Ниже — оба случая: часы "
            "выставляются и часы сверх плана не выставляются (списываются).",
            icon="📌",
        )
    render_break_even(scenario, sym)
    st.divider()
    render_tornado(scenario, base, sym)
    st.divider()
    render_what_if(scenario, base, sym)
