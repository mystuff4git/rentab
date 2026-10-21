"""Страница 01: настройка фирмы (страница v0.3 на ядре, методология фазы 2).

Три вкладки: юрисдикция, налоги и курсы; команда; накладные фирмы. Ставки в
подписях берутся из реестра ядра. Себестоимость часа не вводится, а
считается ядром: c = W / (H × u) (docs/methodology.md, раздел 3). Сохранения
профиля на диск, как в v0.3, нет: данные живут только в сессии (L-10).
"""

from __future__ import annotations

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
from rentab_core.errors import CurrencyConversionError
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
)
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, ZERO, Currency, decimal_sum, to_decimal
from rentab_core.tax import employer_contribution_rate
from ui import state
from ui.labels import (
    COMPENSATION_LABELS,
    JURISDICTION_LABELS,
    RF_REGIME_LABELS,
    kz_form_label,
    npd_client_label,
    rf_rate_pct,
    social_contributions_label,
    usn_object_label,
    vat_label,
)

# Страна трудоустройства по валюте оклада — для ставки взносов по умолчанию.
COUNTRY_BY_CURRENCY = {Currency.RUB: Country.RF, Currency.KZT: Country.KZ}
DEFAULT_UTILIZATION_PCT = float(DEFAULT_TARGET_UTILIZATION * PERCENT)

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

# Редактор накладных: ключ виджета и его неизменяемая база (см. вкладку 3).
OVERHEADS_EDITOR = "overheads_editor"
OVERHEADS_EDITOR_BASE = "overheads_editor_base"
OVERHEAD_COLUMNS = ["Название", "Сумма", "Период", "Валюта"]

state.init_defaults()
state.persist_widgets()

st.title("⚙️ Настройка среды")
st.caption("Заполните три вкладки: юрисдикция, команда, накладные фирмы.")
st.info(
    "Данные фирмы хранятся только в этой сессии и не сохраняются на сервере. "
    "Экспорт и импорт профиля в файл появятся в следующих версиях.",
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
            key="jurisdiction_choice",
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
                key="rf_regime_choice",
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
                        key="rf_usn_object",
                    )
                    or UsnObject.INCOME
                )
            with col_vat:
                usn_vat = (
                    st.selectbox(
                        "НДС (с 2026 г.)",
                        options=list(UsnVat),
                        format_func=vat_label,
                        key="rf_usn_vat",
                    )
                    or UsnVat.NONE
                )
            with col_sc:
                usn_sc = (
                    st.selectbox(
                        "Страховые взносы",
                        options=list(SocialContributions),
                        format_func=social_contributions_label,
                        key="rf_usn_sc",
                    )
                    or SocialContributions.STANDARD
                )
            rf_profile = UsnProfile(tax_object=usn_object, vat=usn_vat, social_contributions=usn_sc)
        elif rf_regime == "ausn":
            st.info(
                f"АУСН: налог {rf_rate_pct('ausn.income')}% с дохода, страховые взносы "
                f"{rf_rate_pct('ausn.social_contributions')}%, НДС не применяется. "
                "Доступен для компаний с доходом до 60 млн ₽/год и штатом до 5 чел.",
                icon="ℹ️",
            )
            rf_profile = AusnProfile()
        elif rf_regime == "osno":
            st.info(
                "На ОСНО ставки фиксированные по НК РФ — редактирование не требуется.",
                icon="ℹ️",
            )
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("НДС", f"{rf_rate_pct('osno.vat')}%")
            col_b.metric("Налог на прибыль", f"{rf_rate_pct('osno.profit')}%")
            col_c.metric("Страховые взносы", f"{rf_rate_pct('social_contributions.standard')}%")
            rf_profile = OsnoProfile()
        else:
            npd_client = (
                st.radio(
                    "Тип клиентов",
                    options=list(NpdClientType),
                    format_func=npd_client_label,
                    horizontal=True,
                    key="rf_npd_client",
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
                    key="kz_form_choice",
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
                    key="kz_vat_choice",
                )
                or KzVat.NONE
            )
        kz_profile = KzOurProfile(form=kz_form, vat=kz_vat)

    jurisdiction = Jurisdiction(mode=mode, rf=rf_profile, kz=kz_profile)
    st.session_state[state.JURISDICTION] = jurisdiction

    st.markdown("### 💱 Курсы валют")
    st.caption(
        "Суммы в другой валюте (оклады, накладные, пошлины) пересчитываются в валюту "
        "проекта по этим курсам. Курс тенге по умолчанию — индикативный из v0.3: "
        "введите актуальный. 0 — курс не задан."
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

    def contribution_pct(currency: Currency) -> float:
        """Ставка взносов по умолчанию для страны трудоустройства, %."""
        country = COUNTRY_BY_CURRENCY.get(currency, Country.RF)
        return float(employer_contribution_rate(jurisdiction_now, country) * PERCENT)

    # Поля карточки «Добавить сотрудника». Значения задаются до виджетов, чтобы
    # колбэки могли очищать карточку (менять ключ виджета после его создания нельзя).
    member_defaults: dict[str, object] = {
        "add_member_name": "",
        "add_member_billing": 0.0,
        "add_member_currency": home.value,
        "add_member_compensation": "salary",
        "add_member_gross_salary": 0.0,
        "add_member_employer_rate_pct": contribution_pct(home),
        "add_member_monthly_cost": 0.0,
        "add_member_contract_hours": float(DEFAULT_CONTRACT_HOURS_PER_MONTH),
        "add_member_utilization_pct": DEFAULT_UTILIZATION_PCT,
    }
    for key, default in member_defaults.items():
        st.session_state.setdefault(key, default)

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
        )

    def reset_contribution_rate() -> None:
        """Колбэк смены валюты: ставка взносов страны трудоустройства."""
        currency = Currency(st.session_state["add_member_currency"])
        st.session_state["add_member_employer_rate_pct"] = contribution_pct(currency)

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
        ):
            st.session_state[key] = member_defaults[key]

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
            on_change=reset_contribution_rate,
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
                    format="%.1f",
                    key="add_member_employer_rate_pct",
                    help="Подставлено по стране трудоустройства; можно переопределить.",
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
            cells[0].write(f"**{member.name}**")
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
