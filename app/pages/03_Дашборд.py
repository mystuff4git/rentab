"""Страница 03: дашборд сметы (страница v0.3 на ядре, методология фазы 2).

Показатели берутся из `rentab_core.dashboard`, смета XLSX — из
`rentab_core.estimate_xlsx`. Все суммы — в валюте проекта: пересчёт валют
выполняет ядро при расчёте (L-06). Маржа и эффективная ставка налога — от
выручки без НДС (L-15); светофор этапов — для всех моделей цены (L-04, L-19).
Показывается смета активного проекта; проект выбирается в боковой панели.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pydantic import ValidationError

from rentab_core import RentabError, dashboard, estimate_xlsx
from rentab_core.analysis.dashboard import Profitability
from rentab_core.analysis.plan_fact import plan_fact
from rentab_core.analysis.sensitivity import BreakEvenStatus, break_even
from rentab_core.models import ROLE_LABELS, CappedPricing, FixedPricing
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT
from rentab_core.results import Estimate
from ui import state
from ui.labels import (
    EXPENSE_TYPE_LABELS,
    PROFITABILITY_MESSAGES,
    STAGE_FLAG_BG_COLORS,
    STAGE_FLAG_LABELS,
    money,
    signed_hours,
)
from ui.projects import render_project_switcher

# Цвета слоёв структуры цены (как в v0.3).
COLOR_LABOR = "#3498db"
COLOR_OVERHEADS = "#9b59b6"
COLOR_TAX = "#e74c3c"
COLOR_OWN = "#95a5a6"
COLOR_PROFIT = "#2ecc71"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

state.init_defaults()
state.persist_widgets()
render_project_switcher()


def render_estimate_download(estimate: Estimate) -> None:
    """Поле «Название фирмы» и кнопка скачивания XLSX-сметы."""
    st.markdown("#### Экспорт сметы")
    firm_name = st.text_input(
        "Название фирмы (для шапки документа)",
        key=state.FIRM_NAME,
        placeholder="Например, ООО «Юридическая фирма»",
    )
    today = dt.date.today()
    title = estimate.project_name.strip() or "project"
    st.download_button(
        label="📄 Скачать смету проекта",
        data=estimate_xlsx(estimate, firm_name=firm_name, issue_date=today),
        file_name=f"Смета_{title}_{today.isoformat()}.xlsx",
        mime=XLSX_MIME,
        width="content",
    )


def render_profitability(value: Profitability, loss_message: str) -> None:
    """Индикатор рентабельности проекта."""
    if value == Profitability.PROFITABLE:
        st.success(PROFITABILITY_MESSAGES[value])
    elif value == Profitability.BREAKEVEN:
        st.warning(PROFITABILITY_MESSAGES[value])
    else:
        st.error(loss_message)


st.title("📊 Дашборд")

result = state.estimate()
if result is None:
    st.info(
        f"Сначала сформируйте смету проекта «{state.slot_title(state.active_id())}» "
        "на странице «Проект»"
    )
    st.stop()

sym = CURRENCY_SYMBOLS[result.currency]
view = dashboard(result)

st.subheader(result.project_name or "Без названия")
meta = []
if result.client:
    meta.append(f"**Клиент:** {result.client}")
meta.append(f"**Режим:** {result.regime_label}")
meta.append(f"**Валюта:** {result.currency.value}")
st.caption("  ·  ".join(meta))

# ===========================================================================
# Главные показатели
# ===========================================================================
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"Выручка без НДС, {sym}", f"{result.revenue:,.0f}")
c2.metric(
    f"НДС и возмещаемые расходы, {sym}",
    f"{result.vat + result.disbursements_billed:,.0f}",
    help=f"НДС {result.vat:,.0f} + пошлины и иные возмещаемые расходы "
    f"{result.disbursements_billed:,.0f}. Это транзит, а не доход фирмы.",
)
c3.metric(f"К оплате клиентом, {sym}", f"{result.total_client:,.0f}")
margin_delta = None if view.margin_pct is None else f"{view.margin_pct:.1f}% маржа"
c4.metric(f"Чистая прибыль (NNE), {sym}", f"{result.nne:,.0f}", delta=margin_delta)
if isinstance(result.pricing, CappedPricing) and result.write_off > 0:
    st.caption(
        f"Потолок сработал: по ставкам {result.shadow_value_net:,.0f} {sym}, "
        f"списано {result.write_off:,.0f} {sym}."
    )
for item in result.unresolved_assignments:
    st.warning(
        f"Этап «{item.stage_name}»: исполнителя «{item.employee}» нет в команде, "
        "назначение не вошло в расчёт."
    )


def render_analytics_teaser(estimate: Estimate) -> None:
    """Запас часов и NNE по факту — если смета рассчитана по текущим данным проекта."""
    try:
        scenario = state.scenario()
        report = break_even(scenario)
    except (ValidationError, RentabError):
        return
    mix = report.rows[0]
    if mix.nne != estimate.nne or report.revenue != estimate.revenue:
        return
    symbol = CURRENCY_SYMBOLS[estimate.currency]
    if mix.status == BreakEvenStatus.HEADROOM and mix.extra_hours is not None:
        text = f"🎯 Запас часов до безубыточности (микс проекта): {signed_hours(mix.extra_hours)}"
    elif mix.status == BreakEvenStatus.NOT_REACHED:
        text = "🎯 Часы сверх плана оплачивает клиент: точка безубыточности не наступает"
    else:
        text = "🎯 Проект убыточен по плану: что делать с часами — на странице «Чувствительность»"
    col_text, col_link = st.columns([3, 1])
    col_text.markdown(text)
    col_link.page_link("pages/04_Чувствительность.py", label="Чувствительность", icon="➡️")
    if scenario.project.actuals is not None:
        try:
            fact = plan_fact(scenario).fact
        except RentabError:
            return
        col_text.markdown(
            f"📈 NNE по факту: {money(fact.nne, symbol)} (по плану {money(estimate.nne, symbol)})"
        )
        col_link.page_link("pages/06_План–факт.py", label="План–факт", icon="➡️")


render_analytics_teaser(result)

st.markdown("---")
col_chart, col_metrics = st.columns([1.4, 1])
with col_chart:
    st.markdown("#### Структура выручки без НДС")
    structure = view.price_structure
    layers = [
        ("Прямые трудозатраты", structure.direct_labor, COLOR_LABOR),
        ("Накладные", structure.overheads, COLOR_OVERHEADS),
        ("Собственные расходы", structure.own, COLOR_OWN),
        ("Налог", structure.tax, COLOR_TAX),
        ("Прибыль (NNE)", structure.profit, COLOR_PROFIT),
    ]
    fig = go.Figure()
    for layer_name, layer_value, layer_color in layers:
        fig.add_trace(
            go.Bar(
                x=[float(layer_value)],
                y=["Выручка"],
                name=layer_name,
                orientation="h",
                marker={"color": layer_color},
                hovertemplate=f"<b>{layer_name}</b>: %{{x:,.0f}} {sym}<extra></extra>",
            )
        )
    fig.update_layout(
        barmode="relative",
        height=220,
        margin={"t": 10, "b": 10, "l": 10, "r": 10},
        legend={"orientation": "h", "yanchor": "top", "y": -0.3},
        xaxis={"title": f"Сумма, {sym}", "tickformat": ",.0f"},
        yaxis={"showticklabels": False},
    )
    st.plotly_chart(fig, width="stretch")
    if isinstance(result.pricing, FixedPricing) and result.pricing.price is None:
        st.caption(
            f"Цена «издержки + маржа» при целевой марже {view.target_margin_pct:.0f}%: "
            f"НДС в цену не закладывается, он начисляется сверху."
        )

with col_metrics:
    st.markdown("#### Показатели")
    st.metric(
        f"Средневзвешенная ставка, {sym}/ч",
        f"{result.blended_rate:,.0f}",
        help="Σ(ставка × часы) / Σ часов.",
    )
    st.metric(
        "Коэффициент рычага",
        f"{result.leverage:.2f}",
        help="Часы младших / часы старших. Выше — выше маржинальность.",
    )
    st.metric(
        f"Ставка накладных, {sym}/ч",
        f"{result.overhead_rate:,.1f}",
        help="Σ накладных в месяц / биллируемая ёмкость команды (Σ часы × утилизация).",
    )
    st.metric(
        "Эффективная ставка налога",
        "—" if view.effective_tax_rate_pct is None else f"{view.effective_tax_rate_pct:.1f}%",
        help="Налог на доход или прибыль / выручка без НДС.",
    )
    st.metric(
        f"Стоимость недозагрузки, {sym}",
        f"{result.underutilization_cost:,.0f}",
        help="Часть трудозатрат, которая оплачивает небиллируемое время команды: "
        "Σ (себестоимость биллируемого часа − контрактного) × часы.",
    )

st.markdown("---")
st.markdown("#### Этапы проекта")
if view.stages:
    stage_df = pd.DataFrame(
        [
            {
                "Этап": stage.stage_name,
                "Часы": f"{stage.hours:,.1f}",
                f"Выручка этапа, {sym}": f"{stage.revenue:,.0f}",
                f"Издержки, {sym}": f"{stage.costs:,.0f}",
                f"Налог, {sym}": f"{stage.tax:,.0f}",
                f"NNE, {sym}": f"{stage.nne:,.0f}",
                "Маржа, %": f"{stage.margin * PERCENT:.1f}",
                "Статус": STAGE_FLAG_LABELS[stage.flag],
                "_flag": stage.flag.value,
            }
            for stage in view.stages
        ]
    )
    bg_by_flag = {flag.value: style for flag, style in STAGE_FLAG_BG_COLORS.items()}

    def color_by_flag(row: pd.Series) -> list[str]:
        """Фон строки по цвету светофора."""
        style = bg_by_flag.get(str(row["_flag"]), "")
        return [style for _ in row]

    # Служебная колонка _flag нужна только для подсветки; Styler.hide в
    # Streamlit 1.66 её не скрывает, поэтому скрываем через column_config.
    st.dataframe(
        stage_df.style.apply(color_by_flag, axis=1),
        width="stretch",
        hide_index=True,
        column_config={"_flag": None},
    )
    st.caption(
        "Выручка этапа — доля по теневой стоимости (ставка × часы); маржа — после "
        f"накладных, собственных расходов и налога; зелёный — не ниже цели "
        f"{view.target_margin_pct:.0f}%."
    )
else:
    st.info("Нет этапов для анализа.")

with st.expander("Исполнители"):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Этап": line.stage_name,
                    "Исполнитель": f"{line.employee_name} ({ROLE_LABELS[line.role]})",
                    "Часы": f"{line.hours:,.1f}",
                    f"Ставка, {sym}/ч": f"{line.billing_rate:,.0f}",
                    f"Себестоимость часа, {sym}": f"{line.cost_rate:,.0f}",
                    f"Трудозатраты, {sym}": f"{line.labor_cost:,.0f}",
                }
                for line in result.lines
            ]
        ),
        width="stretch",
        hide_index=True,
    )

if result.disbursements:
    st.markdown("#### Расходы проекта")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Название": item.name,
                    "Тип": EXPENSE_TYPE_LABELS[item.kind],
                    "Этап": item.stage or "—",
                    "Сумма": f"{item.original_amount:,.0f} {item.original_currency.value}",
                    f"В валюте проекта, {sym}": f"{item.amount:,.0f}",
                }
                for item in result.disbursements
            ]
        ),
        width="stretch",
        hide_index=True,
    )

st.markdown("---")
render_profitability(
    view.profitability,
    "❌ Проект убыточен — пересмотрите цену, часы или состав команды.",
)
render_estimate_download(result)
