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

from rentab_core.duties import (
    DEFAULT_QUANTITY,
    QUANTITY_DEFAULTS,
    ApplicantCategory,
    Quantity,
)
from rentab_core.fx import RatesQuote
from rentab_core.models import (
    DEFAULT_TARGET_MARGIN,
    CappedPricing,
    Country,
    Disbursement,
    Employee,
    ExchangeRates,
    Firm,
    FirmFacts,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    LegalForm,
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
# Данные фирмы для проверки применимости режимов (FirmFacts).
FACT_LEGAL_FORM = "fact_legal_form"
FACT_INCOME_RF = "fact_income_rf"
FACT_INCOME_KZ = "fact_income_kz"
FACT_HEADCOUNT = "fact_headcount"
FACT_AGENCY = "fact_agency_contracts"
# Загрузка курсов: дата, последний ответ источника и ошибка.
FX_DATE = "fx_date"
FX_QUOTE = "fx_quote"
FX_ERROR = "fx_error"
# Пошлины: количества (классы, пункты формулы) и категория заявителя по стране.
DUTY_QUANTITY_PREFIX = "duty_qty_"
DUTY_CATEGORY_PREFIX = "duty_category_"

# Модели цены в интерфейсе: у фиксированной цены два способа задания.
PRICING_HOURLY = "hourly"
PRICING_CAPPED = "capped"
PRICING_FIXED = "fixed"
PRICING_AGREED = "agreed"
PRICING_KINDS = (PRICING_HOURLY, PRICING_CAPPED, PRICING_FIXED, PRICING_AGREED)

# Курс «₽ за 1 ₸» по умолчанию — индикативный, как в v0.3; точный курс
# загружается с сайта ЦБ РФ (НБРК) кнопкой на странице «Настройка» или вводится
# вручную. Курс доллара по умолчанию не задан: 0 — «нет курса».
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
    "rf_osno_sc",
    FACT_LEGAL_FORM,
    FACT_INCOME_RF,
    FACT_INCOME_KZ,
    FACT_HEADCOUNT,
    FACT_AGENCY,
    FX_DATE,
)
# Префиксы ключей виджетов, которые создаются по стране и единице.
PERSISTENT_WIDGET_PREFIXES: tuple[str, ...] = (DUTY_QUANTITY_PREFIX, DUTY_CATEGORY_PREFIX)


def persist_widgets() -> None:
    """Сохраняет значения виджетов при переходе между страницами."""
    for key in list(st.session_state.keys()):
        if key in PERSISTENT_WIDGET_KEYS or str(key).startswith(PERSISTENT_WIDGET_PREFIXES):
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


def fx_quote() -> RatesQuote | None:
    """Последние загруженные курсы ЦБ РФ или НБРК."""
    value = st.session_state.get(FX_QUOTE)
    return value if isinstance(value, RatesQuote) else None


def _optional_number(key: str) -> Decimal | None:
    """Число из виджета без значения по умолчанию; None — поле пустое."""
    value = st.session_state.get(key)
    return None if value is None else to_decimal(float(value))


def firm_facts() -> FirmFacts:
    """Данные фирмы для проверки применимости налоговых режимов."""
    headcount = st.session_state.get(FACT_HEADCOUNT)
    return FirmFacts(
        rf_legal_form=LegalForm(st.session_state.get(FACT_LEGAL_FORM, LegalForm.ORGANIZATION)),
        annual_income_rf=_optional_number(FACT_INCOME_RF),
        annual_income_kz=_optional_number(FACT_INCOME_KZ),
        headcount=None if headcount is None else int(headcount),
        agency_contracts=bool(st.session_state.get(FACT_AGENCY, False)),
    )


def firm() -> Firm:
    """Фирма из данных страницы «Настройка»."""
    return Firm(
        name=text(FIRM_NAME),
        jurisdiction=jurisdiction() or Jurisdiction(),
        team=tuple(team()),
        overheads=tuple(overheads()),
        exchange_rates=exchange_rates(),
        facts=firm_facts(),
    )


def duty_quantity_key(country: Country, quantity: Quantity) -> str:
    """Ключ виджета количества (классов, пунктов формулы) для пошлин страны."""
    return f"{DUTY_QUANTITY_PREFIX}{country.value}_{quantity.value}"


def duty_category_key(country: Country) -> str:
    """Ключ виджета категории заявителя для пошлин страны."""
    return f"{DUTY_CATEGORY_PREFIX}{country.value}"


def duty_quantities(country: Country) -> dict[Quantity, int]:
    """Количества для пошлин страны из виджетов «Проекта»."""
    return {
        quantity: int(
            st.session_state.get(
                duty_quantity_key(country, quantity),
                QUANTITY_DEFAULTS.get(quantity, DEFAULT_QUANTITY),
            )
        )
        for quantity in Quantity
    }


def duty_category(country: Country) -> ApplicantCategory:
    """Категория заявителя для пошлин страны; по умолчанию — юрлицо."""
    return ApplicantCategory(
        st.session_state.get(duty_category_key(country), ApplicantCategory.LEGAL_ENTITY)
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
