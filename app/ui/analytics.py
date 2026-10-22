"""Общее для страниц аналитики: «Чувствительность», «Сравнение», «План–факт».

Страницы считают по текущим данным активного проекта (`ui.state.scenario`),
а не по последней смете дашборда: кнопка «Рассчитать смету» им не нужна. Если
смета дашборда рассчитана до последних изменений, страница об этом говорит.
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
from ui import state
from ui.labels import PRICING_LABELS, money, pct_label, regime_label

ResultT = TypeVar("ResultT")


def require_scenario() -> Scenario:
    """Сценарий активного проекта; без команды, юрисдикции или часов — подсказка и стоп."""
    if not state.team():
        st.warning("Сначала заполните состав команды на странице **01 Настройка → Команда**.")
        st.stop()
    if state.jurisdiction() is None:
        st.warning(
            "Сначала выберите юрисдикцию на странице **01 Настройка → Юрисдикция и налоги**."
        )
        st.stop()
    if not any(stage.assignments for stage in state.stages()):
        st.info(
            f"В проекте «{state.slot_title(state.active_id())}» нет этапов с часами: "
            "добавьте их на странице **02 Проект**."
        )
        st.stop()
    try:
        return state.scenario()
    except ValidationError:
        st.error("Укажите потолок или согласованную цену больше нуля на странице «Проект».")
        st.stop()


def compute(fn: Callable[[], ResultT]) -> ResultT:
    """Расчёт ядра; цель недостижима или нет курса — сообщение и стоп."""
    try:
        return fn()
    except FixedPriceError as exc:
        st.error(f"Не удалось рассчитать фиксированную цену: {exc}")
        st.stop()
    except CurrencyConversionError as exc:
        st.error(f"{exc}. Курсы задаются на странице «Настройка».")
        st.stop()


def render_header(scenario: Scenario, estimate: Estimate) -> None:
    """Проект, модель цены, режим и NNE плана; предупреждение об устаревшей смете."""
    sym = CURRENCY_SYMBOLS[estimate.currency]
    profile = scenario.firm.jurisdiction.tax_profile(estimate.calc_country)
    st.subheader(estimate.project_name or "Без названия")
    st.caption(
        f"{PRICING_LABELS[state.pricing_kind()]} · {regime_label(profile)} · "
        f"NNE по плану {money(estimate.nne, sym)} ({pct_label(estimate.margin)})"
    )
    saved = state.estimate()
    if saved is not None and saved != estimate:
        st.caption(
            "ℹ️ Смета на дашборде рассчитана до последних изменений проекта; здесь — по "
            "текущим данным."
        )
