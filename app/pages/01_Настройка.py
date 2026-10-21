"""Страница 01: настройка фирмы (перенос `pages/01_Настройка.py` v0.3 на ядро).

Три вкладки: юрисдикция и налоги, команда, накладные фирмы. Ставки в
подписях берутся из реестра ядра, себестоимость часа считает калькулятор
ФОТ ядра. Сохранения профиля на диск, как в v0.3, нет: данные живут только
в сессии (L-10).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from rentab_core.costing import (
    employee_full_cost,
    overhead_rate,
    total_overheads_monthly,
)
from rentab_core.models import (
    DEFAULT_CONTRACT_HOURS_PER_MONTH,
    ROLE_LABELS,
    AusnProfile,
    Country,
    Employee,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Payroll,
    Period,
    RfTaxProfile,
    Role,
    SocialContributions,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, Currency, to_decimal
from rentab_core.tax import employer_contribution_rate
from ui import state
from ui.labels import (
    JURISDICTION_LABELS,
    RF_REGIME_LABELS,
    kz_form_label,
    npd_client_label,
    rf_rate_pct,
    social_contributions_label,
    usn_object_label,
    vat_label,
)

# Допустимое расхождение введённого вручную cost_rate с расчётом по ФОТ.
COST_RATE_FOT_TOLERANCE = 0.20

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

tab_jur, tab_team, tab_exp = st.tabs(["🏛️ Юрисдикция и налоги", "👥 Команда", "🧾 Расходы фирмы"])

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

    exchange_rate: float | None = None
    if mode == JurisdictionMode.BOTH:
        st.markdown("### 💱 Обменный курс")
        exchange_rate = st.number_input(
            "RUB за 1 KZT (например, 0.18 → 1 ₸ ≈ 0.18 ₽)",
            min_value=0.0001,
            value=float(st.session_state[state.EXCHANGE_RATE]),
            step=0.01,
            format="%.4f",
            key="exchange_rate_input",
        )
        st.session_state[state.EXCHANGE_RATE] = exchange_rate

    jurisdiction = Jurisdiction(
        mode=mode,
        rf=rf_profile,
        kz=kz_profile,
        exchange_rate_rub_per_kzt=None if exchange_rate is None else to_decimal(exchange_rate),
    )
    st.session_state[state.JURISDICTION] = jurisdiction

    with st.expander("Текущие настройки", expanded=False):
        st.json(jurisdiction.model_dump(mode="json"))


# ===========================================================================
# ВКЛАДКА 2 — КОМАНДА
# ===========================================================================
with tab_team:
    st.subheader("Состав команды")
    st.caption(
        "Для каждого сотрудника задайте оклад и контрактные часы — "
        "калькулятор ФОТ автоматически предложит cost_rate (полная стоимость "
        "часа с учётом взносов работодателя). При желании cost_rate можно "
        "ввести вручную."
    )
    # Поля карточки «Добавить сотрудника». Значения задаются до виджетов, чтобы
    # колбэки могли очищать карточку (менять ключ виджета после его создания нельзя).
    member_defaults: dict[str, object] = {
        "add_member_name": "",
        "add_member_billing": 0.0,
        "add_member_gross_salary": 0.0,
        "add_member_contract_hours": float(DEFAULT_CONTRACT_HOURS_PER_MONTH),
        "add_member_employer_rate_pct": float(
            employer_contribution_rate(state.jurisdiction()) * PERCENT
        ),
        "add_member_cost_rate": 0.0,
    }
    for key, default in member_defaults.items():
        st.session_state.setdefault(key, default)

    def form_payroll() -> Payroll:
        """Параметры ФОТ из карточки сотрудника."""
        return Payroll(
            gross_salary=to_decimal(float(st.session_state["add_member_gross_salary"])),
            contract_hours_per_month=to_decimal(
                float(st.session_state["add_member_contract_hours"])
            ),
            employer_contribution_rate=(
                to_decimal(float(st.session_state["add_member_employer_rate_pct"])) / PERCENT
            ),
        )

    def use_fot_cost_rate() -> None:
        """Колбэк: переносит стоимость часа из калькулятора ФОТ в cost_rate."""
        st.session_state["add_member_cost_rate"] = float(
            st.session_state["fot_calc_result"].cost_rate_per_hour
        )

    def add_member() -> None:
        """Колбэк: добавляет сотрудника и очищает карточку."""
        name = str(st.session_state["add_member_name"]).strip()
        if not name:
            st.session_state["add_member_message"] = ("error", "Имя не может быть пустым.")
            return
        state.team().append(
            Employee(
                name=name,
                role=st.session_state["add_member_role"],
                billing_rate=to_decimal(float(st.session_state["add_member_billing"])),
                cost_rate=to_decimal(float(st.session_state["add_member_cost_rate"])),
                payroll=form_payroll(),
            )
        )
        st.session_state["add_member_message"] = ("success", f"Добавлен(а): {name}")
        for key in ("add_member_name", "add_member_billing", "add_member_gross_salary"):
            st.session_state[key] = member_defaults[key]
        st.session_state["add_member_cost_rate"] = member_defaults["add_member_cost_rate"]
        st.session_state.pop("fot_calc_result", None)

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

    st.number_input(
        "Ставка для клиента (₽ или ₸ / ч)",
        min_value=0.0,
        step=500.0,
        format="%.0f",
        key="add_member_billing",
    )

    with st.container(border=True):
        st.markdown("**🧮 Параметры ФОТ**")
        st.caption(
            "Калькулятор посчитает полную стоимость сотрудника "
            "(оклад + взносы работодателя) и стоимость одного часа."
        )
        col_gs, col_ch, col_er = st.columns(3)
        with col_gs:
            st.number_input(
                "Оклад (₽ или ₸ / мес)",
                min_value=0.0,
                step=10_000.0,
                format="%.0f",
                key="add_member_gross_salary",
                help="Оклад до вычетов (gross).",
            )
        with col_ch:
            st.number_input(
                "Контрактные часы / мес",
                min_value=1.0,
                step=8.0,
                format="%.0f",
                key="add_member_contract_hours",
                help="Сколько часов сотрудник работает по договору. Стандартная норма ≈ 168 ч/мес.",
            )
        with col_er:
            st.number_input(
                "Взносы работодателя, %",
                min_value=0.0,
                max_value=100.0,
                step=0.5,
                format="%.1f",
                key="add_member_employer_rate_pct",
                help="Подставлено по выбранной юрисдикции; можно переопределить.",
            )

        if st.button("🧮 Рассчитать стоимость часа", key="btn_calc_fot"):
            try:
                st.session_state["fot_calc_result"] = employee_full_cost(form_payroll())
            except ValidationError as exc:
                st.error(f"Не удалось посчитать: {exc}")

        fot_result = st.session_state.get("fot_calc_result")
        if fot_result:
            er_pct = (
                fot_result.employer_taxes / fot_result.gross_salary * PERCENT
                if fot_result.gross_salary > 0
                else 0
            )
            hourly_cost = fot_result.cost_rate_per_hour
            st.markdown(
                f"""
                | Показатель | Значение |
                |---|---|
                | Оклад | {fot_result.gross_salary:,.0f} |
                | Взносы работодателя ({er_pct:.1f}%) | {fot_result.employer_taxes:,.0f} |
                | **Итого расход на сотрудника, мес** | **{fot_result.total_monthly_cost:,.0f}** |
                | **Стоимость часа (ФОТ)** ← рекомендуемый cost_rate | **{hourly_cost:,.0f}** |
                """
            )
            st.button(
                "✅ Использовать как cost_rate", key="btn_use_fot", on_click=use_fot_cost_rate
            )

    new_cost = st.number_input(
        "Себестоимость часа (cost_rate)",
        min_value=0.0,
        step=500.0,
        format="%.0f",
        key="add_member_cost_rate",
        help="Если рассчитали через ФОТ — используйте кнопку выше; иначе введите значение вручную.",
    )

    if fot_result and fot_result.cost_rate_per_hour > 0 and new_cost > 0:
        suggested = float(fot_result.cost_rate_per_hour)
        delta = abs(new_cost - suggested) / suggested
        if delta > COST_RATE_FOT_TOLERANCE:
            st.warning(
                f"Введённый cost_rate ({new_cost:,.0f}) отличается от расчёта "
                f"по ФОТ ({suggested:,.0f}) на {delta * 100:.0f}% — "
                "проверьте корректность данных.",
                icon="⚠️",
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
        widths = [2, 1.2, 1, 1, 1, 0.5]
        h1, h2, h3, h4, h5, _h6 = st.columns(widths)
        h1.markdown("**Имя**")
        h2.markdown("**Роль**")
        h3.markdown("**Ставка клиенту**")
        h4.markdown("**Себестоимость**")
        h5.markdown("**ФОТ ₽/ч**")
        for idx, member in enumerate(team_list):
            c1, c2, c3, c4, c5, c6 = st.columns(widths)
            c1.write(f"**{member.name}**")
            c2.write(ROLE_LABELS[member.role])
            c3.write(f"{member.billing_rate:,.0f}")
            c4.write(f"{member.cost_rate:,.0f}")
            payroll = member.payroll
            if payroll is not None and payroll.gross_salary > 0:
                c5.write(f"{employee_full_cost(payroll).cost_rate_per_hour:,.0f}")
            else:
                c5.write("—")
            if c6.button("🗑", key=f"del_member_{idx}", help="Удалить"):
                team_list.pop(idx)
                st.rerun()
        st.caption(
            f"Всего: {len(team_list)} сотрудников · "
            "столбцы: Имя · Роль · Ставка клиенту · Себестоимость · ФОТ ₽/ч (расчёт) · 🗑"
        )


# ===========================================================================
# ВКЛАДКА 3 — РАСХОДЫ ФИРМЫ
# ===========================================================================
with tab_exp:
    st.subheader("Накладные расходы фирмы (Overheads)")
    st.caption(
        "Ставка накладных = Σ(месячные накладные) / биллируемые часы в месяц. "
        "Аллоцируется на проект пропорционально отработанным часам."
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

    st.markdown("### Часы фирмы")
    st.session_state.setdefault(state.CONTRACT_HOURS, float(st.session_state[state.BILLABLE_HOURS]))
    col_ch_total, col_bh = st.columns(2)
    with col_ch_total:
        contract_hours_total = st.number_input(
            "Контрактные часы в месяц (всего по команде)",
            min_value=0.0,
            value=float(st.session_state[state.CONTRACT_HOURS]),
            step=10.0,
            help="Сумма контрактных часов всех сотрудников. "
            "Кнопка ниже рассчитывает автоматически из карточек команды.",
            key="contract_hours_total_input",
        )
        st.session_state[state.CONTRACT_HOURS] = contract_hours_total
        if st.button("🔄 Рассчитать автоматически", key="btn_autocalc_contract"):
            sum_contract = sum(
                float(m.payroll.contract_hours_per_month)
                for m in state.team()
                if m.payroll is not None
            )
            if sum_contract <= 0:
                st.warning(
                    "Контрактные часы не заполнены ни у одного сотрудника. "
                    "Заполните их в карточках команды и попробуйте снова."
                )
            else:
                st.session_state[state.CONTRACT_HOURS] = sum_contract
                st.rerun()
    with col_bh:
        billable_hours = st.number_input(
            "Биллируемые часы в месяц (плановые)",
            min_value=1.0,
            value=float(st.session_state[state.BILLABLE_HOURS]),
            step=10.0,
            help="Сколько часов реально выставляется клиентам в месяц. "
            "Обычно 60–75% от контрактных. "
            "Используется как знаменатель в формуле overhead_rate.",
            key="billable_hours_input",
        )
        st.session_state[state.BILLABLE_HOURS] = billable_hours

    total_monthly = total_overheads_monthly(parsed)
    oh_rate = overhead_rate(parsed, to_decimal(billable_hours))
    utilization_pct = (
        billable_hours / contract_hours_total * 100.0 if contract_hours_total > 0 else 0.0
    )
    primary_country = Country.KZ if mode == JurisdictionMode.KZ else Country.RF
    sym = CURRENCY_SYMBOLS[primary_country.currency]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Контрактные часы", f"{contract_hours_total:,.0f} ч/мес")
    m2.metric("Биллируемые часы", f"{billable_hours:,.0f} ч/мес")
    m3.metric("Утилизация", f"{utilization_pct:.0f}%")
    m4.metric(f"Ставка накладных, {sym}/ч", f"{oh_rate:,.1f}")
    st.caption(
        f"Накладные в месяц: **{total_monthly:,.0f} {sym}** · "
        "Ставка накладных = накладные ÷ биллируемые часы."
    )
