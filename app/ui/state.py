"""Состояние сессии Streamlit и сборка моделей ядра из него.

Данные фирмы и проекта живут только в `st.session_state` текущей сессии:
приложение ничего не пишет на диск сервера (CLAUDE.md, L-10). Списки
(команда, накладные, этапы, расходы) хранятся сразу моделями ядра; страницы
только добавляют и удаляют элементы, а `scenario()` собирает вход расчёта.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypeVar, cast

import streamlit as st

from rentab_core.models import (
    DEFAULT_BILLABLE_HOURS_PER_MONTH,
    DEFAULT_TARGET_MARGIN,
    Country,
    Disbursement,
    Employee,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    Overhead,
    PricingModel,
    Project,
    Scenario,
    Stage,
)
from rentab_core.money import PERCENT, to_decimal
from rentab_core.results import Estimate
from ui.labels import PRICING_LABELS

# Ключи session_state. Имена виджетов v0.3 сохранены, где это возможно.
JURISDICTION = "jurisdiction"
TEAM = "team"
OVERHEADS = "overheads"
BILLABLE_HOURS = "billable_hours_per_month"
CONTRACT_HOURS = "contract_hours_per_month"
EXCHANGE_RATE = "exchange_rate_rub_per_kzt"
FIRM_NAME = "firm_name"
PROJECT_NAME = "project_name"
PROJECT_CLIENT = "project_client"
PROJECT_DESCRIPTION = "project_description"
PROJECT_STAGES = "project_stages"
SELECTED_DUTIES = "selected_duties"
CUSTOM_EXPENSES = "custom_expenses"
PRICING_MODEL = "pricing_model"
TARGET_MARGIN_PCT = "target_margin"
CALC_COUNTRY = "calc_country_choice"
ESTIMATE = "estimate"

# Курс «1 ₸ = x ₽» по умолчанию — индикативный, как в v0.3 (L-06 → фаза 3).
DEFAULT_EXCHANGE_RATE = 0.18
DEFAULT_TARGET_MARGIN_PCT = int(DEFAULT_TARGET_MARGIN * PERCENT)

ItemT = TypeVar("ItemT")

# Ключи виджетов, значения которых должны пережить переход между страницами.
# Streamlit удаляет ключ виджета, если на текущей странице этого виджета нет;
# повторное присваивание ключа самому себе отменяет эту очистку.
PERSISTENT_WIDGET_KEYS: tuple[str, ...] = (
    "jurisdiction_choice",
    "rf_regime_choice",
    "rf_usn_object",
    "rf_usn_vat",
    "rf_usn_sc",
    "rf_npd_client",
    "kz_form_choice",
    "kz_vat_choice",
    PROJECT_NAME,
    PROJECT_CLIENT,
    PROJECT_DESCRIPTION,
    PRICING_MODEL,
    TARGET_MARGIN_PCT,
    "margin_slider",
    CALC_COUNTRY,
    FIRM_NAME,
)


def persist_widgets() -> None:
    """Сохраняет значения виджетов при переходе между страницами."""
    for key in PERSISTENT_WIDGET_KEYS:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def init_defaults() -> None:
    """Заполняет session_state значениями по умолчанию (не перетирая введённое)."""
    st.session_state.setdefault(TEAM, [])
    st.session_state.setdefault(OVERHEADS, [])
    st.session_state.setdefault(BILLABLE_HOURS, float(DEFAULT_BILLABLE_HOURS_PER_MONTH))
    st.session_state.setdefault(EXCHANGE_RATE, DEFAULT_EXCHANGE_RATE)
    st.session_state.setdefault(PROJECT_STAGES, [])
    st.session_state.setdefault(SELECTED_DUTIES, {country.value: [] for country in Country})
    st.session_state.setdefault(CUSTOM_EXPENSES, [])


def items(key: str, _item_type: type[ItemT]) -> list[ItemT]:
    """Список моделей из session_state (тот же объект: его можно менять на месте)."""
    init_defaults()
    return cast(list[ItemT], st.session_state[key])


def team() -> list[Employee]:
    """Сотрудники фирмы."""
    return items(TEAM, Employee)


def overheads() -> list[Overhead]:
    """Накладные расходы фирмы."""
    return items(OVERHEADS, Overhead)


def stages() -> list[Stage]:
    """Этапы проекта."""
    return items(PROJECT_STAGES, Stage)


def custom_expenses() -> list[Disbursement]:
    """Произвольные расходы проекта."""
    return items(CUSTOM_EXPENSES, Disbursement)


def selected_duties() -> dict[str, list[str]]:
    """Выбранные пошлины по странам: {"RF": [...], "KZ": [...]}."""
    init_defaults()
    return cast(dict[str, list[str]], st.session_state[SELECTED_DUTIES])


def jurisdiction() -> Jurisdiction | None:
    """Юрисдикция, выбранная на странице «Настройка»; None — ещё не выбрана."""
    value = st.session_state.get(JURISDICTION)
    return value if isinstance(value, Jurisdiction) else None


def estimate() -> Estimate | None:
    """Последняя рассчитанная смета."""
    return cast(Estimate | None, st.session_state.get(ESTIMATE))


def number(key: str, default: float) -> Decimal:
    """Число из session_state как Decimal."""
    return to_decimal(float(st.session_state.get(key, default)))


def text(key: str) -> str:
    """Строка из session_state."""
    return str(st.session_state.get(key, ""))


def firm() -> Firm:
    """Фирма из данных страницы «Настройка»."""
    contract_hours = st.session_state.get(CONTRACT_HOURS)
    return Firm(
        name=text(FIRM_NAME),
        jurisdiction=jurisdiction() or Jurisdiction(),
        team=tuple(team()),
        overheads=tuple(overheads()),
        billable_hours_per_month=number(BILLABLE_HOURS, float(DEFAULT_BILLABLE_HOURS_PER_MONTH)),
        contract_hours_per_month=(
            None if contract_hours is None else to_decimal(float(contract_hours))
        ),
    )


def pricing() -> PricingModel:
    """Модель ценообразования; целевая маржа в интерфейсе — целые проценты."""
    if st.session_state.get(PRICING_MODEL) == PRICING_LABELS["fixed"]:
        margin_pct = int(st.session_state.get(TARGET_MARGIN_PCT, DEFAULT_TARGET_MARGIN_PCT))
        return FixedPricing(target_margin=Decimal(margin_pct) / PERCENT)
    return HourlyPricing()


def project(duty_disbursements: list[Disbursement]) -> Project:
    """Проект из данных страницы «Проект».

    Args:
        duty_disbursements: Пошлины из каталогов; идут перед произвольными
            расходами, как в v0.3.
    """
    calc_country = st.session_state.get(CALC_COUNTRY)
    return Project(
        name=text(PROJECT_NAME),
        client=text(PROJECT_CLIENT),
        description=text(PROJECT_DESCRIPTION),
        pricing=pricing(),
        stages=tuple(stages()),
        disbursements=(*duty_disbursements, *custom_expenses()),
        calc_country=None if calc_country is None else Country(calc_country),
    )


def scenario(duty_disbursements: list[Disbursement]) -> Scenario:
    """Вход расчёта сметы."""
    return Scenario(firm=firm(), project=project(duty_disbursements))
