"""Общие помощники smoke-тестов интерфейса (`streamlit.testing`)."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from streamlit.testing.v1 import AppTest

from rentab_core.estimate import calculate_estimate
from rentab_core.results import Estimate
from tests.app.conftest import APP_DIR
from ui import nav, state
from ui.labels import NBSP

TIMEOUT_SECONDS = 60
# Имя файла выгрузки профиля начинается так (views/home.export_file_name).
EXPORT_PREFIX = "Rentab_"
HOME = nav.HOME


def run_page(path: str, session: dict[str, Any] | None = None) -> AppTest:
    """Открывает страницу из точки входа с заданным состоянием сессии.

    Приложение запускается с Home.py (меню `st.navigation`) и переходит на
    страницу так же, как пользователь из меню.
    """
    app = AppTest.from_file(str(APP_DIR / "Home.py"), default_timeout=TIMEOUT_SECONDS)
    for key, value in (session or {}).items():
        app.session_state[key] = value
    app.run()
    if path != HOME:
        app.switch_page(path).run()
    assert not app.exception, app.exception
    return app


def plain(text: str) -> str:
    """Текст интерфейса с обычными пробелами вместо неразрывных."""
    return text.replace(NBSP, " ")


def click(app: AppTest, label: str) -> None:
    """Нажимает кнопку по подписи и перезапускает страницу."""
    (button,) = [b for b in app.button if b.label == label]
    button.click().run()
    assert not app.exception, app.exception


def metrics(app: AppTest) -> dict[str, str]:
    """Показатели страницы: подпись → значение (пробелы обычные)."""
    return {m.label: plain(m.value) for m in app.metric}


def slot_ids(app: AppTest) -> list[str]:
    """Номера проектов сессии по порядку."""
    return [slot.id for slot in app.session_state[state.PROJECTS]]


def switch(app: AppTest, slot_id: str) -> None:
    """Выбирает активный проект в боковой панели."""
    app.selectbox(key=state.PROJECT_SWITCH).set_value(slot_id).run()
    assert not app.exception, app.exception


def open_tab(app: AppTest, label: str) -> None:
    """Открывает вкладку «Результата» («Смета» или «Чувствительность»)."""
    app.session_state[state.RESULT_TAB] = label
    app.run()
    assert not app.exception, app.exception


def estimate_of(app: AppTest) -> Estimate:
    """Смета активного проекта по текущим значениям сессии."""
    values = app.session_state.to_dict()
    return calculate_estimate(state.project_record(values).scenario(state.firm(values)))


def exported(downloads: dict[str, bytes]) -> bytes:
    """Последний файл выгрузки профиля (см. фикстуру `downloads`)."""
    (name,) = [name for name in downloads if name.startswith(EXPORT_PREFIX)][-1:]
    return downloads.pop(name)


def upload(app: AppTest, data: bytes, name: str = "profile.json") -> None:
    """Загружает файл в загрузчик профиля на главной."""
    app.file_uploader[0].set_value((name, data, "application/json")).run()
    assert not app.exception, app.exception


# ---------------------------------------------------------------------------
# Таблицы аналитики (HTML, ui.components.table_html)
# ---------------------------------------------------------------------------
class _TableParser(HTMLParser):
    """Строки таблицы: заголовок столбца → текст ячейки (без пояснения и значка)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self.notes: list[dict[str, str]] = []
        self.classes: list[set[str]] = []
        self._label = ""
        self._text: list[str] = []
        self._note: list[str] = []
        self._skip = 0
        self._in_note = 0
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "tr":
            self._row: dict[str, str] = {}
            self._row_notes: dict[str, str] = {}
            self._row_classes = classes
        elif tag == "td":
            self._in_cell = True
            self._label = attributes.get("data-label") or ""
            self._text, self._note = [], []
        elif tag == "span" and self._in_cell:
            if "rt-flag" in classes:
                self._skip += 1
            elif "rt-note" in classes:
                self._in_note += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._skip:
            self._skip -= 1
        elif tag == "span" and self._in_note:
            self._in_note -= 1
        elif tag == "td":
            self._in_cell = False
            self._row[self._label] = plain("".join(self._text).strip())
            self._row_notes[self._label] = plain("".join(self._note).strip())
        elif tag == "tr" and getattr(self, "_row", None):
            self.rows.append(self._row)
            self.notes.append(self._row_notes)
            self.classes.append(self._row_classes)
            self._row = {}

    def handle_data(self, data: str) -> None:
        if not self._in_cell or self._skip:
            return
        (self._note if self._in_note else self._text).append(data)


def table(app: AppTest, key: str) -> _TableParser:
    """Таблица аналитики по ключу (`Table.key`)."""
    marker = f'class="rt-table rt-table--{key}"'
    bodies = [item.value for item in app.html if marker in item.value]
    assert len(bodies) == 1, f"таблица «{key}»: найдено {len(bodies)}"
    parser = _TableParser()
    parser.feed(bodies[0])
    return parser


def column(app: AppTest, key: str, name: str) -> list[str]:
    """Тексты столбца таблицы аналитики."""
    return [row.get(name, "") for row in table(app, key).rows]


def has_table(app: AppTest, key: str) -> bool:
    """Есть ли на странице таблица аналитики с ключом."""
    return any(f'class="rt-table rt-table--{key}"' in item.value for item in app.html)
