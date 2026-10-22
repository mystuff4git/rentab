"""Страница 04: чувствительность сметы (docs/methodology.md, разделы 16–17).

Запас часов до безубыточности при зафиксированной цене (микс проекта и каждый
сотрудник), торнадо ±X% и ползунки «что если». Проект не меняется: ползунки
только пересчитывают смету; их значения у каждого проекта свои и живут в
сессии (в файл не попадают). Вся математика — в
`rentab_core.analysis.sensitivity`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

import pandas as pd
import plotly.graph_objects as go
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
from rentab_core.estimate import calculate_estimate
from rentab_core.models import ROLE_LABELS, CappedPricing
from rentab_core.money import CURRENCY_SYMBOLS, Currency
from rentab_core.results import Estimate
from ui import state
from ui.analytics import compute, render_header, require_scenario
from ui.labels import (
    FACTOR_LABELS,
    STAGE_FLAG_LABELS,
    TAX_BRANCH_LABELS,
    TORNADO_NOTE_LABELS,
    hours_label,
    money,
    pct_label,
    points_label,
    signed_hours,
    signed_money,
    signed_pct,
)
from ui.projects import render_project_switcher

# Цвета столбиков торнадо: −X% и +X% (палитра проверена на различимость
# при нарушениях цветового зрения; единый стиль графиков — фаза 6).
COLOR_LOW = "#2a78d6"
COLOR_HIGH = "#eb6834"
# Торнадо: отклонение X, % (границы и шаг ползунка).
TORNADO_MIN_PCT = 5
TORNADO_MAX_PCT = 50
TORNADO_STEP_PCT = 5
# Ползунки «что если», %: (ключ, подпись, минимум, максимум).
WHATIF_STEP_PCT = 5
WHATIF_SLIDERS: tuple[tuple[str, Factor, int, int], ...] = (
    (state.WHATIF_HOURS, Factor.HOURS, -50, 200),
    (state.WHATIF_BILLING_RATES, Factor.BILLING_RATES, -50, 50),
    (state.WHATIF_UTILIZATION, Factor.UTILIZATION, -50, 50),
    (state.WHATIF_OVERHEADS, Factor.OVERHEADS, -50, 100),
)
WHATIF_FX_RANGE = (-50, 50)
WHATIF_PRICE_RANGE = (-50, 50)
BAR_HEIGHT_PX = 56
CHART_MARGIN_PX = 90

state.init_defaults()
state.persist_widgets()
render_project_switcher()

st.title("🎯 Чувствительность")
scenario = require_scenario()
report = compute(lambda: break_even(scenario))
base = compute(lambda: calculate_estimate(scenario))
sym = CURRENCY_SYMBOLS[base.currency]
rule = price_rule(scenario.project.pricing)
render_header(scenario, base)

if is_cost_plus(scenario.project.pricing):
    st.info(
        "Цена «издержки + маржа» считается по плану и дальше держится: она уже предложена "
        "клиенту. Иначе NNE = маржа × цена росло бы вместе с часами (методология, раздел 16.6).",
        icon="📌",
    )
elif rule == PriceRule.BILLED:
    st.info(
        "В почасовой модели часы сверх плана оплачивает клиент. Ниже — оба случая: часы "
        "выставляются и часы сверх плана не выставляются (списываются).",
        icon="📌",
    )


# ===========================================================================
# 1. ЗАПАС ЧАСОВ ДО БЕЗУБЫТОЧНОСТИ
# ===========================================================================
def direction_label(row: BreakEven) -> str:
    """Микс проекта или сотрудник с ролью."""
    if row.employee is None:
        return "Микс проекта"
    assert row.role is not None
    role = ROLE_LABELS[row.role]
    return row.employee if row.employee == role else f"{row.employee} ({role})"


def explanation(row: BreakEven) -> str:
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
    if row.status == BreakEvenStatus.NOT_REACHED:
        return "не наступает"
    return "—" if row.extra_hours is None else signed_hours(row.extra_hours)


def break_even_table(rows: tuple[BreakEven, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Чем добираются часы": direction_label(row),
                "Часов в плане": hours_label(row.plan_hours),
                "Час с накладными": money(row.cost_per_hour, sym),
                "Запас": headroom(row),
                "К плану": "—" if row.share_of_plan is None else signed_pct(row.share_of_plan),
                "Часов в точке": "—" if row.total_hours is None else hours_label(row.total_hours),
                "Пояснение": explanation(row),
            }
            for row in rows
        ]
    )


st.markdown("### 1. Запас часов до безубыточности")
if rule == PriceRule.BILLED:
    st.markdown("**Если часы сверх плана не выставляются** (выручка остаётся суммой сметы)")
    st.dataframe(break_even_table(report.written_off), hide_index=True, width="stretch")
    st.markdown("**Если часы выставляются по ставкам**")
st.dataframe(break_even_table(report.rows), hide_index=True, width="stretch")
st.caption(
    "Запас — сколько часов можно потратить сверх плана, пока NNE не станет нулём при "
    "зафиксированной цене: x* = min(α / β) по ветвям налога (методология, раздел 16). "
    "Микс — все назначения растут в одной пропорции; сотрудник — растут только его часы."
)
if isinstance(scenario.project.pricing, CappedPricing):
    st.caption("С потолком часы оплачиваются, пока выручка не дойдёт до потолка.")


# ===========================================================================
# 2. ТОРНАДО
# ===========================================================================
def factor_label(bar: TornadoBar) -> str:
    if bar.factor == Factor.FX:
        assert bar.currency is not None
        return f"Курс {CURRENCY_SYMBOLS[bar.currency]} к {sym}"
    return FACTOR_LABELS[bar.factor]


st.markdown("---")
st.markdown("### 2. Торнадо: что сильнее всего двигает NNE")
st.slider(
    "Отклонение каждого фактора, ±%",
    min_value=TORNADO_MIN_PCT,
    max_value=TORNADO_MAX_PCT,
    step=TORNADO_STEP_PCT,
    key=state.TORNADO_PCT,
    format="%d%%",
)
deviation = state.tornado_deviation()
chart = compute(lambda: tornado(scenario, deviation))
pct = int(deviation * 100)
bars = list(reversed(chart.bars))  # Plotly рисует снизу вверх: самый сильный фактор — сверху.
labels = [factor_label(bar) for bar in bars]
figure = go.Figure()
for name, values, color in (
    (f"−{pct}%", [bar.low_delta for bar in bars], COLOR_LOW),
    (f"+{pct}%", [bar.high_delta for bar in bars], COLOR_HIGH),
):
    figure.add_trace(
        go.Bar(
            x=[float(value) for value in values],
            y=labels,
            name=name,
            orientation="h",
            marker={"color": color},
            customdata=[signed_money(value, sym) for value in values],
            hovertemplate=f"<b>%{{y}}</b>, {name}: ΔNNE %{{customdata}}<extra></extra>",
        )
    )
figure.update_layout(
    barmode="group",
    bargap=0.35,
    height=CHART_MARGIN_PX + BAR_HEIGHT_PX * len(bars),
    margin={"t": 10, "b": 10, "l": 10, "r": 10},
    legend={"orientation": "h", "yanchor": "bottom", "y": 1.0},
    xaxis={"title": f"Изменение NNE, {sym}", "tickformat": ",.0f", "zeroline": True},
)
st.plotly_chart(figure, width="stretch")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Фактор": factor_label(bar),
                f"NNE при −{pct}%": f"{money(bar.low, sym)} ({signed_money(bar.low_delta, sym)})",
                f"NNE при +{pct}%": f"{money(bar.high, sym)} ({signed_money(bar.high_delta, sym)})",
                "Пояснение": "" if bar.note is None else TORNADO_NOTE_LABELS[bar.note],
            }
            for bar in chart.bars
        ]
    ),
    hide_index=True,
    width="stretch",
)
if not any(bar.factor == Factor.FX for bar in chart.bars):
    st.caption("Курс: в проекте нет сумм в других валютах.")
st.caption(
    f"Базовое NNE — {money(base.nne, sym)}. Каждый фактор меняется отдельно, остальное — как в "
    "плане; цена — по правилу модели (методология, раздел 17.1)."
)


# ===========================================================================
# 3. ЧТО ЕСЛИ
# ===========================================================================
def price_label() -> str:
    pricing = scenario.project.pricing
    if isinstance(pricing, CappedPricing):
        return "Потолок"
    return "Цена «издержки + маржа» (держится)" if is_cost_plus(pricing) else "Согласованная цена"


def render_metrics(plan: Estimate, changed: Estimate) -> None:
    """Показатели «что если» против плана."""
    columns = st.columns(3)
    rows: list[tuple[str, Decimal, Decimal, Literal["normal", "inverse", "off"]]] = [
        (f"Выручка без НДС, {sym}", plan.revenue, changed.revenue, "normal"),
        (f"Издержки, {sym}", plan.total_costs, changed.total_costs, "inverse"),
        (f"Налог, {sym}", plan.tax.tax, changed.tax.tax, "inverse"),
        (f"NNE, {sym}", plan.nne, changed.nne, "normal"),
        (f"К оплате, {sym}", plan.total_client, changed.total_client, "off"),
    ]
    for index, (label, before, after, color) in enumerate(rows):
        columns[index % 3].metric(
            label,
            money(after, sym),
            delta=signed_money(after - before, sym),
            delta_color=color,
        )
    columns[len(rows) % 3].metric(
        "Маржа", pct_label(changed.margin), delta=points_label(changed.margin - plan.margin)
    )


st.markdown("---")
st.markdown("### 3. Что если — проект не меняется")
st.caption(
    "Ползунки только пересчитывают смету: этапы, ставки и цена проекта остаются прежними. "
    "Значения у каждого проекта свои и в файл не сохраняются."
)
currencies: list[Currency] = list(foreign_currencies(scenario, base))
slider_columns = st.columns(2)
sliders: list[tuple[str, str, int, int]] = [
    (key, FACTOR_LABELS[factor], low, high) for key, factor, low, high in WHATIF_SLIDERS
]
sliders += [
    (state.whatif_fx_key(currency), f"Курс {CURRENCY_SYMBOLS[currency]} к {sym}", *WHATIF_FX_RANGE)
    for currency in currencies
]
if rule != PriceRule.BILLED:
    sliders.append((state.WHATIF_PRICE, price_label(), *WHATIF_PRICE_RANGE))
for index, (key, label, low, high) in enumerate(sliders):
    slider_columns[index % 2].slider(
        f"{label}, %", min_value=low, max_value=high, step=WHATIF_STEP_PCT, key=key, format="%d%%"
    )
st.button("↺ Сбросить «что если»", on_click=state.reset_what_if)

result = compute(lambda: what_if(scenario, state.what_if()))
render_metrics(result.base, result.changed)
if result.repriced is not None:
    change = result.repriced / result.base.revenue - 1 if result.base.revenue > 0 else Decimal(0)
    st.caption(
        f"Пересчитанная цена «издержки + маржа»: {money(result.repriced, sym)} "
        f"({signed_pct(change, Decimal('0.1'))}) — если бы цена следовала за издержками."
    )
if result.utilization_capped:
    st.caption(
        "Утилизация упирается в 100% у сотрудников: " + ", ".join(result.utilization_capped) + "."
    )
if result.changed.stages:
    plan_stages = {stage.stage_name: stage for stage in result.base.stages}
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Этап": stage.stage_name,
                    "Маржа по плану": pct_label(plan_stages[stage.stage_name].margin),
                    "Маржа «что если»": pct_label(stage.margin),
                    "Статус «что если»": STAGE_FLAG_LABELS[stage.flag],
                }
                for stage in result.changed.stages
                if stage.stage_name in plan_stages
            ]
        ),
        hide_index=True,
        width="stretch",
    )
