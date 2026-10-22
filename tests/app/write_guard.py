"""Регистратор записи на диск для тестов приватности (L-10).

Аудит-хук Python (`sys.addaudithook`) видит каждое открытие файла и каждую
операцию с файловой системой в процессе — и в коде приложения, и в
библиотеках (openpyxl, Streamlit). Хук нельзя снять, поэтому он ставится один
раз и пишет события, только пока открыт контекст `record_writes`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Флаги os.open, при которых открытие меняет файл.
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
# Режимы open(), при которых файл меняется.
WRITE_MODES = frozenset("wax+")
# События, которые меняют файловую систему: событие → индексы аргументов-путей.
MUTATING_EVENTS: dict[str, tuple[int, ...]] = {
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.replace": (0, 1),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.truncate": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.move": (1,),
    "shutil.rmtree": (0,),
    "sqlite3.connect": (0,),
}


@dataclass
class WriteLog:
    """Записи на диск за время контекста.

    Attributes:
        allowed: Каталог, где запись разрешена (временный каталог системы).
        opened: Все открытые файлы, и на чтение тоже.
        writes: (событие, путь) для каждой записи.
        created: Файлы, созданные в разрешённом каталоге.
    """

    allowed: Path
    opened: list[Path] = field(default_factory=list)
    writes: list[tuple[str, Path]] = field(default_factory=list)
    created: set[Path] = field(default_factory=set)

    def outside(self) -> list[tuple[str, Path]]:
        """Записи вне разрешённого каталога."""
        return [
            (event, path) for event, path in self.writes if not path.is_relative_to(self.allowed)
        ]

    def leftovers(self) -> list[Path]:
        """Созданные во временном каталоге файлы, которые остались на диске.

        Каталоги не в счёт: `tempfile` открывает на запись сам каталог, чтобы
        создать в нём безымянный файл (O_TMPFILE), который на диске не остаётся.
        """
        return sorted(path for path in self.created if path.is_file())


_active: list[WriteLog] = []
_hook_installed = False


def _path(value: Any) -> Path | None:
    """Путь из аргумента события; дескрипторы и пустые значения — None."""
    if isinstance(value, int) or value is None:
        return None
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    try:
        return Path(os.path.realpath(os.fspath(value)))
    except TypeError:
        return None


def _is_write_open(mode: Any, flags: Any) -> bool:
    if isinstance(mode, str) and WRITE_MODES & set(mode):
        return True
    return isinstance(flags, int) and flags != -1 and bool(flags & WRITE_FLAGS)


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if not _active:
        return
    log = _active[-1]
    if event == "open":
        path_arg, mode, flags = (*args, None, None)[:3]
        path = _path(path_arg)
        if path is None:
            return
        log.opened.append(path)
        if not _is_write_open(mode, flags):
            return
        log.writes.append((event, path))
        if path.is_relative_to(log.allowed):
            log.created.add(path)
    elif event in MUTATING_EVENTS:
        for index in MUTATING_EVENTS[event]:
            path = _path(args[index]) if index < len(args) else None
            if path is not None:
                log.writes.append((event, path))


@contextmanager
def record_writes(allowed: Path | None = None) -> Iterator[WriteLog]:
    """Записывает операции записи на диск, пока открыт контекст.

    Байткод на это время не пишется: `__pycache__` — работа интерпретатора,
    а не приложения.
    """
    global _hook_installed
    if not _hook_installed:
        sys.addaudithook(_hook)
        _hook_installed = True
    log = WriteLog(allowed=(allowed or Path(tempfile.gettempdir())).resolve())
    dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    _active.append(log)
    try:
        yield log
    finally:
        _active.pop()
        sys.dont_write_bytecode = dont_write_bytecode
