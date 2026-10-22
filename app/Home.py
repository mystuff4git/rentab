"""Rentab — точка входа приложения Streamlit.

Запуск из корня репозитория:

    uv run streamlit run app/Home.py

Расчёты выполняет ядро `rentab_core`; страницы в `pages/` только собирают
ввод и рисуют результат. Данные фирмы хранятся только в текущей сессии:
приложение ничего не записывает на диск сервера (L-10). Главная страница
загружает демо-фирму и переносит данные в файл JSON и обратно
(`rentab_core.io`): файл скачивает и загружает сам пользователь.
"""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd
import streamlit as st

from rentab_core.demo import demo_workspace
from rentab_core.errors import ProfileImportError
from rentab_core.io import ImportResult, NoteLevel, Workspace, dumps, loads
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, decimal_sum
from ui import state
from ui.labels import PRICING_LABELS, money
from ui.projects import render_project_switcher

PRIVACY_DISCLAIMER = (
    "**Данные не покидают вашу сессию.** Всё, что вы вводите, хранится только в памяти "
    "этой сессии и исчезает, когда вы закрываете вкладку. Приложение ничего не "
    "записывает на диск сервера и не показывает ваши данные другим посетителям. "
    "Чтобы вернуться к работе позже, скачайте файл JSON и загрузите его снова. "
    "Наружу уходит только дата — при загрузке курсов ЦБ РФ и НБРК."
)
JSON_MIME = "application/json"
# Загрузчик файла пересоздаётся с новым ключом после загрузки: так он очищается.
IMPORT_NONCE = "import_nonce"
IMPORT_FILE_PREFIX = "import_file_"
# Замена данных сессии ждёт подтверждения: «demo» или «import».
PENDING_REPLACE = "pending_replace"
SOURCE_DEMO = "demo"
SOURCE_IMPORT = "import"
# Итог последней загрузки: заголовок, замечания, ошибка.
REPLACE_REPORT = "replace_report"
EXPORT_PROJECTS = "export_include_projects"
NOTE_ICONS = {NoteLevel.INFO: "ℹ️", NoteLevel.WARNING: "⚠️"}
# Имя файла выгрузки: название фирмы без знаков препинания, не длиннее 40 символов.
FILE_NAME_MAX = 40


def uploader_key() -> str:
    """Ключ загрузчика файла."""
    return f"{IMPORT_FILE_PREFIX}{st.session_state.get(IMPORT_NONCE, 0)}"


def uploaded_bytes() -> bytes | None:
    """Содержимое загруженного файла профиля."""
    uploaded = st.session_state.get(uploader_key())
    return None if uploaded is None else bytes(uploaded.getvalue())


def export_file_name(firm_name: str, today: dt.date) -> str:
    """Имя файла выгрузки: «Rentab_Демо_фирма_Слайд_3_2026-10-22.json»."""
    slug = re.sub(r"\W+", "_", firm_name).strip("_")[:FILE_NAME_MAX] or "профиль"
    return f"Rentab_{slug}_{today.isoformat()}.json"


def describe(workspace: Workspace) -> str:
    """Что в файле: фирма, сотрудники, проекты."""
    firm = workspace.firm
    name = f"фирма «{firm.name}»" if firm.name else "фирма без названия"
    return f"{name}, сотрудников: {len(firm.team)}, проектов: {len(workspace.projects)}"


def _apply(source: str) -> None:
    """Колбэк-помощник: заменяет данные сессии демо-фирмой или файлом."""
    if source == SOURCE_DEMO:
        workspace = demo_workspace()
        title = f"Загружена демо-фирма: {describe(workspace)}."
        notes: list[tuple[NoteLevel, str]] = []
    else:
        data = uploaded_bytes()
        if data is None:
            return
        try:
            result: ImportResult = loads(data)
        except ProfileImportError as exc:
            st.session_state[REPLACE_REPORT] = {"error": str(exc)}
            return
        workspace = result.workspace
        title = f"Загружены данные из файла: {describe(workspace)}."
        notes = [(note.level, note.message) for note in result.notes]
    notes += [(NoteLevel.WARNING, message) for message in state.apply_workspace(workspace)]
    st.session_state[REPLACE_REPORT] = {"title": title, "notes": notes}
    st.session_state[IMPORT_NONCE] = int(st.session_state.get(IMPORT_NONCE, 0)) + 1


def request_replace(source: str) -> None:
    """Колбэк: заменить данные сразу или, если они уже есть, спросить подтверждение."""
    st.session_state.pop(REPLACE_REPORT, None)
    if state.has_user_data():
        st.session_state[PENDING_REPLACE] = source
    else:
        _apply(source)


def confirm_replace() -> None:
    """Колбэк: подтверждение замены данных."""
    _apply(str(st.session_state.pop(PENDING_REPLACE)))


def cancel_replace() -> None:
    """Колбэк: отмена замены данных."""
    st.session_state.pop(PENDING_REPLACE, None)


def render_report() -> None:
    """Итог последней загрузки: успех и замечания или ошибка."""
    report = st.session_state.get(REPLACE_REPORT)
    if report is None:
        return
    if "error" in report:
        st.error(f"Файл не загружен. {report['error']}", icon="⛔")
        return
    st.success(report["title"], icon="✅")
    notes = report["notes"]
    if notes:
        with st.expander(f"Что изменилось при загрузке ({len(notes)})", expanded=True):
            st.markdown("\n".join(f"- {NOTE_ICONS[level]} {message}" for level, message in notes))


def render_pending() -> None:
    """Подтверждение замены данных сессии."""
    source = st.session_state.get(PENDING_REPLACE)
    if source is None:
        return
    what = "демо-фирма" if source == SOURCE_DEMO else "данные из файла"
    st.warning(
        f"В сессии уже есть данные: их заменит {what}. Если они нужны, сначала "
        "скачайте их в JSON (справа).",
        icon="⚠️",
    )
    col_yes, col_no, _ = st.columns([1, 1, 3])
    col_yes.button("Заменить данные", type="primary", on_click=confirm_replace)
    col_no.button("Отмена", on_click=cancel_replace)


def render_import() -> None:
    """Загрузка файла профиля: проверка, сводка и кнопка загрузки."""
    st.markdown("**📂 Загрузить из файла**")
    st.file_uploader(
        "Файл профиля Rentab (.json) или firm_profile.json v0.3",
        type=["json"],
        key=uploader_key(),
    )
    data = uploaded_bytes()
    if data is None:
        st.caption("Фирма, курсы и проекты из файла заменят данные этой сессии.")
        return
    try:
        result = loads(data)
    except ProfileImportError as exc:
        st.error(str(exc), icon="⛔")
        return
    kind = "профиль v0.3 — будет переведён" if result.from_v03 else "профиль Rentab"
    st.caption(f"В файле ({kind}): {describe(result.workspace)}.")
    st.button(
        "📂 Загрузить данные из файла",
        on_click=request_replace,
        args=(SOURCE_IMPORT,),
        disabled=PENDING_REPLACE in st.session_state,
    )


def render_export() -> None:
    """Выгрузка фирмы и проектов в JSON."""
    st.markdown("**💾 Сохранить в файл**")
    st.session_state.setdefault(EXPORT_PROJECTS, True)
    include = st.checkbox("Вместе с проектами", key=EXPORT_PROJECTS)
    if include:
        for title in state.incomplete_projects():
            st.caption(f"⚠️ «{title}»: потолок или цена не заданы — в файле проект почасовой.")
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    st.download_button(
        "💾 Скачать JSON",
        data=dumps(state.workspace(), exported_at=now, include_projects=include),
        file_name=export_file_name(state.text(state.FIRM_NAME), now.date()),
        mime=JSON_MIME,
    )
    st.caption("Файл остаётся у вас: приложение его не хранит.")


def render_projects() -> None:
    """Проекты сессии: цена, часы и результат последней сметы."""
    rows = []
    active = state.active_id()
    for slot in state.projects():
        values = state.slot_values(slot)
        record = state.project_record(values, strict=False)
        result = state.slot_estimate(slot)
        hours = decimal_sum(
            item.hours for stage in record.project.stages for item in stage.assignments
        )
        rows.append(
            {
                "": "▶" if slot.id == active else "",
                "Проект": state.slot_title(slot.id),
                "Клиент": record.project.client,
                "Модель цены": PRICING_LABELS[state.pricing_kind(values)],
                "Часов": float(hours),
                "Факт": "—" if record.project.actuals is None else "введён",
                "NNE": "—"
                if result is None
                else money(result.nne, CURRENCY_SYMBOLS[result.currency]),
                "Маржа": "—" if result is None else f"{result.margin * PERCENT:.1f}%",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "▶ — активный проект: его открывают страницы «Проект» и «Дашборд». Переключить, "
        "создать, скопировать или удалить проект можно в боковой панели. NNE и маржа — по "
        "последней рассчитанной смете."
    )


st.set_page_config(
    page_title="Rentab",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

state.init_defaults()
state.persist_widgets()
render_project_switcher()

st.title("⚖️ Rentab")
st.subheader("Калькулятор рентабельности IP-проектов")
st.markdown(
    """
    Rentab — инструмент для расчёта стоимости и рентабельности проектов
    в сфере IP-юридического консалтинга по методологии Д. Майстера:
    **себестоимость биллируемого часа · утилизация · рычаг · чистая прибыль (NNE)**.
    НДС начисляется сверху, пошлины — возмещаемые расходы вне выручки.
    """
)
st.info(PRIVACY_DISCLAIMER, icon="🔒")

st.markdown("### 🚀 Данные")
render_report()
render_pending()
col_demo, col_import, col_export = st.columns(3)
with col_demo:
    st.markdown("**🎓 Демо-фирма**")
    st.caption(
        "Фирма со слайда 3 защиты ВКР (партнёр, юрист, стажёр; УСН 6% + НДС 5%) и четыре "
        "проекта: слайд 3 (план и факт), товарный знак (с фактом), изобретение, ответ на "
        "уведомление экспертизы."
    )
    st.button(
        "🎓 Загрузить демо-фирму",
        on_click=request_replace,
        args=(SOURCE_DEMO,),
        disabled=PENDING_REPLACE in st.session_state,
    )
with col_import:
    render_import()
with col_export:
    render_export()

st.markdown("### 📁 Проекты сессии")
render_projects()

st.markdown(
    """
    ---

    ### Навигация

    Используйте меню **слева** для перехода между разделами:

    | Страница | Назначение |
    |---|---|
    | **01 Настройка** | Юрисдикция, налоги, курсы; команда с утилизацией; накладные |
    | **02 Проект** | Этапы и исполнители, модель цены, НДС, расходы, смета |
    | **03 Дашборд** | NNE и маржа, структура выручки, светофор этапов, смета XLSX |
    | **04 Чувствительность** | Запас часов до безубыточности, торнадо, «что если» |
    | **05 Сравнение** | Матрица «модель цены × налоговый режим» с доступностью режимов |
    | **06 План–факт** | Фактические часы, реализация, списания, перерасход по этапам и ролям |
    """
)
if not state.team():
    st.info(
        "Начните с раздела **01 Настройка** — выберите юрисдикцию и введите состав команды — "
        "или загрузите демо-фирму.",
        icon="👆",
    )

st.caption("Rentab · наследник ВКР НИУ ВШЭ (v0.3) · Python · Streamlit · Plotly")
