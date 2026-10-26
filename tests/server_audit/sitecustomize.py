"""Аудит-хук записи на диск для настоящего сервера Streamlit (ADR 0006, 0010).

Python импортирует `sitecustomize` при старте, если его каталог в PYTHONPATH:

    PYTHONPATH=tests/server_audit:. uv run streamlit run app/Home.py

Хук (`tests/app/write_guard.py`) записывает каждую запись на диск, пока
процесс жив, а при выходе (Ctrl+C, `docker stop`) печатает в stderr итог:
сколько записей, какие из них вне временного каталога и какие файлы там
остались. Байткод на это время не пишется.
"""

from __future__ import annotations

import atexit
import sys

from tests.app.write_guard import record_writes

PREFIX = "[write-audit]"

_context = record_writes()
_log = _context.__enter__()


def _report() -> None:
    """Итог аудита при выходе процесса."""
    _context.__exit__(None, None, None)
    outside = _log.outside()
    leftovers = _log.leftovers()
    print(
        f"{PREFIX} записей: {len(_log.writes)}, вне временного каталога: {len(outside)}, "
        f"осталось файлов: {len(leftovers)}",
        file=sys.stderr,
    )
    for event, path in _log.writes:
        print(f"{PREFIX} {event} {path}", file=sys.stderr)
    for path in leftovers:
        print(f"{PREFIX} остался {path}", file=sys.stderr)


atexit.register(_report)
