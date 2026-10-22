"""Общие помощники smoke-тестов интерфейса (`streamlit.testing`)."""

from __future__ import annotations

from typing import Any

from streamlit.testing.v1 import AppTest

from tests.app.conftest import APP_DIR
from ui import state

TIMEOUT_SECONDS = 60
# Имя файла выгрузки профиля начинается так (Home.export_file_name).
EXPORT_PREFIX = "Rentab_"


def run_page(path: str, session: dict[str, Any] | None = None) -> AppTest:
    """Открывает страницу из точки входа с заданным состоянием сессии.

    Приложение запускается с Home.py и переходит на страницу так же, как
    пользователь из меню: иначе не работают ссылки между страницами.
    """
    app = AppTest.from_file(str(APP_DIR / "Home.py"), default_timeout=TIMEOUT_SECONDS)
    for key, value in (session or {}).items():
        app.session_state[key] = value
    app.run()
    if path != "Home.py":
        app.switch_page(path).run()
    assert not app.exception, app.exception
    return app


def click(app: AppTest, label: str) -> None:
    """Нажимает кнопку по подписи и перезапускает страницу."""
    (button,) = [b for b in app.button if b.label == label]
    button.click().run()
    assert not app.exception, app.exception


def metrics(app: AppTest) -> dict[str, str]:
    """Показатели страницы: подпись → значение."""
    return {m.label: m.value for m in app.metric}


def slot_ids(app: AppTest) -> list[str]:
    """Номера проектов сессии по порядку."""
    return [slot.id for slot in app.session_state[state.PROJECTS]]


def switch(app: AppTest, slot_id: str) -> None:
    """Выбирает активный проект в боковой панели."""
    app.selectbox(key=state.PROJECT_SWITCH).set_value(slot_id).run()
    assert not app.exception, app.exception


def exported(downloads: dict[str, bytes]) -> bytes:
    """Последний файл выгрузки профиля (см. фикстуру `downloads`)."""
    (name,) = [name for name in downloads if name.startswith(EXPORT_PREFIX)][-1:]
    return downloads.pop(name)


def upload(app: AppTest, data: bytes, name: str = "profile.json") -> None:
    """Загружает файл в загрузчик профиля на главной."""
    app.file_uploader[0].set_value((name, data, "application/json")).run()
    assert not app.exception, app.exception
