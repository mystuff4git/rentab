"""Переключатель проектов сессии в боковой панели (на всех страницах).

Значения проектов меняются только в колбэках (`ui.state`): так Streamlit
успевает разложить значения выбранного проекта по ключам до создания
виджетов страницы.
"""

from __future__ import annotations

import streamlit as st

from ui import state

# Удаление проекта ждёт подтверждения.
DELETE_PENDING = "project_delete_pending"


def _ask_delete() -> None:
    """Колбэк: спросить подтверждение удаления."""
    st.session_state[DELETE_PENDING] = True


def _cancel_delete() -> None:
    """Колбэк: отменить удаление."""
    st.session_state.pop(DELETE_PENDING, None)


def _confirm_delete() -> None:
    """Колбэк: удалить активный проект."""
    st.session_state.pop(DELETE_PENDING, None)
    state.delete_project()


def render_project_switcher() -> None:
    """Выбор активного проекта, новый проект, копия и удаление."""
    slots = state.projects()
    # Подписи считаются заранее: format_func не должен читать session_state
    # (streamlit.testing вызывает его вне сессии).
    titles = {slot.id: state.slot_title(slot.id) for slot in slots}
    # Значение переключателя задаётся до виджета: после загрузки файла или
    # перехода со страницы, где переключателя нет, он показывает активный проект.
    st.session_state[state.PROJECT_SWITCH] = state.active_id()
    with st.sidebar:
        st.markdown("### 📁 Проекты сессии")
        st.selectbox(
            "Активный проект",
            options=[slot.id for slot in slots],
            format_func=titles.__getitem__,
            key=state.PROJECT_SWITCH,
            on_change=state.switch_project,
            help="У каждого проекта свои этапы, цена, пошлины, факт и «что если».",
        )
        col_new, col_copy = st.columns(2)
        col_new.button(
            "➕ Новый", on_click=state.new_project, key="btn_project_new", width="stretch"
        )
        col_copy.button(
            "⧉ Копия",
            on_click=state.duplicate_project,
            key="btn_project_copy",
            width="stretch",
            help="Копия активного проекта без сметы.",
        )
        if st.session_state.get(DELETE_PENDING):
            st.warning(f"Удалить проект «{state.slot_title(state.active_id())}»?")
            col_yes, col_no = st.columns(2)
            col_yes.button(
                "Да, удалить",
                on_click=_confirm_delete,
                key="btn_project_delete_yes",
                type="primary",
            )
            col_no.button("Отмена", on_click=_cancel_delete, key="btn_project_delete_no")
        else:
            st.button(
                "🗑 Удалить проект",
                on_click=_ask_delete,
                key="btn_project_delete",
                width="stretch",
            )
        st.caption("Сохранить проекты в файл и загрузить их обратно можно на главной странице.")
