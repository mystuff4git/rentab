"""Шаг 2 «Проект»: этапы и исполнители, цена, НДС, пошлины и расходы.

Страница собирает этапы с исполнителями (на этапе их может быть несколько),
пошлины и расходы проекта, модель цены и режим НДС. Внизу — сводка сметы по
текущим данным (`rentab_core.calculate_estimate`): кнопки «Рассчитать» нет,
смета пересчитывается при каждом изменении. Подробности — на шаге
«Результат». Вся математика — в ядре; здесь только ввод и показ. Проектов в
сессии несколько: активный выбирается в боковой панели (`ui.projects`).
"""

from __future__ import annotations

import streamlit as st

from rentab_core import CurrencyConversionError
from rentab_core.analysis.stages import classify
from rentab_core.analysis.team import blended_rate, leverage
from rentab_core.duties import (
    APPLICANT_CATEGORY_LABELS,
    QUANTITY_DEFAULTS,
    QUANTITY_LABELS,
    ApplicantCategory,
    Duty,
    Quantity,
    load_catalog,
)
from rentab_core.estimate import resolve_stages
from rentab_core.models import (
    Assignment,
    Country,
    Disbursement,
    DisbursementKind,
    JurisdictionMode,
    Project,
    Stage,
    VatMode,
)
from rentab_core.money import CURRENCY_SYMBOLS, Currency, decimal_sum, to_decimal
from rentab_core.results import Estimate, StageFlag
from ui import formulas, nav, state
from ui.analytics import require_firm, scenario_regime
from ui.components import FLAG_STATUS, Kpi, Table, empty_state, kpi_grid, render_table
from ui.labels import (
    COUNTRY_LABELS,
    CUSTOM_EXPENSE_LABELS,
    DASH,
    NEEDS_REVIEW_MARK,
    PATENT_OFFICE_LABELS,
    PRICING_LABELS,
    VAT_MODE_LABELS,
    employee_title,
    hours_label,
    money,
    money_rate,
    number_label,
    pct_label,
)

# Шаг слайдера и поля целевой маржи, % (границы — ui.state).
TARGET_MARGIN_STEP_PCT = 1
NEW_STAGE = "➕ Новый этап"
NO_STAGE = "Без привязки (по теневой стоимости)"

LEVERAGE_QUANTUM = to_decimal("0.01")

st.title("Проект")
st.caption(
    "Шаг 2: этапы и исполнители, модель цены и НДС, пошлины и расходы. Смета "
    "пересчитывается сразу — сводка внизу, подробности на шаге «Результат»."
)
require_firm("project")
team = state.team()
jurisdiction = state.jurisdiction()
assert jurisdiction is not None  # require_firm остановил бы страницу

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
st.subheader("Данные проекта")
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

# ===========================================================================
# БЛОК 2 — ЭТАПЫ И ИСПОЛНИТЕЛИ
# ===========================================================================
st.subheader("Этапы и исполнители")
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


def delete_assignment(stage_idx: int, assignment_idx: int) -> None:
    """Колбэк 🗑: убрать исполнителя с этапа; последний — вместе с этапом."""
    current = state.stages()
    stage = current[stage_idx]
    remaining = tuple(a for i, a in enumerate(stage.assignments) if i != assignment_idx)
    if remaining:
        current[stage_idx] = stage.model_copy(update={"assignments": remaining})
        return
    # Этап удалён: привязанные к нему расходы делятся по всем этапам, факт по нему удаляется.
    current.pop(stage_idx)
    state.drop_stage_actuals(stage.name)
    expenses = state.custom_expenses()
    expenses[:] = [
        item.model_copy(update={"stage": None}) if item.stage == stage.name else item
        for item in expenses
    ]


team_by_name = {member.name: member for member in team}
if not stages:
    empty_state(
        "stages",
        "🗂️",
        "Пока нет этапов",
        "Добавьте первый этап формой выше: название этапа, исполнитель и часы. На этап можно "
        "добавить несколько исполнителей.",
    )
for stage_idx, stage in enumerate(stages):
    total = decimal_sum(a.hours for a in stage.assignments)
    with st.container(border=True, key=f"stage-{stage_idx}"):
        st.markdown(f"**{stage_idx + 1}. {stage.name}** · {hours_label(total)}")
        for assignment_idx, assignment in enumerate(stage.assignments):
            member = team_by_name.get(assignment.employee)
            col_who, col_hours, col_value, col_delete = st.columns(
                [2.5, 0.8, 1.4, 0.4], vertical_alignment="center"
            )
            if member is None:
                col_who.warning(
                    f"Исполнителя «{assignment.employee}» нет в составе команды: "
                    "назначение не войдёт в расчёт."
                )
            else:
                col_who.write(employee_title(member.name, member.role))
                sym = CURRENCY_SYMBOLS[member.currency]
                col_value.write(f"{money(member.billing_rate * assignment.hours, sym)} по ставке")
            col_hours.write(hours_label(assignment.hours))
            col_delete.button(
                "",
                icon=":material/delete:",
                key=f"del_assignment_{stage_idx}_{assignment_idx}",
                help="Удалить",
                on_click=delete_assignment,
                args=(stage_idx, assignment_idx),
            )

# Показатели команды пересчитываются ядром на каждом изменении этапов.
try:
    resolved, _ = resolve_stages(
        state.firm(), Project(stages=tuple(stages)), calc_country.currency, calc_country
    )
except CurrencyConversionError as exc:
    st.warning(f"{exc}. Курсы задаются на шаге «Фирма».")
    resolved = []
lines = [line for stage_lines in resolved for line in stage_lines.lines]
if lines:
    hours_total = decimal_sum(line.hours for line in lines)
    shadow_total = decimal_sum(line.shadow_value for line in lines)
    blended = blended_rate(lines)
    lever = leverage(lines)
    kpi_grid(
        [
            Kpi(
                "project-blended",
                "Средневзвешенная ставка",
                money_rate(blended, currency_symbol),
                formulas.blended_rate(shadow_total, hours_total, blended, currency_symbol),
            ),
            Kpi(
                "project-leverage",
                "Коэффициент рычага",
                number_label(lever, LEVERAGE_QUANTUM),
                formulas.leverage(lines, lever),
            ),
            Kpi(
                "project-hours",
                "Всего часов",
                hours_label(hours_total),
                formulas.project_hours(hours_total),
            ),
        ],
        columns=3,
    )

# ===========================================================================
# БЛОК 3 — ЦЕНА
# ===========================================================================
st.subheader("Цена")
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
    st.session_state[state.TARGET_MARGIN_PCT] = int(st.session_state[state.MARGIN_SLIDER])


def _sync_margin_from_input() -> None:
    """Колбэк поля ввода: двигает слайдер, если значение в его диапазоне."""
    value = int(st.session_state[state.TARGET_MARGIN_PCT])
    if state.slider_pct(value) == value:
        st.session_state[state.MARGIN_SLIDER] = value


st.session_state.setdefault(state.TARGET_MARGIN_PCT, state.DEFAULT_TARGET_MARGIN_PCT)
st.session_state.setdefault(
    state.MARGIN_SLIDER, state.slider_pct(int(st.session_state[state.TARGET_MARGIN_PCT]))
)
margin_help = "Доля NNE в выручке без НДС после всех издержек и налога. " + (
    "Определяет фиксированную цену."
    if kind == state.PRICING_FIXED
    else "Порог светофора этапов: зелёный — не ниже цели."
)
col_slider, col_input = st.columns([3, 1])
with col_slider:
    st.slider(
        "Целевая маржа NNE (%)",
        min_value=state.TARGET_MARGIN_SLIDER_MIN_PCT,
        max_value=state.TARGET_MARGIN_SLIDER_MAX_PCT,
        step=TARGET_MARGIN_STEP_PCT,
        key=state.MARGIN_SLIDER,
        on_change=_sync_margin_from_slider,
        help=margin_help,
    )
with col_input:
    st.number_input(
        "или введите",
        min_value=state.TARGET_MARGIN_INPUT_MIN_PCT,
        max_value=state.TARGET_MARGIN_INPUT_MAX_PCT,
        step=TARGET_MARGIN_STEP_PCT,
        key=state.TARGET_MARGIN_PCT,
        on_change=_sync_margin_from_input,
        help="Точное значение — приоритет над слайдером.",
    )

# ===========================================================================
# БЛОК 4 — РАСХОДЫ ПРОЕКТА
# ===========================================================================
selected = state.selected_duties()


def project_duties(country: Country) -> list[Disbursement]:
    """Пошлины страны с количествами и категорией заявителя из виджетов."""
    return state.duty_selection(country).disbursements(country)


with st.expander("Расходы проекта — пошлины, субподряд и др. (необязательно)"):
    st.markdown("#### Пошлины из каталога")
    st.caption(
        "Пошлины — возмещаемые расходы: клиент их оплачивает отдельной строкой, "
        "в выручку и налоговую базу они не входят. Размер зависит от числа классов МКТУ "
        f"и пунктов формулы. {NEEDS_REVIEW_MARK} — сумма не сверена с первоисточником "
        "полностью (docs/rates_verification.md); пошлины без суммы в смету не попадают."
    )
    for country in jurisdiction.countries:
        catalog = load_catalog(country)
        sym = CURRENCY_SYMBOLS[catalog.currency]
        duties_by_action = {duty.action: duty for duty in catalog.duties}

        def duty_label(
            action: str, duties: dict[str, Duty] = duties_by_action, symbol: str = sym
        ) -> str:
            """Пошлина в списке: название, базовая сумма и пометка проверки."""
            duty = duties[action]
            mark = f" {NEEDS_REVIEW_MARK}" if duty.needs_review else ""
            if duty.amount <= 0:
                return f"{action} — сумма не установлена{mark}"
            return f"{action} — от {money(duty.amount, symbol)}{mark}"

        st.markdown(f"**{PATENT_OFFICE_LABELS[country]} ({sym})**")
        # Выбор задаётся через ключ, а не default: при переключении проекта
        # список остаётся на экране, и браузер не заменил бы его новым default.
        multiselect_key = state.duty_multiselect_key(country)
        st.session_state.setdefault(
            multiselect_key, [a for a in selected.get(country.value, []) if a in duties_by_action]
        )
        chosen = st.multiselect(
            f"Выберите пошлины ({country.value})",
            options=list(duties_by_action),
            format_func=duty_label,
            key=multiselect_key,
        )
        selected[country.value] = chosen
        if not chosen:
            continue
        quantities = sorted(
            {q for action in chosen for q in duties_by_action[action].quantities},
            key=list(Quantity).index,
        )
        if catalog.categories or quantities:
            columns = st.columns(max(len(quantities) + int(catalog.categories), 1))
            defaults = state.duty_quantities(country)
            for column, quantity in zip(columns, quantities, strict=False):
                key = state.duty_quantity_key(country, quantity)
                st.session_state.setdefault(key, defaults[quantity])
                column.number_input(
                    QUANTITY_LABELS[quantity],
                    min_value=0 if quantity in QUANTITY_DEFAULTS else 1,
                    step=1,
                    key=key,
                )
            if catalog.categories:
                columns[-1].selectbox(
                    "Заявитель",
                    options=list(ApplicantCategory),
                    format_func=lambda c: APPLICANT_CATEGORY_LABELS[c],
                    key=state.duty_category_key(country),
                )
        items = project_duties(country)
        if items:
            render_table(
                Table(
                    columns=["Пошлина", "Сумма"],
                    rows=[
                        {"Пошлина": item.name, "Сумма": money(item.amount, sym)} for item in items
                    ],
                    numeric=frozenset({"Сумма"}),
                    key=f"duties-{country.value}",
                )
            )
            subtotal = decimal_sum(item.amount for item in items)
            vat_note = ", с НДС" if catalog.vat_included else ""
            st.info(f"Сумма: **{money(subtotal, sym)}**{vat_note}")
        skipped = [a for a in chosen if duties_by_action[a].amount <= 0]
        if skipped:
            st.warning(
                "Без суммы, в смету не попадут: "
                + "; ".join(skipped)
                + ". Введите их как произвольный возмещаемый расход."
            )
        review = [duties_by_action[a] for a in chosen if duties_by_action[a].needs_review]
        for duty in review:
            if duty.amount > 0:
                st.caption(f"{NEEDS_REVIEW_MARK} {duty.action}: {duty.note}")

    st.divider()
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

    def delete_expense(index: int) -> None:
        """Колбэк 🗑: удалить произвольный расход."""
        state.custom_expenses().pop(index)

    custom_list = state.custom_expenses()
    if custom_list:
        st.markdown("**Текущие произвольные расходы**")
    for idx, item in enumerate(custom_list):
        col_name_, col_kind, col_amount, col_stage, col_delete = st.columns(
            [2, 2, 1, 1.5, 0.5], vertical_alignment="center"
        )
        col_name_.write(f"**{item.name}**")
        col_kind.write(CUSTOM_EXPENSE_LABELS[item.kind])
        col_amount.write(money(item.amount, CURRENCY_SYMBOLS[item.currency]))
        col_stage.write(item.stage or DASH)
        col_delete.button(
            "",
            icon=":material/delete:",
            key=f"del_custom_{idx}",
            help="Удалить",
            on_click=delete_expense,
            args=(idx,),
        )


# ===========================================================================
# БЛОК 5 — СВОДКА СМЕТЫ ПО ТЕКУЩИМ ДАННЫМ
# ===========================================================================
st.subheader("Смета проекта")
if not project_name.strip():
    st.caption("Название проекта попадёт в смету XLSX и в список проектов.")
if not any(stage.assignments for stage in stages):
    st.caption("Смета появится, когда в проекте будет хотя бы один этап с часами.")
else:
    outcome = state.estimate_outcome(state.scenario)
    if not isinstance(outcome, Estimate):
        st.error(outcome, icon="⛔")
    else:
        result = outcome
        sym = CURRENCY_SYMBOLS[result.currency]
        flag: StageFlag = classify(result.margin, result.target_margin)
        kpi_grid(
            [
                Kpi(
                    "summary-revenue",
                    "Выручка без НДС",
                    money(result.revenue, sym),
                    formulas.revenue(result),
                ),
                Kpi(
                    "summary-total",
                    "К оплате клиентом",
                    money(result.total_client, sym),
                    formulas.total_client(result),
                    caption=f"НДС {money(result.vat, sym)} · возмещаемые "
                    f"{money(result.disbursements_billed, sym)}",
                ),
                Kpi(
                    "summary-nne",
                    "Чистая прибыль (NNE)",
                    money(result.nne, sym),
                    formulas.nne(result),
                    status=FLAG_STATUS[flag],
                ),
                Kpi(
                    "summary-margin",
                    "Маржа NNE",
                    pct_label(result.margin),
                    formulas.margin(result),
                    status=FLAG_STATUS[flag],
                    caption=f"цель {pct_label(result.target_margin, to_decimal(1))}",
                ),
            ]
        )
        st.caption(
            f"Режим: {scenario_regime(state.scenario())} · издержки "
            f"{money(result.total_costs, sym)} · налог {money(result.tax.tax, sym)}"
        )
        if result.tax.deduction > 0:
            capped = " — упёрлось в предел 50%" if result.tax.deduction_capped else ""
            st.caption(
                f"Налог УСН «доходы» уменьшен на страховые взносы на часах проекта: "
                f"{money(result.tax.deduction, sym)} из {money(result.tax.contributions, sym)}"
                f"{capped}."
            )
        if result.tax.nondeductible > 0:
            st.caption(
                f"Вознаграждение из прибыли {money(result.tax.nondeductible, sym)} не уменьшает "
                "базу налога «доходы − расходы»."
            )
        for missing in result.unresolved_assignments:
            st.warning(
                f"Этап «{missing.stage_name}»: исполнителя «{missing.employee}» нет в команде, "
                "назначение не вошло в расчёт."
            )

nav.footer(nav.PROJECT)
