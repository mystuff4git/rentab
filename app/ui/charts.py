"""Единый стиль графиков Plotly: структура цены, торнадо, план–факт.

Цвета серий проверены валидатором палитр (OKLab, модель дальтонизма
Machado 2009): соседние цвета различимы при протанопии и дейтеранопии
(ΔE ≥ 10,1) и при обычном зрении (ΔE ≥ 17,6), контраст к светлому, белому и
тёмному фону — не ниже 3:1. Поэтому набор один для обеих тем. Фон, шрифт и
сетку берёт тема Streamlit (`theme="streamlit"`), подписи значений — те же
строки «1 234 567 ₽», что в таблицах (`ui.labels`).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import plotly.graph_objects as go
import streamlit as st

# Структура цены: трудозатраты, накладные, собственные расходы, налог, прибыль.
COLOR_LABOR = "#3767A4"
COLOR_OVERHEADS = "#A280C8"
COLOR_OWN = "#B8892D"
COLOR_TAX = "#A94753"
COLOR_PROFIT = "#46A16B"
# Пара серий: «−X%» и «+X%» в торнадо, «план» и «факт».
COLOR_BASE = "#3767A4"
COLOR_CHANGE = "#B8892D"
# Фон между сегментами (зазор 2 px) — как фон страницы темы.
SURFACE_LIGHT = "#FBF9F5"
SURFACE_DARK = "#1C1A17"
SEGMENT_GAP_PX = 2
BAR_GAP = 0.35
BAR_ROW_PX = 52
CHART_PADDING_PX = 80
STRUCTURE_HEIGHT_PX = 170
ROLES_HEIGHT_PX = 320
MARGIN = {"t": 8, "b": 8, "l": 8, "r": 8}
# Десятичная запятая и неразрывный пробел между разрядами: 1 234 567.
SEPARATORS = ", "
CONFIG = {"displayModeBar": False}


def _surface() -> str:
    """Фон страницы для зазоров между сегментами (тема определяется браузером)."""
    return SURFACE_DARK if st.context.theme.type == "dark" else SURFACE_LIGHT


def _layout(figure: go.Figure, height: int, **extra: object) -> go.Figure:
    figure.update_layout(
        separators=SEPARATORS,
        height=height,
        margin=MARGIN,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0, "title": None},
        hoverlabel={"align": "left"},
        bargap=BAR_GAP,
        **extra,
    )
    figure.update_xaxes(showgrid=True, zeroline=True, title=None)
    figure.update_yaxes(showgrid=False, title=None)
    return figure


def show(figure: go.Figure) -> None:
    """График на странице: ширина по контейнеру, без панели инструментов."""
    st.plotly_chart(figure, width="stretch", theme="streamlit", config=CONFIG)


def price_structure(layers: Sequence[tuple[str, Decimal, str]], axis_title: str) -> go.Figure:
    """Структура выручки: сегменты одной полосы.

    Args:
        layers: (название, сумма, подпись суммы) по порядку; нулевые пропускаются.
        axis_title: Подпись оси (единица).
    """
    colors = (COLOR_LABOR, COLOR_OVERHEADS, COLOR_OWN, COLOR_TAX, COLOR_PROFIT)
    figure = go.Figure()
    surface = _surface()
    for (name, value, text), color in zip(layers, colors, strict=True):
        if value == 0:
            continue
        figure.add_trace(
            go.Bar(
                x=[float(value)],
                y=[""],
                name=name,
                orientation="h",
                marker={"color": color, "line": {"color": surface, "width": SEGMENT_GAP_PX}},
                customdata=[text],
                hovertemplate=f"<b>{name}</b>: %{{customdata}}<extra></extra>",
            )
        )
    _layout(figure, STRUCTURE_HEIGHT_PX, barmode="relative")
    figure.update_xaxes(tickformat=",.0f", title=axis_title)
    figure.update_yaxes(showticklabels=False)
    return figure


def paired_bars(
    categories: Sequence[str],
    series: Sequence[tuple[str, Sequence[Decimal], Sequence[str]]],
    *,
    horizontal: bool,
    axis_title: str,
) -> go.Figure:
    """Две серии рядом: «−X% / +X%» (торнадо) или «план / факт».

    Args:
        categories: Подписи групп; у горизонтального графика — сверху вниз.
        series: (название, значения, подписи значений) — две серии.
        horizontal: Горизонтальные столбики (торнадо).
        axis_title: Подпись оси значений.
    """
    figure = go.Figure()
    colors = (COLOR_BASE, COLOR_CHANGE)
    # Plotly рисует горизонтальные категории снизу вверх: первая — сверху.
    labels = list(reversed(categories)) if horizontal else list(categories)
    for (name, values, texts), color in zip(series, colors, strict=True):
        numbers = [float(value) for value in values]
        custom = list(texts)
        if horizontal:
            numbers.reverse()
            custom.reverse()
        bar = go.Bar(
            x=numbers if horizontal else labels,
            y=labels if horizontal else numbers,
            name=name,
            orientation="h" if horizontal else "v",
            marker={"color": color, "cornerradius": 4},
            customdata=custom,
            hovertemplate=(
                f"<b>%{{{'y' if horizontal else 'x'}}}</b> · {name}: %{{customdata}}<extra></extra>"
            ),
        )
        figure.add_trace(bar)
    height = CHART_PADDING_PX + BAR_ROW_PX * len(categories) if horizontal else ROLES_HEIGHT_PX
    _layout(figure, height, barmode="group")
    if horizontal:
        figure.update_xaxes(tickformat=",.0f", title=axis_title)
    else:
        figure.update_xaxes(showgrid=False, zeroline=False)
        figure.update_yaxes(showgrid=True, tickformat=",.0f", title=axis_title)
    return figure
