"""Страница 02: проект и смета (страница v0.3 на ядре, методология фазы 2).

Страница собирает этапы с исполнителями (на этапе их может быть несколько),
пошлины и расходы проекта, модель цены и режим НДС, а по кнопке
«Рассчитать смету» вызывает `rentab_core.calculate_estimate`. Вся
математика — в ядре; здесь только ввод и показ.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from rentab_core import CurrencyConversionError, FixedPriceError, calculate_estimate
from rentab_core.analysis.team import blended_rate, leverage
from rentab_core.duties import duty_disbursements, load_catalog
from rentab_core.estimate import resolve_stages
from rentab_core.models import (
    ROLE_LABELS,
    Assignment,
    Country,
    Disbursement,
    DisbursementKind,
    JurisdictionMode,
    Project,
    Stage,
    VatMode,
)
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, Currency, decimal_sum, to_decimal
from ui import state
from ui.labels import (
    COUNTRY_LABELS,
    CUSTOM_EXPENSE_LABELS,
    PATENT_OFFICE_LABELS,
    PRICING_LABELS,
    VAT_MODE_LABELS,
)

# Слайдер целевой маржи, % (02_Проект.py:67–70).
TARGET_MARGIN_MIN_PCT = 0
TARGET_MARGIN_MAX_PCT = 80
TARGET_MARGIN_STEP_PCT = 1
# Ручной ввод маржи допускает значения шире слайдера.
TARGET_MARGIN_INPUT_MIN_PCT = 0
TARGET_MARGIN_INPUT_MAX_PCT = 99
NEW_STAGE = "➕ Новый этап"
NO_STAGE = "Без привязки (по теневой стоимости)"

state.init_defaults()
state.persist_widgets()

st.title("📋 Проект и смета")

team = state.team()
if not team:
    st.warning("Сначала заполните состав команды на странице **01 Настройка → Команда**.")
    st.stop()

jurisdiction = state.jurisdiction()
if jurisdiction is None:
    st.warning("Сначала выберите юрисдикцию на странице **01 Настройка → Юрисдикция и налоги**.")
    st.stop()

# ===========================================================================
# БЛОК 0 — ЮРИСДИКЦИЯ РАСЧЁТА (режим «Оба») И ВАЛЮТА ПРОЕКТА
# ===========================================================================
if jurisdiction.mode == JurisdictionMode.BOTH:
    st.radio(
        "В какой юрисдикции рассчитать налог и смету этого проекта?",
        options=[country.value for country in Country],
        format_func=lambda c: COUNTRY_LABELS[Country(c)],
        horizontal=True,
        key=state.CALC_COUNTRY,
        help="Валюта проекта — валюта этой страны; остальные суммы пересчитываются по курсу.",
    )
calc_choice = st.session_state.get(state.CALC_COUNTRY)
calc_country = jurisdiction.calc_country(None if calc_choice is None else Country(calc_choice))
currency_symbol = CURRENCY_SYMBOLS[calc_country.currency]

# ===========================================================================
# БЛОК 1 — ОБЩИЕ ДАННЫЕ ПРОЕКТА
# ===========================================================================
st.subheader("1. Данные проекта")
col_name, col_client = st.columns([2, 1])
with col_name:
    project_name = st.text_input(
        "Название проекта *",
        placeholder="Регистрация товарного знака «Ромашка»",
        key=state.PROJECT_NAME,
    )
with col_client:
    st.text_input("Клиент (необязательно)", placeholder="ООО «Ромашка»", key=state.PROJECT_CLIENT)
st.text_area(
    "Краткое описание (необязательно)",
    placeholder="Классы МКТУ 05, 29, 30; приоритет по Парижской конвенции…",
    height=80,
    key=state.PROJECT_DESCRIPTION,
)

st.markdown("---")

# ===========================================================================
# БЛОК 2 — ЭТАПЫ И ИСПОЛНИТЕЛИ
# ===========================================================================
st.subheader("2. Этапы и исполнители")
st.caption(
    "На этапе может быть несколько исполнителей: например, юрист готовит ответ "
    "10 ч, партнёр проверяет 2 ч. Выберите существующий этап, чтобы добавить "
    "на него исполнителя."
)
team_names = [member.name for member in team]
stages = state.stages()
stage_names = [stage.name for stage in stages]


def add_assignment() -> None:
    """Колбэк формы: новый этап или ещё один исполнитель на существующем этапе.

    Колбэк срабатывает до отрисовки страницы, поэтому новый этап сразу
    появляется в списке «Этап».
    """
    choice = st.session_state["assignment_stage"]
    name = str(st.session_state["assignment_new_stage"]).strip()
    hours = float(st.session_state["assignment_hours"])
    executor = str(st.session_state["assignment_executor"])
    current = state.stages()
    names = [stage.name for stage in current]
    assignment = Assignment(employee=executor, hours=to_decimal(hours))
    if hours <= 0:
        message = ("error", "Часы должны быть больше нуля.")
    elif choice == NEW_STAGE and not name:
        message = ("error", "Введите название нового этапа.")
    elif choice == NEW_STAGE and name in names:
        message = ("error", f"Этап «{name}» уже есть: выберите его в списке «Этап».")
    elif choice == NEW_STAGE:
        current.append(Stage(name=name, assignments=(assignment,)))
        message = ("success", f"Добавлен этап: {name}")
    else:
        idx = names.index(str(choice))
        stage = current[idx]
        current[idx] = stage.model_copy(update={"assignments": (*stage.assignments, assignment)})
        message = ("success", f"На этап «{stage.name}» добавлен исполнитель: {executor}")
    st.session_state["assignment_message"] = message


with st.form("add_assignment_form", clear_on_submit=True):
    col_stage, col_new, col_exec, col_hours = st.columns([1.5, 2, 1.5, 1])
    with col_stage:
        st.selectbox("Этап", options=[NEW_STAGE, *stage_names], key="assignment_stage")
    with col_new:
        st.text_input(
            "Название нового этапа",
            placeholder="Поиск по базам / Подача заявки / Ответ на уведомление",
            key="assignment_new_stage",
        )
    with col_exec:
        st.selectbox("Исполнитель", options=team_names, key="assignment_executor")
    with col_hours:
        st.number_input(
            "Часы", min_value=0.0, value=0.0, step=1.0, format="%.1f", key="assignment_hours"
        )
    st.form_submit_button("➕ Добавить", on_click=add_assignment)
assignment_message = st.session_state.pop("assignment_message", None)
if assignment_message is not None:
    kind_, text_ = assignment_message
    (st.success if kind_ == "success" else st.error)(text_)

team_by_name = {member.name: member for member in team}
if not stages:
    st.info("Пока нет этапов. Добавьте первый этап через форму выше.")
else:
    for stage_idx, stage in enumerate(list(stages)):
        total = decimal_sum(a.hours for a in stage.assignments)
        st.markdown(f"**{stage_idx + 1}. {stage.name}** · {total:.1f} ч")
        for assignment_idx, assignment in enumerate(stage.assignments):
            member = team_by_name.get(assignment.employee)
            c1, c2, c3, c4 = st.columns([2.5, 0.8, 1.2, 0.5])
            if member is None:
                c1.warning(
                    f"Исполнителя «{assignment.employee}» нет в составе команды: "
                    "назначение не войдёт в расчёт."
                )
            else:
                c1.write(f"{member.name} ({ROLE_LABELS[member.role]})")
                sym = CURRENCY_SYMBOLS[member.currency]
                c3.write(f"{member.billing_rate * assignment.hours:,.0f} {sym} по ставке")
            c2.write(f"{assignment.hours:.1f} ч")
            if c4.button("🗑", key=f"del_assignment_{stage_idx}_{assignment_idx}", help="Удалить"):
                remaining = tuple(a for i, a in enumerate(stage.assignments) if i != assignment_idx)
                if remaining:
                    stages[stage_idx] = stage.model_copy(update={"assignments": remaining})
                else:
                    # Этап удалён: привязанные к нему расходы делятся по всем этапам.
                    stages.pop(stage_idx)
                    expenses = state.custom_expenses()
                    expenses[:] = [
                        item.model_copy(update={"stage": None})
                        if item.stage == stage.name
                        else item
                        for item in expenses
                    ]
                st.rerun()

# Показатели команды пересчитываются ядром на каждом изменении этапов.
try:
    resolved, _ = resolve_stages(state.firm(), Project(stages=tuple(stages)), calc_country.currency)
except CurrencyConversionError as exc:
    st.warning(f"{exc}. Курсы задаются на странице «Настройка».")
    resolved = []
lines = [line for stage_lines in resolved for line in stage_lines.lines]
col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric(f"Средневзвешенная ставка, {currency_symbol}/ч", f"{blended_rate(lines):,.0f}")
col_m2.metric(
    "Коэффициент рычага",
    f"{leverage(lines):.2f}",
    help="Часы младших / часы старших. Выше — выше маржа.",
)
col_m3.metric("Всего часов", f"{decimal_sum(line.hours for line in lines):.1f}")

st.markdown("---")

# ===========================================================================
# БЛОК 3 — ЦЕНА
# ===========================================================================
st.subheader("3. Цена")
st.radio(
    "Модель ценообразования",
    options=list(state.PRICING_KINDS),
    format_func=lambda k: PRICING_LABELS[k],
    horizontal=True,
    key=state.PRICING_MODEL,
)
kind = state.pricing_kind()
st.radio(
    "Цены в проекте указаны",
    options=[mode.value for mode in VatMode],
    format_func=lambda m: VAT_MODE_LABELS[VatMode(m)],
    horizontal=True,
    key=state.VAT_MODE,
    help="Относится к ставкам для клиента, потолку и согласованной цене. "
    "НДС — не доход фирмы: маржа считается от выручки без НДС.",
)
if kind == state.PRICING_CAPPED:
    st.number_input(
        f"Потолок стоимости услуг, {currency_symbol}",
        min_value=0.0,
        step=10_000.0,
        format="%.0f",
        key=state.CAP_AMOUNT,
        help="Выручка = min(Σ ставка × часы, потолок).",
    )
elif kind == state.PRICING_AGREED:
    st.number_input(
        f"Согласованная цена услуг, {currency_symbol}",
        min_value=0.0,
        step=10_000.0,
        format="%.0f",
        key=state.AGREED_PRICE,
        help="Цена уже согласована с клиентом; смета покажет, какая получается маржа.",
    )


def _sync_margin_from_slider() -> None:
    """Колбэк слайдера: переносит значение в поле ввода."""
    st.session_state[state.TARGET_MARGIN_PCT] = int(st.session_state["margin_slider"])


def _sync_margin_from_input() -> None:
    """Колбэк поля ввода: двигает слайдер, если значение в его диапазоне."""
    value = int(st.session_state[state.TARGET_MARGIN_PCT])
    if TARGET_MARGIN_MIN_PCT <= value <= TARGET_MARGIN_MAX_PCT:
        st.session_state["margin_slider"] = value


st.session_state.setdefault(state.TARGET_MARGIN_PCT, state.DEFAULT_TARGET_MARGIN_PCT)
initial_slider = int(st.session_state[state.TARGET_MARGIN_PCT])
if not TARGET_MARGIN_MIN_PCT <= initial_slider <= TARGET_MARGIN_MAX_PCT:
    initial_slider = state.DEFAULT_TARGET_MARGIN_PCT
st.session_state.setdefault("margin_slider", initial_slider)
margin_help = "Доля NNE в выручке без НДС после всех издержек и налога. " + (
    "Определяет фиксированную цену."
    if kind == state.PRICING_FIXED
    else "Порог светофора этапов: зелёный — не ниже цели."
)
col_slider, col_input = st.columns([3, 1])
with col_slider:
    st.slider(
        "Целевая маржа NNE (%)",
        min_value=TARGET_MARGIN_MIN_PCT,
        max_value=TARGET_MARGIN_MAX_PCT,
        step=TARGET_MARGIN_STEP_PCT,
        key="margin_slider",
        on_change=_sync_margin_from_slider,
        help=margin_help,
    )
with col_input:
    st.number_input(
        "или введите",
        min_value=TARGET_MARGIN_INPUT_MIN_PCT,
        max_value=TARGET_MARGIN_INPUT_MAX_PCT,
        step=TARGET_MARGIN_STEP_PCT,
        key=state.TARGET_MARGIN_PCT,
        on_change=_sync_margin_from_input,
        help="Точное значение — приоритет над слайдером.",
    )

st.markdown("---")

# ===========================================================================
# БЛОК 4 — РАСХОДЫ ПРОЕКТА
# ===========================================================================
selected = state.selected_duties()
with st.expander("4. Расходы проекта — пошлины, субподряд и др. (необязательно)"):
    st.markdown("#### Пошлины из каталога")
    st.caption(
        "Пошлины — возмещаемые расходы: клиент их оплачивает отдельной строкой, "
        "в выручку и налоговую базу они не входят."
    )
    for country in jurisdiction.countries:
        catalog = load_catalog(country)
        amounts = catalog.amounts()
        sym = CURRENCY_SYMBOLS[catalog.currency]
        st.markdown(f"**{PATENT_OFFICE_LABELS[country]} ({sym})**")
        chosen = st.multiselect(
            f"Выберите пошлины ({country.value})",
            options=list(amounts),
            default=selected.get(country.value, []),
            key=f"duties_multiselect_{country.value}",
        )
        selected[country.value] = chosen
        if chosen:
            subtotal = decimal_sum(amounts[action] for action in chosen)
            st.info(f"Сумма: **{subtotal:,.0f} {sym}**")
    st.caption(
        "Суммы пошлин в каталогах пока не заполнены (0, фаза 3), поэтому выбранные "
        "пошлины в смету не попадают. Введите пошлину как произвольный возмещаемый расход."
    )

    st.markdown("---")
    st.markdown("#### Произвольный расход")
    st.caption(
        "Собственный расход (курьер, нотариальный перевод, командировка) уменьшает "
        "прибыль и закладывается в фикс-цену; его можно отнести на этап."
    )
    with st.form("add_custom_expense_form", clear_on_submit=True):
        col_cn, col_cc = st.columns([2, 1])
        with col_cn:
            ce_name = st.text_input("Название", placeholder="Командировка в Роспатент")
        with col_cc:
            ce_kind = (
                st.selectbox(
                    "Категория",
                    options=list(CUSTOM_EXPENSE_LABELS),
                    format_func=lambda k: CUSTOM_EXPENSE_LABELS[k],
                )
                or DisbursementKind.OWN
            )
        col_ca, col_cu, col_cs = st.columns([1, 1, 2])
        with col_ca:
            ce_amount = st.number_input(
                "Сумма", min_value=0.0, value=0.0, step=500.0, format="%.0f"
            )
        with col_cu:
            ce_currency = st.selectbox("Валюта", options=list(Currency)) or Currency.RUB
        with col_cs:
            ce_stage = st.selectbox(
                "Этап (для собственного расхода)", options=[NO_STAGE, *stage_names]
            )
        if st.form_submit_button("➕ Добавить расход"):
            if not ce_name.strip():
                st.error("Название расхода не может быть пустым.")
            elif ce_amount <= 0:
                st.error("Сумма должна быть больше нуля.")
            else:
                state.custom_expenses().append(
                    Disbursement(
                        name=ce_name.strip(),
                        kind=ce_kind,
                        amount=to_decimal(ce_amount),
                        currency=ce_currency,
                        stage=None if ce_stage == NO_STAGE else str(ce_stage),
                    )
                )
                st.success(f"Добавлен расход: {ce_name}")

    custom_list = state.custom_expenses()
    if custom_list:
        st.markdown("**Текущие произвольные расходы**")
        for idx, item in enumerate(list(custom_list)):
            c1, c2, c3, c4, c5 = st.columns([2, 2, 1, 1.5, 0.5])
            c1.write(f"**{item.name}**")
            c2.write(CUSTOM_EXPENSE_LABELS[item.kind])
            c3.write(f"{item.amount:,.0f} {item.currency.value}")
            c4.write(item.stage or "—")
            if c5.button("🗑", key=f"del_custom_{idx}", help="Удалить"):
                custom_list.pop(idx)
                st.rerun()

st.markdown("---")

# ===========================================================================
# БЛОК 5 — РАСЧЁТ
# ===========================================================================
st.subheader("5. Рассчитать смету")
calc_disabled = not (project_name.strip() and any(stage.assignments for stage in stages))
if calc_disabled:
    st.caption(
        "Чтобы рассчитать смету, укажите название проекта и добавьте хотя бы один этап с часами."
    )

if st.button("💰 Рассчитать смету", type="primary", disabled=calc_disabled):
    duties = [
        item
        for country in jurisdiction.countries
        for item in duty_disbursements(country, selected.get(country.value, []))
    ]
    try:
        st.session_state[state.ESTIMATE] = calculate_estimate(state.scenario(duties))
    except FixedPriceError as exc:
        st.error(f"Не удалось рассчитать фиксированную цену: {exc}")
        st.stop()
    except CurrencyConversionError as exc:
        st.error(f"{exc}. Курсы задаются на странице «Настройка».")
        st.stop()
    except ValidationError:
        st.error("Укажите потолок или согласованную цену больше нуля.")
        st.stop()

# ===========================================================================
# БЛОК 6 — ПРЕДВАРИТЕЛЬНЫЙ РЕЗУЛЬТАТ
# ===========================================================================
result = state.estimate()
if result is not None:
    st.markdown("### Предварительный результат")
    sym = CURRENCY_SYMBOLS[result.currency]
    p1, p2, p3, p4 = st.columns(4)
    p1.metric(f"Выручка без НДС, {sym}", f"{result.revenue:,.0f}")
    p2.metric(f"НДС, {sym}", f"{result.vat:,.0f}")
    p3.metric(f"К оплате клиентом, {sym}", f"{result.total_client:,.0f}")
    p4.metric(
        f"Чистая прибыль (NNE), {sym}",
        f"{result.nne:,.0f}",
        delta=f"{result.margin * PERCENT:.1f}% маржа · цель {result.target_margin * PERCENT:.0f}%",
    )
    st.caption(
        f"Режим: {result.regime_label} · Все издержки: {result.total_costs:,.0f} {sym} · "
        f"Налог: {result.tax.tax:,.0f} {sym} · Возмещаемые расходы: "
        f"{result.disbursements_billed:,.0f} {sym}"
    )
    for missing in result.unresolved_assignments:
        st.warning(
            f"Этап «{missing.stage_name}»: исполнителя «{missing.employee}» нет в команде, "
            "назначение не вошло в расчёт."
        )

    st.page_link("pages/03_Дашборд.py", label="📊 Перейти к дашборду", icon="➡️")

    with st.expander("Детали расчёта"):
        rows = [
            {
                "Этап": line.stage_name,
                "Сотрудник": line.employee_name,
                "Роль": ROLE_LABELS[line.role],
                "Часы": float(line.hours),
                f"Себестоимость часа, {sym}": float(line.cost_rate),
                f"Трудозатраты, {sym}": float(line.labor_cost),
                f"По ставке, {sym}": float(line.shadow_value),
            }
            for line in result.lines
        ]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
