"""Состояние сессии Streamlit и сборка моделей ядра из него.

Данные фирмы и проектов живут только в `st.session_state` текущей сессии:
приложение ничего не пишет на диск сервера (CLAUDE.md, L-10). Сохранить их
можно только в файл JSON, который пользователь скачивает сам (`rentab_core.io`).

Фирма и активный проект хранятся в ключах виджетов и в списках моделей ядра;
страницы только добавляют и удаляют элементы. Проектов в сессии несколько:
неактивные лежат в слотах (`ProjectSlot`) снимками тех же ключей. При
переключении снимок активного проекта сохраняется в его слот, а значения
выбранного раскладываются по ключам — в колбэке, до создания виджетов.

Модели собираются чистыми функциями от словаря значений (`project_record`,
`project_values`, `firm_values`): их можно проверить без Streamlit, и путь
«запись → значения → запись» данных не теряет.

Смета не хранится: страницы считают её по текущим значениям проекта
(`estimate_outcome`), поэтому устаревшей сметы не бывает.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypeVar, cast

import streamlit as st
from pydantic import ValidationError

from rentab_core.analysis.plan_fact import actuals_from_plan, plan_pairs
from rentab_core.analysis.sensitivity import DEFAULT_TORNADO_DEVIATION, WhatIf
from rentab_core.duties import (
    DEFAULT_QUANTITY,
    QUANTITY_DEFAULTS,
    ApplicantCategory,
    Quantity,
)
from rentab_core.errors import CurrencyConversionError, FixedPriceError
from rentab_core.estimate import calculate_estimate
from rentab_core.fx import RatesQuote
from rentab_core.io import DutySelection, ProjectRecord, Workspace
from rentab_core.models import (
    DEFAULT_KZ_TAX_PROFILE,
    DEFAULT_RF_TAX_PROFILE,
    DEFAULT_TARGET_MARGIN,
    ActualHours,
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
    KzOurProfile,
    LegalForm,
    MonthlyCost,
    NpdProfile,
    OsnoProfile,
    Overhead,
    PricingModel,
    Project,
    ProjectActuals,
    Role,
    Salary,
    Scenario,
    Stage,
    TaxProfile,
    UsnProfile,
    VatMode,
)
from rentab_core.money import ONE, PERCENT, Currency, format_number, round_to, to_decimal
from rentab_core.results import Estimate
from ui.labels import fixed_price_error

Values = Mapping[str, Any]

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
MARGIN_SLIDER = "margin_slider"
CAP_AMOUNT = "cap_amount"
AGREED_PRICE = "agreed_price"
VAT_MODE = "vat_mode"
CALC_COUNTRY = "calc_country_choice"
# Вкладка страницы «Результат»: «Смета» или «Чувствительность».
RESULT_TAB = "result_tab"
# Виджеты юрисдикции на шаге «Фирма».
JURISDICTION_CHOICE = "jurisdiction_choice"
RF_REGIME = "rf_regime_choice"
RF_USN_OBJECT = "rf_usn_object"
RF_USN_VAT = "rf_usn_vat"
RF_USN_SC = "rf_usn_sc"
RF_OSNO_SC = "rf_osno_sc"
RF_NPD_CLIENT = "rf_npd_client"
KZ_FORM = "kz_form_choice"
KZ_VAT = "kz_vat_choice"
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
# Редактор накладных: ключ виджета и его неизменяемая база (L-21).
OVERHEADS_EDITOR = "overheads_editor"
OVERHEADS_EDITOR_BASE = "overheads_editor_base"
# Пошлины: количества (классы, пункты формулы) и категория заявителя по стране.
DUTY_QUANTITY_PREFIX = "duty_qty_"
DUTY_CATEGORY_PREFIX = "duty_category_"
DUTY_MULTISELECT_PREFIX = "duties_multiselect_"
# План–факт: факт проекта (None — не введён) и сумма, выставленная вручную.
ACTUAL_HOURS = "actual_hours"
ACTUAL_BILLED_MANUAL = "actual_billed_manual"
ACTUAL_BILLED = "actual_billed"
# Редактор факта: неизменяемая база и номер ключа виджета (L-21). Номер растёт при
# загрузке проекта и при сбросе факта: новый виджет не получает правки другого
# проекта, которые браузер хранит у прежнего.
ACTUALS_EDITOR_BASE = "actuals_editor_base"
ACTUALS_EDITOR_NONCE = "actuals_editor_nonce"
ACTUALS_TABLE_PREFIX = "actuals_table_"
# «Что если» и торнадо, проценты: у каждого проекта свои, в файл не попадают.
WHATIF_PREFIX = "whatif_"
WHATIF_HOURS = "whatif_hours"
WHATIF_BILLING_RATES = "whatif_billing_rates"
WHATIF_UTILIZATION = "whatif_utilization"
WHATIF_OVERHEADS = "whatif_overheads"
WHATIF_PRICE = "whatif_price"
WHATIF_FX_PREFIX = "whatif_fx_"
TORNADO_PCT = "tornado_pct"
# Сравнение: потолок для сравнения — у проекта; показатель и «все варианты» — общие.
COMPARE_CAP = "compare_cap"
COMPARE_METRIC = "compare_metric"
COMPARE_ALL_VARIANTS = "compare_all_variants"
# Карточка сотрудника на шаге «Фирма» (добавление и правка).
MEMBER_NAME = "add_member_name"
MEMBER_ROLE = "add_member_role"
MEMBER_BILLING = "add_member_billing"
MEMBER_CURRENCY = "add_member_currency"
MEMBER_COMPENSATION = "add_member_compensation"
MEMBER_GROSS = "add_member_gross_salary"
MEMBER_RATE_PCT = "add_member_employer_rate_pct"
MEMBER_MONTHLY = "add_member_monthly_cost"
MEMBER_HOURS = "add_member_contract_hours"
MEMBER_UTILIZATION = "add_member_utilization_pct"
MEMBER_FROM_PROFIT = "add_member_paid_from_profit"
# Ставку взносов изменили руками: не пересчитывать её по окладу.
MEMBER_RATE_MANUAL = "add_member_rate_manual"
# Имя сотрудника, которого правит карточка; нет ключа — карточка добавляет нового.
EDIT_MEMBER = "edit_member"
COMPENSATION_SALARY = "salary"
COMPENSATION_MONTHLY = "monthly"
# Проекты сессии: слоты, активный проект, счётчик номеров, переключатель.
PROJECTS = "projects"
ACTIVE_PROJECT = "active_project"
NEXT_PROJECT_ID = "next_project_id"
PROJECT_SWITCH = "project_switch"

# Модели цены в интерфейсе: у фиксированной цены два способа задания.
PRICING_HOURLY = "hourly"
PRICING_CAPPED = "capped"
PRICING_FIXED = "fixed"
PRICING_AGREED = "agreed"
PRICING_KINDS = (PRICING_HOURLY, PRICING_CAPPED, PRICING_FIXED, PRICING_AGREED)

# Курс «₽ за 1 ₸» по умолчанию — индикативный, как в v0.3; точный курс
# загружается с сайта ЦБ РФ (НБРК) кнопкой на шаге «Фирма» или вводится
# вручную. Курс доллара по умолчанию не задан: 0 — «нет курса».
DEFAULT_RUB_PER_KZT = 0.18
NO_RATE = 0.0
DEFAULT_TARGET_MARGIN_PCT = int(DEFAULT_TARGET_MARGIN * PERCENT)
# Поле и слайдер целевой маржи на шаге «Проект», целые проценты
# (слайдер — как в v0.3, 02_Проект.py:67–70; поле допускает значения шире).
TARGET_MARGIN_INPUT_MIN_PCT = 0
TARGET_MARGIN_INPUT_MAX_PCT = 99
TARGET_MARGIN_SLIDER_MIN_PCT = 0
TARGET_MARGIN_SLIDER_MAX_PCT = 80
COPY_SUFFIX = " (копия)"
# Отклонение для торнадо по умолчанию, % (rentab_core.analysis.sensitivity).
DEFAULT_TORNADO_PCT = int(DEFAULT_TORNADO_DEVIATION * PERCENT)

# Значения проекта: снимок этих ключей — состояние проекта в его слоте.
# При загрузке проекта каждый ключ задаётся явно: браузер хранит значение
# виджета, который остаётся на экране, и не заменяет его новым `default`.
PROJECT_KEYS: tuple[str, ...] = (
    PROJECT_NAME,
    PROJECT_CLIENT,
    PROJECT_DESCRIPTION,
    PRICING_MODEL,
    TARGET_MARGIN_PCT,
    MARGIN_SLIDER,
    CAP_AMOUNT,
    AGREED_PRICE,
    VAT_MODE,
    CALC_COUNTRY,
    PROJECT_STAGES,
    SELECTED_DUTIES,
    CUSTOM_EXPENSES,
    ACTUAL_HOURS,
    ACTUAL_BILLED_MANUAL,
    ACTUAL_BILLED,
    TORNADO_PCT,
    COMPARE_CAP,
)
PROJECT_PREFIXES: tuple[str, ...] = (
    DUTY_QUANTITY_PREFIX,
    DUTY_CATEGORY_PREFIX,
    DUTY_MULTISELECT_PREFIX,
    WHATIF_PREFIX,
)
# Значения фирмы: заменяются при загрузке демо-фирмы и импорте.
FIRM_KEYS: tuple[str, ...] = (
    JURISDICTION,
    TEAM,
    OVERHEADS,
    RUB_PER_KZT,
    RUB_PER_USD,
    FIRM_NAME,
    JURISDICTION_CHOICE,
    RF_REGIME,
    RF_USN_OBJECT,
    RF_USN_VAT,
    RF_USN_SC,
    RF_OSNO_SC,
    RF_NPD_CLIENT,
    KZ_FORM,
    KZ_VAT,
    FACT_LEGAL_FORM,
    FACT_INCOME_RF,
    FACT_INCOME_KZ,
    FACT_HEADCOUNT,
    FACT_AGENCY,
    FX_DATE,
    FX_QUOTE,
    FX_ERROR,
    OVERHEADS_EDITOR,
    OVERHEADS_EDITOR_BASE,
)

ItemT = TypeVar("ItemT")

# Ключи виджетов, значения которых должны пережить переход между страницами.
# Streamlit удаляет ключ виджета, если на текущей странице этого виджета нет;
# повторное присваивание ключа самому себе отменяет эту очистку.
PERSISTENT_WIDGET_KEYS: tuple[str, ...] = (
    JURISDICTION_CHOICE,
    RF_REGIME,
    RF_USN_OBJECT,
    RF_USN_VAT,
    RF_USN_SC,
    RF_NPD_CLIENT,
    KZ_FORM,
    KZ_VAT,
    RUB_PER_KZT,
    RUB_PER_USD,
    PROJECT_NAME,
    PROJECT_CLIENT,
    PROJECT_DESCRIPTION,
    PRICING_MODEL,
    TARGET_MARGIN_PCT,
    MARGIN_SLIDER,
    CAP_AMOUNT,
    AGREED_PRICE,
    VAT_MODE,
    CALC_COUNTRY,
    FIRM_NAME,
    RF_OSNO_SC,
    FACT_LEGAL_FORM,
    FACT_INCOME_RF,
    FACT_INCOME_KZ,
    FACT_HEADCOUNT,
    FACT_AGENCY,
    FX_DATE,
    ACTUAL_BILLED_MANUAL,
    ACTUAL_BILLED,
    TORNADO_PCT,
    COMPARE_CAP,
    COMPARE_METRIC,
    COMPARE_ALL_VARIANTS,
    RESULT_TAB,
)
# Префиксы ключей виджетов, которые создаются по стране, единице или фактору.
PERSISTENT_WIDGET_PREFIXES: tuple[str, ...] = (
    DUTY_QUANTITY_PREFIX,
    DUTY_CATEGORY_PREFIX,
    WHATIF_PREFIX,
)


@dataclass
class ProjectSlot:
    """Проект сессии.

    Attributes:
        id: Номер проекта в сессии («p1», «p2»…), в файл не попадает.
        values: Снимок значений проекта (`PROJECT_KEYS`). У активного проекта
            устарел: его значения — в ключах виджетов.
    """

    id: str
    values: dict[str, Any]


def session() -> Values:
    """`st.session_state` как словарь значений."""
    return cast(Values, st.session_state)


def _values(values: Values | None) -> Values:
    """Переданные значения или, если их нет, значения сессии (пустой словарь — тоже значения)."""
    return session() if values is None else values


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
    st.session_state.setdefault(ACTUAL_HOURS, None)
    st.session_state.setdefault(TORNADO_PCT, DEFAULT_TORNADO_PCT)
    for key in WHATIF_KEYS:
        st.session_state.setdefault(key, 0)
    projects()


def items(key: str, _item_type: type[ItemT]) -> list[ItemT]:
    """Список моделей из session_state (тот же объект: его можно менять на месте)."""
    if key not in st.session_state:
        init_defaults()
    return cast(list[ItemT], st.session_state[key])


def team() -> list[Employee]:
    """Сотрудники фирмы."""
    return items(TEAM, Employee)


def overheads() -> list[Overhead]:
    """Накладные расходы фирмы."""
    return items(OVERHEADS, Overhead)


def stages() -> list[Stage]:
    """Этапы активного проекта."""
    return items(PROJECT_STAGES, Stage)


def custom_expenses() -> list[Disbursement]:
    """Произвольные расходы активного проекта."""
    return items(CUSTOM_EXPENSES, Disbursement)


def selected_duties() -> dict[str, list[str]]:
    """Выбранные пошлины активного проекта по странам: {"RF": [...], "KZ": [...]}."""
    if SELECTED_DUTIES not in st.session_state:
        init_defaults()
    return cast(dict[str, list[str]], st.session_state[SELECTED_DUTIES])


def jurisdiction(values: Values | None = None) -> Jurisdiction | None:
    """Юрисдикция, выбранная на шаге «Фирма»; None — ещё не выбрана."""
    value = _values(values).get(JURISDICTION)
    return value if isinstance(value, Jurisdiction) else None


def home_currency(value: Jurisdiction | None) -> Currency:
    """Основная валюта фирмы: тенге для «Только РК», иначе рубль."""
    if value is not None and value.mode == JurisdictionMode.KZ:
        return Currency.KZT
    return Currency.RUB


def number(key: str, default: float, values: Values | None = None) -> Decimal:
    """Число из значений как Decimal."""
    return to_decimal(float(_values(values).get(key, default)))


def text(key: str, values: Values | None = None) -> str:
    """Строка из значений."""
    return str(_values(values).get(key, ""))


# ---------------------------------------------------------------------------
# Фирма
# ---------------------------------------------------------------------------
def _rate(key: str, default: float, values: Values) -> Decimal | None:
    """Курс из значений; 0 — курс не задан."""
    value = number(key, default, values)
    return value if value > 0 else None


def exchange_rates(values: Values | None = None) -> ExchangeRates:
    """Курсы валют фирмы с шага «Фирма»."""
    values = _values(values)
    return ExchangeRates(
        rub_per_kzt=_rate(RUB_PER_KZT, DEFAULT_RUB_PER_KZT, values),
        rub_per_usd=_rate(RUB_PER_USD, NO_RATE, values),
    )


def fx_quote(values: Values | None = None) -> RatesQuote | None:
    """Последние загруженные курсы ЦБ РФ или НБРК."""
    value = _values(values).get(FX_QUOTE)
    return value if isinstance(value, RatesQuote) else None


def _optional_number(key: str, values: Values) -> Decimal | None:
    """Число из виджета без значения по умолчанию; None — поле пустое."""
    value = values.get(key)
    return None if value is None else to_decimal(float(value))


def firm_facts(values: Values | None = None) -> FirmFacts:
    """Данные фирмы для проверки применимости налоговых режимов."""
    values = _values(values)
    headcount = values.get(FACT_HEADCOUNT)
    return FirmFacts(
        rf_legal_form=LegalForm(values.get(FACT_LEGAL_FORM, LegalForm.ORGANIZATION)),
        annual_income_rf=_optional_number(FACT_INCOME_RF, values),
        annual_income_kz=_optional_number(FACT_INCOME_KZ, values),
        headcount=None if headcount is None else int(headcount),
        agency_contracts=bool(values.get(FACT_AGENCY, False)),
    )


def firm(values: Values | None = None) -> Firm:
    """Фирма из данных шага «Фирма»."""
    values = _values(values)
    return Firm(
        name=text(FIRM_NAME, values),
        jurisdiction=jurisdiction(values) or Jurisdiction(),
        team=tuple(values.get(TEAM, ())),
        overheads=tuple(values.get(OVERHEADS, ())),
        exchange_rates=exchange_rates(values),
        facts=firm_facts(values),
    )


def normalized_jurisdiction(value: Jurisdiction) -> Jurisdiction:
    """Юрисдикция такой, какой её соберёт шаг «Фирма»: режимы стран заданы явно."""
    countries = value.countries
    return Jurisdiction(
        mode=value.mode,
        rf=(value.rf or DEFAULT_RF_TAX_PROFILE) if Country.RF in countries else None,
        kz=(value.kz or DEFAULT_KZ_TAX_PROFILE) if Country.KZ in countries else None,
    )


def _optional_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def firm_values(value: Firm, quote: RatesQuote | None = None) -> dict[str, Any]:
    """Значения ключей шага «Фирма» для фирмы и загруженных курсов."""
    jurisdiction_ = normalized_jurisdiction(value.jurisdiction)
    facts = value.facts
    result: dict[str, Any] = {
        JURISDICTION: jurisdiction_,
        JURISDICTION_CHOICE: jurisdiction_.mode,
        TEAM: list(value.team),
        OVERHEADS: list(value.overheads),
        RUB_PER_KZT: _optional_float(value.exchange_rates.rub_per_kzt) or NO_RATE,
        RUB_PER_USD: _optional_float(value.exchange_rates.rub_per_usd) or NO_RATE,
        FIRM_NAME: value.name,
        FACT_LEGAL_FORM: facts.rf_legal_form,
        FACT_INCOME_RF: _optional_float(facts.annual_income_rf),
        FACT_INCOME_KZ: _optional_float(facts.annual_income_kz),
        FACT_HEADCOUNT: facts.headcount,
        FACT_AGENCY: facts.agency_contracts,
    }
    rf = jurisdiction_.rf
    if rf is not None:
        result[RF_REGIME] = rf.regime
    if isinstance(rf, UsnProfile):
        result |= {
            RF_USN_OBJECT: rf.tax_object,
            RF_USN_VAT: rf.vat,
            RF_USN_SC: rf.social_contributions,
        }
    elif isinstance(rf, OsnoProfile):
        result[RF_OSNO_SC] = rf.social_contributions
    elif isinstance(rf, NpdProfile):
        result[RF_NPD_CLIENT] = rf.client_type
    kz = jurisdiction_.kz
    if isinstance(kz, KzOurProfile):
        result |= {KZ_FORM: kz.form, KZ_VAT: kz.vat}
    if quote is not None:
        result |= {FX_QUOTE: quote, FX_DATE: quote.requested}
    return result


# ---------------------------------------------------------------------------
# Карточка сотрудника
# ---------------------------------------------------------------------------
def card_values(employee: Employee) -> dict[str, Any]:
    """Значения карточки «Фирма → Команда» для правки сотрудника."""
    result: dict[str, Any] = {
        MEMBER_NAME: employee.name,
        MEMBER_ROLE: employee.role,
        MEMBER_BILLING: float(employee.billing_rate),
        MEMBER_CURRENCY: employee.currency.value,
        MEMBER_HOURS: float(employee.contract_hours_per_month),
        MEMBER_UTILIZATION: float(employee.target_utilization * PERCENT),
        MEMBER_FROM_PROFIT: employee.paid_from_profit,
        MEMBER_GROSS: 0.0,
        MEMBER_MONTHLY: 0.0,
    }
    compensation = employee.compensation
    if isinstance(compensation, Salary):
        result |= {
            MEMBER_COMPENSATION: COMPENSATION_SALARY,
            MEMBER_GROSS: float(compensation.gross_salary),
            MEMBER_RATE_PCT: float(compensation.employer_contribution_rate * PERCENT),
        }
    else:
        result[MEMBER_COMPENSATION] = COMPENSATION_MONTHLY
        result[MEMBER_MONTHLY] = float(compensation.amount)
    return result


def card_employee(values: Values | None = None) -> Employee:
    """Сотрудник из значений карточки (имя без пробелов по краям; не проверяется на пустоту).

    Raises:
        pydantic.ValidationError: Значения вне допустимых границ.
    """
    values = _values(values)
    compensation: Salary | MonthlyCost
    if values.get(MEMBER_COMPENSATION) == COMPENSATION_MONTHLY:
        compensation = MonthlyCost(amount=number(MEMBER_MONTHLY, 0.0, values))
    else:
        compensation = Salary(
            gross_salary=number(MEMBER_GROSS, 0.0, values),
            employer_contribution_rate=number(MEMBER_RATE_PCT, 0.0, values) / PERCENT,
        )
    return Employee(
        name=text(MEMBER_NAME, values).strip(),
        role=Role(values.get(MEMBER_ROLE, Role.PARTNER)),
        compensation=compensation,
        currency=Currency(values.get(MEMBER_CURRENCY, Currency.RUB.value)),
        billing_rate=number(MEMBER_BILLING, 0.0, values),
        contract_hours_per_month=number(MEMBER_HOURS, 0.0, values),
        target_utilization=number(MEMBER_UTILIZATION, 0.0, values) / PERCENT,
        paid_from_profit=bool(values.get(MEMBER_FROM_PROFIT, False)),
    )


def rename_in_project(values: Values, old: str, new: str) -> dict[str, Any]:
    """Назначения и факт проекта с новым именем сотрудника (только изменённые ключи)."""
    result: dict[str, Any] = {}
    stages_ = cast(list[Stage] | None, values.get(PROJECT_STAGES))
    if stages_:
        result[PROJECT_STAGES] = [
            stage.model_copy(
                update={
                    "assignments": tuple(
                        item.model_copy(update={"employee": new}) if item.employee == old else item
                        for item in stage.assignments
                    )
                }
            )
            for stage in stages_
        ]
    rows = cast(list[ActualHours] | None, values.get(ACTUAL_HOURS))
    if rows:
        result[ACTUAL_HOURS] = [
            row.model_copy(update={"employee": new}) if row.employee == old else row for row in rows
        ]
    return result


def names_in_use() -> set[str]:
    """Имена сотрудников команды и исполнителей во всех проектах сессии."""
    names = {member.name for member in team()}
    for slot in projects():
        values = slot_values(slot)
        names |= {
            item.employee
            for stage in cast(list[Stage], values.get(PROJECT_STAGES) or [])
            for item in stage.assignments
        }
        names |= {row.employee for row in cast(list[ActualHours], values.get(ACTUAL_HOURS) or [])}
    return names


def rename_employee(old: str, new: str) -> None:
    """Колбэк-помощник: новое имя сотрудника — в назначениях и факте всех проектов сессии."""
    for slot in projects():
        if slot.id == active_id():
            for key, value in rename_in_project(session(), old, new).items():
                st.session_state[key] = value
        else:
            slot.values.update(rename_in_project(slot.values, old, new))
    _renew_actuals_editor()


# ---------------------------------------------------------------------------
# Проект
# ---------------------------------------------------------------------------
def duty_quantity_key(country: Country, quantity: Quantity) -> str:
    """Ключ виджета количества (классов, пунктов формулы) для пошлин страны."""
    return f"{DUTY_QUANTITY_PREFIX}{country.value}_{quantity.value}"


def duty_category_key(country: Country) -> str:
    """Ключ виджета категории заявителя для пошлин страны."""
    return f"{DUTY_CATEGORY_PREFIX}{country.value}"


def default_quantity(quantity: Quantity) -> int:
    """Количество по умолчанию: одна единица, позиций сверх лимита — 0."""
    return QUANTITY_DEFAULTS.get(quantity, DEFAULT_QUANTITY)


def duty_quantities(country: Country, values: Values | None = None) -> dict[Quantity, int]:
    """Количества для пошлин страны из виджетов «Проекта»."""
    values = _values(values)
    return {
        quantity: int(values.get(duty_quantity_key(country, quantity), default_quantity(quantity)))
        for quantity in Quantity
    }


def duty_category(country: Country, values: Values | None = None) -> ApplicantCategory:
    """Категория заявителя для пошлин страны; по умолчанию — юрлицо."""
    values = _values(values)
    return ApplicantCategory(values.get(duty_category_key(country), ApplicantCategory.LEGAL_ENTITY))


def duty_selection(country: Country, values: Values | None = None) -> DutySelection:
    """Выбранные пошлины страны с количествами, отличными от умолчания."""
    values = _values(values)
    selected = cast(dict[str, list[str]], values.get(SELECTED_DUTIES, {}))
    quantities = {
        quantity: count
        for quantity, count in duty_quantities(country, values).items()
        if count != default_quantity(quantity)
    }
    return DutySelection(
        actions=tuple(selected.get(country.value, ())),
        quantities=quantities,
        category=duty_category(country, values),
    )


def pricing_kind(values: Values | None = None) -> str:
    """Выбранная модель цены: hourly, capped, fixed или agreed."""
    value = _values(values).get(PRICING_MODEL, PRICING_HOURLY)
    return value if value in PRICING_KINDS else PRICING_HOURLY


def pricing(values: Values | None = None) -> PricingModel:
    """Модель ценообразования из данных шага «Проект».

    Raises:
        pydantic.ValidationError: Потолок или согласованная цена не заданы (0).
    """
    values = _values(values)
    kind = pricing_kind(values)
    if kind == PRICING_CAPPED:
        return CappedPricing(cap=number(CAP_AMOUNT, 0.0, values))
    if kind == PRICING_FIXED:
        return FixedPricing()
    if kind == PRICING_AGREED:
        return FixedPricing(price=number(AGREED_PRICE, 0.0, values))
    return HourlyPricing()


def pricing_complete(values: Values) -> bool:
    """Задан ли потолок или согласованная цена, если модель цены их требует."""
    kind = pricing_kind(values)
    if kind == PRICING_CAPPED:
        return number(CAP_AMOUNT, 0.0, values) > 0
    if kind == PRICING_AGREED:
        return number(AGREED_PRICE, 0.0, values) > 0
    return True


def target_margin(values: Values | None = None) -> Decimal:
    """Целевая маржа; в интерфейсе — целые проценты."""
    margin_pct = int(_values(values).get(TARGET_MARGIN_PCT, DEFAULT_TARGET_MARGIN_PCT))
    return Decimal(margin_pct) / PERCENT


def vat_mode(values: Values | None = None) -> VatMode:
    """Как указаны цены проекта."""
    return VatMode(_values(values).get(VAT_MODE, VatMode.ON_TOP.value))


def project_actuals(values: Values, stage_names: set[str]) -> ProjectActuals | None:
    """Факт проекта из значений; строки удалённых этапов не сохраняются."""
    rows = cast(list[ActualHours] | None, values.get(ACTUAL_HOURS))
    if rows is None:
        return None
    billed = number(ACTUAL_BILLED, 0.0, values)
    manual = bool(values.get(ACTUAL_BILLED_MANUAL)) and billed > 0
    return ProjectActuals(
        hours=tuple(row for row in rows if row.stage in stage_names),
        billed=billed if manual else None,
    )


def project_record(values: Values | None = None, *, strict: bool = True) -> ProjectRecord:
    """Проект и выбранные пошлины из значений проекта.

    Args:
        values: Значения проекта; по умолчанию — активный проект сессии.
        strict: Если потолок или согласованная цена не заданы — ошибка
            (для расчёта); иначе проект сохраняется как почасовой (для
            выгрузки черновика).

    Raises:
        pydantic.ValidationError: strict и потолок или цена не заданы.
    """
    values = _values(values)
    calc_country = values.get(CALC_COUNTRY)
    stages_ = tuple(values.get(PROJECT_STAGES, ()))
    project = Project(
        name=text(PROJECT_NAME, values),
        client=text(PROJECT_CLIENT, values),
        description=text(PROJECT_DESCRIPTION, values),
        pricing=pricing(values) if strict or pricing_complete(values) else HourlyPricing(),
        target_margin=target_margin(values),
        vat_mode=vat_mode(values),
        stages=stages_,
        disbursements=tuple(values.get(CUSTOM_EXPENSES, ())),
        calc_country=None if calc_country is None else Country(calc_country),
        actuals=project_actuals(values, {stage.name for stage in stages_}),
    )
    duties: dict[Country, DutySelection] = {}
    for country in Country:
        selection = duty_selection(country, values)
        if selection != DutySelection():
            duties[country] = selection
    return ProjectRecord(project=project, duties=duties)


def slider_pct(pct: int) -> int:
    """Положение слайдера маржи: значение поля или, если оно шире слайдера, по умолчанию."""
    if TARGET_MARGIN_SLIDER_MIN_PCT <= pct <= TARGET_MARGIN_SLIDER_MAX_PCT:
        return pct
    return DEFAULT_TARGET_MARGIN_PCT


def duty_multiselect_key(country: Country) -> str:
    """Ключ списка выбранных пошлин страны на шаге «Проект»."""
    return f"{DUTY_MULTISELECT_PREFIX}{country.value}"


def margin_pct(margin: Decimal) -> int:
    """Целевая маржа в целых процентах для поля «Проекта»."""
    pct = int(round_to(margin * PERCENT, ONE))
    return min(max(pct, TARGET_MARGIN_INPUT_MIN_PCT), TARGET_MARGIN_INPUT_MAX_PCT)


def project_values(record: ProjectRecord) -> dict[str, Any]:
    """Значения ключей «Проекта» для проекта и выбранных пошлин."""
    project = record.project
    pct = margin_pct(project.target_margin)
    result: dict[str, Any] = {
        PROJECT_NAME: project.name,
        PROJECT_CLIENT: project.client,
        PROJECT_DESCRIPTION: project.description,
        PRICING_MODEL: PRICING_HOURLY,
        CAP_AMOUNT: 0.0,
        AGREED_PRICE: 0.0,
        TARGET_MARGIN_PCT: pct,
        MARGIN_SLIDER: slider_pct(pct),
        VAT_MODE: project.vat_mode.value,
        CALC_COUNTRY: None if project.calc_country is None else project.calc_country.value,
        PROJECT_STAGES: list(project.stages),
        CUSTOM_EXPENSES: list(project.disbursements),
        SELECTED_DUTIES: {
            country.value: list(record.duties[country].actions) if country in record.duties else []
            for country in Country
        },
        ACTUAL_HOURS: None if project.actuals is None else list(project.actuals.hours),
        ACTUAL_BILLED_MANUAL: project.actuals is not None and project.actuals.billed is not None,
        ACTUAL_BILLED: 0.0
        if project.actuals is None or project.actuals.billed is None
        else float(project.actuals.billed),
        TORNADO_PCT: DEFAULT_TORNADO_PCT,
        COMPARE_CAP: 0.0,
        **dict.fromkeys(WHATIF_KEYS, 0),
    }
    if isinstance(project.pricing, CappedPricing):
        result |= {PRICING_MODEL: PRICING_CAPPED, CAP_AMOUNT: float(project.pricing.cap)}
    elif isinstance(project.pricing, FixedPricing) and project.pricing.price is not None:
        result |= {PRICING_MODEL: PRICING_AGREED, AGREED_PRICE: float(project.pricing.price)}
    elif isinstance(project.pricing, FixedPricing):
        result[PRICING_MODEL] = PRICING_FIXED
    for country in Country:
        selection = record.duties.get(country, DutySelection())
        result[duty_multiselect_key(country)] = list(selection.actions)
        for quantity in Quantity:
            result[duty_quantity_key(country, quantity)] = selection.quantities.get(
                quantity, default_quantity(quantity)
            )
        result[duty_category_key(country)] = selection.category
    return result


def whatif_fx_key(currency: Currency) -> str:
    """Ключ ползунка «что если» для курса валюты."""
    return f"{WHATIF_FX_PREFIX}{currency.value}"


# Ползунки «что если»: по ключу — отклонение в процентах.
WHATIF_KEYS: tuple[str, ...] = (
    WHATIF_HOURS,
    WHATIF_BILLING_RATES,
    WHATIF_UTILIZATION,
    WHATIF_OVERHEADS,
    WHATIF_PRICE,
    *(whatif_fx_key(currency) for currency in Currency),
)


def _share(key: str, values: Values) -> Decimal:
    return Decimal(int(values.get(key, 0))) / PERCENT


def what_if(values: Values | None = None) -> WhatIf:
    """Отклонения «что если» активного проекта (проценты ползунков → доли)."""
    values = _values(values)
    return WhatIf(
        hours=_share(WHATIF_HOURS, values),
        billing_rates=_share(WHATIF_BILLING_RATES, values),
        utilization=_share(WHATIF_UTILIZATION, values),
        overheads=_share(WHATIF_OVERHEADS, values),
        price=_share(WHATIF_PRICE, values),
        fx={
            currency: _share(whatif_fx_key(currency), values)
            for currency in Currency
            if int(values.get(whatif_fx_key(currency), 0)) != 0
        },
    )


def reset_what_if() -> None:
    """Колбэк: все ползунки «что если» — в ноль."""
    for key in WHATIF_KEYS:
        st.session_state[key] = 0


def tornado_deviation(values: Values | None = None) -> Decimal:
    """Отклонение для торнадо, доля."""
    return Decimal(int(_values(values).get(TORNADO_PCT, DEFAULT_TORNADO_PCT))) / PERCENT


def compare_cap(values: Values | None = None) -> Decimal | None:
    """Потолок для сравнения; None — не задан."""
    value = number(COMPARE_CAP, 0.0, values)
    return value if value > 0 else None


# ---------------------------------------------------------------------------
# План–факт
# ---------------------------------------------------------------------------
# Столбцы редактора факта. На экране — подпись пары, план и факт (узкий экран
# без горизонтальной прокрутки); этап, исполнитель и отметка — скрытые столбцы.
ACTUALS_LABEL = "Исполнитель · этап"
ACTUALS_STAGE = "Этап"
ACTUALS_EMPLOYEE = "Исполнитель"
ACTUALS_PLAN = "План, ч"
ACTUALS_FACT = "Факт, ч"
ACTUALS_NOTE = "Отметка"
UNPLANNED_NOTE = "вне плана"
ACTUALS_COLUMNS = (
    ACTUALS_LABEL,
    ACTUALS_STAGE,
    ACTUALS_EMPLOYEE,
    ACTUALS_PLAN,
    ACTUALS_FACT,
    ACTUALS_NOTE,
)
ACTUALS_VISIBLE = (ACTUALS_LABEL, ACTUALS_PLAN, ACTUALS_FACT)


def actuals() -> list[ActualHours] | None:
    """Факт активного проекта; None — не введён."""
    return cast(list[ActualHours] | None, st.session_state.get(ACTUAL_HOURS))


def actuals_table_key() -> str:
    """Ключ редактора факта активного проекта."""
    return f"{ACTUALS_TABLE_PREFIX}{int(st.session_state.get(ACTUALS_EDITOR_NONCE, 0))}"


def _renew_actuals_editor() -> None:
    """Новый редактор факта: база пересоберётся из факта проекта."""
    st.session_state.pop(ACTUALS_EDITOR_BASE, None)
    st.session_state[ACTUALS_EDITOR_NONCE] = int(st.session_state.get(ACTUALS_EDITOR_NONCE, 0)) + 1


def actuals_rows(stages_: list[Stage], rows: list[ActualHours]) -> list[dict[str, Any]]:
    """Строки редактора факта: пары плана, затем работа вне плана, по этапам.

    Пара плана без строки факта получает пустой факт (считается нулём).
    """
    plan = plan_pairs(Project(stages=tuple(stages_)))
    fact = {(row.stage, row.employee): row.hours for row in rows}
    result = []
    for stage in stages_:
        keys = [key for key in plan if key[0] == stage.name]
        keys += [key for key in fact if key[0] == stage.name and key not in plan]
        for key in keys:
            hours = fact.get(key)
            note = "" if key in plan else UNPLANNED_NOTE
            result.append(
                {
                    # Исполнитель — первым: на узком экране конец подписи обрезается.
                    ACTUALS_LABEL: " · ".join(part for part in (key[1], key[0], note) if part),
                    ACTUALS_STAGE: key[0],
                    ACTUALS_EMPLOYEE: key[1],
                    ACTUALS_PLAN: float(plan.get(key, 0)),
                    ACTUALS_FACT: None if hours is None else float(hours),
                    ACTUALS_NOTE: note,
                }
            )
    return result


def parse_actuals_rows(rows: Iterable[Mapping[Any, Any]]) -> list[ActualHours]:
    """Факт из строк редактора: пустой факт — нет строки; работа вне плана с нулём — убрана."""
    result = []
    for row in rows:
        value = row[ACTUALS_FACT]
        if value is None or value != value:  # пусто или NaN
            continue
        hours = to_decimal(max(float(value), 0.0))
        if row[ACTUALS_NOTE] == UNPLANNED_NOTE and hours == 0:
            continue
        result.append(
            ActualHours(
                stage=str(row[ACTUALS_STAGE]), employee=str(row[ACTUALS_EMPLOYEE]), hours=hours
            )
        )
    return result


def start_actuals() -> None:
    """Колбэк: факт, равный плану, — с него начинается ввод факта."""
    project = Project(stages=tuple(stages()))
    st.session_state[ACTUAL_HOURS] = list(actuals_from_plan(project))
    _renew_actuals_editor()


def clear_actuals() -> None:
    """Колбэк: удалить факт проекта."""
    st.session_state[ACTUAL_HOURS] = None
    st.session_state[ACTUAL_BILLED_MANUAL] = False
    st.session_state[ACTUAL_BILLED] = 0.0
    _renew_actuals_editor()


def add_actual(stage: str, employee: str, hours: Decimal) -> None:
    """Колбэк-помощник: строка факта (работа вне плана) или новые часы пары."""
    rows = [row for row in actuals() or [] if (row.stage, row.employee) != (stage, employee)]
    rows.append(ActualHours(stage=stage, employee=employee, hours=hours))
    st.session_state[ACTUAL_HOURS] = rows
    _renew_actuals_editor()


def drop_stage_actuals(stage: str) -> None:
    """Факт удалённого этапа удаляется вместе с ним."""
    rows = actuals()
    if rows is not None:
        st.session_state[ACTUAL_HOURS] = [row for row in rows if row.stage != stage]
        _renew_actuals_editor()


def scenario() -> Scenario:
    """Вход расчёта сметы активного проекта: пошлины — по странам юрисдикции.

    Raises:
        pydantic.ValidationError: Потолок или согласованная цена не заданы (0).
    """
    return project_record().scenario(firm())


# ---------------------------------------------------------------------------
# Проекты сессии
# ---------------------------------------------------------------------------
def _is_project_key(key: str) -> bool:
    return key in PROJECT_KEYS or key.startswith(PROJECT_PREFIXES)


def _copy(value: Any) -> Any:
    """Копия значения для снимка: списки и словари списков — новые объекты."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    return value


def snapshot(values: Values) -> dict[str, Any]:
    """Значения проекта из values (копии изменяемых списков)."""
    return {key: _copy(value) for key, value in values.items() if _is_project_key(str(key))}


def _new_id() -> str:
    number_ = int(st.session_state.get(NEXT_PROJECT_ID, 1))
    st.session_state[NEXT_PROJECT_ID] = number_ + 1
    return f"p{number_}"


def projects() -> list[ProjectSlot]:
    """Проекты сессии; в новой сессии — один проект из текущих значений."""
    if PROJECTS not in st.session_state:
        slot = ProjectSlot(id=_new_id(), values={})
        st.session_state[PROJECTS] = [slot]
        st.session_state[ACTIVE_PROJECT] = slot.id
    return cast(list[ProjectSlot], st.session_state[PROJECTS])


def active_id() -> str:
    """Номер активного проекта."""
    projects()
    return str(st.session_state[ACTIVE_PROJECT])


def _slot(slot_id: str) -> ProjectSlot:
    return next(slot for slot in projects() if slot.id == slot_id)


def slot_values(slot: ProjectSlot) -> Values:
    """Значения проекта: у активного — из ключей виджетов."""
    return session() if slot.id == active_id() else slot.values


def slot_scenario(slot: ProjectSlot) -> Scenario:
    """Вход расчёта сметы проекта сессии.

    Raises:
        pydantic.ValidationError: Потолок или согласованная цена не заданы (0).
    """
    return project_record(slot_values(slot)).scenario(firm())


PRICE_MISSING = "Укажите потолок или согласованную цену больше нуля на шаге «Проект»."


def calc_profile(scenario: Scenario) -> TaxProfile:
    """Налоговый режим, по которому считается смета проекта."""
    jurisdiction_ = scenario.firm.jurisdiction
    return jurisdiction_.tax_profile(jurisdiction_.calc_country(scenario.project.calc_country))


def estimate_error(exc: Exception, scenario: Scenario) -> str:
    """Сообщение интерфейса об ошибке расчёта сметы."""
    if isinstance(exc, FixedPriceError):
        return (
            "Не удалось рассчитать фиксированную цену: "
            f"{fixed_price_error(exc, calc_profile(scenario))}"
        )
    return f"{exc}. Курсы задаются на шаге «Фирма»."


def estimate_outcome(build: Callable[[], Scenario]) -> Estimate | str:
    """Смета по текущим данным или причина, по которой её нельзя посчитать."""
    try:
        scenario_ = build()
    except ValidationError:
        return PRICE_MISSING
    try:
        return calculate_estimate(scenario_)
    except (FixedPriceError, CurrencyConversionError) as exc:
        return estimate_error(exc, scenario_)


def slot_title(slot_id: str) -> str:
    """Название проекта для списка: без названия — «Без названия · 2»."""
    slots = projects()
    index = next(i for i, slot in enumerate(slots) if slot.id == slot_id)
    name = text(PROJECT_NAME, slot_values(slots[index])).strip()
    return name or f"Без названия · {index + 1}"


def _stash_active() -> None:
    """Сохраняет значения активного проекта в его слот."""
    _slot(active_id()).values = snapshot(session())


def _clear(keys_filter: Callable[[str], bool]) -> None:
    """Удаляет из сессии ключи, отобранные фильтром."""
    for key in list(st.session_state.keys()):
        if keys_filter(str(key)):
            del st.session_state[key]


def full_values(values: Values) -> dict[str, Any]:
    """Значения всех ключей проекта: снимок поверх значений его записи.

    В снимке нет ключей виджетов, которые проект ещё не показывал (например,
    потолка); их значения берутся из записи проекта.
    """
    return {**project_values(project_record(values, strict=False)), **snapshot(values)}


def _load(slot: ProjectSlot) -> None:
    """Делает проект активным: его значения — в ключи виджетов, каждое явно."""
    _clear(_is_project_key)
    for key, value in full_values(slot.values).items():
        st.session_state[key] = value
    _renew_actuals_editor()
    st.session_state[ACTIVE_PROJECT] = slot.id
    st.session_state[PROJECT_SWITCH] = slot.id
    init_defaults()


def _add(values: dict[str, Any]) -> None:
    """Колбэк-помощник: новый проект после активного, он становится активным."""
    _stash_active()
    slots = projects()
    slot = ProjectSlot(id=_new_id(), values=values)
    slots.insert(slots.index(_slot(active_id())) + 1, slot)
    _load(slot)


def switch_project() -> None:
    """Колбэк переключателя: сохранить активный проект и открыть выбранный."""
    target = str(st.session_state[PROJECT_SWITCH])
    if target != active_id():
        _stash_active()
        _load(_slot(target))


def new_project() -> None:
    """Колбэк: пустой проект."""
    _add(project_values(ProjectRecord()))


def duplicate_project() -> None:
    """Колбэк: копия активного проекта."""
    values = snapshot(session())
    values[PROJECT_NAME] = text(PROJECT_NAME).strip() + COPY_SUFFIX
    _add(values)


def delete_project() -> None:
    """Колбэк: удалить активный проект; последний заменяется пустым."""
    slots = projects()
    index = slots.index(_slot(active_id()))
    slots.pop(index)
    if not slots:
        slots.append(ProjectSlot(id=_new_id(), values=project_values(ProjectRecord())))
    _load(slots[min(index, len(slots) - 1)])


def has_user_data() -> bool:
    """Есть ли в сессии введённые данные, которые заменит загрузка."""
    if team() or overheads():
        return True
    empty = ProjectRecord()
    return any(project_record(slot_values(slot), strict=False) != empty for slot in projects())


def workspace() -> Workspace:
    """Фирма, курсы и все проекты сессии (черновики цены — как почасовые)."""
    return Workspace(
        firm=firm(),
        rates_quote=fx_quote(),
        projects=tuple(project_record(slot_values(slot), strict=False) for slot in projects()),
    )


def incomplete_projects() -> list[str]:
    """Проекты, у которых не задан потолок или согласованная цена."""
    return [slot_title(slot.id) for slot in projects() if not pricing_complete(slot_values(slot))]


def apply_workspace(value: Workspace) -> list[str]:
    """Колбэк-помощник: заменяет фирму и проекты сессии загруженными.

    Returns:
        Замечания интерфейса: например, маржа округлена до целого процента.
    """
    notes: list[str] = []
    _clear(lambda key: key in FIRM_KEYS or _is_project_key(key))
    for key in (PROJECTS, ACTIVE_PROJECT):
        st.session_state.pop(key, None)
    for key, item in firm_values(value.firm, value.rates_quote).items():
        st.session_state[key] = item
    records = value.projects or (ProjectRecord(),)
    slots: list[ProjectSlot] = []
    for index, record in enumerate(records):
        margin = record.project.target_margin * PERCENT
        if margin != margin_pct(record.project.target_margin):
            name = record.project.name.strip() or f"№ {index + 1}"
            notes.append(
                f"Проект «{name}»: целевая маржа {format_number(margin)}% округлена до "
                f"{margin_pct(record.project.target_margin)}% — в интерфейсе целые проценты."
            )
        slots.append(ProjectSlot(id=_new_id(), values=project_values(record)))
    st.session_state[PROJECTS] = slots
    st.session_state[ACTIVE_PROJECT] = slots[0].id
    _load(slots[0])
    return notes
