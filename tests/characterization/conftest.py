"""Фикстуры characterization-тестов v0.3."""

from __future__ import annotations

from typing import Any

import pytest

from tests.characterization.snapshot import SnapshotCheck, assert_snapshot


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> SnapshotCheck:
    """Возвращает функцию сравнения со снимком (учитывает --update-snapshots)."""
    update = bool(request.config.getoption("--update-snapshots"))

    def check(name: str, actual: Any) -> None:
        assert_snapshot(name, actual, update=update)

    return check
