"""Шаг 1 «Фирма»: юрисдикция и налоги, команда, накладные.

Четыре вкладки: юрисдикция, налоги, применимость режима и курсы; команда;
накладные фирмы; реквизиты и логотип для КП (логотип проверяет ядро —
`documents.read_logo`). Ставки в подписях берутся из реестра ядра. Себестоимость
часа не вводится, а считается ядром: c = W / (H × u) (docs/methodology.md,
раздел 3); ставка взносов подставляется по окладу и тарифу (раздел 3.1.1).
Сотрудника можно изменить: карточка заполняется его данными, а новое имя
переходит в назначения и факт всех проектов сессии. Сохранения профиля на
диск, как в v0.3, нет: данные живут только в сессии (L-10); в файл JSON их
выгружает и загружает сам пользователь на главной.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from rentab_core.costing import (
    EmployeeCost,
    billable_capacity,
    employee_cost,
    overhead_rate,
    total_overheads_monthly,
)
from rentab_core.documents import MAX_LOGO_BYTES, MAX_LOGO_SIDE_PX
from rentab_core.errors import CurrencyConversionError, ExchangeRateFetchError
from rentab_core.fx import RATE_SOURCE_LABELS, fetch_rates
from rentab_core.models import (
    DEFAULT_CONTRACT_HOURS_PER_MONTH,
    DEFAULT_TARGET_UTILIZATION,
    ROLE_LABELS,
    AusnProfile,
    Country,
    Employee,
    Firm,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    LegalForm,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Period,
    RfTaxProfile,
    Role,
    SocialContributions,
    UsnObject,
    UsnProfile,
    UsnVat,
    employment_country,
)
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, ZERO, Currency, decimal_sum, to_decimal
from rentab_core.tax import ContributionBreakdown, employer_contributions
from rentab_core.tax.eligibility import Severity, check_firm
from ui import formulas, nav, state
from ui.components import Kpi, empty_state, kpi_grid
from ui.labels import (
    COMPENSATION_LABELS,
    JURISDICTION_LABELS,
    LEGAL_FORM_LABELS,
    RF_REGIME_LABELS,
    SEVERITY_ICONS,
    hours_label,
    kz_form_label,
    money,
    money_rate,
    npd_client_label,
    pct_label,
    registry_amount,
    rf_rate_pct,
    social_contributions_label,
    usn_object_label,
    vat_label,
)

DEFAULT_UTILIZATION_PCT = float(DEFAULT_TARGET_UTILIZATION * PERCENT)
MEMBER_RATE_MANUAL = state.MEMBER_RATE_MANUAL
# Ставка взносов в карточке совпадает с расчётной, если разница меньше этого, п. п.
RATE_MATCH_PCT = 0.005
MEMBER_MESSAGE = "add_member_message"
# Предел загрузчика логотипа, МБ (Streamlit принимает целые мегабайты).
LOGO_UPLOAD_LIMIT_MB = 1

# Типовые статьи накладных («Загрузить типовой шаблон», data/firm_expenses.json v0.3).
OVERHEAD_TEMPLATE: tuple[str, ...] = (
    "Аренда офиса",
    "Связь и интернет",
    "Мобильная связь",
    "1С: Бухгалтерия / CRM",
    "КонсультантПлюс",
    "Notion / облачные сервисы",
    "Зарплата ассистента (ФОТ + взносы)",
    "Бухгалтер на аутсорсе",
    "Канцтовары и расходники",
    "Прочие накладные",
)

# Редактор накладных: ключ виджета и его неизменяемая база — state.OVERHEADS_EDITOR*.
OVERHEADS_EDITOR = state.OVERHEADS_EDITOR
OVERHEADS_EDITOR_BASE = state.OVERHEADS_EDITOR_BASE
OVERHEAD_COLUMNS = ["Название", "Сумма", "Период", "Валюта"]

st.title("Фирма")
st.caption(
    "Шаг 1: юрисдикция и налоговый режим, команда с утилизацией, накладные фирмы. "
    "Данные хранятся только в этой сессии; сохранить их в файл можно на главной."
)

tab_jur, tab_team, tab_exp, tab_letterhead = st.tabs(
    ["Юрисдикция и налоги", "Команда", "Накладные", "Реквизиты и логотип"]
)

# ===========================================================================
# ВКЛАДКА 1 — ЮРИСДИКЦИЯ И НАЛОГИ
# ===========================================================================
with tab_jur:
    st.subheader("Юрисдикция")
    mode = (
        st.radio(
            "Где работает фирма?",
            options=list(JurisdictionMode),
            format_func=lambda m: JURISDICTION_LABELS[m],
            horizontal=True,
            key=state.JURISDICTION_CHOICE,
        )
        or JurisdictionMode.RF
    )

    rf_profile: RfTaxProfile | None = None
    if mode in (JurisdictionMode.RF, JurisdictionMode.BOTH):
        st.markdown("#### 🇷🇺 Параметры РФ")
        rf_regime = (
            st.radio(
                "Налоговый режим (РФ)",
                options=list(RF_REGIME_LABELS),
                format_func=lambda k: RF_REGIME_LABELS[k],
                horizontal=True,
                key=state.RF_REGIME,
            )
            or "usn"
        )
        if rf_regime == "usn":
            col_obj, col_vat, col_sc = st.columns(3)
            with col_obj:
                usn_object = (
                    st.selectbox(
                        "Объект налогообложения",
                        options=list(UsnObject),
                        format_func=usn_object_label,
                        key=state.RF_USN_OBJECT,
                    )
                    or UsnObject.INCOME
                )
            with col_vat:
                usn_vat = (
                    st.selectbox(
                        "НДС (с 2026 г.)",
                        options=list(UsnVat),
                        format_func=vat_label,
                        key=state.RF_USN_VAT,
                    )
                    or UsnVat.NONE
                )
            with col_sc:
                usn_sc = (
                    st.selectbox(
                        "Страховые взносы",
                        options=list(SocialContributions),
                        format_func=social_contributions_label,
                        key=state.RF_USN_SC,
                    )
                    or SocialContributions.STANDARD
                )
            rf_profile = UsnProfile(tax_object=usn_object, vat=usn_vat, social_contributions=usn_sc)
        elif rf_regime == "ausn":
            st.info(
                f"АУСН: налог {rf_rate_pct('ausn.income')}% с дохода, страховые взносы "
                f"{rf_rate_pct('ausn.social_contributions')}%, НДС не применяется. "
                f"Доступен при доходе до {registry_amount(Country.RF, 'ausn.income_limit')} ₽ "
                f"в год и штате до {registry_amount(Country.RF, 'ausn.headcount_limit')} чел.",
                icon="ℹ️",
            )
            rf_profile = AusnProfile()
        elif rf_regime == "osno":
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("НДС", f"{rf_rate_pct('osno.vat')}%")
            col_b.metric("Налог на прибыль", f"{rf_rate_pct('osno.profit')}%")
            with col_c:
                osno_sc = (
                    st.selectbox(
                        "Страховые взносы",
                        options=list(SocialContributions),
                        format_func=social_contributions_label,
                        key=state.RF_OSNO_SC,
                    )
                    or SocialContributions.STANDARD
                )
            rf_profile = OsnoProfile(social_contributions=osno_sc)
        else:
            npd_client = (
                st.radio(
                    "Тип клиентов",
                    options=list(NpdClientType),
                    format_func=npd_client_label,
                    horizontal=True,
                    key=state.RF_NPD_CLIENT,
                )
                or NpdClientType.LEGAL_ENTITY
            )
            rf_profile = NpdProfile(client_type=npd_client)

    kz_profile: KzOurProfile | None = None
    if mode in (JurisdictionMode.KZ, JurisdictionMode.BOTH):
        st.markdown("#### 🇰🇿 Параметры РК (ОУР)")
        col_form, col_kz_vat = st.columns(2)
        with col_form:
            kz_form = (
                st.radio(
                    "Форма организации",
                    options=list(KzForm),
                    format_func=kz_form_label,
                    horizontal=True,
                    key=state.KZ_FORM,
                )
                or KzForm.TOO
            )
        with col_kz_vat:
            kz_vat = (
                st.radio(
                    "НДС",
                    options=list(KzVat),
                    format_func=vat_label,
                    horizontal=True,
                    key=state.KZ_VAT,
                )
                or KzVat.NONE
            )
        kz_profile = KzOurProfile(form=kz_form, vat=kz_vat)

    jurisdiction = Jurisdiction(mode=mode, rf=rf_profile, kz=kz_profile)
    st.session_state[state.JURISDICTION] = jurisdiction

    st.markdown("#### Применимость режима")
    st.caption(
        "Лимиты дохода и штата, запреты НПД, обязательный НДС. Расчёт сметы проверка "
        "не останавливает. Пустое поле — значение не задано, проверка по нему пропускается."
    )
    col_form_rf, col_head, col_agency = st.columns(3)
    with col_form_rf:
        if mode != JurisdictionMode.KZ:
            st.selectbox(
                "Форма в РФ",
                options=list(LegalForm),
                format_func=lambda f: LEGAL_FORM_LABELS[f],
                key=state.FACT_LEGAL_FORM,
            )
    with col_head:
        st.number_input(
            "Средняя численность работников",
            min_value=0,
            step=1,
            value=None,
            key=state.FACT_HEADCOUNT,
            help="По трудовым договорам. Пусто — по составу команды (у ИП без владельца).",
        )
    with col_agency:
        st.checkbox(
            "Работаем по агентским договорам",
            key=state.FACT_AGENCY,
            help="Например, платим пошлины за клиента как агент. Для НПД это запрещено.",
        )
    col_income_rf, col_income_kz = st.columns(2)
    if mode != JurisdictionMode.KZ:
        col_income_rf.number_input(
            "Ожидаемый доход за год в РФ без НДС, ₽",
            min_value=0.0,
            step=1_000_000.0,
            format="%.0f",
            value=None,
            key=state.FACT_INCOME_RF,
        )
    if mode != JurisdictionMode.RF:
        col_income_kz.number_input(
            "Ожидаемый оборот за год в РК без НДС, ₸",
            min_value=0.0,
            step=1_000_000.0,
            format="%.0f",
            value=None,
            key=state.FACT_INCOME_KZ,
        )
    issues = check_firm(
        Firm(jurisdiction=jurisdiction, team=tuple(state.team()), facts=state.firm_facts())
    )
    if not issues:
        st.success("Выбранный режим применим по введённым данным.", icon="✅")
    for issue in issues:
        text = f"{SEVERITY_ICONS[issue.severity]} **{issue.regime_name}**: {issue.message}"
        if issue.severity == Severity.BLOCKER:
            st.error(text)
        elif issue.severity == Severity.WARNING:
            st.warning(text)
        else:
            st.info(text)

    st.markdown("#### Курсы валют")
    st.caption(
        "Суммы в другой валюте (оклады, накладные, пошлины) пересчитываются в валюту "
        "проекта по этим курсам. Загрузите официальные курсы ЦБ РФ (если ЦБ недоступен — "
        "НБРК) или введите вручную. 0 — курс не задан."
    )

    def load_rates() -> None:
        """Колбэк: курсы ЦБ РФ (НБРК) на дату в поля курсов."""
        on = st.session_state.get(state.FX_DATE) or dt.date.today()
        try:
            quote = fetch_rates(on)
        except ExchangeRateFetchError as exc:
            st.session_state[state.FX_ERROR] = str(exc)
            return
        st.session_state.pop(state.FX_ERROR, None)
        st.session_state[state.FX_QUOTE] = quote
        st.session_state[state.RUB_PER_KZT] = float(quote.rates.rub_per_kzt or ZERO)
        st.session_state[state.RUB_PER_USD] = float(quote.rates.rub_per_usd or ZERO)

    col_date, col_load = st.columns([1, 2])
    st.session_state.setdefault(state.FX_DATE, dt.date.today())
    with col_date:
        st.date_input("Дата курсов", format="DD.MM.YYYY", key=state.FX_DATE)
    with col_load:
        st.write("")
        st.button("⬇️ Загрузить курсы ЦБ РФ", on_click=load_rates, key="btn_load_rates")
    fx_error = st.session_state.get(state.FX_ERROR)
    if fx_error:
        st.warning(f"{fx_error}", icon="⚠️")
    quote = state.fx_quote()
    if quote is not None:
        st.caption(
            f"Загружены курсы {RATE_SOURCE_LABELS[quote.source]} на "
            f"{quote.rate_date:%d.%m.%Y}. Поля ниже можно изменить вручную."
        )
    col_kzt, col_usd = st.columns(2)
    with col_kzt:
        st.number_input(
            "₽ за 1 ₸",
            min_value=0.0,
            step=0.01,
            format="%.4f",
            key=state.RUB_PER_KZT,
            help="Например, 0.18 — «1 ₸ ≈ 0,18 ₽».",
        )
    with col_usd:
        st.number_input(
            "₽ за 1 $",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            key=state.RUB_PER_USD,
            help="Нужен, если есть накладные или расходы в долларах.",
        )


# ===========================================================================
# ВКЛАДКА 2 — КОМАНДА
# ===========================================================================
with tab_team:
    st.subheader("Состав команды")
    st.caption(
        "Для каждого сотрудника задайте полную стоимость в месяц (оклад + взносы или, "
        "для партнёра, нормативное вознаграждение), контрактные часы и целевую "
        "утилизацию. Себестоимость биллируемого часа = стоимость в месяц / "
        "(контрактные часы × утилизация): небиллируемое время тоже оплачивается "
        "и возмещается через проданные часы."
    )
    jurisdiction_now = state.jurisdiction()
    home = state.home_currency(jurisdiction_now)

    # Поля карточки сотрудника. Значения задаются до виджетов, чтобы колбэки могли
    # очищать и заполнять карточку (менять ключ виджета после его создания нельзя).
    member_defaults: dict[str, object] = {
        state.MEMBER_NAME: "",
        state.MEMBER_ROLE: next(iter(Role)),
        state.MEMBER_BILLING: 0.0,
        state.MEMBER_CURRENCY: home.value,
        state.MEMBER_COMPENSATION: state.COMPENSATION_SALARY,
        state.MEMBER_GROSS: 0.0,
        state.MEMBER_RATE_PCT: 0.0,
        state.MEMBER_MONTHLY: 0.0,
        state.MEMBER_HOURS: float(DEFAULT_CONTRACT_HOURS_PER_MONTH),
        state.MEMBER_UTILIZATION: DEFAULT_UTILIZATION_PCT,
        state.MEMBER_FROM_PROFIT: False,
    }
    for key, default in member_defaults.items():
        st.session_state.setdefault(key, default)

    def contributions_for(gross: float, currency: Currency) -> ContributionBreakdown:
        """Взносы работодателя за год с оклада по стране трудоустройства."""
        return employer_contributions(
            to_decimal(gross), jurisdiction_now, employment_country(currency)
        )

    def card_contributions() -> ContributionBreakdown:
        """Взносы работодателя с оклада из карточки."""
        return contributions_for(
            float(st.session_state[state.MEMBER_GROSS]),
            Currency(st.session_state[state.MEMBER_CURRENCY]),
        )

    def mark_rate_manual() -> None:
        """Колбэк: ставку взносов изменили руками — больше не пересчитывать её."""
        st.session_state[MEMBER_RATE_MANUAL] = True

    def reset_rate_manual() -> None:
        """Колбэк: вернуть ставку взносов по расчёту."""
        st.session_state[MEMBER_RATE_MANUAL] = False

    def reset_card() -> None:
        """Колбэк-помощник: пустая карточка нового сотрудника."""
        for key, value in member_defaults.items():
            st.session_state[key] = value
        st.session_state[MEMBER_RATE_MANUAL] = False
        st.session_state.pop(state.EDIT_MEMBER, None)

    def start_edit(index: int) -> None:
        """Колбэк ✏️: карточка заполняется данными сотрудника."""
        member = state.team()[index]
        for key, value in state.card_values(member).items():
            st.session_state[key] = value
        manual = False
        if st.session_state[state.MEMBER_COMPENSATION] == state.COMPENSATION_SALARY:
            computed = contributions_for(
                float(st.session_state[state.MEMBER_GROSS]), member.currency
            ).effective_rate
            entered = float(st.session_state[state.MEMBER_RATE_PCT])
            manual = abs(entered - float(computed * PERCENT)) > RATE_MATCH_PCT
        st.session_state[MEMBER_RATE_MANUAL] = manual
        st.session_state[state.EDIT_MEMBER] = member.name
        st.session_state.pop(MEMBER_MESSAGE, None)

    def cancel_edit() -> None:
        """Колбэк «Отмена»: карточка снова добавляет нового сотрудника."""
        reset_card()

    def card_employee_or_error() -> Employee | None:
        """Сотрудник из карточки или None с сообщением об ошибке."""
        if not str(st.session_state[state.MEMBER_NAME]).strip():
            st.session_state[MEMBER_MESSAGE] = ("error", "Имя не может быть пустым.")
            return None
        try:
            return state.card_employee()
        except ValidationError as exc:
            st.session_state[MEMBER_MESSAGE] = ("error", f"Проверьте поля: {exc}")
            return None

    def add_member() -> None:
        """Колбэк: добавляет сотрудника и очищает карточку."""
        employee = card_employee_or_error()
        if employee is None:
            return
        if employee.name in {member.name for member in state.team()}:
            st.session_state[MEMBER_MESSAGE] = (
                "error",
                f"Сотрудник «{employee.name}» уже есть в команде: этапы ссылаются на "
                "исполнителя по имени.",
            )
            return
        state.team().append(employee)
        reset_card()
        st.session_state[MEMBER_MESSAGE] = ("success", f"Добавлен(а): {employee.name}")

    def save_member() -> None:
        """Колбэк: сохраняет правку сотрудника; новое имя — во всех проектах сессии."""
        employee = card_employee_or_error()
        if employee is None:
            return
        original = str(st.session_state.get(state.EDIT_MEMBER, ""))
        team_now = state.team()
        index = next((i for i, member in enumerate(team_now) if member.name == original), None)
        if index is None:
            st.session_state[MEMBER_MESSAGE] = ("error", f"Сотрудника «{original}» уже нет.")
            reset_card()
            return
        renamed = employee.name != original
        if renamed and employee.name in state.names_in_use():
            st.session_state[MEMBER_MESSAGE] = (
                "error",
                f"Имя «{employee.name}» уже есть в команде или в проектах сессии.",
            )
            return
        team_now[index] = employee
        if renamed:
            state.rename_employee(original, employee.name)
        reset_card()
        note = " Новое имя — в этапах и факте всех проектов сессии." if renamed else ""
        st.session_state[MEMBER_MESSAGE] = ("success", f"Сохранено: {employee.name}.{note}")

    def delete_member(index: int) -> None:
        """Колбэк 🗑: удалить сотрудника (его назначения останутся «нет в команде»)."""
        team_now = state.team()
        if st.session_state.get(state.EDIT_MEMBER) == team_now[index].name:
            reset_card()
        removed = team_now.pop(index)
        st.session_state[MEMBER_MESSAGE] = ("success", f"Удалён(а): {removed.name}")

    # Ставка взносов по умолчанию — эффективная для оклада, страны и тарифа
    # (раздел 3.1.1). Пересчитывается до создания виджета, пока её не изменили руками.
    breakdown = card_contributions()
    if not st.session_state.get(MEMBER_RATE_MANUAL):
        st.session_state[state.MEMBER_RATE_PCT] = round(
            float(breakdown.effective_rate * PERCENT), 2
        )

    editing = st.session_state.get(state.EDIT_MEMBER)
    team_list = state.team()
    st.markdown("#### Текущая команда")
    if not team_list:
        empty_state(
            "team",
            "👥",
            "Команда пока пуста",
            "Добавьте первого сотрудника в карточке ниже — расчёт начинается с себестоимости "
            "его часа. Или загрузите демо-фирму на главной.",
        )
    for idx, member in enumerate(team_list):
        cost = employee_cost(member)
        sym = CURRENCY_SYMBOLS[member.currency]
        profit_mark = " · из прибыли" if member.paid_from_profit else ""
        current = " · ✏️ в карточке" if member.name == editing else ""
        with st.container(border=True, key=f"member-{idx}"):
            col_info, col_actions = st.columns([6, 1], vertical_alignment="center")
            role = ROLE_LABELS[member.role]
            col_info.markdown(f"**{member.name}** · {role}{profit_mark}{current}")
            col_info.caption(
                f"Ставка {money_rate(member.billing_rate, sym)} · в месяц "
                f"{money(cost.monthly_cost, sym)} · контрактный час "
                f"{money_rate(cost.contract_cost_rate, sym)} · биллируемый час "
                f"{money_rate(cost.cost_rate, sym)} · утилизация "
                f"{pct_label(member.target_utilization, to_decimal('0.1'))}"
            )
            with col_actions, st.container(horizontal=True, horizontal_alignment="right"):
                st.button(
                    "",
                    icon=":material/edit:",
                    key=f"edit_member_{idx}",
                    help="Изменить",
                    on_click=start_edit,
                    args=(idx,),
                )
                st.button(
                    "",
                    icon=":material/delete:",
                    key=f"del_member_{idx}",
                    help="Удалить",
                    on_click=delete_member,
                    args=(idx,),
                )
    if team_list:
        st.caption(
            f"Всего: {len(team_list)} · «биллируемый час» — себестоимость биллируемого часа, "
            "она идёт в расчёт проекта."
        )

    st.markdown(f"#### Изменить сотрудника «{editing}»" if editing else "#### Добавить сотрудника")
    col_name, col_role = st.columns([2, 1])
    with col_name:
        st.text_input("Имя / ФИО", placeholder="Иванов И.", key=state.MEMBER_NAME)
    with col_role:
        st.selectbox(
            "Роль",
            options=list(Role),
            format_func=lambda r: ROLE_LABELS[r],
            key=state.MEMBER_ROLE,
        )

    col_bill, col_cur = st.columns([2, 1])
    with col_bill:
        st.number_input(
            "Ставка для клиента за час",
            min_value=0.0,
            step=500.0,
            format="%.0f",
            key=state.MEMBER_BILLING,
        )
    with col_cur:
        st.selectbox(
            "Валюта оклада и ставки",
            options=[Currency.RUB.value, Currency.KZT.value],
            format_func=lambda c: f"{c} ({CURRENCY_SYMBOLS[Currency(c)]})",
            key=state.MEMBER_CURRENCY,
            help="В режиме «Оба» — валюта страны, где работает сотрудник.",
        )

    with st.container(border=True):
        st.markdown("**Полная стоимость сотрудника в месяц**")
        st.radio(
            "Как задать",
            options=list(COMPENSATION_LABELS),
            format_func=lambda k: COMPENSATION_LABELS[k],
            horizontal=True,
            key=state.MEMBER_COMPENSATION,
            help=(
                "Нормативное вознаграждение партнёра (по Майстеру) — рыночная цена "
                "его труда. Оно отделяет оплату труда партнёра от прибыли фирмы."
            ),
        )
        if st.session_state[state.MEMBER_COMPENSATION] == state.COMPENSATION_MONTHLY:
            st.number_input(
                "Сумма в месяц",
                min_value=0.0,
                step=10_000.0,
                format="%.0f",
                key=state.MEMBER_MONTHLY,
            )
        else:
            col_gs, col_er = st.columns(2)
            with col_gs:
                st.number_input(
                    "Оклад в месяц",
                    min_value=0.0,
                    step=10_000.0,
                    format="%.0f",
                    key=state.MEMBER_GROSS,
                    help="Оклад до вычетов (gross).",
                )
            with col_er:
                st.number_input(
                    "Взносы работодателя, %",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.5,
                    format="%.2f",
                    key=state.MEMBER_RATE_PCT,
                    on_change=mark_rate_manual,
                    help=(
                        "Эффективная ставка за год: с предельной базой и тарифом страны "
                        "трудоустройства. Можно переопределить."
                    ),
                )
            sym_card = CURRENCY_SYMBOLS[Currency(st.session_state[state.MEMBER_CURRENCY])]
            if st.session_state.get(MEMBER_RATE_MANUAL):
                st.caption("Ставка взносов введена вручную.")
                st.button("↺ Ставка по расчёту", on_click=reset_rate_manual, key="btn_rate_auto")
            elif breakdown.gross_monthly > 0:
                parts = "; ".join(
                    f"{item.name} {money(item.annual, sym_card)}"
                    for item in breakdown.items
                    if item.annual > 0
                )
                st.caption(
                    f"Взносы за год ({breakdown.tariff_label}): "
                    f"{money(breakdown.annual_total, sym_card)}"
                    + (f" — {parts}" if parts else "")
                    + ". Ставка — годовые взносы / годовой оклад."
                )
        st.checkbox(
            "Вознаграждение выплачивается из прибыли",
            key=state.MEMBER_FROM_PROFIT,
            help=(
                "Например, партнёр получает дивиденды, а не зарплату. В себестоимость "
                "проекта сумма входит, но базу налога «доходы − расходы» не уменьшает."
            ),
        )
        col_ch, col_u = st.columns(2)
        with col_ch:
            st.number_input(
                "Контрактные часы в месяц",
                min_value=1.0,
                step=8.0,
                format="%.0f",
                key=state.MEMBER_HOURS,
                help="Сколько часов сотрудник работает по договору. Норма ≈ 168 ч/мес.",
            )
        with col_u:
            st.number_input(
                "Целевая утилизация, %",
                min_value=1.0,
                max_value=100.0,
                step=5.0,
                format="%.1f",
                key=state.MEMBER_UTILIZATION,
                help=(
                    "Доля контрактных часов, которую фирма продаёт клиентам. "
                    "Обычно 60–75%, у партнёров ниже."
                ),
            )

        preview: EmployeeCost | None
        try:
            preview = employee_cost(state.card_employee())
        except ValidationError:
            preview = None
        if preview is not None and preview.monthly_cost > 0:
            sym = CURRENCY_SYMBOLS[Currency(st.session_state[state.MEMBER_CURRENCY])]
            kpi_grid(
                [
                    Kpi(
                        "member-monthly",
                        "В месяц",
                        money(preview.monthly_cost, sym),
                        formulas.monthly_cost(preview, sym),
                    ),
                    Kpi(
                        "member-contract",
                        "Контрактный час",
                        money_rate(preview.contract_cost_rate, sym),
                        formulas.contract_rate(preview, sym),
                    ),
                    Kpi(
                        "member-billable",
                        "Биллируемый час",
                        money_rate(preview.cost_rate, sym),
                        formulas.billable_rate(preview, sym),
                    ),
                    Kpi(
                        "member-idle",
                        "Недозагрузка",
                        money_rate(preview.underutilization_rate, sym),
                        formulas.underutilization_rate(preview, sym),
                    ),
                ]
            )

    if editing:
        col_save, col_cancel = st.columns([1, 1])
        col_save.button(
            "💾 Сохранить изменения", type="primary", key="btn_save_member", on_click=save_member
        )
        col_cancel.button("Отмена", key="btn_cancel_member", on_click=cancel_edit)
    else:
        st.button(
            "➕ Добавить сотрудника", type="primary", key="btn_add_member", on_click=add_member
        )
    message = st.session_state.pop(MEMBER_MESSAGE, None)
    if message is not None:
        kind, text = message
        (st.success if kind == "success" else st.error)(text)


# ===========================================================================
# ВКЛАДКА 3 — РАСХОДЫ ФИРМЫ
# ===========================================================================
with tab_exp:
    st.subheader("Накладные расходы фирмы")
    st.caption(
        "Ставка накладных = Σ(месячные накладные) / биллируемая ёмкость команды. "
        "На проект относится пропорционально его часам."
    )

    def editor_rows(items: list[Overhead]) -> list[dict[str, object]]:
        """Строки редактора накладных."""
        return [
            {
                "Название": item.name,
                "Сумма": float(item.amount),
                "Период": item.period.value,
                "Валюта": item.currency.value,
            }
            for item in items
        ]

    def reset_overheads(items: list[Overhead]) -> None:
        """Колбэк шаблона и очистки: новая база редактора без прежних правок."""
        st.session_state[state.OVERHEADS] = items
        st.session_state[OVERHEADS_EDITOR_BASE] = editor_rows(items)
        st.session_state.pop(OVERHEADS_EDITOR, None)

    col_tpl, col_clear = st.columns(2)
    col_tpl.button(
        "📥 Загрузить типовой шаблон",
        on_click=reset_overheads,
        args=(
            [
                Overhead(name=name, amount=to_decimal(0), period=Period.MONTHLY)
                for name in OVERHEAD_TEMPLATE
            ],
        ),
    )
    col_clear.button("🧹 Очистить список", on_click=reset_overheads, args=([],))

    # Данные редактора не должны меняться, пока он на экране: Streamlit сбрасывает
    # правки, если входные данные изменились (в v0.3 так терялись правки сумм).
    # База пересобирается из модели только при входе на страницу и по кнопкам выше.
    if OVERHEADS_EDITOR not in st.session_state or OVERHEADS_EDITOR_BASE not in st.session_state:
        st.session_state[OVERHEADS_EDITOR_BASE] = editor_rows(state.overheads())
    source = pd.DataFrame(st.session_state[OVERHEADS_EDITOR_BASE], columns=OVERHEAD_COLUMNS)
    edited = st.data_editor(
        source,
        num_rows="dynamic",
        column_config={
            "Название": st.column_config.TextColumn(required=True),
            "Сумма": st.column_config.NumberColumn(min_value=0.0, format="%.0f"),
            "Период": st.column_config.SelectboxColumn(
                options=[p.value for p in Period], required=True
            ),
            "Валюта": st.column_config.SelectboxColumn(
                options=[c.value for c in Currency], required=True
            ),
        },
        hide_index=True,
        width="stretch",
        key=OVERHEADS_EDITOR,
    )

    parsed: list[Overhead] = []
    for _, row in edited.iterrows():
        if not str(row["Название"]).strip():
            continue
        try:
            parsed.append(
                Overhead(
                    name=str(row["Название"]),
                    amount=to_decimal(float(row["Сумма"])),
                    currency=Currency(str(row["Валюта"])),
                    period=Period(str(row["Период"])),
                )
            )
        except (ValidationError, ValueError, TypeError) as exc:
            # Пустая сумма в новой строке редактора — тоже сюда (NaN или None).
            st.warning(f"Пропущена запись «{row['Название']}»: {exc}")
    st.session_state[state.OVERHEADS] = parsed

    st.markdown("#### Ёмкость фирмы и ставка накладных")
    team_now = state.team()
    contract_total = decimal_sum(m.contract_hours_per_month for m in team_now)
    capacity_total = billable_capacity(team_now)
    primary_currency = state.home_currency(state.jurisdiction())
    sym = CURRENCY_SYMBOLS[primary_currency]
    rates_now = state.exchange_rates()
    try:
        total_monthly = total_overheads_monthly(parsed, primary_currency, rates_now)
        oh_rate = overhead_rate(
            Firm(team=tuple(team_now), overheads=tuple(parsed), exchange_rates=rates_now),
            primary_currency,
        )
    except CurrencyConversionError as exc:
        st.warning(f"{exc}. Курсы задаются на вкладке «Юрисдикция и налоги».")
        total_monthly = oh_rate = ZERO
    utilization = capacity_total / contract_total if contract_total > 0 else None

    kpi_grid(
        [
            Kpi(
                "contract-hours",
                "Контрактные часы",
                f"{hours_label(contract_total)}/мес",
                formulas.contract_hours_total(contract_total),
            ),
            Kpi(
                "capacity",
                "Биллируемая ёмкость",
                f"{hours_label(capacity_total)}/мес",
                formulas.capacity_total(capacity_total),
            ),
            Kpi(
                "firm-utilization",
                "Утилизация фирмы",
                pct_label(utilization, to_decimal("0.1")),
                formulas.firm_utilization(capacity_total, contract_total),
            ),
            Kpi(
                "overhead-rate",
                "Ставка накладных",
                money_rate(oh_rate, sym),
                formulas.overhead_rate(oh_rate, total_monthly, capacity_total, sym),
                caption=f"Накладные в месяц: {money(total_monthly, sym)}",
            ),
        ]
    )
    st.caption(
        "Ставка накладных = накладные ÷ биллируемая ёмкость команды (Σ контрактные часы × "
        "утилизация). Ёмкость считается по карточкам сотрудников на вкладке «Команда»."
    )

# ===========================================================================
# ВКЛАДКА 4 — РЕКВИЗИТЫ И ЛОГОТИП (для КП)
# ===========================================================================
with tab_letterhead:
    st.subheader("Реквизиты и логотип")
    st.caption(
        "Попадают в шапку коммерческого предложения (PDF и DOCX) и в файл JSON вместе с "
        "фирмой. Всё необязательно: пустые строки в КП не печатаются."
    )
    st.text_input("Название фирмы", key=state.FIRM_NAME, placeholder="ООО «Юридическая фирма»")
    st.text_input(
        "Реквизиты одной строкой",
        key=state.LETTERHEAD_KEYS["requisites"],
        placeholder="ИНН 7700000000 · КПП 770001001 · ОГРН 1027700000000 (в РК — БИН)",
    )
    st.text_input(
        "Адрес", key=state.LETTERHEAD_KEYS["address"], placeholder="Москва, ул. Тверская, д. 1"
    )
    col_phone, col_email, col_site = st.columns(3)
    col_phone.text_input("Телефон", key=state.LETTERHEAD_KEYS["phone"])
    col_email.text_input("E-mail", key=state.LETTERHEAD_KEYS["email"])
    col_site.text_input("Сайт", key=state.LETTERHEAD_KEYS["website"])
    col_signer, col_position = st.columns(2)
    col_signer.text_input(
        "Подписант КП (ФИО)",
        key=state.LETTERHEAD_KEYS["signatory_name"],
        placeholder="Иванов Иван Иванович",
    )
    col_position.text_input(
        "Должность подписанта",
        key=state.LETTERHEAD_KEYS["signatory_position"],
        placeholder="управляющий партнёр",
    )
    st.text_area(
        "Банковские реквизиты (необязательно)",
        key=state.LETTERHEAD_KEYS["bank_details"],
        height=80,
        placeholder="р/с … в АО «Банк», БИК …, к/с …",
    )

    st.markdown("#### Логотип")
    logo = st.session_state.get(state.LH_LOGO)
    col_logo, col_upload = st.columns([1, 2], vertical_alignment="center")
    with col_logo:
        if logo is None:
            st.caption("Логотипа нет: шапка КП — только с реквизитами.")
        else:
            with st.container(key="logo-preview"):
                st.image(logo.data, width=200)
            st.button(
                "Удалить логотип",
                icon=":material/delete:",
                on_click=state.remove_logo,
                key="btn_remove_logo",
            )
    with col_upload:
        st.file_uploader(
            f"PNG или JPEG, до {MAX_LOGO_BYTES // 1024} КБ и {MAX_LOGO_SIDE_PX} px по стороне",
            type=["png", "jpg", "jpeg"],
            key=state.logo_uploader_key(),
            # Браузер не отправит файл больше 1 МБ; предел 500 КБ проверяет ядро.
            max_upload_size=LOGO_UPLOAD_LIMIT_MB,
            on_change=state.load_logo,
        )
    error = st.session_state.get(state.LOGO_ERROR)
    if error:
        st.error(error)

nav.footer(nav.FIRM)
