"""Компоненты интерфейса: KPI-карточки, пустые состояния, таблицы аналитики.

Стили — в `ui.theme`. KPI-карточка — `st.metric` в контейнере с рамкой: его
подсказка («?») показывает формулу показателя с цифрами проекта
(`ui.formulas`). Таблицы аналитики — HTML: на узком экране каждая строка
становится карточкой «подпись: значение», и горизонтальной прокрутки нет.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

import streamlit as st

from rentab_core.results import StageFlag
from ui.labels import STAGE_FLAG_ICONS, STAGE_FLAG_LABELS

DeltaColor = Literal["normal", "inverse", "off"]
KPI_COLUMNS = 4


class Status(StrEnum):
    """Статус KPI-карточки: цвет полоски сверху."""

    GOOD = "good"
    WARN = "warn"
    BAD = "bad"


FLAG_STATUS: Mapping[StageFlag, Status] = {
    StageFlag.GREEN: Status.GOOD,
    StageFlag.YELLOW: Status.WARN,
    StageFlag.RED: Status.BAD,
}


@dataclass(frozen=True)
class Kpi:
    """Показатель для карточки.

    Attributes:
        key: Уникальный на странице ключ карточки.
        label: Подпись.
        value: Значение, уже отформатированное.
        help: Подсказка: формула и цифры проекта (Markdown).
        status: Цвет полоски; None — без статуса.
        delta: Дельта `st.metric` (с ASCII-минусом).
        delta_color: Цвет дельты.
        caption: Строка под значением.
    """

    key: str
    label: str
    value: str
    help: str
    status: Status | None = None
    delta: str | None = None
    delta_color: DeltaColor = "normal"
    caption: str | None = None


def kpi(item: Kpi) -> None:
    """KPI-карточка."""
    suffix = f"--{item.status.value}" if item.status is not None else ""
    with st.container(border=True, key=f"kpi-{item.key}{suffix}"):
        st.metric(
            item.label,
            item.value,
            delta=item.delta,
            delta_color=item.delta_color,
            help=item.help,
        )
        if item.caption:
            st.caption(item.caption)


def kpi_grid(items: Sequence[Kpi], columns: int = KPI_COLUMNS) -> None:
    """Карточки рядами по `columns`; на узком экране — одна под другой."""
    for start in range(0, len(items), columns):
        for column, item in zip(st.columns(columns), items[start : start + columns], strict=False):
            with column:
                kpi(item)


@dataclass(frozen=True)
class Action:
    """Действие пустого состояния: переход на страницу или кнопка с колбэком."""

    label: str
    page: str | None = None
    on_click: Callable[[], None] | None = None
    icon: str | None = None


def empty_state(key: str, icon: str, title: str, text: str, actions: Sequence[Action] = ()) -> None:
    """Пустое состояние: значок, заголовок, одна фраза и действия."""
    with st.container(border=True, key=f"empty-{key}"):
        st.markdown(
            f'<div class="rt-empty-icon">{icon}</div>\n\n**{html.escape(title)}**',
            unsafe_allow_html=True,
        )
        st.markdown(text)
        for index, action in enumerate(actions):
            if action.page is not None:
                st.page_link(action.page, label=action.label, icon=action.icon)
            elif action.on_click is not None:
                st.button(
                    action.label,
                    on_click=action.on_click,
                    key=f"empty-{key}-{index}",
                    icon=action.icon,
                    type="primary" if index == 0 else "secondary",
                )


# ---------------------------------------------------------------------------
# Таблицы аналитики
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Cell:
    """Ячейка таблицы: текст и оформление.

    Attributes:
        text: Текст (экранируется).
        note: Пояснение мелким шрифтом под текстом.
        flag: Значок светофора перед текстом.
        best: Лучшее значение (жирным).
        current: Текущая ячейка (с фоном).
    """

    text: str
    note: str = ""
    flag: StageFlag | None = None
    best: bool = False
    current: bool = False


CellValue = str | Cell


@dataclass(frozen=True)
class Row:
    """Строка таблицы: ячейки по столбцам, приглушённая или итоговая."""

    cells: Mapping[str, CellValue]
    muted: bool = False
    total: bool = False


@dataclass(frozen=True)
class Table:
    """Таблица аналитики.

    Attributes:
        columns: Заголовки по порядку; первый — «ведущий» (заголовок карточки
            на узком экране).
        rows: Строки (словарь заголовок → ячейка или Row).
        numeric: Столбцы с числами (по правому краю, без переноса).
        key: CSS-класс таблицы для отличия в тестах и стилях.
        stack: На узком экране строки становятся карточками; False — таблица
            остаётся таблицей (для двух-четырёх узких столбцов).
    """

    columns: Sequence[str]
    rows: Sequence[Mapping[str, CellValue] | Row]
    numeric: frozenset[str] = field(default_factory=frozenset)
    key: str = ""
    stack: bool = True


def flag_html(flag: StageFlag) -> str:
    """Значок и подпись статуса светофора."""
    status = FLAG_STATUS[flag].value
    icon = STAGE_FLAG_ICONS[flag]
    return (
        f'<span class="rt-flag rt-flag--{status}" aria-hidden="true">{icon}</span>'
        f"{STAGE_FLAG_LABELS[flag]}"
    )


def _cell_html(column: str, value: CellValue, numeric: bool, lead: bool) -> str:
    cell = value if isinstance(value, Cell) else Cell(text=value)
    classes = [
        name
        for name, on in (
            ("num", numeric),
            ("rt-lead", lead),
            ("rt-best", cell.best),
            ("rt-current", cell.current),
        )
        if on
    ]
    content = html.escape(cell.text)
    if cell.flag is not None:
        content = flag_html(cell.flag) + (f" {content}" if cell.text else "")
    if cell.note:
        content += f'<span class="rt-note">{html.escape(cell.note)}</span>'
    class_attr = f' class="{" ".join(classes)}"' if classes else ""
    return f'<td data-label="{html.escape(column)}"{class_attr}>{content}</td>'


def table_html(table: Table) -> str:
    """HTML таблицы: на узком экране строки — карточки (`ui.theme`)."""
    num_class = ' class="num"'
    head = "".join(
        f"<th{num_class if column in table.numeric else ''}>{html.escape(column)}</th>"
        for column in table.columns
    )
    body = []
    for item in table.rows:
        row = item if isinstance(item, Row) else Row(cells=item)
        classes = [name for name, on in (("rt-muted", row.muted), ("rt-total", row.total)) if on]
        class_attr = f' class="{" ".join(classes)}"' if classes else ""
        cells = "".join(
            _cell_html(column, row.cells.get(column, ""), column in table.numeric, index == 0)
            for index, column in enumerate(table.columns)
        )
        body.append(f"<tr{class_attr}>{cells}</tr>")
    key_class = f" rt-table--{table.key}" if table.key else ""
    if not table.stack:
        key_class += " rt-flat"
    return (
        f'<div class="rt-table{key_class}"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def render_table(table: Table) -> None:
    """Таблица аналитики на странице."""
    st.html(table_html(table))
