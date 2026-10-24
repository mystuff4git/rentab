"""Страница 05: сравнение моделей цены и налоговых режимов (docs/methodology.md, раздел 19).

Матрица «модель цены × налоговый режим» для активного проекта: строки —
режимы стран юрисдикции фирмы, столбцы — почасовая, с потолком, «издержки +
маржа» и согласованная цена (если она есть). Недоступные режимы помечены
причиной из валидатора применимости, их цифры приглушены. Расчёт —
`rentab_core.analysis.compare`; проект не меняется.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from rentab_core.analysis.compare import (
    CAP_MISSING,
    Comparison,
    ComparisonCell,
    ComparisonRow,
    best_cells,
    compare,
)
from rentab_core.estimate import calculate_estimate
from rentab_core.fx import convert
from rentab_core.models import CappedPricing, Country
from rentab_core.money import CURRENCY_SYMBOLS, Currency
from rentab_core.tax.eligibility import Severity
from ui import state
from ui.analytics import compute, render_header, require_scenario
from ui.labels import (
    COMPARE_METRICS,
    COUNTRY_LABELS,
    PRICING_KIND_LABELS,
    SEVERITY_ICONS,
    money,
    pct_label,
    regime_label,
)
from ui.projects import render_project_switcher

DEFAULT_METRIC = "nne"
CURRENT_MARK = "▶ "
AVAILABLE_LABEL = "✅ доступен"
UNAVAILABLE_LABEL = "⛔ недоступен"
CAVEAT_LABEL = "⚠️ с оговорками"
CHECK_LABEL = "ℹ️ проверить условия"
UNREACHABLE_SHORT = "цель недостижима"
CAP_MISSING_SHORT = "задайте потолок"
# Оформление матрицы: недоступные режимы приглушены, лучшие значения — жирным,
# текущая ячейка — с фоном.
MUTED_STYLE = "color: #8a8986"
BEST_STYLE = "font-weight: 700"
CURRENT_STYLE = "background-color: #e8f0fb"
CAP_STEP = 10_000.0

state.init_defaults()
state.persist_widgets()
render_project_switcher()

st.title("⚖️ Сравнение")
st.caption("Матрица «модель цены × налоговый режим» для активного проекта.")
scenario = require_scenario()
base = compute(lambda: calculate_estimate(scenario))
render_header(scenario, base)
sym = CURRENCY_SYMBOLS[base.currency]

st.session_state.setdefault(state.COMPARE_METRIC, DEFAULT_METRIC)
st.session_state.setdefault(state.COMPARE_ALL_VARIANTS, False)
col_metric, col_options = st.columns([3, 2])
with col_metric:
    st.radio(
        "Показать",
        options=list(COMPARE_METRICS),
        format_func=COMPARE_METRICS.__getitem__,
        horizontal=True,
        key=state.COMPARE_METRIC,
    )
with col_options:
    st.checkbox(
        "Все варианты НДС и ставок НПД",
        key=state.COMPARE_ALL_VARIANTS,
        help="По строке на каждый вариант; недоступные — с причиной.",
    )
if not isinstance(scenario.project.pricing, CappedPricing):
    st.number_input(
        f"Потолок для сравнения, {sym}",
        min_value=0.0,
        step=CAP_STEP,
        format="%.0f",
        key=state.COMPARE_CAP,
        help="В проекте потолка нет: укажите его, чтобы заполнить столбец «С потолком». "
        "Потолок — в режиме НДС проекта.",
    )
metric = str(st.session_state[state.COMPARE_METRIC])
result = compare(
    scenario,
    cap=state.compare_cap(),
    all_variants=bool(st.session_state[state.COMPARE_ALL_VARIANTS]),
)


def value_text(cell: ComparisonCell, currency: Currency) -> str:
    """Значение показателя в ячейке; у сумм в другой валюте — ещё и в валюте проекта."""
    if cell.estimate is None:
        error = cell.error or ""
        if error == CAP_MISSING:
            return CAP_MISSING_SHORT
        return UNREACHABLE_SHORT if "недостижима" in error else error
    value: Decimal = getattr(cell.estimate, metric)
    if metric == "margin":
        return pct_label(value)
    text = money(value, CURRENCY_SYMBOLS[currency])
    if currency != result.currency:
        rates = scenario.firm.exchange_rates
        converted = convert(value, currency, result.currency, rates)
        text += f" (≈ {money(converted, sym)})"
    return text


def availability(row: ComparisonRow) -> str:
    if not row.available:
        return UNAVAILABLE_LABEL
    if any(issue.severity == Severity.WARNING for issue in row.issues):
        return CAVEAT_LABEL
    return CHECK_LABEL if row.issues else AVAILABLE_LABEL


def render_group(comparison: Comparison, country: Country) -> None:
    """Таблица режимов одной страны."""
    indexed = [
        (index, row) for index, row in enumerate(comparison.rows) if row.option.country == country
    ]
    if not indexed:
        return
    currency = country.currency
    best = best_cells(comparison, "margin" if metric == "margin" else metric)
    columns = [
        (CURRENT_MARK if column.is_current else "") + PRICING_KIND_LABELS[column.kind]
        for column in comparison.columns
    ]
    records = []
    styles = []
    for index, row in indexed:
        mark = CURRENT_MARK if row.option.is_current else ""
        record = {
            "Режим": mark + regime_label(row.option.profile),
            "Доступность": availability(row),
        }
        style = {"Режим": "", "Доступность": ""}
        for position, (title, cell) in enumerate(zip(columns, row.cells, strict=True)):
            text = value_text(cell, currency)
            record[title] = text if row.available else f"({text})"
            cell_style = MUTED_STYLE if not row.available else ""
            if (index, position) in best and metric in ("nne", "margin"):
                cell_style = BEST_STYLE
            if row.option.is_current and comparison.columns[position].is_current:
                cell_style = f"{cell_style}; {CURRENT_STYLE}".strip("; ")
            style[title] = cell_style
        if not row.available:
            style["Режим"] = style["Доступность"] = MUTED_STYLE
        records.append(record)
        styles.append(style)
    frame = pd.DataFrame(records)
    style_frame = pd.DataFrame(styles, index=frame.index, columns=frame.columns)
    st.dataframe(
        frame.style.apply(lambda _: style_frame, axis=None),
        hide_index=True,
        width="stretch",
    )


countries = scenario.firm.jurisdiction.countries
for country in countries:
    if len(countries) > 1:
        st.markdown(f"#### {COUNTRY_LABELS[country]}, {CURRENCY_SYMBOLS[country.currency]}")
    render_group(result, country)

st.caption(
    f"{CURRENT_MARK.strip()} — текущий режим и модель цены проекта (ячейка с фоном — смета "
    "проекта). Жирным — лучшее NNE или маржа среди доступных режимов в столбце. В скобках — "
    "цифры недоступного режима: смета считается, но применять режим фирма не может."
)
notes = [
    "НДС не меняет NNE при ценах без НДС — только сумму «к оплате».",
    "В «издержки + маржа» NNE = маржа × цена: режим с высоким налогом даёт более высокую "
    "цену для клиента, а не меньшую прибыль — сравнивайте «к оплате».",
    "Ставка взносов, рассчитанная автоматически, пересчитывается по режиму строки "
    "(АУСН — 0%); заданная вручную — остаётся (методология, раздел 19.3).",
]
st.caption(" ".join(notes))

with_issues = [row for row in result.rows if row.issues]
if with_issues:
    with st.expander(f"Почему режим недоступен или с оговорками ({len(with_issues)})"):
        for row in with_issues:
            lines = "\n".join(
                f"  - {SEVERITY_ICONS[issue.severity]} {issue.message}" for issue in row.issues
            )
            st.markdown(f"- **{regime_label(row.option.profile)}**\n{lines}")

with st.expander("Все показатели"):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Режим": regime_label(row.option.profile),
                    "Модель цены": PRICING_KIND_LABELS[column.kind],
                    "Доступен": "да" if row.available else "нет",
                    "Цена без НДС": ""
                    if cell.estimate is None
                    else money(cell.estimate.revenue, s),
                    "НДС": "" if cell.estimate is None else money(cell.estimate.vat, s),
                    "К оплате": ""
                    if cell.estimate is None
                    else money(cell.estimate.total_client, s),
                    "Налог": "" if cell.estimate is None else money(cell.estimate.tax.tax, s),
                    "NNE": "" if cell.estimate is None else money(cell.estimate.nne, s),
                    "Маржа": "" if cell.estimate is None else pct_label(cell.estimate.margin),
                    "Примечание": cell.error or "",
                }
                for row in result.rows
                for column, cell in zip(result.columns, row.cells, strict=True)
                for s in (CURRENCY_SYMBOLS[row.option.country.currency],)
            ]
        ),
        hide_index=True,
        width="stretch",
    )
