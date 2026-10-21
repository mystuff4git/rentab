"""Страница 02: проект и смета (перенос `pages/02_Проект.py` v0.3 на ядро).

Страница собирает этапы, исполнителей, пошлины и расходы проекта, а по
кнопке «Рассчитать смету» вызывает `rentab_core.calculate_estimate`.
Вся математика — в ядре; здесь только ввод и показ.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from rentab_core import FixedPriceError, calculate_estimate
from rentab_core.analysis.team import blended_rate, leverage
from rentab_core.duties import duty_disbursements, load_catalog
from rentab_core.estimate import resolve_stages
from rentab_core.models import (
    ROLE_LABELS,
    Assignment,
    Country,
    Disbursement,
    DisbursementKind,
    FixedPricing,
    JurisdictionMode,
    Stage,
)
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, ZERO, Currency, decimal_sum, to_decimal
from rentab_core.results import FixedPriceEstimate
from ui import state
from ui.labels import (
    COUNTRY_LABELS,
    CUSTOM_EXPENSE_LABELS,
    PATENT_OFFICE_LABELS,
    PRICING_LABELS,
)

# Слайдер целевой маржи, % (02_Проект.py:67–70).
TARGET_MARGIN_MIN_PCT = 5
TARGET_MARGIN_MAX_PCT = 80
TARGET_MARGIN_STEP_PCT = 1
# Ручной ввод маржи допускает значения шире слайдера.
TARGET_MARGIN_INPUT_MIN_PCT = 1
TARGET_MARGIN_INPUT_MAX_PCT = 99

state.init_defaults()
state.persist_widgets()

st.title("📋 Проект и смета")

# ===========================================================================
# БЛОК 0 — МОДЕЛЬ ЦЕНООБРАЗОВАНИЯ
# ===========================================================================
st.radio(
    "Модель ценообразования",
    list(PRICING_LABELS.values()),
    horizontal=True,
    key=state.PRICING_MODEL,
)
is_fixed_price = isinstance(state.pricing(), FixedPricing)

st.markdown("---")

team = state.team()
if not team:
    st.warning("Сначала заполните состав команды на странице **01 Настройка → Команда**.")
    st.stop()

jurisdiction = state.jurisdiction()
if jurisdiction is None:
    st.warning("Сначала выберите юрисдикцию на странице **01 Настройка → Юрисдикция и налоги**.")
    st.stop()

primary_country = Country.KZ if jurisdiction.mode == JurisdictionMode.KZ else Country.RF
currency_symbol = CURRENCY_SYMBOLS[primary_country.currency]

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
# БЛОК 2 — ЭТАПЫ ПРОЕКТА
# ===========================================================================
st.subheader("2. Этапы проекта")
team_names = [member.name for member in team]
stages = state.stages()

with st.form("add_stage_form", clear_on_submit=True):
    col_sname, col_exec, col_hours = st.columns([2, 1.5, 1])
    with col_sname:
        stage_name_input = st.text_input(
            "Название этапа",
            placeholder="Поиск по базам / Подача заявки / Ответ на уведомление",
        )
    with col_exec:
        stage_executor = st.selectbox(
            "Исполнитель",
            options=team_names,
            help="Выбирается из состава команды, заданного на странице Настройка.",
        )
    with col_hours:
        stage_hours = st.number_input("Часы", min_value=0.0, value=0.0, step=1.0, format="%.1f")
    if st.form_submit_button("➕ Добавить этап"):
        if not stage_name_input.strip():
            st.error("Название этапа не может быть пустым.")
        elif stage_hours <= 0:
            st.error("Часы должны быть больше нуля.")
        else:
            # KNOWN-BUG L-05 → фаза 2: один исполнитель на этап, как в v0.3.
            stages.append(
                Stage(
                    name=stage_name_input.strip(),
                    assignments=(
                        Assignment(employee=str(stage_executor), hours=to_decimal(stage_hours)),
                    ),
                )
            )
            st.success(f"Добавлен этап: {stage_name_input}")

team_by_name = {member.name: member for member in team}
if not stages:
    st.info("Пока нет этапов. Добавьте первый этап через форму выше.")
else:
    st.markdown("#### Состав этапов")
    for idx, stage in enumerate(list(stages)):
        for assignment in stage.assignments:
            member = team_by_name.get(assignment.employee)
            if member is None:
                st.warning(
                    f"Исполнитель «{assignment.employee}» этапа «{stage.name}» отсутствует "
                    "в текущем составе команды. Удалите этап или измените состав."
                )
                continue
            c1, c2, c3, c4, c5 = st.columns([2, 1.5, 0.8, 1.2, 0.5])
            c1.write(f"**{stage.name}**")
            c2.write(f"{member.name} ({ROLE_LABELS[member.role]})")
            c3.write(f"{assignment.hours:.1f} ч")
            rate = member.cost_rate if is_fixed_price else member.billing_rate
            c4.write(f"{rate * assignment.hours:,.0f} {currency_symbol}")
            if c5.button("🗑", key=f"del_stage_{idx}", help="Удалить этап"):
                stages.pop(idx)
                st.rerun()
    if is_fixed_price:
        st.caption(
            "Колонки: Этап · Исполнитель · Часы · Себестоимость этапа "
            f"({currency_symbol} = cost_rate × hours)"
        )
    else:
        st.caption(
            "Колонки: Этап · Исполнитель · Часы · Выручка по этапу "
            f"({currency_symbol} = billing × hours)"
        )

# Показатели команды пересчитываются ядром на каждом изменении этапов.
resolved, _ = resolve_stages(state.firm(), state.project([]))
lines = [line for stage_lines in resolved for line in stage_lines.lines]
col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric(f"Средневзвешенная ставка, {currency_symbol}/ч", f"{blended_rate(lines):,.0f}")
col_m2.metric(
    "Коэффициент рычага",
    f"{leverage(lines):.2f}",
    help="Часы младших / часы старших. Выше — выше маржа.",
)
col_m3.metric("Всего часов", f"{decimal_sum(line.hours for line in lines):.1f}")


def _sync_margin_from_slider() -> None:
    """Колбэк слайдера: переносит значение в поле ввода."""
    st.session_state[state.TARGET_MARGIN_PCT] = int(st.session_state["margin_slider"])


def _sync_margin_from_input() -> None:
    """Колбэк поля ввода: двигает слайдер, если значение в его диапазоне."""
    value = int(st.session_state[state.TARGET_MARGIN_PCT])
    if TARGET_MARGIN_MIN_PCT <= value <= TARGET_MARGIN_MAX_PCT:
        st.session_state["margin_slider"] = value


if is_fixed_price:
    st.session_state.setdefault(state.TARGET_MARGIN_PCT, state.DEFAULT_TARGET_MARGIN_PCT)
    initial_slider = int(st.session_state[state.TARGET_MARGIN_PCT])
    if not TARGET_MARGIN_MIN_PCT <= initial_slider <= TARGET_MARGIN_MAX_PCT:
        initial_slider = state.DEFAULT_TARGET_MARGIN_PCT
    st.session_state.setdefault("margin_slider", initial_slider)

    col_slider, col_input = st.columns([3, 1])
    with col_slider:
        st.slider(
            "Целевая маржа (%)",
            min_value=TARGET_MARGIN_MIN_PCT,
            max_value=TARGET_MARGIN_MAX_PCT,
            step=TARGET_MARGIN_STEP_PCT,
            key="margin_slider",
            on_change=_sync_margin_from_slider,
            help=(
                "Целевая маржа — желаемая доля прибыли от фиксированной цены "
                "после всех издержек и налогов."
            ),
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
    if int(st.session_state[state.TARGET_MARGIN_PCT]) > TARGET_MARGIN_MAX_PCT:
        st.info(
            "Высокая маржа. Убедитесь, что рыночная цена проекта конкурентоспособна.",
            icon="ℹ️",
        )

st.markdown("---")

# ===========================================================================
# БЛОК 3 — РАСХОДЫ ПРОЕКТА
# ===========================================================================
selected = state.selected_duties()
with st.expander("3. Расходы проекта — пошлины, субподряд и др. (необязательно)"):
    st.markdown("#### Пошлины из каталога")
    st.caption(
        "Пошлины — агентские транзитные расходы: перевыставляются клиенту "
        "без наценки и исключаются из налоговой базы."
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
        "Суммы пошлин в каталогах пока не заполнены (0), поэтому выбранные пошлины "
        "в смету не попадают. Введите пошлину как произвольный перевыставляемый расход."
    )

    st.markdown("---")
    st.markdown("#### Произвольный расход")
    st.caption(
        "Например: командировка юриста (собственный расход), "
        "нотариус для клиента (перевыставляемый), перевод документов."
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
        col_ca, col_cu = st.columns([1, 1])
        with col_ca:
            ce_amount = st.number_input(
                "Сумма", min_value=0.0, value=0.0, step=500.0, format="%.0f"
            )
        with col_cu:
            ce_currency = st.selectbox("Валюта", options=list(Currency)) or Currency.RUB
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
                    )
                )
                st.success(f"Добавлен расход: {ce_name}")

    custom_list = state.custom_expenses()
    if custom_list:
        st.markdown("**Текущие произвольные расходы**")
        for idx, item in enumerate(list(custom_list)):
            c1, c2, c3, c4 = st.columns([2, 2, 1, 0.5])
            c1.write(f"**{item.name}**")
            c2.write(CUSTOM_EXPENSE_LABELS[item.kind])
            c3.write(f"{item.amount:,.0f} {item.currency.value}")
            if c4.button("🗑", key=f"del_custom_{idx}", help="Удалить"):
                custom_list.pop(idx)
                st.rerun()

st.markdown("---")

# ===========================================================================
# БЛОК 4 — ЮРИСДИКЦИЯ РАСЧЁТА (режим «Оба»)
# ===========================================================================
if jurisdiction.mode == JurisdictionMode.BOTH:
    st.subheader("4. Выбор юрисдикции для сметы")
    st.radio(
        "В какой юрисдикции рассчитать налог этого проекта?",
        options=[country.value for country in Country],
        format_func=lambda c: COUNTRY_LABELS[Country(c)],
        horizontal=True,
        key=state.CALC_COUNTRY,
    )

# ===========================================================================
# БЛОК 5 — РАСЧЁТ
# ===========================================================================
st.subheader("5. Рассчитать смету")
calc_disabled = not (project_name.strip() and lines)
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

# ===========================================================================
# БЛОК 6 — ПРЕДВАРИТЕЛЬНЫЙ РЕЗУЛЬТАТ
# ===========================================================================
result = state.estimate()
if result is not None:
    st.markdown("### Предварительный результат")
    sym = CURRENCY_SYMBOLS[result.currency]
    p1, p2, p3 = st.columns(3)
    if isinstance(result, FixedPriceEstimate):
        p1.metric(f"Фиксированная цена, {sym}", f"{result.fixed_price:,.0f}")
        p2.metric(f"Чистая прибыль (NNE), {sym}", f"{result.nne:,.0f}")
        p3.metric(
            "Реальная маржа",
            f"{result.actual_margin * PERCENT:.1f}%",
            delta=f"цель {result.target_margin * PERCENT:.0f}%",
        )
        st.caption(
            f"Режим: {result.regime_label} · "
            f"Все издержки: {result.total_costs:,.0f} {sym} · "
            f"Налог: {result.tax.total_tax:,.0f} {sym}"
        )
    else:
        p1.metric(f"Выручка, {sym}", f"{result.gross_revenue:,.0f}")
        p2.metric(f"Налог, {sym}", f"{result.tax.total_tax:,.0f}")
        p3.metric(
            f"Чистая прибыль (NNE), {sym}",
            f"{result.nne:,.0f}",
            delta=(
                f"{result.nne / result.gross_revenue * PERCENT:.1f}%"
                if result.gross_revenue != ZERO
                else None
            ),
        )
        st.caption(
            f"Режим: {result.regime_label} · "
            f"Пошлины (транзит): {result.disbursements_billed:,.0f} {sym} · "
            f"Собственные расходы: {result.disbursements_own:,.0f} {sym}"
        )

    st.page_link("pages/03_Дашборд.py", label="📊 Перейти к дашборду", icon="➡️")

    with st.expander("Детали расчёта"):
        rows = []
        for line in result.lines:
            row: dict[str, object] = {
                "Сотрудник": line.employee_name,
                "Роль": ROLE_LABELS[line.role],
                "Часы": float(line.hours),
                f"Себестоимость, {sym}": float(line.labor_cost),
            }
            if not isinstance(result, FixedPriceEstimate):
                row[f"Выручка, {sym}"] = float(line.revenue)
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
