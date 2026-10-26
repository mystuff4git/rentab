"""Проверка установленного приложения вне окружения разработки (ADR 0010).

Запускается в Docker-образе (job `docker` в CI) и в окружении «как на
Streamlit Community Cloud» из корня приложения, где лежат `app/`,
`tests/` и `docs/examples/`:

    python -m tests.app.deploy_smoke

Проверяет без pytest и dev-зависимостей:

- страницы открываются с демо-фирмой без ошибок (AppTest);
- КП в PDF и DOCX и смета XLSX демо-фирмы побайтно совпадают с
  `docs/examples/` — шрифты встроены, системные не нужны;
- за всё это время ничего не записано на диск вне временного каталога и во
  временном каталоге ничего не осталось (аудит-хук `tests/app/write_guard.py`).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

from rentab_core import __version__
from tests.app.write_guard import record_writes
from tests.export_examples import documents

ROOT = Path.cwd()
DEMO_BUTTON = "🎓 Загрузить демо-фирму"
PAGES = (
    "views/firm.py",
    "views/project.py",
    "views/result.py",
    "views/compare.py",
    "views/plan_fact.py",
)
TIMEOUT_SECONDS = 120
# AppTest вне pytest предупреждает «missing ScriptRunContext» о главном потоке: это
# голый режим проверки, а не ошибка приложения.
BARE_MODE_LOGGER = "streamlit.runtime.scriptrunner_utils.script_run_context"


def check_pages() -> None:
    """Главная, демо-фирма и все шаги визарда — без исключений."""
    app = AppTest.from_file(str(ROOT / "app" / "Home.py"), default_timeout=TIMEOUT_SECONDS)
    app.run()
    assert not app.exception, app.exception
    (button,) = [b for b in app.button if b.label == DEMO_BUTTON]
    button.click().run()
    assert not app.exception, app.exception
    for page in PAGES:
        app.switch_page(page).run()
        assert not app.exception, (page, app.exception)
    print(f"страницы с демо-фирмой: {len(PAGES) + 1}, без ошибок")


def check_documents() -> None:
    """Документы демо-фирмы побайтно равны примерам `docs/examples/`."""
    for name, data in documents():
        expected = (ROOT / "docs" / "examples" / name).read_bytes()
        assert data == expected, f"{name}: не совпадает с docs/examples"
        print(f"{name}: {len(data)} байт, совпадает с docs/examples")


def main() -> None:
    logging.getLogger(BARE_MODE_LOGGER).setLevel(logging.ERROR)
    print(f"Python {sys.version.split()[0]} · Rentab {__version__}")
    with record_writes() as log:
        check_pages()
        check_documents()
    assert not log.outside(), f"запись вне временного каталога: {log.outside()}"
    assert not log.leftovers(), f"во временном каталоге остались файлы: {log.leftovers()}"
    print(f"запись на диск: {len(log.writes)} — все во временном каталоге, файлов не осталось")


if __name__ == "__main__":
    main()
