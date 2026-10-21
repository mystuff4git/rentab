"""
Rentab v0.3 — страница 02: Проект и смета.

Страница собирает все параметры проекта в одной форме:

0. Выбор модели ценообразования: «Биллинговая» или «Фиксированная».
1. Общие данные проекта (название — обязательно; клиент / описание — нет).
2. Этапы проекта: сотрудник из состава команды + часы. Под таблицей
   автоматически пересчитывается Blended Rate и Leverage. В режиме
   «Фиксированная» вместо колонки выручки показывается себестоимость.
3. Необязательный блок расходов (свёрнут в expander): пошлины из каталогов
   Роспатента / Казпатента + произвольный расход (свой с указанием категории).
4. (только для «Фиксированной») слайдер целевой маржи.

По кнопке «Рассчитать смету» страница:
- в режиме «Биллинговая» считает Gross/NNE через calculator.py + TaxCalculator
  (как в v0.2);
- в режиме «Фиксированная» вызывает FixedPriceCalculator.calculate() —
  он сам строит Total Costs, Fixed Price и stage_flags;
- сохраняет результат в st.session_state["results"] с маркером pricing_model
  ("billing" | "fixed"), чтобы дашборд знал, какой набор полей читать.

Хардкодов нет — все параметры берутся из session_state и констант модулей.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from modules.calculator import (
    blended_rate,
    gross_revenue,
    leverage,
    nne,
)
from modules.expenses import Expense, ExpenseManager
from modules.fixed_price import FixedPriceCalculator
from modules.jurisdiction import (
    CURRENCY_CODES,
    CURRENCY_SYMBOLS,
    TaxCalculator,
)
from modules.project import (
    ProjectStage,
    duties_display_options,
    load_duties_catalog,
)
from modules.team import total_direct_labor


# ---------------------------------------------------------------------------
# Подписи моделей ценообразования.
# Ключи — внутренние идентификаторы, которые сохраняются в results
# и читаются дашбордом. Значения — подписи для st.radio.
# ---------------------------------------------------------------------------
PRICING_MODELS: dict[str, str] = {
    "billing": "Биллинговая (часы × ставка)",
    "fixed":   "Фиксированная (издержки + маржа)",
}
PRICING_MODEL_LABELS: list[str] = list(PRICING_MODELS.values())

# Параметры слайдера целевой маржи (в процентах).
# Все значения в одном месте — никаких магических чисел в UI.
TARGET_MARGIN_MIN_PCT: int = 5
TARGET_MARGIN_MAX_PCT: int = 80
TARGET_MARGIN_DEFAULT_PCT: int = 30
TARGET_MARGIN_STEP_PCT: int = 1

# ---------------------------------------------------------------------------
# Пути к каталогам пошлин
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent.parent / "data"
DUTIES_CATALOGS: dict[str, Path] = {
    "RF": DATA_DIR / "rospatent_duties.json",
    "KZ": DATA_DIR / "qazpatent_duties.json",
}

# Категории «собственных» расходов проекта для произвольной строки.
CUSTOM_EXPENSE_CATEGORIES: dict[str, str] = {
    "disbursement_own": "Собственный расход (не перевыставляется клиенту)",
    "disbursement_billable": "Перевыставляемый клиенту (помимо пошлин)",
    "project_extra": "Прочее",
}


# ---------------------------------------------------------------------------
# Инициализация session_state дефолтами
# ---------------------------------------------------------------------------
st.session_state.setdefault("project_stages", [])          # list[dict]
st.session_state.setdefault("selected_duties", {"RF": [], "KZ": []})
st.session_state.setdefault("custom_expenses", [])         # list[dict]

# ---------------------------------------------------------------------------
# Заголовок
# ---------------------------------------------------------------------------
st.title("📋 Проект и смета")

# ---------------------------------------------------------------------------
# Блок 0 — Выбор модели ценообразования
# ---------------------------------------------------------------------------
# Переключатель определяет, по какой формуле считать смету:
#   «Биллинговая»   — выручка = часы × ставка (поведение v0.2);
#   «Фиксированная» — цена = издержки / (1 − margin − tax_rate) (v0.3).
# Значение сохраняется в session_state["pricing_model"] (PROFILE_KEYS),
# поэтому при загрузке профиля выбор автоматически восстанавливается.
pricing_model_label = st.radio(
    "Модель ценообразования",
    PRICING_MODEL_LABELS,
    horizontal=True,
    key="pricing_model",
)
# Преобразуем подпись радио к внутреннему идентификатору ("billing" | "fixed").
# Используем явный поиск по PRICING_MODELS — это надёжнее, чем парсить строку.
pricing_model_key: str = next(
    (k for k, v in PRICING_MODELS.items() if v == pricing_model_label),
    "billing",
)
is_fixed_price: bool = pricing_model_key == "fixed"

st.markdown("---")

# ---------------------------------------------------------------------------
# Предусловия: без Setup страница не считается
# ---------------------------------------------------------------------------
team: list[dict] = st.session_state.get("team", [])
if not team:
    st.warning(
        "Сначала заполните состав команды на странице **01 Настройка → Команда**."
    )
    st.stop()

jurisdiction_params: dict | None = st.session_state.get("jurisdiction_params")
if not jurisdiction_params:
    st.warning(
        "Сначала выберите юрисдикцию на странице **01 Настройка → Юрисдикция и налоги**."
    )
    st.stop()

jurisdiction = jurisdiction_params.get("jurisdiction", "RF")

# Основная страна для валютной разметки (символ + ISO-код).
# В режиме «Оба» по умолчанию берём РФ; пользователь может переключить
# в блоке расчёта ниже.
primary_country = "KZ" if jurisdiction == "KZ" else "RF"
currency_symbol = CURRENCY_SYMBOLS[primary_country]
currency_code = CURRENCY_CODES[primary_country]


# ===========================================================================
# БЛОК 1 — ОБЩИЕ ДАННЫЕ ПРОЕКТА
# ===========================================================================
st.subheader("1. Данные проекта")

col_name, col_client = st.columns([2, 1])
with col_name:
    project_name = st.text_input(
        "Название проекта *",
        value=st.session_state.get("project_name", ""),
        placeholder="Регистрация товарного знака «Ромашка»",
        key="project_name",
    )
with col_client:
    project_client = st.text_input(
        "Клиент (необязательно)",
        value=st.session_state.get("project_client", ""),
        placeholder="ООО «Ромашка»",
        key="project_client",
    )

project_description = st.text_area(
    "Краткое описание (необязательно)",
    value=st.session_state.get("project_description", ""),
    placeholder="Классы МКТУ 05, 29, 30; приоритет по Парижской конвенции…",
    height=80,
    key="project_description",
)

st.markdown("---")


# ===========================================================================
# БЛОК 2 — ЭТАПЫ ПРОЕКТА
# ===========================================================================
st.subheader("2. Этапы проекта")

team_names: list[str] = [m["name"] for m in team]

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
        stage_hours = st.number_input(
            "Часы",
            min_value=0.0,
            value=0.0,
            step=1.0,
            format="%.1f",
        )

    added = st.form_submit_button("➕ Добавить этап")
    if added:
        if not stage_name_input.strip():
            st.error("Название этапа не может быть пустым.")
        elif stage_hours <= 0:
            st.error("Часы должны быть больше нуля.")
        else:
            st.session_state["project_stages"].append(
                {
                    "name": stage_name_input.strip(),
                    "executor": stage_executor,
                    "hours": float(stage_hours),
                }
            )
            st.success(f"Добавлен этап: {stage_name_input}")

# --- отображение списка этапов ---
stages_data: list[dict] = st.session_state["project_stages"]

# Словарь-lookup по имени — используется и в отрисовке списка, и в сборке
# team_with_hours ниже по странице.
team_by_name: dict[str, dict] = {m["name"]: m for m in team}

if not stages_data:
    st.info("Пока нет этапов. Добавьте первый этап через форму выше.")
else:
    st.markdown("#### Состав этапов")
    for idx, stage in enumerate(list(stages_data)):
        executor = stage["executor"]
        member = team_by_name.get(executor)
        if member is None:
            # Исполнитель удалён из команды на Setup — предупреждаем и пропускаем.
            st.warning(
                f"Исполнитель «{executor}» этапа «{stage['name']}» отсутствует "
                f"в текущем составе команды. Удалите этап или измените состав."
            )
            continue

        c1, c2, c3, c4, c5 = st.columns([2, 1.5, 0.8, 1.2, 0.5])
        c1.write(f"**{stage['name']}**")
        c2.write(f"{executor} ({member['role']})")
        c3.write(f"{stage['hours']:.1f} ч")
        # В фикс-прайс модели billing_rate не участвует в расчёте — показываем
        # себестоимость часа сотрудника, чтобы пользователь видел, из чего
        # будут складываться издержки этапа.
        if is_fixed_price:
            c4.write(f"{member['cost_rate'] * stage['hours']:,.0f} {currency_symbol}")
        else:
            c4.write(f"{member['billing_rate'] * stage['hours']:,.0f} {currency_symbol}")
        if c5.button("🗑", key=f"del_stage_{idx}", help="Удалить этап"):
            st.session_state["project_stages"].pop(idx)
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

# --- реактивный пересчёт Blended Rate и Leverage ---
# Собираем team_with_hours одним проходом: каждой строке этапа соответствует
# член команды с «унаследованными» billing_rate/cost_rate/role. Имя этапа
# пробрасываем дальше, чтобы дашборд мог построить детальную таблицу по этапам.
team_with_hours: list[dict] = []
for stage in stages_data:
    member = team_by_name.get(stage["executor"])
    if member is None:
        continue
    team_with_hours.append(
        {
            "stage_name": stage["name"],
            "name": member["name"],
            "role": member["role"],
            "billing_rate": float(member["billing_rate"]),
            "cost_rate": float(member["cost_rate"]),
            "hours": float(stage["hours"]),
        }
    )

br_current = blended_rate(team_with_hours)
lev_current = leverage(team_with_hours)
hours_total = sum(m["hours"] for m in team_with_hours)

col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric(f"Средневзвешенная ставка, {currency_symbol}/ч", f"{br_current:,.0f}")
col_m2.metric(
    "Коэффициент рычага",
    f"{lev_current:.2f}",
    help="Часы младших / часы старших. Выше — выше маржа.",
)
col_m3.metric("Всего часов", f"{hours_total:.1f}")

# ---------------------------------------------------------------------------
# Слайдер + ручной ввод целевой маржи — только для фикс-прайс модели.
# ---------------------------------------------------------------------------
# Хранится в session_state["target_margin"] (number_input — главный) в ПРОЦЕНТАХ
# (целое 1..99). Слайдер — удобный shortcut в типичном диапазоне 5..80.
# Оба виджета синхронизированы через on_change-колбэки.
# Ключ совпадает с PROFILE_KEYS, поэтому значение сохраняется в профиль.

# Верхняя граница ручного ввода — выше слайдера, чтобы можно было задать
# нестандартно высокую маржу и получить предупреждение, если рынок может
# не принять цену.
TARGET_MARGIN_INPUT_MIN_PCT: int = 1
TARGET_MARGIN_INPUT_MAX_PCT: int = 99


def _sync_margin_from_slider() -> None:
    """Колбэк слайдера: подтягивает значение в number_input."""
    st.session_state["target_margin"] = int(st.session_state["margin_slider"])


def _sync_margin_from_input() -> None:
    """Колбэк number_input: обновляет слайдер, если значение в его диапазоне."""
    val = int(st.session_state["target_margin"])
    if TARGET_MARGIN_MIN_PCT <= val <= TARGET_MARGIN_MAX_PCT:
        st.session_state["margin_slider"] = val


if is_fixed_price:
    # Инициализация дефолтов до первого рендера виджетов — иначе Streamlit
    # ругается, что значение и key-state неконсистентны.
    st.session_state.setdefault("target_margin", TARGET_MARGIN_DEFAULT_PCT)
    initial_slider_val = int(st.session_state["target_margin"])
    if not (TARGET_MARGIN_MIN_PCT <= initial_slider_val <= TARGET_MARGIN_MAX_PCT):
        initial_slider_val = TARGET_MARGIN_DEFAULT_PCT
    st.session_state.setdefault("margin_slider", initial_slider_val)

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
            key="target_margin",
            on_change=_sync_margin_from_input,
            help="Точное значение — приоритет над слайдером.",
        )

    if int(st.session_state["target_margin"]) > TARGET_MARGIN_MAX_PCT:
        st.info(
            "Высокая маржа. Убедитесь, что рыночная цена проекта "
            "конкурентоспособна.",
            icon="ℹ️",
        )

st.markdown("---")


# ===========================================================================
# БЛОК 3 — РАСХОДЫ ПРОЕКТА (НЕОБЯЗАТЕЛЬНО)
# ===========================================================================
with st.expander("3. Расходы проекта — пошлины, субподряд и др. (необязательно)"):
    # --- 3.1. Пошлины из каталога(ов) ---
    st.markdown("#### Пошлины из каталога")
    st.caption(
        "Пошлины — агентские транзитные расходы: перевыставляются клиенту "
        "без наценки и исключаются из налоговой базы."
    )

    # В режиме «Оба» показываем оба каталога, иначе — только актуальный.
    active_countries: list[str] = (
        ["RF", "KZ"] if jurisdiction == "Both" else [jurisdiction]
    )

    for country_code in active_countries:
        catalog_path = DUTIES_CATALOGS.get(country_code)
        if catalog_path is None or not catalog_path.exists():
            st.caption(
                f"Каталог пошлин для {country_code} не найден "
                f"({catalog_path}) — пропускаем."
            )
            continue

        duties_list = load_duties_catalog(catalog_path)
        duties_map = duties_display_options(duties_list)  # {action: amount}
        sym = CURRENCY_SYMBOLS[country_code]

        st.markdown(
            f"**{'🇷🇺 Роспатент' if country_code == 'RF' else '🇰🇿 Казпатент'} "
            f"({sym})**"
        )

        selected_labels = st.multiselect(
            f"Выберите пошлины ({country_code})",
            options=list(duties_map.keys()),
            default=st.session_state["selected_duties"].get(country_code, []),
            key=f"duties_multiselect_{country_code}",
        )
        st.session_state["selected_duties"][country_code] = selected_labels

        if selected_labels:
            subtotal = sum(duties_map[lbl] for lbl in selected_labels)
            st.info(f"Сумма: **{subtotal:,.0f} {sym}**")

    st.markdown("---")

    # --- 3.2. Произвольный расход ---
    st.markdown("#### Произвольный расход")
    st.caption(
        "Например: командировка юриста (собственный расход), "
        "нотариус для клиента (перевыставляемый), перевод документов."
    )

    with st.form("add_custom_expense_form", clear_on_submit=True):
        col_cn, col_cc = st.columns([2, 1])
        with col_cn:
            ce_name = st.text_input(
                "Название", placeholder="Командировка в Роспатент"
            )
        with col_cc:
            ce_category = st.selectbox(
                "Категория",
                options=list(CUSTOM_EXPENSE_CATEGORIES.keys()),
                format_func=lambda k: CUSTOM_EXPENSE_CATEGORIES[k],
            )

        col_ca, col_cu = st.columns([1, 1])
        with col_ca:
            ce_amount = st.number_input(
                "Сумма", min_value=0.0, value=0.0, step=500.0, format="%.0f"
            )
        with col_cu:
            ce_currency = st.selectbox(
                "Валюта", options=list(CURRENCY_CODES.values()) + ["USD"]
            )

        ce_submit = st.form_submit_button("➕ Добавить расход")
        if ce_submit:
            if not ce_name.strip():
                st.error("Название расхода не может быть пустым.")
            elif ce_amount <= 0:
                st.error("Сумма должна быть больше нуля.")
            else:
                st.session_state["custom_expenses"].append(
                    {
                        "name": ce_name.strip(),
                        "category": ce_category,
                        "amount": float(ce_amount),
                        "currency": ce_currency,
                        "period": "one-time",
                        "billable": ce_category == "disbursement_billable",
                    }
                )
                st.success(f"Добавлен расход: {ce_name}")

    # Список текущих произвольных расходов.
    custom_list: list[dict] = st.session_state["custom_expenses"]
    if custom_list:
        st.markdown("**Текущие произвольные расходы**")
        for idx, exp in enumerate(list(custom_list)):
            c1, c2, c3, c4 = st.columns([2, 2, 1, 0.5])
            c1.write(f"**{exp['name']}**")
            c2.write(CUSTOM_EXPENSE_CATEGORIES.get(exp["category"], exp["category"]))
            c3.write(f"{exp['amount']:,.0f} {exp['currency']}")
            if c4.button("🗑", key=f"del_custom_{idx}", help="Удалить"):
                st.session_state["custom_expenses"].pop(idx)
                st.rerun()

st.markdown("---")


# ===========================================================================
# БЛОК 4 — ВЫБОР ЮРИСДИКЦИИ ДЛЯ РАСЧЁТА (в режиме «Оба»)
# ===========================================================================
# В режиме «Оба» TaxCalculator должен работать с одним набором параметров.
# Даём пользователю выбрать, какую юрисдикцию применить к смете.
if jurisdiction == "Both":
    st.subheader("4. Выбор юрисдикции для сметы")
    calc_country = st.radio(
        "В какой юрисдикции рассчитать налог этого проекта?",
        options=["RF", "KZ"],
        format_func=lambda c: "🇷🇺 Россия" if c == "RF" else "🇰🇿 Казахстан",
        horizontal=True,
        key="calc_country_choice",
    )
else:
    calc_country = jurisdiction

# Выбираем params и валюту для расчёта.
if calc_country == "RF":
    tax_params = jurisdiction_params.get("rf") or {
        "country": "RF",
        "regime": "USN",
        "object": "income",
        "vat": "none",
    }
else:
    tax_params = jurisdiction_params.get("kz") or {
        "country": "KZ",
        "regime": "OUR",
        "form": "too",
        "vat": "none",
    }

calc_symbol = CURRENCY_SYMBOLS[calc_country]
calc_code = CURRENCY_CODES[calc_country]


# ===========================================================================
# БЛОК 5 — КНОПКА «РАССЧИТАТЬ СМЕТУ»
# ===========================================================================
st.subheader("5. Рассчитать смету")

calc_disabled = not (project_name.strip() and team_with_hours)
if calc_disabled:
    st.caption(
        "Чтобы рассчитать смету, укажите название проекта и добавьте "
        "хотя бы один этап с часами."
    )

calc_clicked = st.button(
    "💰 Рассчитать смету",
    type="primary",
    disabled=calc_disabled,
)

if calc_clicked:
    # --- 1. Менеджер расходов: накладные фирмы + расходы проекта ---
    # Шаг идентичен для обеих моделей: ExpenseManager одинаково нужен
    # и в gross_revenue/NNE, и в Total Costs фикс-прайса.
    billable_hours_month = float(
        st.session_state.get("billable_hours_per_month", 160.0)
    )
    manager = ExpenseManager(billable_hours_per_month=billable_hours_month)

    # Накладные фирмы из Setup.
    for item in st.session_state.get("firm_expenses", []):
        try:
            manager.add_firm_overhead(Expense.from_dict(item))
        except ValueError as exc:
            st.warning(f"Пропущен накладной расход: {exc}")

    # Пошлины — только из активных каталогов для выбранной юрисдикции.
    # (В режиме «Оба» берём обе страны: фирма всё равно их оплачивает.)
    disb_countries = (
        ["RF", "KZ"] if jurisdiction == "Both" else [jurisdiction]
    )
    for cc in disb_countries:
        catalog_path = DUTIES_CATALOGS.get(cc)
        if catalog_path is None or not catalog_path.exists():
            continue
        duties_list = load_duties_catalog(catalog_path)
        duties_map = duties_display_options(duties_list)
        for label in st.session_state["selected_duties"].get(cc, []):
            amount = float(duties_map.get(label, 0.0))
            if amount <= 0:
                continue
            manager.add_project_expense(
                Expense(
                    name=label,
                    category="disbursement_billable",
                    amount=amount,
                    currency=CURRENCY_CODES[cc],
                    period="one-time",
                    billable=True,
                )
            )

    # Произвольные расходы проекта.
    for item in st.session_state.get("custom_expenses", []):
        try:
            manager.add_project_expense(Expense.from_dict(item))
        except ValueError as exc:
            st.warning(f"Пропущен произвольный расход: {exc}")

    # --- 2. Общие сериализуемые поля результата ---
    project_expenses_dump = [e.to_dict() for e in manager.project_expenses]
    overhead_rate_val = manager.calculate_overhead_rate(billable_hours_month)
    total_hours_val = sum(m["hours"] for m in team_with_hours)

    # Базовый словарь результата — поля, общие для обеих моделей.
    base_results: dict = {
        # идентификация проекта
        "project_name": project_name,
        "client": project_client,
        "description": project_description,
        # юрисдикция
        "calc_country": calc_country,
        "jurisdiction_mode": jurisdiction,
        "currency": calc_symbol,
        "currency_code": calc_code,
        # показатели команды
        "blended_rate": br_current,
        "leverage": lev_current,
        "total_hours": total_hours_val,
        "overhead_rate": overhead_rate_val,
        # детализация для дашборда
        "team_with_hours": team_with_hours,
        "project_expenses": project_expenses_dump,
    }

    # --- 3. Расчёт по выбранной модели -----------------------------------
    if is_fixed_price:
        # Собираем team_stages в формате FixedPriceCalculator: один
        # ProjectStage = один словарь с name + assigned_members.
        # Каждой роли в стадии достаётся часовой объём этого этапа.
        team_stages_for_fp: list[dict] = []
        for stage in stages_data:
            member = team_by_name.get(stage["executor"])
            if member is None:
                continue
            team_stages_for_fp.append(
                {
                    "name": stage["name"],
                    "assigned_members": [
                        {
                            "name": member["name"],
                            "role": member["role"],
                            "billing_rate": float(member["billing_rate"]),
                            "cost_rate": float(member["cost_rate"]),
                            "hours": float(stage["hours"]),
                        }
                    ],
                }
            )

        # Слайдер хранит проценты — переводим в долю.
        target_margin_pct = int(
            st.session_state.get("target_margin", TARGET_MARGIN_DEFAULT_PCT)
        )
        target_margin_decimal = target_margin_pct / 100.0

        fp_calc = FixedPriceCalculator()
        try:
            fp_result = fp_calc.calculate(
                team_stages=team_stages_for_fp,
                expense_manager=manager,
                jurisdiction_params=tax_params,
                target_margin=target_margin_decimal,
            )
        except ValueError as exc:
            st.error(f"Не удалось рассчитать фиксированную цену: {exc}")
            st.stop()

        stage_flags = fp_calc.get_stage_flags(
            stages=team_stages_for_fp,
            fixed_price=fp_result["fixed_price"],
            target_margin=target_margin_decimal,
        )

        # Подпись режима для шапки дашборда — берём из TaxCalculator
        # на номинальной цене (нужен только regime_label).
        regime_label = TaxCalculator(tax_params).calculate_tax(
            revenue=fp_result["fixed_price"],
            params={**tax_params, "expenses": fp_result["total_costs"]},
        )["regime_label"]

        results = {
            **base_results,
            "pricing_model": "fixed",
            "regime_label": regime_label,
            # фикс-прайс показатели
            "fixed_price": float(fp_result["fixed_price"]),
            "total_costs": float(fp_result["total_costs"]),
            "direct_labor": float(fp_result["direct_labor"]),
            "overheads_alloc": float(fp_result["overheads_alloc"]),
            "disbursements_own": float(fp_result["disbursements_own"]),
            "tax": float(fp_result["tax_amount"]),
            "nne": float(fp_result["nne"]),
            "actual_margin": float(fp_result["actual_margin"]),
            "target_margin": target_margin_decimal,
            "stage_flags": stage_flags,
            # для совместимости с экспортом / навигацией
            "disbursements": manager.get_disbursements_billed(),
        }
    else:
        # --- Биллинговая модель: поведение v0.2 ---
        gr_val = gross_revenue(team_with_hours)
        direct_labor_val = total_direct_labor(team_with_hours)

        # Накладные аллоцируем пропорционально часам проекта:
        #   overhead_rate   = monthly_overheads / billable_hours_per_month
        #   overheads_alloc = overhead_rate × total_hours_project
        overheads_alloc = manager.get_overheads_allocated(total_hours_val)

        disbursements_billed = manager.get_disbursements_billed()
        disbursements_own = manager.get_disbursements_own()

        tax_calc = TaxCalculator(tax_params)
        tax_result = tax_calc.calculate_tax(
            revenue=gr_val + disbursements_billed,
            params={
                **tax_params,
                "expenses": direct_labor_val + overheads_alloc + disbursements_own,
                "disbursements_billed": disbursements_billed,
            },
        )
        tax_total = float(tax_result["total_tax"])

        nne_val = nne(
            gross=gr_val,
            direct_labor=direct_labor_val,
            overheads_alloc=overheads_alloc,
            disbursements_own=disbursements_own,
            tax=tax_total,
        )

        results = {
            **base_results,
            "pricing_model": "billing",
            "regime_label": tax_result["regime_label"],
            # финансы
            "gross_revenue": gr_val,
            "direct_labor": direct_labor_val,
            "overheads_alloc": overheads_alloc,
            "disbursements": disbursements_billed,
            "disbursements_own": disbursements_own,
            "income_tax": float(tax_result["income_tax"]),
            "vat": float(tax_result["vat"]),
            "tax": tax_total,
            "taxable_base": float(tax_result["taxable_base"]),
            "nne": nne_val,
            "total_client": gr_val + disbursements_billed,
        }

    st.session_state["results"] = results
    # Обратная совместимость: старый ключ также обновляем,
    # чтобы 03_Dashboard работал без изменений на старых сессиях.
    st.session_state["project_result"] = results


# ===========================================================================
# БЛОК 6 — PREVIEW РАСЧЁТА
# ===========================================================================
results_current: dict | None = st.session_state.get("results")
if results_current:
    st.markdown("### Предварительный результат")

    sym = results_current["currency"]
    result_model = results_current.get("pricing_model", "billing")

    if result_model == "fixed":
        # Фикс-прайс preview: цена / NNE / реальная маржа.
        p1, p2, p3 = st.columns(3)
        p1.metric(
            f"Фиксированная цена, {sym}",
            f"{results_current['fixed_price']:,.0f}",
        )
        p2.metric(
            f"Чистая прибыль (NNE), {sym}",
            f"{results_current['nne']:,.0f}",
        )
        p3.metric(
            "Реальная маржа",
            f"{results_current['actual_margin'] * 100:.1f}%",
            delta=(
                f"цель {results_current['target_margin'] * 100:.0f}%"
            ),
        )
        st.caption(
            f"Режим: {results_current['regime_label']} · "
            f"Все издержки: {results_current['total_costs']:,.0f} {sym} · "
            f"Налог: {results_current['tax']:,.0f} {sym}"
        )
    else:
        # Биллинговая preview (как в v0.2): выручка / налог / NNE.
        p1, p2, p3 = st.columns(3)
        p1.metric(f"Выручка, {sym}", f"{results_current['gross_revenue']:,.0f}")
        p2.metric(f"Налог, {sym}", f"{results_current['tax']:,.0f}")
        p3.metric(
            f"Чистая прибыль (NNE), {sym}",
            f"{results_current['nne']:,.0f}",
            delta=(
                f"{(results_current['nne'] / results_current['gross_revenue'] * 100):.1f}%"
                if results_current["gross_revenue"]
                else None
            ),
        )
        st.caption(
            f"Режим: {results_current['regime_label']} · "
            f"Пошлины (транзит): {results_current['disbursements']:,.0f} {sym} · "
            f"Собственные расходы: {results_current['disbursements_own']:,.0f} {sym}"
        )

    # Кнопка-подсказка. Сам переход возможен через sidebar Streamlit,
    # поэтому кнопка здесь — навигационная подсказка.
    st.page_link(
        "pages/03_Дашборд.py",
        label="📊 Перейти к дашборду",
        icon="➡️",
    )

    with st.expander("Детали расчёта"):
        rows = []
        for m in results_current["team_with_hours"]:
            row = {
                "Сотрудник": m["name"],
                "Роль": m["role"],
                "Часы": m["hours"],
                f"Себестоимость, {sym}": m["cost_rate"] * m["hours"],
            }
            # Колонку выручки показываем только в биллинг-модели —
            # в фикс-прайсе billing_rate не используется.
            if result_model != "fixed":
                row[f"Выручка, {sym}"] = m["billing_rate"] * m["hours"]
            rows.append(row)
        st.dataframe(
            pd.DataFrame(rows),
            width='stretch',
            hide_index=True,
        )
