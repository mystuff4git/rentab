"""Шаг 5 «План–факт» (docs/methodology.md, раздел 18).

Фактические часы вводятся по парам «этап × сотрудник» в редакторе: план
только для чтения, факт — редактируется; работа вне плана добавляется
формой. На экране у редактора три столбца (пара, план, факт): на узком экране
он помещается без прокрутки. Выставлено по факту — по модели цены или
вручную. Расчёт — `rentab_core.analysis.plan_fact`; факт хранится в слоте
проекта и в файле профиля.

Редактор факта (L-21): его вход — неизменяемая база в сессии; она
пересобирается из факта только для нового виджета (новый проект, «заполнить
по плану», строка вне плана), а не из результата самого редактора.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from rentab_core.analysis.plan_fact import EmployeeVariance, PlanFact, plan_fact
from rentab_core.analysis.stages import classify
from rentab_core.models import ROLE_LABELS, VatMode
from rentab_core.money import CURRENCY_SYMBOLS, decimal_sum, to_decimal
from rentab_core.results import StageResult
from ui import charts, formulas, nav, state
from ui.analytics import compute, render_header, require_scenario
from ui.components import (
    FLAG_STATUS,
    Action,
    Cell,
    CellValue,
    Kpi,
    Row,
    Table,
    empty_state,
    kpi_grid,
    render_table,
)
from ui.labels import (
    BILLING_RULE_LABELS,
    DASH,
    hours_label,
    metric_delta,
    metric_points,
    money,
    pct_label,
    points_label,
    signed_hours,
    signed_money,
    signed_pct,
)

HOURS_STEP = 0.5
BILLED_STEP = 10_000.0
DELETE_PENDING = "actuals_delete_pending"
UNPLANNED_STAGE = "unplanned_stage"
UNPLANNED_EMPLOYEE = "unplanned_employee"
UNPLANNED_HOURS = "unplanned_hours"
UNPLANNED_MESSAGE = "unplanned_message"

st.title("План–факт")
scenario = require_scenario("plan-fact")

if state.actuals() is None:
    empty_state(
        "actuals",
        "⏱️",
        f"Факт по проекту «{state.slot_title(state.active_id())}» не введён",
        "Начните с факта, равного плану, и поправьте часы: смета по факту, реализация и "
        "перерасход появятся сразу.",
        (Action("📋 Заполнить факт по плану", on_click=state.start_actuals),),
    )
    nav.footer(nav.PLAN_FACT)
    st.stop()


# ===========================================================================
# 1. ФАКТИЧЕСКИЕ ЧАСЫ
# ===========================================================================
st.markdown("#### Фактические часы")
table_key = state.actuals_table_key()
if table_key not in st.session_state or state.ACTUALS_EDITOR_BASE not in st.session_state:
    st.session_state[state.ACTUALS_EDITOR_BASE] = state.actuals_rows(
        state.stages(), state.actuals() or []
    )
base_rows = st.session_state[state.ACTUALS_EDITOR_BASE]
edited = st.data_editor(
    pd.DataFrame(base_rows, columns=list(state.ACTUALS_COLUMNS)),
    num_rows="fixed",
    disabled=[column for column in state.ACTUALS_COLUMNS if column != state.ACTUALS_FACT],
    column_order=list(state.ACTUALS_VISIBLE),
    column_config={
        # Ширины столбцов — чтобы три столбца поместились в экран телефона.
        state.ACTUALS_LABEL: st.column_config.TextColumn(width="medium"),
        state.ACTUALS_PLAN: st.column_config.NumberColumn(format="%.1f", width="small"),
        state.ACTUALS_FACT: st.column_config.NumberColumn(
            min_value=0.0,
            step=HOURS_STEP,
            format="%.1f",
            width="small",
            help="Пусто — факт не введён (0 ч).",
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
    st.markdown("**Работа вне плана**")
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
result: PlanFact = compute(scenario, lambda: plan_fact(scenario))
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
    col_yes, col_no = st.columns(2)
    col_yes.button("Да, удалить факт", on_click=confirm_delete, type="primary")
    col_no.button("Отмена", on_click=cancel_delete, key="actuals_delete_cancel")
else:
    st.button("🗑 Удалить факт", on_click=ask_delete)


# ===========================================================================
# 2. ИТОГ «ПЛАН → ФАКТ»
# ===========================================================================
st.divider()
render_header(scenario, plan)
st.markdown("#### План → факт")
plan_real, fact_real = result.plan_realization, result.fact_realization
fact_flag = classify(fact.margin, fact.target_margin)
kpi_grid(
    [
        Kpi(
            "fact-billed",
            "Выставлено без НДС",
            money(fact.revenue, sym),
            formulas.billed(result, BILLING_RULE_LABELS[result.rule]),
            delta=metric_delta(fact.revenue - plan.revenue, sym),
        ),
        Kpi(
            "fact-costs",
            "Издержки",
            money(fact.total_costs, sym),
            formulas.fact_costs(result),
            delta=metric_delta(fact.total_costs - plan.total_costs, sym),
            delta_color="inverse",
        ),
        Kpi(
            "fact-nne",
            "NNE по факту",
            money(fact.nne, sym),
            formulas.fact_nne(result),
            status=FLAG_STATUS[fact_flag],
            delta=metric_delta(result.delta_nne, sym),
        ),
        Kpi(
            "fact-margin",
            "Маржа по факту",
            pct_label(fact.margin),
            formulas.fact_margin(result),
            status=FLAG_STATUS[fact_flag],
            delta=metric_points(fact.margin - plan.margin),
        ),
        Kpi(
            "fact-realization",
            "Реализация",
            pct_label(fact_real.rate),
            formulas.realization(fact_real.rate, fact.revenue, fact_real.shadow_value, sym),
            caption=f"по плану {pct_label(plan_real.rate)}",
        ),
        Kpi(
            "fact-hours",
            "Часы",
            hours_label(fact.total_hours),
            formulas.project_hours(fact.total_hours),
            caption=f"по плану {hours_label(plan.total_hours)}",
        ),
    ],
    columns=3,
)


def summary_row(label: str, plan_text: str, fact_text: str, delta: str = "") -> dict[str, str]:
    """Строка итога «план → факт»."""
    return {"Показатель": label, "План": plan_text, "Факт": fact_text, "Отклонение": delta}


summary = [
    summary_row(
        "Часы",
        hours_label(plan.total_hours),
        hours_label(fact.total_hours),
        signed_hours(fact.total_hours - plan.total_hours),
    ),
    summary_row(
        "Теневая стоимость (часы по ставкам)",
        money(plan_real.shadow_value, sym),
        money(fact_real.shadow_value, sym),
        signed_money(fact_real.shadow_value - plan_real.shadow_value, sym),
    ),
    summary_row(
        "Выставлено без НДС",
        money(plan.revenue, sym),
        money(fact.revenue, sym),
        signed_money(fact.revenue - plan.revenue, sym),
    ),
    summary_row(
        "Реализация (выставлено / по ставкам)",
        pct_label(plan_real.rate),
        pct_label(fact_real.rate),
        ""
        if plan_real.rate is None or fact_real.rate is None
        else points_label(fact_real.rate - plan_real.rate),
    ),
    summary_row("Списание", money(plan_real.write_off, sym), money(fact_real.write_off, sym)),
    summary_row("Премия к ставкам", money(plan_real.premium, sym), money(fact_real.premium, sym)),
    summary_row(
        "Трудозатраты",
        money(plan.direct_labor, sym),
        money(fact.direct_labor, sym),
        signed_money(fact.direct_labor - plan.direct_labor, sym),
    ),
    summary_row(
        "Накладные",
        money(plan.overheads_alloc, sym),
        money(fact.overheads_alloc, sym),
        signed_money(fact.overheads_alloc - plan.overheads_alloc, sym),
    ),
    summary_row(
        "Собственные расходы",
        money(plan.disbursements_own, sym),
        money(fact.disbursements_own, sym),
        signed_money(fact.disbursements_own - plan.disbursements_own, sym),
    ),
    summary_row(
        "Налог",
        money(plan.tax.tax, sym),
        money(fact.tax.tax, sym),
        signed_money(fact.tax.tax - plan.tax.tax, sym),
    ),
    summary_row(
        "NNE", money(plan.nne, sym), money(fact.nne, sym), signed_money(result.delta_nne, sym)
    ),
    summary_row(
        "Маржа",
        pct_label(plan.margin),
        pct_label(fact.margin),
        points_label(fact.margin - plan.margin),
    ),
    summary_row(
        "К оплате",
        money(plan.total_client, sym),
        money(fact.total_client, sym),
        signed_money(fact.total_client - plan.total_client, sym),
    ),
]
render_table(
    Table(
        columns=["Показатель", "План", "Факт", "Отклонение"],
        rows=summary,
        numeric=frozenset({"План", "Факт", "Отклонение"}),
        key="plan-fact-summary",
        stack=False,
    )
)
st.caption(
    "Реализация — выставлено / теневая стоимость часов по ставкам (без НДС). Больше 100% — "
    "фиксированная цена дороже часов по ставкам: это премия, а не списание (методология, "
    "раздел 18.3)."
)


# ===========================================================================
# 3. ПЕРЕРАСХОД ПО ЭТАПАМ
# ===========================================================================
def stage_margin(stage: StageResult | None) -> str:
    """Маржа этапа для «план → факт»."""
    return DASH if stage is None else pct_label(stage.margin)


st.markdown("#### Перерасход по этапам")
render_table(
    Table(
        columns=["Этап", "Статус по факту", "Маржа: план → факт", "Δ ч", "Δ %", "Δ издержек"],
        rows=[
            {
                "Этап": Cell(
                    stage.stage_name,
                    note=f"{hours_label(stage.plan_hours)} → {hours_label(stage.fact_hours)}",
                ),
                "Статус по факту": DASH if stage.fact is None else Cell("", flag=stage.fact.flag),
                "Маржа: план → факт": f"{stage_margin(stage.plan)} → {stage_margin(stage.fact)}",
                "Δ ч": signed_hours(stage.delta_hours),
                "Δ %": DASH if stage.delta_share is None else signed_pct(stage.delta_share),
                "Δ издержек": signed_money(stage.delta_cost, sym),
            }
            for stage in result.stages
        ],
        numeric=frozenset({"Маржа: план → факт", "Δ ч", "Δ %", "Δ издержек"}),
        key="plan-fact-stages",
    )
)
st.caption("Δ издержек этапа — трудозатраты и накладные его часов, без собственных расходов.")


# ===========================================================================
# 4. ПЕРЕРАСХОД ПО РОЛЯМ И ИСПОЛНИТЕЛЯМ
# ===========================================================================
def employee_row(item: EmployeeVariance) -> dict[str, CellValue]:
    """Строка перерасхода исполнителя."""
    unplanned = (
        "" if item.unplanned_hours == 0 else f" · вне плана {hours_label(item.unplanned_hours)}"
    )
    return {
        "Исполнитель": Cell(item.employee, note=ROLE_LABELS[item.role] + unplanned),
        "План, ч": hours_label(item.plan_hours),
        "Факт, ч": hours_label(item.fact_hours),
        "Δ ч": signed_hours(item.delta_hours),
        "Час с накладными": money(item.cost_per_hour, sym),
        "Δ издержек": signed_money(item.delta_cost, sym),
    }


st.markdown("#### Перерасход по ролям и исполнителям")
total_cost = decimal_sum(item.delta_cost for item in result.employees)
render_table(
    Table(
        columns=["Исполнитель", "План, ч", "Факт, ч", "Δ ч", "Час с накладными", "Δ издержек"],
        rows=[
            *(employee_row(item) for item in result.employees),
            Row(
                cells={"Исполнитель": "Итого", "Δ издержек": signed_money(total_cost, sym)},
                total=True,
            ),
        ],
        numeric=frozenset({"План, ч", "Факт, ч", "Δ ч", "Час с накладными", "Δ издержек"}),
        key="plan-fact-employees",
    )
)
st.caption("Δ издержек = (себестоимость часа + ставка накладных) × Δ часов (раздел 18.4).")
roles = list(result.roles)
charts.show(
    charts.paired_bars(
        [ROLE_LABELS[item.role] for item in roles],
        [
            (
                "План",
                [item.plan_hours for item in roles],
                [hours_label(item.plan_hours) for item in roles],
            ),
            (
                "Факт",
                [item.fact_hours for item in roles],
                [hours_label(item.fact_hours) for item in roles],
            ),
        ],
        horizontal=False,
        axis_title="Часы",
    )
)
render_table(
    Table(
        columns=["Роль", "План, ч", "Факт, ч", "Δ ч", "Δ издержек"],
        rows=[
            {
                "Роль": ROLE_LABELS[item.role],
                "План, ч": hours_label(item.plan_hours),
                "Факт, ч": hours_label(item.fact_hours),
                "Δ ч": signed_hours(item.delta_hours),
                "Δ издержек": signed_money(item.delta_cost, sym),
            }
            for item in roles
        ],
        numeric=frozenset({"План, ч", "Факт, ч", "Δ ч", "Δ издержек"}),
        key="plan-fact-roles",
    )
)

nav.footer(nav.PLAN_FACT)
