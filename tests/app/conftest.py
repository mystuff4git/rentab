"""Фикстуры smoke-тестов интерфейса (app/)."""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"

# Страницы импортируют общие модули как `ui.*`: Streamlit кладёт app/ в sys.path,
# AppTest — нет, поэтому добавляем сами.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
