"""Снимки (snapshots) для characterization-тестов.

Снимок — JSON-файл в tests/characterization/snapshots с зафиксированным выходом
v0.3. Тест сравнивает текущий выход со снимком; перезапись выполняется только
явно, флагом `pytest --update-snapshots`, и diff снимков разбирается в PR.

Числа с плавающей точкой округляются до FLOAT_DIGITS знаков: этого хватает,
чтобы поймать любое изменение в копейках, и при этом снимок не зависит от
шума последних битов float.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
FLOAT_DIGITS = 6
MAX_REPORTED_DIFFS = 20

# Сигнатура фикстуры `snapshot`: (имя снимка, текущее значение) -> None.
SnapshotCheck = Callable[[str, Any], None]


def normalize(value: Any) -> Any:
    """Приводит значение к JSON-совместимому виду с округлёнными float."""
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)
        rounded = round(value, FLOAT_DIGITS)
        return 0.0 if rounded == 0 else rounded  # убираем -0.0
    if isinstance(value, Mapping):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [normalize(item) for item in value]
    raise TypeError(f"Неподдерживаемый тип в снимке: {type(value).__name__}")


def _diff(expected: Any, actual: Any, path: str = "$") -> Iterator[str]:
    """Перечисляет различия между снимком и текущим значением."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(expected.keys() | actual.keys()):
            if key not in actual:
                yield f"{path}.{key}: ключ пропал (было {expected[key]!r})"
            elif key not in expected:
                yield f"{path}.{key}: новый ключ ({actual[key]!r})"
            else:
                yield from _diff(expected[key], actual[key], f"{path}.{key}")
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            yield f"{path}: длина {len(expected)} → {len(actual)}"
        for index, (exp_item, act_item) in enumerate(zip(expected, actual, strict=False)):
            yield from _diff(exp_item, act_item, f"{path}[{index}]")
    elif type(expected) is not type(actual) or expected != actual:
        yield f"{path}: {expected!r} → {actual!r}"


def assert_snapshot(name: str, actual: Any, *, update: bool) -> None:
    """Сравнивает значение со снимком `snapshots/<name>.json`.

    Args:
        name: Относительное имя снимка без расширения, например `"matrix/RF_OSNO__fixed"`.
        actual: Текущий выход v0.3.
        update: Перезаписать снимок вместо сравнения.
    """
    path = SNAPSHOT_DIR / f"{name}.json"
    normalized = normalize(actual)
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True)
        path.write_text(text + "\n", encoding="utf-8")
        return
    if not path.exists():
        pytest.fail(
            f"Нет снимка {path.relative_to(SNAPSHOT_DIR.parent)}. "
            "Создайте его: uv run pytest --update-snapshots"
        )
    expected = json.loads(path.read_text(encoding="utf-8"))
    diffs = list(_diff(expected, normalized))
    if diffs:
        shown = "\n".join(diffs[:MAX_REPORTED_DIFFS])
        more = len(diffs) - MAX_REPORTED_DIFFS
        tail = f"\n... и ещё {more}" if more > 0 else ""
        pytest.fail(f"Выход v0.3 разошёлся со снимком {name}:\n{shown}{tail}")
