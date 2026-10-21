"""Базовые проверки пакета rentab_core: версия и отсутствие UI-зависимостей."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version

import rentab_core

# Модули интерфейса, которые ядро не должно подтягивать даже транзитивно.
UI_MODULES: tuple[str, ...] = ("streamlit", "plotly")


def test_version_matches_installed_metadata() -> None:
    assert rentab_core.__version__ == version("rentab")


def test_core_import_does_not_load_ui_modules() -> None:
    # Проверяем в чистом процессе: в текущем sys.modules уже может быть что угодно.
    code = (
        "import sys, rentab_core; "
        f"loaded = [m for m in {UI_MODULES!r} if m in sys.modules]; "
        "print(','.join(loaded))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == ""
