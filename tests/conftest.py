"""Общие настройки pytest для всего набора тестов Rentab."""

from __future__ import annotations

import pytest
from hypothesis import settings

# Property-тесты должны быть воспроизводимыми: CI и локальный запуск проверяют
# один и тот же набор примеров, а падение не зависит от «везения».
settings.register_profile("rentab", derandomize=True, deadline=None, database=None)
settings.load_profile("rentab")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Регистрирует флаг перезаписи снимков characterization-тестов."""
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help=(
            "Перезаписать снимки в tests/characterization/snapshots. "
            "Использовать только осознанно: diff снимков обязателен к разбору в PR."
        ),
    )
