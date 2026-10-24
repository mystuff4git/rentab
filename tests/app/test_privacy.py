"""Приватность (L-10): приложение не пишет на диск и не читает профиль v0.3 само.

Главный тест проходит по всем шагам так, как это сделал бы пользователь,
и падает, если хоть один байт записан вне временного каталога системы или
временный файл остался на диске. Временные файлы создаёт openpyxl, собирая
смету XLSX в памяти, и сразу удаляет. Статическая проверка дополняет его:
ветки кода, которые прогон не прошёл, тоже не вызывают функций записи.
"""

from __future__ import annotations

import ast
import datetime as dt
import functools
import tomllib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from rentab_core import fx
from rentab_core.io import MAX_PROFILE_BYTES
from tests.app.conftest import APP_DIR
from tests.app.helpers import (
    HOME,
    click,
    exported,
    open_tab,
    run_page,
    slot_ids,
    switch,
    upload,
)
from tests.app.write_guard import record_writes
from tests.core.test_fx import CBR, CBR_XML, FakeFetcher
from ui import nav, state

ROOT = APP_DIR.parent
CORE_DIR = ROOT / "src" / "rentab_core"
V03_FIXTURE = ROOT / "tests" / "fixtures" / "legacy" / "v03_firm_profile.json"
CONFIG = ROOT / ".streamlit" / "config.toml"
BYTES_PER_MB = 1024 * 1024


def user_journey(downloads: dict[str, bytes], v03_profile: bytes) -> None:
    """Все страницы и все действия с данными: демо, курсы, команда, проекты, аналитика, файлы."""
    app = run_page(HOME)
    click(app, "🎓 Загрузить демо-фирму")

    # Фирма: курсы, новый сотрудник, правка с переименованием, удаление, накладные.
    app.switch_page(nav.FIRM).run()
    click(app, "⬇️ Загрузить курсы ЦБ РФ")
    app.text_input(key="add_member_name").input("Новый юрист")
    app.number_input(key="add_member_gross_salary").set_value(120_000.0)
    app.run()
    click(app, "➕ Добавить сотрудника")
    app.button(key="edit_member_1").click().run()
    app.text_input(key="add_member_name").input("Юрист Петрова")
    app.number_input(key="add_member_utilization_pct").set_value(70.0)
    app.run()
    click(app, "💾 Сохранить изменения")
    app.button(key="edit_member_3").click().run()
    click(app, "Отмена")
    app.button(key="del_member_3").click().run()
    click(app, "📥 Загрузить типовой шаблон")

    # Проект: смета считается сразу; проекты — новый, копия, удаление.
    app.switch_page(nav.PROJECT).run()
    ids = slot_ids(app)
    switch(app, ids[1])
    click(app, "⧉ Копия")
    click(app, "➕ Новый")
    click(app, "🗑 Удалить проект")
    click(app, "Да, удалить")

    # Результат: смета XLSX для кнопки «Скачать», вкладка «Чувствительность».
    app.switch_page(nav.RESULT).run()
    switch(app, ids[1])
    assert app.get("download_button")
    click(app, "Чувствительность")
    app.slider(key=state.WHATIF_HOURS).set_value(50).run()
    app.slider(key=state.TORNADO_PCT).set_value(20).run()
    click(app, "↺ Сбросить «что если»")
    open_tab(app, "Смета")

    # Сравнение и план–факт.
    app.switch_page(nav.COMPARE).run()
    app.number_input(key=state.COMPARE_CAP).set_value(90_000.0).run()
    app.radio(key=state.COMPARE_METRIC).set_value("total_client").run()
    app.checkbox(key=state.COMPARE_ALL_VARIANTS).check().run()
    app.switch_page(nav.PLAN_FACT).run()
    switch(app, ids[2])
    click(app, "📋 Заполнить факт по плану")
    app.number_input(key="unplanned_hours").set_value(2.0)
    click(app, "➕ Добавить")
    app.checkbox(key=state.ACTUAL_BILLED_MANUAL).check().run()
    app.number_input(key=state.ACTUAL_BILLED).set_value(100_000.0).run()
    assert app.session_state[state.ACTUAL_HOURS]

    app.switch_page(HOME).run()
    data = exported(downloads)
    assert b'"actuals"' in data
    assert "Юрист Петрова".encode() in data
    upload(app, data)
    click(app, "📂 Загрузить данные из файла")
    click(app, "Заменить данные")
    upload(app, v03_profile, "firm_profile.json")
    click(app, "📂 Загрузить данные из файла")
    click(app, "Заменить данные")
    assert len(app.session_state[state.TEAM]) == 4
    assert not app.exception


def test_app_writes_nothing_outside_tempdir(
    monkeypatch: pytest.MonkeyPatch, downloads: dict[str, bytes]
) -> None:
    # Настоящая загрузка курсов с кэшем в памяти, но без сети.
    fx.clear_cache()
    fetch = functools.partial(
        fx.fetch_rates,
        fetch=FakeFetcher({CBR: CBR_XML}),
        clock=lambda: dt.datetime(2026, 10, 22, 12),
    )
    monkeypatch.setattr("rentab_core.fx.fetch_rates", fetch)
    v03_profile = V03_FIXTURE.read_bytes()
    with record_writes() as log:
        user_journey(downloads, v03_profile)
    assert log.outside() == [], "Приложение пишет на диск вне временного каталога"
    assert log.leftovers() == [], "Во временном каталоге остались файлы"
    # Регистратор действительно видел запись: openpyxl собирает XLSX через временные файлы.
    assert log.writes


def test_recorder_catches_writes(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    with record_writes(allowed) as log:
        (allowed / "left.txt").write_text("x", encoding="utf-8")
        (allowed / "gone.txt").write_text("x", encoding="utf-8")
        (allowed / "gone.txt").unlink()
        outside.mkdir()
        (outside / "data.json").write_bytes(b"{}")
        (outside / "data.json").rename(outside / "moved.json")
        assert (outside / "moved.json").read_bytes() == b"{}"
    assert sorted((event, path.name) for event, path in log.outside()) == [
        ("open", "data.json"),
        ("os.mkdir", "outside"),
        ("os.rename", "data.json"),
        ("os.rename", "moved.json"),
    ]
    assert log.leftovers() == [(allowed / "left.txt").resolve()]
    assert (outside / "moved.json").resolve() in log.opened


def test_recorder_sees_writes_of_streamlit_script(tmp_path: Path) -> None:
    target = (tmp_path / "leak.json").resolve()
    app = AppTest.from_string(f"from pathlib import Path\nPath({str(target)!r}).write_text('{{}}')")
    with record_writes(tmp_path / "allowed") as log:
        app.run()
    assert ("open", target) in log.outside()


def test_no_profile_autoload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # v0.3 читал data/firm_profile.json при старте каждой сессии (app.py:36–45).
    (tmp_path / "data").mkdir()
    for path in (tmp_path / "firm_profile.json", tmp_path / "data" / "firm_profile.json"):
        path.write_bytes(V03_FIXTURE.read_bytes())
    monkeypatch.chdir(tmp_path)
    with record_writes() as log:
        app = run_page(HOME)
        app.switch_page(nav.FIRM).run()
    assert not [path for path in log.opened if path.name == "firm_profile.json"]
    assert app.session_state[state.TEAM] == []
    assert len(slot_ids(app)) == 1
    assert log.outside() == []


# ---------------------------------------------------------------------------
# Статическая проверка: в коде приложения и ядра нет вызовов записи файлов
# ---------------------------------------------------------------------------
# Методы, которые пишут, удаляют или переименовывают файлы.
WRITE_METHODS = frozenset(
    {
        "write_text",
        "write_bytes",
        "mkdir",
        "makedirs",
        "touch",
        "unlink",
        "rmdir",
        "rename",
        "dump",
        "safe_dump",
        "to_csv",
        "to_excel",
        "to_json",
        "to_parquet",
        "to_pickle",
    }
)
OS_WRITE_FUNCTIONS = frozenset(
    {"open", "write", "remove", "unlink", "rename", "replace", "mkdir", "makedirs", "truncate"}
)
FORBIDDEN_MODULES = frozenset({"shutil", "tempfile", "sqlite3", "shelve", "dbm", "pickle"})
# Генераторы документации для разработчика: пишут файл, путь к которому передан
# в командной строке (`python -m ...`), только из функции main.
ALLOWED_WRITERS = frozenset(
    {
        (CORE_DIR / "rates" / "verification.py", "main"),
        (CORE_DIR / "io" / "schema.py", "main"),
    }
)


class WriteFinder(ast.NodeVisitor):
    """Ищет в модуле вызовы записи файлов и импорт модулей для работы с файлами."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.functions: list[str] = []
        self.found: list[str] = []

    def _report(self, node: ast.AST, what: str) -> None:
        function = self.functions[-1] if self.functions else ""
        if (self.path, function) not in ALLOWED_WRITERS:
            self.found.append(f"{self.path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)} {what}")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                self._report(node, f"import {alias.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if (node.module or "").split(".")[0] in FORBIDDEN_MODULES:
            self._report(node, f"from {node.module} import")

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            self._report(node, "open()")
        elif isinstance(func, ast.Attribute):
            is_os = isinstance(func.value, ast.Name) and func.value.id == "os"
            if func.attr in WRITE_METHODS or (is_os and func.attr in OS_WRITE_FUNCTIONS):
                self._report(node, f".{func.attr}()")
        self.generic_visit(node)


def source_files() -> list[Path]:
    return sorted([*APP_DIR.rglob("*.py"), *CORE_DIR.rglob("*.py")])


def test_code_has_no_file_writes() -> None:
    found = []
    for path in source_files():
        finder = WriteFinder(path)
        finder.visit(ast.parse(path.read_text(encoding="utf-8")))
        found += finder.found
    assert found == []


def test_finder_reports_writes(tmp_path: Path) -> None:
    code = (
        "import shutil\nfrom tempfile import mkstemp\nimport os, json\n"
        "def save(data):\n    open('x', 'w')\n    json.dump(data, f)\n    os.remove('x')\n"
        "    Path('y').write_text('')\n    'a'.replace('a', 'b')\n"
    )
    finder = WriteFinder(ROOT / "app" / "example.py")
    finder.visit(ast.parse(code))
    assert [item.split(" ", 1)[1] for item in finder.found] == [
        "import shutil",
        "from tempfile import",
        "open()",
        ".dump()",
        ".remove()",
        ".write_text()",
    ]


def test_streamlit_config_protects_privacy() -> None:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["browser"]["gatherUsageStats"] is False
    assert config["server"]["maxUploadSize"] * BYTES_PER_MB >= MAX_PROFILE_BYTES


def test_static_files_are_theme_fonts_only() -> None:
    # Раздача app/static включена ради шрифтов темы: там только шрифты и их лицензии.
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["server"]["enableStaticServing"] is True
    files = [path for path in (APP_DIR / "static").rglob("*") if path.is_file()]
    assert files
    assert {path.suffix for path in files} == {".woff2", ".txt"}
    for face in config["theme"]["fontFaces"]:
        assert (APP_DIR / face["url"].removeprefix("app/")).is_file(), face["url"]
