"""Замечания импорта и цепочка миграций схемы файла профиля.

Шаг миграции переводит словарь JSON версии N в версию N + 1 и не зависит от
моделей ядра: модели меняются, а старые файлы должны читаться. Профиль v0.3
(`data/firm_profile.json`) — не версия этого формата, а чужой файл: его
переводит в текущие модели отдельный импортёр `rentab_core.io.v03`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from rentab_core.errors import ProfileImportError
from rentab_core.io.schema import SCHEMA_VERSION
from rentab_core.models import CoreModel

RawProfile = dict[str, Any]


class NoteLevel(StrEnum):
    """Важность замечания импорта."""

    INFO = "info"  # что изменилось при переводе, данные не потеряны
    WARNING = "warning"  # часть данных пропущена или посчитается иначе


class ImportNote(CoreModel):
    """Замечание импорта для пользователя."""

    level: NoteLevel
    message: str


def info(message: str) -> ImportNote:
    """Замечание «для сведения»."""
    return ImportNote(level=NoteLevel.INFO, message=message)


def warning(message: str) -> ImportNote:
    """Предупреждение: часть данных пропущена или посчитается иначе."""
    return ImportNote(level=NoteLevel.WARNING, message=message)


MigrationStep = Callable[[RawProfile], tuple[RawProfile, list[ImportNote]]]

# Шаг N — перевод словаря версии N в версию N + 1. Версия 1 — первая, шагов пока нет.
MIGRATIONS: Mapping[int, MigrationStep] = {}


def upgrade(
    raw: RawProfile,
    version: int,
    *,
    steps: Mapping[int, MigrationStep] = MIGRATIONS,
    target: int = SCHEMA_VERSION,
) -> tuple[RawProfile, list[ImportNote]]:
    """Переводит словарь файла из версии `version` в версию `target`.

    Raises:
        ProfileImportError: Нет шага для одной из промежуточных версий.
    """
    notes: list[ImportNote] = []
    while version < target:
        step = steps.get(version)
        if step is None:
            raise ProfileImportError(
                f"Нет перевода файла профиля с версии схемы {version} на {version + 1}"
            )
        raw, step_notes = step(raw)
        notes += step_notes
        version += 1
        raw["schema_version"] = version
    return raw, notes
