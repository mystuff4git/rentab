"""Шаг 3 «Результат»: вкладки «Смета» и «Чувствительность».

Смета считается по текущим данным активного проекта (`rentab_core`), кнопки
«Рассчитать» нет. «Смета»: KPI-карточки с формулами, структура выручки,
светофор этапов, исполнители и расходы, документы: КП в PDF и DOCX и смета
XLSX с живыми формулами (собираются ядром по нажатию кнопки). «Чувствительность» —
запас часов, торнадо и «что если» (`ui.sensitivity_tab`). Вкладка — виджет с
ключом: открытая вкладка переживает перезапуск и переход между страницами, и
считается только она. Все суммы — в валюте проекта: пересчёт валют выполняет
ядро (L-06); маржа и эффективная ставка налога — от выручки без НДС (L-15).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import streamlit as st
from pydantic import ValidationError

from rentab_core import RentabError, dashboard
from rentab_core.analysis.plan_fact import plan_fact
from rentab_core.analysis.sensitivity import BreakEvenStatus, break_even
from rentab_core.analysis.stages import classify
from rentab_core.costing import billable_capacity, total_overheads_monthly
from rentab_core.documents import OfferTerms
from rentab_core.estimate import calculate_estimate
from rentab_core.export import estimate_xlsx, offer_document, offer_docx, offer_pdf
from rentab_core.models import CappedPricing, FixedPricing, Scenario
from rentab_core.money import CURRENCY_SYMBOLS
from rentab_core.results import Estimate, StageFlag
from ui import charts, formulas, nav, sensitivity_tab, state
from ui.analytics import compute, render_header, require_scenario, scenario_regime
from ui.components import FLAG_STATUS, Cell, Kpi, Row, Table, kpi_grid, render_table
from ui.labels import (
    DASH,
    EXPENSE_TYPE_LABELS,
    PROFITABILITY_MESSAGES,
    employee_title,
    flag_badge,
    hours_label,
    money,
    money_rate,
    number_label,
    pct_label,
    signed_hours,
)

TAB_ESTIMATE = "Смета"
TAB_SENSITIVITY = "Чувствительность"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
DATE_FORMAT = "%d.%m.%Y"
LEVERAGE_QUANTUM = Decimal("0.01")


def open_sensitivity() -> None:
    """Колбэк: открыть вкладку «Чувствительность»."""
    st.session_state[state.RESULT_TAB] = TAB_SENSITIVITY


def render_kpis(scenario: Scenario, estimate: Estimate) -> None:
    """Главные и вспомогательные показатели с формулами в подсказках."""
    sym = CURRENCY_SYMBOLS[estimate.currency]
    flag = classify(estimate.margin, estimate.target_margin)
    view = dashboard(estimate)
    firm = scenario.firm
    try:
        overheads_total = total_overheads_monthly(
            firm.overheads, estimate.currency, firm.exchange_rates
        )
    except RentabError:
        overheads_total = Decimal(0)
    capacity = billable_capacity(firm.team)
    tax_share = estimate.tax.tax / estimate.revenue if estimate.revenue > 0 else None
    kpi_grid(
        [
            Kpi(
                "revenue",
                "Выручка без НДС",
                money(estimate.revenue, sym),
                formulas.revenue(estimate),
            ),
            Kpi(
                "total",
                "К оплате клиентом",
                money(estimate.total_client, sym),
                formulas.total_client(estimate),
                caption=f"НДС {money(estimate.vat, sym)} · возмещаемые "
                f"{money(estimate.disbursements_billed, sym)}",
            ),
            Kpi(
                "nne",
                "Чистая прибыль (NNE)",
                money(estimate.nne, sym),
                formulas.nne(estimate),
                status=FLAG_STATUS[flag],
                caption=PROFITABILITY_MESSAGES[view.profitability],
            ),
            Kpi(
                "margin",
                "Маржа NNE",
                pct_label(estimate.margin),
                formulas.margin(estimate),
                status=FLAG_STATUS[flag],
                caption=f"цель {pct_label(estimate.target_margin, Decimal(1))}",
            ),
        ]
    )
    kpi_grid(
        [
            Kpi(
                "costs",
                "Издержки",
                money(estimate.total_costs, sym),
                formulas.costs(estimate),
            ),
            Kpi("tax", "Налог", money(estimate.tax.tax, sym), formulas.tax(estimate)),
            Kpi(
                "effective-tax",
                "Эффективная ставка налога",
                pct_label(tax_share),
                formulas.effective_tax_rate(estimate),
            ),
            Kpi(
                "underutilization",
                "Стоимость недозагрузки",
                money(estimate.underutilization_cost, sym),
                formulas.underutilization(estimate),
            ),
            Kpi(
                "blended",
                "Средневзвешенная ставка",
                money_rate(estimate.blended_rate, sym),
                formulas.blended_rate(
                    estimate.shadow_value, estimate.total_hours, estimate.blended_rate, sym
                ),
            ),
            Kpi(
                "leverage",
                "Коэффициент рычага",
                number_label(estimate.leverage, LEVERAGE_QUANTUM),
                formulas.leverage(estimate.lines, estimate.leverage),
            ),
            Kpi(
                "overhead-rate",
                "Ставка накладных",
                money_rate(estimate.overhead_rate, sym),
                formulas.overhead_rate(estimate.overhead_rate, overheads_total, capacity, sym),
            ),
            Kpi(
                "hours",
                "Часов в проекте",
                hours_label(estimate.total_hours),
                formulas.project_hours(estimate.total_hours),
            ),
        ]
    )


def render_notes(estimate: Estimate) -> None:
    """Пояснения к смете: потолок, уменьшение налога, исполнители не из команды."""
    sym = CURRENCY_SYMBOLS[estimate.currency]
    if isinstance(estimate.pricing, CappedPricing) and estimate.write_off > 0:
        st.caption(
            f"Потолок сработал: по ставкам {money(estimate.shadow_value_net, sym)}, "
            f"списано {money(estimate.write_off, sym)}."
        )
    if isinstance(estimate.pricing, FixedPricing) and estimate.pricing.price is None:
        st.caption(
            "Цена «издержки + маржа» при целевой марже "
            f"{pct_label(estimate.target_margin, Decimal(1))}: НДС в цену не закладывается, он "
            "начисляется сверху."
        )
    if estimate.tax.deduction > 0:
        capped = " — упёрлось в предел 50%" if estimate.tax.deduction_capped else ""
        st.caption(
            "Налог УСН «доходы» уменьшен на страховые взносы на часах проекта: "
            f"{money(estimate.tax.deduction, sym)} из {money(estimate.tax.contributions, sym)}"
            f"{capped}."
        )
    if estimate.tax.nondeductible > 0:
        st.caption(
            f"Вознаграждение из прибыли {money(estimate.tax.nondeductible, sym)} не уменьшает "
            "базу налога «доходы − расходы»."
        )
    for item in estimate.unresolved_assignments:
        st.warning(
            f"Этап «{item.stage_name}»: исполнителя «{item.employee}» нет в команде, "
            "назначение не вошло в расчёт."
        )


def render_teaser(scenario: Scenario, estimate: Estimate) -> None:
    """Запас часов и NNE по факту — со ссылками на вкладку и шаг."""
    symbol = CURRENCY_SYMBOLS[estimate.currency]
    try:
        mix = break_even(scenario).rows[0]
    except RentabError:
        return
    if mix.status == BreakEvenStatus.HEADROOM and mix.extra_hours is not None:
        text = f"Запас часов до безубыточности (микс проекта): **{signed_hours(mix.extra_hours)}**"
    elif mix.status == BreakEvenStatus.NOT_REACHED:
        text = "Часы сверх плана оплачивает клиент: точка безубыточности не наступает."
    else:
        text = "Проект убыточен по плану: что делать с часами — на вкладке «Чувствительность»."
    col_text, col_link = st.columns([3, 1], vertical_alignment="center")
    col_text.markdown(text)
    col_link.button(
        "Чувствительность",
        icon=":material/query_stats:",
        on_click=open_sensitivity,
        key="btn_open_sensitivity",
    )
    if scenario.project.actuals is not None:
        try:
            fact = plan_fact(scenario).fact
        except (RentabError, ValidationError):
            return
        col_text, col_link = st.columns([3, 1], vertical_alignment="center")
        col_text.markdown(
            f"NNE по факту: **{money(fact.nne, symbol)}** (по плану {money(estimate.nne, symbol)})"
        )
        col_link.page_link(nav.PLAN_FACT, label="План–факт", icon=":material/fact_check:")


def render_structure(estimate: Estimate) -> None:
    """Структура выручки без НДС: издержки, налог и прибыль."""
    sym = CURRENCY_SYMBOLS[estimate.currency]
    structure = dashboard(estimate).price_structure
    st.markdown("#### Структура выручки без НДС")
    layers = [
        ("Трудозатраты", structure.direct_labor),
        ("Накладные", structure.overheads),
        ("Собственные расходы", structure.own),
        ("Налог", structure.tax),
        ("Прибыль (NNE)", structure.profit),
    ]
    charts.show(
        charts.price_structure(
            [(name, value, money(value, sym)) for name, value in layers], f"Сумма, {sym}"
        )
    )
    if structure.profit < 0:
        st.caption(
            f"Убыток {money(-structure.profit, sym)}: издержки и налог больше выручки, "
            "полоса прибыли уходит влево от нуля."
        )


def render_stages(estimate: Estimate) -> None:
    """Светофор этапов."""
    sym = CURRENCY_SYMBOLS[estimate.currency]
    st.markdown("#### Этапы — светофор")
    if not estimate.stages:
        st.caption("Нет этапов для анализа.")
        return
    render_table(
        Table(
            columns=["Этап", "Статус", "Маржа", "NNE", "Выручка", "Издержки", "Налог", "Часы"],
            rows=[
                {
                    "Этап": stage.stage_name,
                    "Статус": Cell("", flag=stage.flag),
                    "Маржа": pct_label(stage.margin),
                    "NNE": money(stage.nne, sym),
                    "Выручка": money(stage.revenue, sym),
                    "Издержки": money(stage.costs, sym),
                    "Налог": money(stage.tax, sym),
                    "Часы": hours_label(stage.hours),
                }
                for stage in estimate.stages
            ],
            numeric=frozenset({"Маржа", "NNE", "Выручка", "Издержки", "Налог", "Часы"}),
            key="stages",
        )
    )
    target = pct_label(estimate.target_margin, Decimal(1))
    st.markdown(
        f"{flag_badge(StageFlag.GREEN)} не ниже цели {target} &nbsp; "
        f"{flag_badge(StageFlag.YELLOW)} ниже цели &nbsp; {flag_badge(StageFlag.RED)} NNE ≤ 0"
    )
    st.caption(
        "Выручка этапа — доля по теневой стоимости (ставка × часы); маржа — после накладных, "
        "собственных расходов и налога (методология, раздел 8)."
    )


def render_lines(estimate: Estimate) -> None:
    """Исполнители и расходы проекта."""
    sym = CURRENCY_SYMBOLS[estimate.currency]
    with st.expander("Исполнители"):
        render_table(
            Table(
                columns=[
                    "Исполнитель",
                    "Этап",
                    "Часы",
                    "Ставка",
                    "Себестоимость часа",
                    "Трудозатраты",
                ],
                rows=[
                    *(
                        {
                            "Исполнитель": employee_title(line.employee_name, line.role),
                            "Этап": line.stage_name,
                            "Часы": hours_label(line.hours),
                            "Ставка": money_rate(line.billing_rate, sym),
                            "Себестоимость часа": money_rate(line.cost_rate, sym),
                            "Трудозатраты": money(line.labor_cost, sym),
                        }
                        for line in estimate.lines
                    ),
                    Row(
                        cells={
                            "Исполнитель": "Итого",
                            "Часы": hours_label(estimate.total_hours),
                            "Трудозатраты": money(estimate.direct_labor, sym),
                        },
                        total=True,
                    ),
                ],
                numeric=frozenset({"Часы", "Ставка", "Себестоимость часа", "Трудозатраты"}),
                key="lines",
            )
        )
    if estimate.disbursements:
        with st.expander("Расходы проекта"):
            render_table(
                Table(
                    columns=["Расход", "Тип", "Этап", "Сумма", "В валюте проекта"],
                    rows=[
                        {
                            "Расход": item.name,
                            "Тип": EXPENSE_TYPE_LABELS[item.kind],
                            "Этап": item.stage or DASH,
                            "Сумма": money(
                                item.original_amount, CURRENCY_SYMBOLS[item.original_currency]
                            ),
                            "В валюте проекта": money(item.amount, sym),
                        }
                        for item in estimate.disbursements
                    ],
                    numeric=frozenset({"Сумма", "В валюте проекта"}),
                    key="disbursements",
                )
            )


def render_documents(scenario: Scenario, estimate: Estimate) -> None:
    """КП в PDF и DOCX и смета XLSX: файлы собираются ядром по нажатию кнопки."""
    st.markdown("#### Документы")
    today = dt.date.today()
    terms = state.offer_terms() or OfferTerms()
    document = offer_document(
        scenario, estimate, issue_date=today, letterhead=state.letterhead(), terms=terms
    )
    regime = scenario_regime(scenario)
    firm_name = scenario.firm.name

    def xlsx() -> bytes:
        return estimate_xlsx(
            estimate,
            scenario=scenario,
            firm_name=firm_name,
            issue_date=today,
            regime_label=regime,
            validity_days=terms.validity_days,
        )

    st.caption(
        f"Коммерческое предложение {document.subtitle}, действует до "
        f"{document.valid_until.strftime(DATE_FORMAT)}: этапы и цены без НДС, НДС, "
        "возмещаемые расходы и условия. Смета XLSX — лист для клиента и внутренний "
        "расчёт с живыми формулами Excel."
    )
    if not firm_name.strip() or state.letterhead() is None:
        col_text, col_link = st.columns([3, 1], vertical_alignment="center")
        col_text.info(
            "В шапке КП не хватает названия фирмы или реквизитов.", icon=":material/info:"
        )
        col_link.page_link(nav.FIRM, label="Реквизиты", icon=":material/badge:")
    title = estimate.project_name.strip() or "project"
    col_pdf, col_docx, col_xlsx = st.columns(3)
    # Файлы собираются только по нажатию (data — функция): ядро возвращает байты в
    # памяти, Streamlit отдаёт их из памяти — на диск сервера ничего не пишется.
    col_pdf.download_button(
        "КП (PDF)",
        data=lambda: offer_pdf(document),
        file_name=f"КП_{title}_{today.isoformat()}.pdf",
        mime=PDF_MIME,
        icon=":material/picture_as_pdf:",
        on_click="ignore",
        width="stretch",
        key="download_offer_pdf",
    )
    col_docx.download_button(
        "КП (DOCX)",
        data=lambda: offer_docx(document),
        file_name=f"КП_{title}_{today.isoformat()}.docx",
        mime=DOCX_MIME,
        icon=":material/description:",
        on_click="ignore",
        width="stretch",
        key="download_offer_docx",
    )
    col_xlsx.download_button(
        "Смета (XLSX)",
        data=xlsx,
        file_name=f"Смета_{title}_{today.isoformat()}.xlsx",
        mime=XLSX_MIME,
        icon=":material/table_view:",
        on_click="ignore",
        width="stretch",
        key="download_estimate_xlsx",
    )


def render_estimate(scenario: Scenario, estimate: Estimate) -> None:
    """Вкладка «Смета»."""
    render_kpis(scenario, estimate)
    render_notes(estimate)
    render_teaser(scenario, estimate)
    render_structure(estimate)
    render_stages(estimate)
    render_lines(estimate)
    render_documents(scenario, estimate)


st.title("Результат")
scenario = require_scenario("result")
estimate = compute(scenario, lambda: calculate_estimate(scenario))
render_header(scenario, estimate, with_nne=False)
st.session_state.setdefault(state.RESULT_TAB, TAB_ESTIMATE)
tab_estimate, tab_sensitivity = st.tabs(
    [TAB_ESTIMATE, TAB_SENSITIVITY], key=state.RESULT_TAB, on_change="rerun"
)
if tab_estimate.open:
    with tab_estimate:
        render_estimate(scenario, estimate)
if tab_sensitivity.open:
    with tab_sensitivity:
        sensitivity_tab.render(scenario, estimate)
nav.footer(nav.RESULT)
