"""Страница 06: план–факт проекта (docs/methodology.md, раздел 18).

Фактические часы вводятся по парам «этап × сотрудник» в редакторе: план
только для чтения, факт — редактируется; работа вне плана добавляется
формой. Выставлено по факту — по модели цены или вручную. Расчёт —
`rentab_core.analysis.plan_fact`; факт хранится в слоте проекта и в файле
профиля.

Редактор факта (L-21): его вход — неизменяемая база в сессии; она
пересобирается из факта только для нового виджета (новый проект, «заполнить
по плану», строка вне плана), а не из результата самого редактора.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from rentab_core.analysis.plan_fact import PlanFact, plan_fact
from rentab_core.models import ROLE_LABELS, VatMode
from rentab_core.money import CURRENCY_SYMBOLS, decimal_sum, to_decimal
from ui import state
from ui.analytics import compute, render_header, require_scenario
from ui.labels import (
    BILLING_RULE_LABELS,
    STAGE_FLAG_LABELS,
    hours_label,
    money,
    pct_label,
    points_label,
    signed_hours,
    signed_money,
    signed_pct,
)
from ui.projects import render_project_switcher

# Цвета столбиков «план» и «факт» (палитра проверена на различимость при
# нарушениях цветового зрения; единый стиль графиков — фаза 6).
COLOR_PLAN = "#2a78d6"
COLOR_FACT = "#eb6834"
HOURS_STEP = 0.5
BILLED_STEP = 10_000.0
DELETE_PENDING = "actuals_delete_pending"
UNPLANNED_STAGE = "unplanned_stage"
UNPLANNED_EMPLOYEE = "unplanned_employee"
UNPLANNED_HOURS = "unplanned_hours"
UNPLANNED_MESSAGE = "unplanned_message"

state.init_defaults()
state.persist_widgets()
render_project_switcher()

st.title("📈 План–факт")
scenario = require_scenario()

if state.actuals() is None:
    st.info(
        f"Факт по проекту «{state.slot_title(state.active_id())}» не введён. Начните с факта, "
        "равного плану, и поправьте часы."
    )
    st.button("📋 Заполнить факт по плану", type="primary", on_click=state.start_actuals)
    st.stop()


# ===========================================================================
# 1. ФАКТИЧЕСКИЕ ЧАСЫ
# ===========================================================================
st.markdown("### 1. Фактические часы")
table_key = state.actuals_table_key()
if table_key not in st.session_state or state.ACTUALS_EDITOR_BASE not in st.session_state:
    st.session_state[state.ACTUALS_EDITOR_BASE] = state.actuals_rows(
        state.stages(), state.actuals() or []
    )
base_rows = st.session_state[state.ACTUALS_EDITOR_BASE]
columns = [
    state.ACTUALS_STAGE,
    state.ACTUALS_EMPLOYEE,
    state.ACTUALS_PLAN,
    state.ACTUALS_FACT,
    state.ACTUALS_NOTE,
]
edited = st.data_editor(
    pd.DataFrame(base_rows, columns=columns),
    num_rows="fixed",
    disabled=[state.ACTUALS_STAGE, state.ACTUALS_EMPLOYEE, state.ACTUALS_PLAN, state.ACTUALS_NOTE],
    column_config={
        state.ACTUALS_PLAN: st.column_config.NumberColumn(format="%.1f"),
        state.ACTUALS_FACT: st.column_config.NumberColumn(
            min_value=0.0, step=HOURS_STEP, format="%.1f", help="Пусто — факт не введён (0 ч)."
        ),
    },
    hide_index=True,
    width="stretch",
    key=table_key,
)
st.session_state[state.ACTUAL_HOURS] = state.parse_actuals_rows(edited.to_dict("records"))
st.caption(
    "План — только для чтения; факт можно править. Пустой факт считается нулём часов. "
    "Строку вне плана с нулём часов убирает следующий пересчёт."
)


def add_unplanned() -> None:
    """Колбэк формы: работа вне плана (или новые часы пары)."""
    hours = float(st.session_state[UNPLANNED_HOURS])
    if hours <= 0:
        st.session_state[UNPLANNED_MESSAGE] = "Часы должны быть больше нуля."
        return
    state.add_actual(
        str(st.session_state[UNPLANNED_STAGE]),
        str(st.session_state[UNPLANNED_EMPLOYEE]),
        to_decimal(hours),
    )


with st.form("unplanned_form", clear_on_submit=True):
    st.markdown("**➕ Работа вне плана**")
    col_stage, col_employee, col_hours = st.columns([2, 2, 1])
    col_stage.selectbox(
        "Этап", options=[stage.name for stage in state.stages()], key=UNPLANNED_STAGE
    )
    col_employee.selectbox(
        "Исполнитель", options=[member.name for member in state.team()], key=UNPLANNED_EMPLOYEE
    )
    col_hours.number_input(
        "Часы", min_value=0.0, step=HOURS_STEP, format="%.1f", key=UNPLANNED_HOURS
    )
    st.form_submit_button("➕ Добавить", on_click=add_unplanned)
message = st.session_state.pop(UNPLANNED_MESSAGE, None)
if message:
    st.error(message)

col_manual, col_billed = st.columns([2, 1])
col_manual.checkbox(
    "Выставлено клиенту вручную",
    key=state.ACTUAL_BILLED_MANUAL,
    help="Например, клиенту уступили часть часов или дали скидку с фикса.",
)
vat_note = "с НДС" if scenario.project.vat_mode == VatMode.INCLUDED else "без НДС"
if st.session_state.get(state.ACTUAL_BILLED_MANUAL):
    col_billed.number_input(
        f"Выставлено, {vat_note}",
        min_value=0.0,
        step=BILLED_STEP,
        format="%.0f",
        key=state.ACTUAL_BILLED,
    )

# Факт из редактора и формы — в сценарий (сценарий страницы собран до редактора).
scenario = state.scenario()
result: PlanFact = compute(lambda: plan_fact(scenario))
plan, fact = result.plan, result.fact
sym = CURRENCY_SYMBOLS[plan.currency]
st.caption(f"Выставлено по факту: {BILLING_RULE_LABELS[result.rule]}.")
if result.missing:
    st.warning(
        "Нет факта — считается 0 ч: "
        + "; ".join(f"{pair.stage_name} · {pair.employee}" for pair in result.missing)
    )
for item in fact.unresolved_assignments:
    st.warning(
        f"Этап «{item.stage_name}»: исполнителя «{item.employee}» нет в команде, факт не вошёл "
        "в расчёт."
    )


def ask_delete() -> None:
    st.session_state[DELETE_PENDING] = True


def confirm_delete() -> None:
    st.session_state.pop(DELETE_PENDING, None)
    state.clear_actuals()


def cancel_delete() -> None:
    st.session_state.pop(DELETE_PENDING, None)


if st.session_state.get(DELETE_PENDING):
    st.warning("Удалить факт проекта? План останется.")
    col_yes, col_no, _ = st.columns([1, 1, 3])
    col_yes.button("Да, удалить факт", on_click=confirm_delete, type="primary")
    col_no.button("Отмена", on_click=cancel_delete, key="actuals_delete_cancel")
else:
    st.button("🗑 Удалить факт", on_click=ask_delete)


# ===========================================================================
# 2. ИТОГ «ПЛАН → ФАКТ»
# ===========================================================================
st.markdown("---")
render_header(scenario, plan)
st.markdown("### 2. План → факт")
m1, m2, m3, m4 = st.columns(4)
m1.metric(
    f"Выставлено без НДС, {sym}",
    money(fact.revenue, sym),
    delta=signed_money(fact.revenue - plan.revenue, sym),
)
m2.metric(
    f"Издержки, {sym}",
    money(fact.total_costs, sym),
    delta=signed_money(fact.total_costs - plan.total_costs, sym),
    delta_color="inverse",
)
m3.metric(f"NNE по факту, {sym}", money(fact.nne, sym), delta=signed_money(result.delta_nne, sym))
m4.metric("Маржа по факту", pct_label(fact.margin), delta=points_label(fact.margin - plan.margin))

plan_real, fact_real = result.plan_realization, result.fact_realization


rows = [
    (
        "Часы",
        hours_label(plan.total_hours),
        hours_label(fact.total_hours),
        signed_hours(fact.total_hours - plan.total_hours),
    ),
    (
        "Теневая стоимость (часы по ставкам)",
        money(plan_real.shadow_value, sym),
        money(fact_real.shadow_value, sym),
        signed_money(fact_real.shadow_value - plan_real.shadow_value, sym),
    ),
    (
        "Выставлено без НДС",
        money(plan.revenue, sym),
        money(fact.revenue, sym),
        signed_money(fact.revenue - plan.revenue, sym),
    ),
    (
        "Реализация (выставлено / по ставкам)",
        pct_label(plan_real.rate),
        pct_label(fact_real.rate),
        ""
        if plan_real.rate is None or fact_real.rate is None
        else points_label(fact_real.rate - plan_real.rate),
    ),
    ("Списание", money(plan_real.write_off, sym), money(fact_real.write_off, sym), ""),
    ("Премия к ставкам", money(plan_real.premium, sym), money(fact_real.premium, sym), ""),
    (
        "Трудозатраты",
        money(plan.direct_labor, sym),
        money(fact.direct_labor, sym),
        signed_money(fact.direct_labor - plan.direct_labor, sym),
    ),
    (
        "Накладные",
        money(plan.overheads_alloc, sym),
        money(fact.overheads_alloc, sym),
        signed_money(fact.overheads_alloc - plan.overheads_alloc, sym),
    ),
    (
        "Собственные расходы",
        money(plan.disbursements_own, sym),
        money(fact.disbursements_own, sym),
        signed_money(fact.disbursements_own - plan.disbursements_own, sym),
    ),
    (
        "Налог",
        money(plan.tax.tax, sym),
        money(fact.tax.tax, sym),
        signed_money(fact.tax.tax - plan.tax.tax, sym),
    ),
    ("NNE", money(plan.nne, sym), money(fact.nne, sym), signed_money(result.delta_nne, sym)),
    (
        "Маржа",
        pct_label(plan.margin),
        pct_label(fact.margin),
        points_label(fact.margin - plan.margin),
    ),
    (
        "К оплате",
        money(plan.total_client, sym),
        money(fact.total_client, sym),
        signed_money(fact.total_client - plan.total_client, sym),
    ),
]
st.dataframe(
    pd.DataFrame(rows, columns=["Показатель", "План", "Факт", "Отклонение"]),
    hide_index=True,
    width="stretch",
)
st.caption(
    "Реализация — выставлено / теневая стоимость часов по ставкам (без НДС). Больше 100% — "
    "фиксированная цена дороже часов по ставкам: это премия, а не списание (методология, "
    "раздел 18.3)."
)


# ===========================================================================
# 3. ПЕРЕРАСХОД ПО ЭТАПАМ
# ===========================================================================
st.markdown("### 3. Перерасход по этапам")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Этап": stage.stage_name,
                "План, ч": hours_label(stage.plan_hours),
                "Факт, ч": hours_label(stage.fact_hours),
                "Δ ч": signed_hours(stage.delta_hours),
                "Δ %": "—" if stage.delta_share is None else signed_pct(stage.delta_share),
                "Δ издержек": signed_money(stage.delta_cost, sym),
                "Маржа: план → факт": (
                    f"{'—' if stage.plan is None else pct_label(stage.plan.margin)} → "
                    f"{'—' if stage.fact is None else pct_label(stage.fact.margin)}"
                ),
                "Светофор: план → факт": (
                    f"{'—' if stage.plan is None else STAGE_FLAG_LABELS[stage.plan.flag]} → "
                    f"{'—' if stage.fact is None else STAGE_FLAG_LABELS[stage.fact.flag]}"
                ),
            }
            for stage in result.stages
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption("Δ издержек этапа — трудозатраты и накладные его часов, без собственных расходов.")


# ===========================================================================
# 4. ПЕРЕРАСХОД ПО РОЛЯМ И ИСПОЛНИТЕЛЯМ
# ===========================================================================
st.markdown("### 4. Перерасход по ролям и исполнителям")
total_cost = decimal_sum(item.delta_cost for item in result.employees)
st.dataframe(
    pd.DataFrame(
        [
            {
                "Исполнитель": item.employee,
                "Роль": ROLE_LABELS[item.role],
                "План, ч": hours_label(item.plan_hours),
                "Факт, ч": hours_label(item.fact_hours),
                "Δ ч": signed_hours(item.delta_hours),
                "Из них вне плана": ""
                if item.unplanned_hours == 0
                else hours_label(item.unplanned_hours),
                "Час с накладными": money(item.cost_per_hour, sym),
                "Δ издержек": signed_money(item.delta_cost, sym),
            }
            for item in result.employees
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption(
    f"Δ издержек = (себестоимость часа + ставка накладных) × Δ часов; всего "
    f"{signed_money(total_cost, sym)}."
)
roles = list(result.roles)
figure = go.Figure()
for name, values, color in (
    ("План", [item.plan_hours for item in roles], COLOR_PLAN),
    ("Факт", [item.fact_hours for item in roles], COLOR_FACT),
):
    figure.add_trace(
        go.Bar(
            x=[ROLE_LABELS[item.role] for item in roles],
            y=[float(value) for value in values],
            name=name,
            marker={"color": color},
            hovertemplate=f"<b>%{{x}}</b>, {name.lower()}: %{{y:,.1f}} ч<extra></extra>",
        )
    )
figure.update_layout(
    barmode="group",
    bargap=0.35,
    height=320,
    margin={"t": 10, "b": 10, "l": 10, "r": 10},
    legend={"orientation": "h", "yanchor": "bottom", "y": 1.0},
    yaxis={"title": "Часы"},
)
st.plotly_chart(figure, width="stretch")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Роль": ROLE_LABELS[item.role],
                "План, ч": hours_label(item.plan_hours),
                "Факт, ч": hours_label(item.fact_hours),
                "Δ ч": signed_hours(item.delta_hours),
                "Δ издержек": signed_money(item.delta_cost, sym),
            }
            for item in roles
        ]
    ),
    hide_index=True,
    width="stretch",
)
