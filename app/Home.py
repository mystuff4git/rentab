"""Rentab — точка входа приложения Streamlit: меню-визард и общие части страниц.

Запуск из корня репозитория:

    uv run streamlit run app/Home.py

Расчёты выполняет ядро `rentab_core`; страницы в `views/` только собирают
ввод и рисуют результат. Меню — `st.navigation` сверху: «Главная» и шаги
«Фирма → Проект → Результат → Сравнение → План–факт» (`ui.nav`). Здесь же для
каждой страницы: значения сессии по умолчанию, сохранение значений виджетов
между страницами, стили (`ui.theme`) и переключатель проектов.

Данные фирмы хранятся только в текущей сессии: приложение ничего не
записывает на диск сервера (L-10).
"""

from __future__ import annotations

import streamlit as st

from ui import nav, state, theme
from ui.projects import render_project_switcher

st.set_page_config(
    page_title="Rentab",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="auto",
)

state.init_defaults()
state.persist_widgets()
theme.apply()
page = st.navigation(nav.pages(), position="top")
render_project_switcher()
page.run()
