"""Общее для страниц расчёта: «Результат», «Сравнение», «План–факт».

Страницы считают по текущим данным активного проекта (`ui.state.scenario`):
смета не хранится, поэтому устаревшей она не бывает. Если считать не из чего,
страница показывает пустое состояние с переходом на нужный шаг.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import streamlit as st
from pydantic import ValidationError

from rentab_core.errors import CurrencyConversionError, FixedPriceError
from rentab_core.models import Scenario
from rentab_core.money import CURRENCY_SYMBOLS
from rentab_core.results import Estimate
from ui import nav, state
from ui.components import Action, empty_state
from ui.labels import PRICING_LABELS, money, pct_label, regime_label

ResultT = TypeVar("ResultT")


def require_firm(key: str) -> None:
    """Без команды или юрисдикции — пустое состояние с переходом на шаг «Фирма» и стоп."""
    if state.team() and state.jurisdiction() is not None:
        return
    text = (
        "Сначала заполните состав команды на шаге «Фирма»: себестоимость часа считается "
        "по сотрудникам. Или загрузите демо-фирму на главной."
        if not state.team()
        else "Сначала выберите юрисдикцию и налоговый режим на шаге «Фирма»."
    )
    empty_state(
        key,
        "👥",
        "Фирма не заполнена",
        text,
        (
            Action("Перейти к шагу «Фирма»", page=nav.FIRM, icon=":material/arrow_forward:"),
            Action("Демо-фирма на главной", page=nav.HOME, icon=":material/home:"),
        ),
    )
    st.stop()


def require_scenario(key: str) -> Scenario:
    """Сценарий активного проекта; нечего считать — пустое состояние и стоп."""
    require_firm(key)
    if not any(stage.assignments for stage in state.stages()):
        empty_state(
            key,
            "🗂️",
            f"В проекте «{state.slot_title(state.active_id())}» нет этапов с часами",
            "Добавьте этапы и исполнителей на шаге «Проект» — смета появится сразу.",
            (Action("Перейти к шагу «Проект»", page=nav.PROJECT, icon=":material/arrow_forward:"),),
        )
        st.stop()
    try:
        return state.scenario()
    except ValidationError:
        st.error(state.PRICE_MISSING, icon="⛔")
        st.stop()


def compute(scenario: Scenario, fn: Callable[[], ResultT]) -> ResultT:
    """Расчёт ядра; цель недостижима или нет курса — сообщение и стоп."""
    try:
        return fn()
    except (FixedPriceError, CurrencyConversionError) as exc:
        st.error(state.estimate_error(exc, scenario), icon="⛔")
        st.stop()


def scenario_regime(scenario: Scenario) -> str:
    """Подпись режима, по которому считается смета (L-18)."""
    return regime_label(state.calc_profile(scenario))


def render_header(scenario: Scenario, estimate: Estimate, *, with_nne: bool = True) -> None:
    """Название проекта, клиент, модель цены, режим и, по желанию, NNE плана."""
    sym = CURRENCY_SYMBOLS[estimate.currency]
    st.subheader(estimate.project_name or state.slot_title(state.active_id()))
    parts = [part for part in (estimate.client,) if part]
    parts += [PRICING_LABELS[state.pricing_kind()], scenario_regime(scenario)]
    if with_nne:
        parts.append(f"NNE по плану {money(estimate.nne, sym)} ({pct_label(estimate.margin)})")
    st.caption(" · ".join(parts))
