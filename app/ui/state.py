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
    DEFAULT_TARGET_MARGIN,
    CappedPricing,
    Country,
    Disbursement,
    Employee,
    ExchangeRates,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    Overhead,
    PricingModel,
    Project,
    Scenario,
    Stage,
    VatMode,
)
from rentab_core.money import PERCENT, Currency, to_decimal
from rentab_core.results import Estimate

# Ключи session_state. Имена виджетов v0.3 сохранены, где это возможно.
JURISDICTION = "jurisdiction"
TEAM = "team"
OVERHEADS = "overheads"
RUB_PER_KZT = "rub_per_kzt"
RUB_PER_USD = "rub_per_usd"
FIRM_NAME = "firm_name"
PROJECT_NAME = "project_name"
PROJECT_CLIENT = "project_client"
PROJECT_DESCRIPTION = "project_description"
PROJECT_STAGES = "project_stages"
SELECTED_DUTIES = "selected_duties"
CUSTOM_EXPENSES = "custom_expenses"
PRICING_MODEL = "pricing_model"
TARGET_MARGIN_PCT = "target_margin"
CAP_AMOUNT = "cap_amount"
AGREED_PRICE = "agreed_price"
VAT_MODE = "vat_mode"
CALC_COUNTRY = "calc_country_choice"
ESTIMATE = "estimate"

# Модели цены в интерфейсе: у фиксированной цены два способа задания.
PRICING_HOURLY = "hourly"
PRICING_CAPPED = "capped"
PRICING_FIXED = "fixed"
PRICING_AGREED = "agreed"
PRICING_KINDS = (PRICING_HOURLY, PRICING_CAPPED, PRICING_FIXED, PRICING_AGREED)

# Курс «₽ за 1 ₸» по умолчанию — индикативный, как в v0.3; загрузка курсов
# ЦБ РФ и НБРК — фаза 3. Курс доллара по умолчанию не задан: 0 — «нет курса».
DEFAULT_RUB_PER_KZT = 0.18
NO_RATE = 0.0
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
    RUB_PER_KZT,
    RUB_PER_USD,
    PROJECT_NAME,
    PROJECT_CLIENT,
    PROJECT_DESCRIPTION,
    PRICING_MODEL,
    TARGET_MARGIN_PCT,
    "margin_slider",
    CAP_AMOUNT,
    AGREED_PRICE,
    VAT_MODE,
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
    st.session_state.setdefault(RUB_PER_KZT, DEFAULT_RUB_PER_KZT)
    st.session_state.setdefault(RUB_PER_USD, NO_RATE)
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


def home_currency(value: Jurisdiction | None) -> Currency:
    """Основная валюта фирмы: тенге для «Только РК», иначе рубль."""
    if value is not None and value.mode == JurisdictionMode.KZ:
        return Currency.KZT
    return Currency.RUB


def estimate() -> Estimate | None:
    """Последняя рассчитанная смета."""
    return cast(Estimate | None, st.session_state.get(ESTIMATE))


def number(key: str, default: float) -> Decimal:
    """Число из session_state как Decimal."""
    return to_decimal(float(st.session_state.get(key, default)))


def text(key: str) -> str:
    """Строка из session_state."""
    return str(st.session_state.get(key, ""))


def _rate(key: str, default: float) -> Decimal | None:
    """Курс из session_state; 0 — курс не задан."""
    value = number(key, default)
    return value if value > 0 else None


def exchange_rates() -> ExchangeRates:
    """Курсы валют фирмы со страницы «Настройка»."""
    return ExchangeRates(
        rub_per_kzt=_rate(RUB_PER_KZT, DEFAULT_RUB_PER_KZT),
        rub_per_usd=_rate(RUB_PER_USD, NO_RATE),
    )


def firm() -> Firm:
    """Фирма из данных страницы «Настройка»."""
    return Firm(
        name=text(FIRM_NAME),
        jurisdiction=jurisdiction() or Jurisdiction(),
        team=tuple(team()),
        overheads=tuple(overheads()),
        exchange_rates=exchange_rates(),
    )


def pricing_kind() -> str:
    """Выбранная модель цены: hourly, capped, fixed или agreed."""
    value = st.session_state.get(PRICING_MODEL, PRICING_HOURLY)
    return value if value in PRICING_KINDS else PRICING_HOURLY


def pricing() -> PricingModel:
    """Модель ценообразования из данных страницы «Проект»."""
    kind = pricing_kind()
    if kind == PRICING_CAPPED:
        return CappedPricing(cap=number(CAP_AMOUNT, 0.0))
    if kind == PRICING_FIXED:
        return FixedPricing()
    if kind == PRICING_AGREED:
        return FixedPricing(price=number(AGREED_PRICE, 0.0))
    return HourlyPricing()


def target_margin() -> Decimal:
    """Целевая маржа; в интерфейсе — целые проценты."""
    margin_pct = int(st.session_state.get(TARGET_MARGIN_PCT, DEFAULT_TARGET_MARGIN_PCT))
    return Decimal(margin_pct) / PERCENT


def vat_mode() -> VatMode:
    """Как указаны цены проекта."""
    return VatMode(st.session_state.get(VAT_MODE, VatMode.ON_TOP.value))


def project(duty_disbursements: list[Disbursement]) -> Project:
    """Проект из данных страницы «Проект».

    Args:
        duty_disbursements: Пошлины из каталогов; идут перед произвольными
            расходами, как в v0.3.

    Raises:
        pydantic.ValidationError: Потолок или согласованная цена не заданы (0).
    """
    calc_country = st.session_state.get(CALC_COUNTRY)
    return Project(
        name=text(PROJECT_NAME),
        client=text(PROJECT_CLIENT),
        description=text(PROJECT_DESCRIPTION),
        pricing=pricing(),
        target_margin=target_margin(),
        vat_mode=vat_mode(),
        stages=tuple(stages()),
        disbursements=(*duty_disbursements, *custom_expenses()),
        calc_country=None if calc_country is None else Country(calc_country),
    )


def scenario(duty_disbursements: list[Disbursement]) -> Scenario:
    """Вход расчёта сметы."""
    return Scenario(firm=firm(), project=project(duty_disbursements))
