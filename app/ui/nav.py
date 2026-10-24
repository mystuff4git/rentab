"""Навигация-визард: «Фирма → Проект → Результат → Сравнение → План–факт».

Меню — `st.navigation(position="top")` в точке входа (`Home.py`). Значок шага
показывает его состояние: ✓ — заполнено, цифра — ещё нет. Внизу каждого шага —
переходы «← назад» и «далее →». Переход между страницами — только через
`st.page_link` и меню: обычная ссылка перезагрузила бы страницу и начала
новую сессию, а данные живут в сессии.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from ui import state

HOME = "views/home.py"
FIRM = "views/firm.py"
PROJECT = "views/project.py"
RESULT = "views/result.py"
COMPARE = "views/compare.py"
PLAN_FACT = "views/plan_fact.py"
DONE_ICON = ":material/check_circle:"
HOME_ICON = ":material/home:"


@dataclass(frozen=True)
class Step:
    """Шаг визарда: файл страницы, подпись, адрес и номер."""

    path: str
    title: str
    url: str
    number: int


STEPS: tuple[Step, ...] = (
    Step(FIRM, "Фирма", "firm", 1),
    Step(PROJECT, "Проект", "project", 2),
    Step(RESULT, "Результат", "result", 3),
    Step(COMPARE, "Сравнение", "compare", 4),
    Step(PLAN_FACT, "План–факт", "plan-fact", 5),
)


def firm_ready() -> bool:
    """Фирма заполнена: выбрана юрисдикция и есть хотя бы один сотрудник."""
    return state.jurisdiction() is not None and bool(state.team())


def project_ready() -> bool:
    """Активный проект заполнен: есть часы, а потолок или цена заданы, если нужны."""
    has_hours = any(item.hours > 0 for stage in state.stages() for item in stage.assignments)
    return has_hours and state.pricing_complete(state.session())


def done() -> dict[str, bool]:
    """Какие шаги заполнены: фирма, проект, факт проекта."""
    return {
        FIRM: firm_ready(),
        PROJECT: project_ready(),
        PLAN_FACT: state.actuals() is not None,
    }


def step_icon(step: Step, finished: bool) -> str:
    """Значок шага в меню: ✓ или его номер."""
    return DONE_ICON if finished else f":material/counter_{step.number}:"


def pages() -> list[st.Page]:
    """Страницы меню: главная и шаги со значками состояния."""
    finished = done()
    return [
        st.Page(HOME, title="Главная", icon=HOME_ICON, default=True),
        *(
            st.Page(
                step.path,
                title=step.title,
                icon=step_icon(step, finished.get(step.path, False)),
                url_path=step.url,
            )
            for step in STEPS
        ),
    ]


def step_of(path: str) -> Step:
    """Шаг по файлу страницы."""
    return next(step for step in STEPS if step.path == path)


def footer(path: str) -> None:
    """Переходы к соседним шагам визарда."""
    index = STEPS.index(step_of(path))
    st.divider()
    col_back, col_next = st.columns(2)
    if index > 0:
        previous = STEPS[index - 1]
        col_back.page_link(
            previous.path, label=f"Назад: {previous.title}", icon=":material/arrow_back:"
        )
    if index < len(STEPS) - 1:
        following = STEPS[index + 1]
        col_next.page_link(
            following.path, label=f"Далее: {following.title}", icon=":material/arrow_forward:"
        )
