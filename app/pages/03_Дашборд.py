"""Страница 03: дашборд сметы (перенос `pages/03_Дашборд.py` v0.3 на ядро).

Показатели берутся из `rentab_core.dashboard`, смета XLSX — из
`rentab_core.estimate_xlsx`. В режиме «Оба» суммы можно показать в другой
валюте: множитель считает `money.display_conversion_factor` (только показ,
в расчёт сметы курс не входит — L-06).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from rentab_core import dashboard, estimate_xlsx
from rentab_core.analysis.dashboard import FixedDashboard, HourlyDashboard, Profitability
from rentab_core.models import ROLE_LABELS, JurisdictionMode
from rentab_core.money import CURRENCY_SYMBOLS, Currency, display_conversion_factor
from rentab_core.results import Estimate, FixedPriceEstimate, HourlyEstimate
from ui import state
from ui.labels import (
    EXPENSE_TYPE_LABELS,
    PROFITABILITY_MESSAGES,
    STAGE_FLAG_BG_COLORS,
    STAGE_FLAG_LABELS,
)

# Цвета слоёв структуры цены (как в v0.3).
COLOR_LABOR = "#3498db"
COLOR_OVERHEADS = "#9b59b6"
COLOR_TAX = "#e74c3c"
COLOR_OWN = "#95a5a6"
COLOR_PROFIT = "#2ecc71"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

state.init_defaults()
state.persist_widgets()


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
    st.info("Сначала сформируйте смету на странице «Проект»")
    st.stop()

# ---------------------------------------------------------------------------
# Валюта отображения в режиме «Оба»
# ---------------------------------------------------------------------------
display_currency = result.currency
factor = Decimal(1)
if result.jurisdiction_mode == JurisdictionMode.BOTH:
    with st.sidebar:
        st.subheader("💱 Валюта отображения")
        stored_rate = float(st.session_state[state.EXCHANGE_RATE])
        rate_input = st.number_input(
            "Курс: 1 KZT = ? RUB",
            min_value=0.0001,
            value=stored_rate,
            step=0.01,
            format="%.4f",
            help="Например, 0.18 ₽/₸ означает «1 ₸ ≈ 0,18 ₽».",
            key="dash_exchange_rate",
        )
        target_code = st.radio(
            "Показать суммы в",
            options=[Currency.RUB.value, Currency.KZT.value],
            index=0 if result.currency == Currency.RUB else 1,
            horizontal=True,
            key="dash_target_currency",
        )
        if st.button("🔄 Пересчитать", width="stretch"):
            st.session_state["display_currency_code"] = target_code
            st.session_state[state.EXCHANGE_RATE] = rate_input
    display_currency = Currency(st.session_state.get("display_currency_code", result.currency))
    factor = display_conversion_factor(
        result.currency,
        display_currency,
        Decimal(str(st.session_state[state.EXCHANGE_RATE])),
    )


def cx(value: Decimal) -> float:
    """Сумма в валюте отображения."""
    return float(value * factor)


sym = CURRENCY_SYMBOLS[display_currency]

st.subheader(result.project_name or "Без названия")
meta = []
if result.client:
    meta.append(f"**Клиент:** {result.client}")
if result.regime_label:
    meta.append(f"**Режим:** {result.regime_label}")
if meta:
    st.caption("  ·  ".join(meta))

view = dashboard(result)

# ===========================================================================
# Фикс-прайс
# ===========================================================================
if isinstance(result, FixedPriceEstimate):
    assert isinstance(view, FixedDashboard)
    fp_c1, fp_c2, fp_c3, fp_c4 = st.columns(4)
    fp_c1.metric(f"Все издержки проекта, {sym}", f"{cx(result.total_costs):,.0f}")
    fp_c2.metric("Целевая маржа", f"{view.target_margin_pct:.0f}%")
    fp_c3.metric(f"Фиксированная цена, {sym}", f"{cx(result.fixed_price):,.0f}")
    fp_c4.metric(
        f"Чистая прибыль (NNE), {sym}",
        f"{cx(result.nne):,.0f}",
        delta=f"{view.actual_margin_pct:.1f}% реальная маржа",
    )

    st.markdown("---")
    st.markdown("#### Структура фиксированной цены")
    structure = view.price_structure
    layers = [
        ("Прямые трудозатраты", cx(structure.direct_labor), COLOR_LABOR),
        ("Накладные", cx(structure.indirect), COLOR_OVERHEADS),
        ("Налог", cx(structure.tax), COLOR_TAX),
        ("Прибыль", cx(structure.profit), COLOR_PROFIT),
    ]
    fig_fp = go.Figure()
    for layer_name, layer_value, layer_color in layers:
        fig_fp.add_trace(
            go.Bar(
                x=[layer_value],
                y=["Фикс. цена"],
                name=layer_name,
                orientation="h",
                marker={"color": layer_color},
                hovertemplate=f"<b>{layer_name}</b>: %{{x:,.0f}} {sym}<extra></extra>",
            )
        )
    fig_fp.update_layout(
        barmode="stack",
        height=200,
        margin={"t": 10, "b": 10, "l": 10, "r": 10},
        legend={"orientation": "h", "yanchor": "top", "y": -0.3},
        xaxis={"title": f"Сумма, {sym}", "tickformat": ",.0f"},
        yaxis={"showticklabels": False},
    )
    st.plotly_chart(fig_fp, width="stretch")
    st.caption(f"Налоговый режим: {result.regime_label or '—'}")

    st.markdown("---")
    st.markdown("#### Анализ этапов")
    if view.stage_rows:
        flag_df = pd.DataFrame(
            [
                {
                    "Этап": row.stage_name,
                    "Часы": f"{row.hours:,.1f}",
                    f"Издержки этапа, {sym}": f"{cx(row.costs):,.0f}",
                    f"Доля выручки, {sym}": f"{cx(row.revenue_share):,.0f}",
                    "Маржа, %": f"{row.margin_pct:.1f}",
                    "Статус": STAGE_FLAG_LABELS[row.flag],
                    "_flag": row.flag.value,
                }
                for row in view.stage_rows
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
            flag_df.style.apply(color_by_flag, axis=1),
            width="stretch",
            hide_index=True,
            column_config={"_flag": None},
        )
    else:
        st.info("Нет этапов для анализа.")

    st.markdown("---")
    render_profitability(
        view.profitability,
        "❌ Проект убыточен — пересмотрите целевую маржу или часы по этапам.",
    )
    render_estimate_download(result)
    st.stop()

# ===========================================================================
# Почасовая модель
# ===========================================================================
assert isinstance(result, HourlyEstimate)
assert isinstance(view, HourlyDashboard)
gross = cx(result.gross_revenue)
disb_billed = cx(result.disbursements_billed)
total_client = cx(result.total_client)
nne_value = cx(result.nne)
margin_delta = f"{view.nne_margin_pct:.1f}% маржи" if view.nne_margin_pct is not None else None

if disb_billed > 0:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Выручка (без перевыст.), {sym}", f"{gross:,.0f}")
    c2.metric(f"Перевыставляемые расходы, {sym}", f"{disb_billed:,.0f}")
    c3.metric(f"Итоговый счёт клиенту, {sym}", f"{total_client:,.0f}")
    c4.metric(f"Чистая прибыль (NNE), {sym}", f"{nne_value:,.0f}", delta=margin_delta)
else:
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Выручка, {sym}", f"{gross:,.0f}")
    c2.metric(f"Итоговый счёт клиенту, {sym}", f"{total_client:,.0f}")
    c3.metric(f"Чистая прибыль (NNE), {sym}", f"{nne_value:,.0f}", delta=margin_delta)

st.markdown("---")
col_chart, col_metrics = st.columns([1.4, 1])
with col_chart:
    st.markdown("#### Структура цены")
    sections = [
        ("Прямые трудозатраты", cx(result.direct_labor), COLOR_LABOR),
        ("Накладные (распределённые)", cx(result.overheads_alloc), COLOR_OVERHEADS),
        ("Налог", cx(result.tax.total_tax), COLOR_TAX),
        ("Расходы за счёт фирмы", cx(result.disbursements_own), COLOR_OWN),
        ("Чистая прибыль (NNE)", nne_value, COLOR_PROFIT),
    ]
    positive = [section for section in sections if section[1] > 0]
    if positive:
        fig = go.Figure(
            data=[
                go.Pie(
                    labels=[name for name, _, _ in positive],
                    values=[value for _, value, _ in positive],
                    marker={"colors": [color for _, _, color in positive]},
                    hole=0.35,
                    textinfo="label+percent",
                    hovertemplate="%{label}: %{value:,.0f} " + sym + "<extra></extra>",
                )
            ]
        )
        fig.update_layout(
            margin={"t": 10, "b": 10, "l": 10, "r": 10},
            height=400,
            showlegend=True,
            legend={"orientation": "h", "yanchor": "top", "y": -0.05},
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Все статьи нулевые — структуру цены построить нельзя.")
    st.caption(f"Налоговый режим: {result.regime_label or '—'}")

with col_metrics:
    st.markdown("#### Показатели")
    st.metric(
        f"Средневзвешенная ставка, {sym}/ч",
        f"{cx(result.blended_rate):,.0f}",
        help="Σ(billing × hours) / Σ(hours). С учётом валюты отображения.",
    )
    st.metric(
        "Коэффициент рычага",
        f"{result.leverage:.2f}",
        help="Часы младших / часы старших. Выше — выше маржинальность.",
    )
    st.metric(
        f"Ставка накладных, {sym}/ч",
        f"{cx(result.overhead_rate):,.1f}",
        help="Σ(месячных накладных) / billable_hours_per_month.",
    )
    st.metric(
        "Эффективная ставка налога",
        f"{view.effective_tax_rate_pct:.1f}%",
        help=f"Налог / итоговый счёт. Базовый режим: {result.regime_label or '—'}.",
    )

st.markdown("---")
st.markdown("#### Этапы проекта")
if not view.stage_rows:
    st.info("В смете нет этапов для отображения.")
else:
    stage_table = [
        {
            "Этап": row.stage_name,
            "Исполнитель": f"{row.employee_name} ({ROLE_LABELS[row.role]})",
            "Часы": f"{row.hours:,.1f}",
            f"Ставка ({sym}/ч)": f"{cx(row.billing_rate):,.0f}",
            f"Выручка этапа, {sym}": f"{cx(row.revenue):,.0f}",
            "Маржа, %": f"{row.margin_pct:.1f}",
        }
        for row in view.stage_rows
    ]
    totals = view.stage_totals
    stage_table.append(
        {
            "Этап": "Итого",
            "Исполнитель": "",
            "Часы": f"{totals.hours:,.1f}",
            f"Ставка ({sym}/ч)": "",
            f"Выручка этапа, {sym}": f"{cx(totals.revenue):,.0f}",
            "Маржа, %": f"{totals.margin_pct:.1f}",
        }
    )

    def bold_total(row: pd.Series) -> list[str]:
        """Жирная строка «Итого»."""
        return ["font-weight: 700" if row["Этап"] == "Итого" else "" for _ in row]

    st.dataframe(
        pd.DataFrame(stage_table).style.apply(bold_total, axis=1),
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
                    f"Сумма, {sym}": f"{cx(item.amount):,.0f}",
                    "Перевыставляется клиенту": "Да" if item.billable else "Нет",
                }
                for item in result.disbursements
            ]
        ),
        width="stretch",
        hide_index=True,
    )

st.markdown("---")
render_profitability(
    view.profitability, "❌ Проект убыточен — пересмотрите ставки или состав команды"
)
render_estimate_download(result)
