"""Импорт и экспорт данных пользователя: фирма и проекты в JSON (L-10, L-16).

Ядро ничего не пишет на диск: `dumps` возвращает байты для кнопки
«Скачать», `loads` читает байты загруженного пользователем файла. Формат —
`docs/profile_format.md`, JSON Schema — `docs/profile.schema.json`.
"""

from rentab_core.io.files import (
    MAX_PROFILE_BYTES,
    ImportResult,
    dumps,
    loads,
    project_title,
)
from rentab_core.io.migrations import ImportNote, NoteLevel
from rentab_core.io.schema import (
    FORMAT_ID,
    SCHEMA_VERSION,
    DutySelection,
    ProfileFile,
    ProjectRecord,
    Workspace,
)

__all__ = [
    "FORMAT_ID",
    "MAX_PROFILE_BYTES",
    "SCHEMA_VERSION",
    "DutySelection",
    "ImportNote",
    "ImportResult",
    "NoteLevel",
    "ProfileFile",
    "ProjectRecord",
    "Workspace",
    "dumps",
    "loads",
    "project_title",
]
