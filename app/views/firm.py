"""Страница 01: настройка фирмы (страница v0.3 на ядре, методология фаз 2–3).

Три вкладки: юрисдикция, налоги, применимость режима и курсы; команда;
накладные фирмы. Ставки в подписях берутся из реестра ядра. Себестоимость
часа не вводится, а считается ядром: c = W / (H × u) (docs/methodology.md,
раздел 3); ставка взносов подставляется по окладу и тарифу (раздел 3.1.1).
Сохранения профиля на диск, как в v0.3, нет: данные живут только в сессии
(L-10); в файл JSON их выгружает и загружает сам пользователь на главной.
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
from rentab_core.errors import CurrencyConversionError, ExchangeRateFetchError
from rentab_core.fx import RATE_SOURCE_LABELS, fetch_rates
from rentab_core.models import (
    DEFAULT_CONTRACT_HOURS_PER_MONTH,
    DEFAULT_TARGET_UTILIZATION,
    ROLE_LABELS,
    AusnProfile,
    Compensation,
    Country,
    Employee,
    Firm,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    LegalForm,
    MonthlyCost,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Period,
    RfTaxProfile,
    Role,
    Salary,
    SocialContributions,
    UsnObject,
    UsnProfile,
    UsnVat,
    employment_country,
)
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, ZERO, Currency, decimal_sum, to_decimal
from rentab_core.tax import ContributionBreakdown, employer_contributions
from rentab_core.tax.eligibility import Severity, check_firm
from ui import state
from ui.labels import (
    COMPENSATION_LABELS,
    JURISDICTION_LABELS,
    LEGAL_FORM_LABELS,
    RF_REGIME_LABELS,
    SEVERITY_ICONS,
    kz_form_label,
    money,
    npd_client_label,
    registry_amount,
    rf_rate_pct,
    social_contributions_label,
    usn_object_label,
    vat_label,
)

DEFAULT_UTILIZATION_PCT = float(DEFAULT_TARGET_UTILIZATION * PERCENT)
# Ставка взносов в карточке сотрудника введена вручную: не пересчитывать её по окладу.
MEMBER_RATE_MANUAL = "add_member_rate_manual"

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

state.init_defaults()
state.persist_widgets()

st.title("⚙️ Настройка среды")
st.caption("Заполните три вкладки: юрисдикция, команда, накладные фирмы.")
st.info(
    "Данные фирмы хранятся только в этой сессии и не сохраняются на сервере. "
    "Сохранить их в файл JSON и загрузить обратно (или открыть демо-фирму) можно "
    "на главной странице.",
    icon="🔒",
)

tab_jur, tab_team, tab_exp = st.tabs(
    ["🏛️ Юрисдикция, налоги, курсы", "👥 Команда", "🧾 Расходы фирмы"]
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
        st.markdown("### 🇷🇺 Параметры РФ")
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
        st.markdown("### 🇰🇿 Параметры РК (ОУР)")
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

    st.markdown("### ✅ Применимость режима")
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

    st.markdown("### 💱 Курсы валют")
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

    with st.expander("Текущие настройки", expanded=False):
        st.json(jurisdiction.model_dump(mode="json"))


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

    # Поля карточки «Добавить сотрудника». Значения задаются до виджетов, чтобы
    # колбэки могли очищать карточку (менять ключ виджета после его создания нельзя).
    member_defaults: dict[str, object] = {
        "add_member_name": "",
        "add_member_billing": 0.0,
        "add_member_currency": home.value,
        "add_member_compensation": "salary",
        "add_member_gross_salary": 0.0,
        "add_member_employer_rate_pct": 0.0,
        "add_member_monthly_cost": 0.0,
        "add_member_contract_hours": float(DEFAULT_CONTRACT_HOURS_PER_MONTH),
        "add_member_utilization_pct": DEFAULT_UTILIZATION_PCT,
        "add_member_paid_from_profit": False,
    }
    for key, default in member_defaults.items():
        st.session_state.setdefault(key, default)

    def card_contributions() -> ContributionBreakdown:
        """Взносы работодателя за год с оклада из карточки по стране трудоустройства."""
        currency = Currency(st.session_state["add_member_currency"])
        gross = to_decimal(float(st.session_state["add_member_gross_salary"]))
        return employer_contributions(gross, jurisdiction_now, employment_country(currency))

    def mark_rate_manual() -> None:
        """Колбэк: ставку взносов изменили руками — больше не пересчитывать её."""
        st.session_state[MEMBER_RATE_MANUAL] = True

    def reset_rate_manual() -> None:
        """Колбэк: вернуть ставку взносов по расчёту."""
        st.session_state[MEMBER_RATE_MANUAL] = False

    # Ставка взносов по умолчанию — эффективная для оклада, страны и тарифа
    # (раздел 3.1.1). Пересчитывается до создания виджета, пока её не изменили руками.
    breakdown = card_contributions()
    if not st.session_state.get(MEMBER_RATE_MANUAL):
        st.session_state["add_member_employer_rate_pct"] = round(
            float(breakdown.effective_rate * PERCENT), 2
        )

    def form_compensation() -> Compensation:
        """Полная стоимость сотрудника из карточки."""
        if st.session_state["add_member_compensation"] == "monthly":
            return MonthlyCost(
                amount=to_decimal(float(st.session_state["add_member_monthly_cost"]))
            )
        return Salary(
            gross_salary=to_decimal(float(st.session_state["add_member_gross_salary"])),
            employer_contribution_rate=(
                to_decimal(float(st.session_state["add_member_employer_rate_pct"])) / PERCENT
            ),
        )

    def form_employee() -> Employee:
        """Сотрудник из карточки (без проверки имени)."""
        return Employee(
            name=str(st.session_state["add_member_name"]).strip(),
            role=st.session_state["add_member_role"],
            compensation=form_compensation(),
            currency=Currency(st.session_state["add_member_currency"]),
            billing_rate=to_decimal(float(st.session_state["add_member_billing"])),
            contract_hours_per_month=to_decimal(
                float(st.session_state["add_member_contract_hours"])
            ),
            target_utilization=(
                to_decimal(float(st.session_state["add_member_utilization_pct"])) / PERCENT
            ),
            paid_from_profit=bool(st.session_state["add_member_paid_from_profit"]),
        )

    def add_member() -> None:
        """Колбэк: добавляет сотрудника и очищает карточку."""
        name = str(st.session_state["add_member_name"]).strip()
        if not name:
            st.session_state["add_member_message"] = ("error", "Имя не может быть пустым.")
            return
        try:
            employee = form_employee()
        except ValidationError as exc:
            st.session_state["add_member_message"] = ("error", f"Проверьте поля: {exc}")
            return
        state.team().append(employee)
        st.session_state["add_member_message"] = ("success", f"Добавлен(а): {name}")
        for key in (
            "add_member_name",
            "add_member_billing",
            "add_member_gross_salary",
            "add_member_monthly_cost",
            "add_member_paid_from_profit",
        ):
            st.session_state[key] = member_defaults[key]
        st.session_state[MEMBER_RATE_MANUAL] = False

    st.markdown("### Добавить сотрудника")
    col_name, col_role = st.columns([2, 1])
    with col_name:
        st.text_input("Имя / ФИО", placeholder="Иванов И.", key="add_member_name")
    with col_role:
        st.selectbox(
            "Роль",
            options=list(Role),
            format_func=lambda r: ROLE_LABELS[r],
            key="add_member_role",
        )

    col_bill, col_cur = st.columns([2, 1])
    with col_bill:
        st.number_input(
            "Ставка для клиента за час",
            min_value=0.0,
            step=500.0,
            format="%.0f",
            key="add_member_billing",
        )
    with col_cur:
        st.selectbox(
            "Валюта оклада и ставки",
            options=[Currency.RUB.value, Currency.KZT.value],
            format_func=lambda c: f"{c} ({CURRENCY_SYMBOLS[Currency(c)]})",
            key="add_member_currency",
            help="В режиме «Оба» — валюта страны, где работает сотрудник.",
        )

    with st.container(border=True):
        st.markdown("**🧮 Полная стоимость сотрудника в месяц**")
        st.radio(
            "Как задать",
            options=list(COMPENSATION_LABELS),
            format_func=lambda k: COMPENSATION_LABELS[k],
            horizontal=True,
            key="add_member_compensation",
            help=(
                "Нормативное вознаграждение партнёра (по Майстеру) — рыночная цена "
                "его труда. Оно отделяет оплату труда партнёра от прибыли фирмы."
            ),
        )
        if st.session_state["add_member_compensation"] == "monthly":
            st.number_input(
                "Сумма в месяц",
                min_value=0.0,
                step=10_000.0,
                format="%.0f",
                key="add_member_monthly_cost",
            )
        else:
            col_gs, col_er = st.columns(2)
            with col_gs:
                st.number_input(
                    "Оклад в месяц",
                    min_value=0.0,
                    step=10_000.0,
                    format="%.0f",
                    key="add_member_gross_salary",
                    help="Оклад до вычетов (gross).",
                )
            with col_er:
                st.number_input(
                    "Взносы работодателя, %",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.5,
                    format="%.2f",
                    key="add_member_employer_rate_pct",
                    on_change=mark_rate_manual,
                    help=(
                        "Эффективная ставка за год: с предельной базой и тарифом страны "
                        "трудоустройства. Можно переопределить."
                    ),
                )
            sym_card = CURRENCY_SYMBOLS[Currency(st.session_state["add_member_currency"])]
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
            key="add_member_paid_from_profit",
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
                key="add_member_contract_hours",
                help="Сколько часов сотрудник работает по договору. Норма ≈ 168 ч/мес.",
            )
        with col_u:
            st.number_input(
                "Целевая утилизация, %",
                min_value=1.0,
                max_value=100.0,
                step=5.0,
                format="%.1f",
                key="add_member_utilization_pct",
                help=(
                    "Доля контрактных часов, которую фирма продаёт клиентам. "
                    "Обычно 60–75%, у партнёров ниже."
                ),
            )

        preview: EmployeeCost | None
        try:
            preview = employee_cost(form_employee())
        except ValidationError:
            preview = None
        if preview is not None and preview.monthly_cost > 0:
            sym = CURRENCY_SYMBOLS[Currency(st.session_state["add_member_currency"])]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(f"В месяц, {sym}", f"{preview.monthly_cost:,.0f}")
            m2.metric(f"Контрактный час, {sym}", f"{preview.contract_cost_rate:,.0f}")
            m3.metric(f"Биллируемый час, {sym}", f"{preview.cost_rate:,.0f}")
            m4.metric(
                f"Недозагрузка, {sym}/ч",
                f"{preview.underutilization_rate:,.0f}",
                help="Себестоимость биллируемого часа минус контрактного: оплата "
                "небиллируемого времени, которую должен вернуть каждый проданный час.",
            )

    st.button("➕ Добавить сотрудника", type="primary", key="btn_add_member", on_click=add_member)
    message = st.session_state.pop("add_member_message", None)
    if message is not None:
        kind, text = message
        (st.success if kind == "success" else st.error)(text)

    st.markdown("### Текущая команда")
    team_list = state.team()
    if not team_list:
        st.info("Пока никого нет. Добавьте первого сотрудника выше.")
    else:
        widths = [2, 1.2, 1, 1, 1, 1, 0.8, 0.5]
        headers = [
            "Имя",
            "Роль",
            "Ставка клиенту",
            "В месяц",
            "Контр. час",
            "Биллир. час",
            "Утилизация",
        ]
        for column, header in zip(st.columns(widths), headers, strict=False):
            column.markdown(f"**{header}**")
        for idx, member in enumerate(team_list):
            cost = employee_cost(member)
            sym = CURRENCY_SYMBOLS[member.currency]
            cells = st.columns(widths)
            profit_mark = " · из прибыли" if member.paid_from_profit else ""
            cells[0].write(f"**{member.name}**{profit_mark}")
            cells[1].write(ROLE_LABELS[member.role])
            cells[2].write(f"{member.billing_rate:,.0f} {sym}")
            cells[3].write(f"{cost.monthly_cost:,.0f} {sym}")
            cells[4].write(f"{cost.contract_cost_rate:,.0f} {sym}")
            cells[5].write(f"{cost.cost_rate:,.0f} {sym}")
            cells[6].write(f"{member.target_utilization * PERCENT:.1f}%")
            if cells[7].button("🗑", key=f"del_member_{idx}", help="Удалить"):
                team_list.pop(idx)
                st.rerun()
        st.caption(
            f"Всего: {len(team_list)} сотрудников · «Биллир. час» — себестоимость "
            "биллируемого часа, она идёт в расчёт проекта."
        )


# ===========================================================================
# ВКЛАДКА 3 — РАСХОДЫ ФИРМЫ
# ===========================================================================
with tab_exp:
    st.subheader("Накладные расходы фирмы (Overheads)")
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

    col_tpl, col_clear = st.columns([1, 1])
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

    st.markdown("### Ёмкость фирмы и ставка накладных")
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
        st.warning(f"{exc}. Курсы задаются на вкладке «Юрисдикция, налоги, курсы».")
        total_monthly = oh_rate = ZERO
    utilization = capacity_total / contract_total * PERCENT if contract_total > 0 else ZERO

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Контрактные часы", f"{contract_total:,.0f} ч/мес")
    m2.metric("Биллируемая ёмкость", f"{capacity_total:,.1f} ч/мес")
    m3.metric("Утилизация фирмы", f"{utilization:.1f}%")
    m4.metric(f"Ставка накладных, {sym}/ч", f"{oh_rate:,.1f}")
    st.caption(
        f"Накладные в месяц: **{total_monthly:,.0f} {sym}** · Ставка накладных = накладные ÷ "
        "биллируемая ёмкость команды (Σ контрактные часы × утилизация). Ёмкость "
        "считается по карточкам сотрудников на вкладке «Команда»."
    )
