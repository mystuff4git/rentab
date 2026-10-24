"""Шаг 4 «Сравнение»: модели цены × налоговые режимы (docs/methodology.md, раздел 19).

Матрица «модель цены × налоговый режим» для активного проекта: строки —
режимы стран юрисдикции фирмы, столбцы — почасовая, с потолком, «издержки +
маржа» и согласованная цена (если она есть). Недоступные режимы помечены
причиной из валидатора применимости, их цифры приглушены. Матрица — HTML: на
узком экране каждый режим — карточка. Расчёт — `rentab_core.analysis.compare`;
проект не меняется.
"""

from __future__ import annotations

from decimal import Decimal

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
from ui import nav, state
from ui.analytics import compute, render_header, require_scenario
from ui.components import Cell, Row, Table, render_table
from ui.labels import (
    COMPARE_METRICS,
    COUNTRY_LABELS,
    DASH,
    PRICING_KIND_LABELS,
    SEVERITY_ICONS,
    money,
    pct_label,
    regime_label,
)

DEFAULT_METRIC = "nne"
CURRENT_MARK = "▶ "
AVAILABLE_LABEL = "✅ доступен"
UNAVAILABLE_LABEL = "⛔ недоступен"
CAVEAT_LABEL = "⚠️ с оговорками"
CHECK_LABEL = "ℹ️ проверить условия"
UNREACHABLE_SHORT = "цель недостижима"
CAP_MISSING_SHORT = "задайте потолок"
REGIME_COLUMN = "Режим"
AVAILABILITY_COLUMN = "Доступность"
CAP_STEP = 10_000.0

st.title("Сравнение")
st.caption("Матрица «модель цены × налоговый режим» для активного проекта.")
scenario = require_scenario("compare")
base = compute(scenario, lambda: calculate_estimate(scenario))
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
    col_cap, _ = st.columns([1, 2])
    col_cap.number_input(
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


def short_error(cell: ComparisonCell) -> str:
    """Короткая причина пустой ячейки: подпись режима ядра в интерфейс не попадает (L-18)."""
    error = cell.error or ""
    if error == CAP_MISSING:
        return CAP_MISSING_SHORT
    return UNREACHABLE_SHORT if "недостижима" in error else error


def value_text(cell: ComparisonCell, currency: Currency) -> str:
    """Значение показателя в ячейке; у сумм в другой валюте — ещё и в валюте проекта."""
    if cell.estimate is None:
        return short_error(cell)
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
    titles = [
        (CURRENT_MARK if column.is_current else "") + PRICING_KIND_LABELS[column.kind]
        for column in comparison.columns
    ]
    rows = []
    for index, row in indexed:
        mark = CURRENT_MARK if row.option.is_current else ""
        cells: dict[str, str | Cell] = {
            REGIME_COLUMN: mark + regime_label(row.option.profile),
            AVAILABILITY_COLUMN: availability(row),
        }
        for position, (title, cell) in enumerate(zip(titles, row.cells, strict=True)):
            text = value_text(cell, currency)
            cells[title] = Cell(
                text if row.available else f"({text})",
                best=(index, position) in best and metric in ("nne", "margin"),
                current=row.option.is_current and comparison.columns[position].is_current,
            )
        rows.append(Row(cells=cells, muted=not row.available))
    render_table(
        Table(
            columns=[REGIME_COLUMN, AVAILABILITY_COLUMN, *titles],
            rows=rows,
            numeric=frozenset(titles),
            key=f"compare-{country.value}",
        )
    )


countries = scenario.firm.jurisdiction.countries
for country in countries:
    if len(countries) > 1:
        st.markdown(f"#### {COUNTRY_LABELS[country]}, {CURRENCY_SYMBOLS[country.currency]}")
    render_group(result, country)

st.caption(
    f"{CURRENT_MARK.strip()} — текущий режим и модель цены проекта (ячейка с фоном — смета "
    "проекта). Жирным — лучшее NNE или маржа среди доступных режимов в столбце. В скобках и "
    "бледным — цифры недоступного режима: смета считается, но применять режим фирма не может."
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
    all_rows = []
    for row in result.rows:
        symbol = CURRENCY_SYMBOLS[row.option.country.currency]
        for column, cell in zip(result.columns, row.cells, strict=True):
            item = cell.estimate
            all_rows.append(
                Row(
                    cells={
                        "Режим и модель": Cell(
                            regime_label(row.option.profile),
                            note=PRICING_KIND_LABELS[column.kind]
                            + ("" if row.available else " · режим недоступен"),
                        ),
                        "Цена без НДС": DASH if item is None else money(item.revenue, symbol),
                        "НДС": DASH if item is None else money(item.vat, symbol),
                        "К оплате": DASH if item is None else money(item.total_client, symbol),
                        "Налог": DASH if item is None else money(item.tax.tax, symbol),
                        "NNE": short_error(cell) if item is None else money(item.nne, symbol),
                        "Маржа": DASH if item is None else pct_label(item.margin),
                    },
                    muted=not row.available,
                )
            )
    render_table(
        Table(
            columns=["Режим и модель", "Цена без НДС", "НДС", "К оплате", "Налог", "NNE", "Маржа"],
            rows=all_rows,
            numeric=frozenset({"Цена без НДС", "НДС", "К оплате", "Налог", "NNE", "Маржа"}),
            key="compare-all",
        )
    )

nav.footer(nav.COMPARE)
