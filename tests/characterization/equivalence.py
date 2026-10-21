"""Сравнение выхода ядра с выходом v0.3 с допуском.

Контракт фазы 1 (ADR 0001, 0003): ядро воспроизводит v0.3 с допуском
1 коп. для денежных величин. Доли, проценты, часы и рычаг сравниваются с
точностью снимка (`snapshot.FLOAT_DIGITS` = 6 знаков). Строки, флаги и
структура (ключи, длины списков) должны совпадать точно.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from tests.characterization.snapshot import MAX_REPORTED_DIFFS, SNAPSHOT_DIR

# Допуск для денег: одна копейка (тиын).
MONEY_TOLERANCE = 0.01
# Допуск для долей, процентов, часов и рычага: точность снимка.
RATIO_TOLERANCE = 1e-6

# Ключи results и dashboard v0.3, значения которых не деньги.
NON_MONEY_KEYS = frozenset(
    {
        "actual_margin",
        "target_margin",
        "stage_margin",
        "leverage",
        "hours",
        "total_hours",
        "actual_margin_pct",
        "target_margin_pct",
        "stage_margin_pct",
        "margin_pct",
        "nne_margin_pct",
        "effective_tax_rate_pct",
    }
)

# XLSX: колонки и строки «Показатель — Значение», где числа не деньги.
NON_MONEY_XLSX_HEADERS = frozenset({"№", "Часы", "Маржа, %"})
NON_MONEY_XLSX_LABELS = frozenset(
    {"Маржа, %", "Коэффициент рычага", "Целевая маржа, %", "Фактическая маржа, %"}
)
XLSX_HEADER_FIRST_CELLS = frozenset({"№", "Показатель", "Этап"})
# Ячейки, которые смета округляет (проценты до 0,1, рычаг до 0,01). v0.3
# округляет float функцией round(), ядро — Decimal «половина вверх»; на точной
# середине (2,675) результаты могут разойтись на один шаг (ADR 0003).
ROUNDED_XLSX_STEPS: Mapping[str, float] = {
    "Маржа, %": 0.1,
    "Целевая маржа, %": 0.1,
    "Фактическая маржа, %": 0.1,
    "Коэффициент рычага": 0.01,
}


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _number_diff(expected: Any, actual: Any, tolerance: float, path: str) -> Iterator[str]:
    if abs(float(expected) - float(actual)) > tolerance:
        yield f"{path}: {expected!r} → {actual!r} (допуск {tolerance})"


def diff(expected: Any, actual: Any, path: str = "$", key: str | None = None) -> Iterator[str]:
    """Перечисляет расхождения между ожидаемым и фактическим значением."""
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        for name in sorted(expected.keys() | actual.keys()):
            if name not in actual:
                yield f"{path}.{name}: ключ пропал (было {expected[name]!r})"
            elif name not in expected:
                yield f"{path}.{name}: новый ключ ({actual[name]!r})"
            else:
                yield from diff(expected[name], actual[name], f"{path}.{name}", name)
    elif isinstance(expected, list) and isinstance(actual, list | tuple):
        if len(expected) != len(actual):
            yield f"{path}: длина {len(expected)} → {len(actual)}"
        for index, (exp_item, act_item) in enumerate(zip(expected, actual, strict=False)):
            yield from diff(exp_item, act_item, f"{path}[{index}]", key)
    elif _is_number(expected) and _is_number(actual):
        tolerance = RATIO_TOLERANCE if key in NON_MONEY_KEYS else MONEY_TOLERANCE
        yield from _number_diff(expected, actual, tolerance, path)
    elif type(expected) is not type(actual) or expected != actual:
        yield f"{path}: {expected!r} → {actual!r}"


def diff_workbook(
    expected: Mapping[str, Any], actual: Mapping[str, Any], *, allow_rounding_ties: bool = False
) -> Iterator[str]:
    """Расхождения между дампами XLSX (`dump_workbook`): значения ячеек и объединения.

    Args:
        expected: Дамп v0.3 (снимок или живой расчёт).
        actual: Дамп ядра.
        allow_rounding_ties: Допускать расхождение округлённых ячеек на один шаг
            округления (для случайных сценариев, где встречаются середины).
    """
    if sorted(expected) != sorted(actual):
        yield f"листы: {sorted(expected)} → {sorted(actual)}"
        return
    for sheet, exp_sheet in expected.items():
        act_sheet = actual[sheet]
        if exp_sheet["merged"] != act_sheet["merged"]:
            yield f"{sheet}: объединения {exp_sheet['merged']} → {act_sheet['merged']}"
        exp_rows, act_rows = exp_sheet["rows"], act_sheet["rows"]
        if len(exp_rows) != len(act_rows):
            yield f"{sheet}: строк {len(exp_rows)} → {len(act_rows)}"
        headers: list[Any] = []
        for row_index, (exp_row, act_row) in enumerate(zip(exp_rows, act_rows, strict=False)):
            path = f"{sheet}!{row_index + 1}"
            if exp_row and exp_row[0] in XLSX_HEADER_FIRST_CELLS:
                headers = list(exp_row)
            if len(exp_row) != len(act_row):
                yield f"{path}: ячеек {len(exp_row)} → {len(act_row)}"
            label = exp_row[0] if exp_row else None
            for col, (exp_cell, act_cell) in enumerate(zip(exp_row, act_row, strict=False)):
                cell_path = f"{path}:{col + 1}"
                if _is_number(exp_cell) and _is_number(act_cell):
                    header = headers[col] if col < len(headers) else None
                    non_money = header in NON_MONEY_XLSX_HEADERS or label in NON_MONEY_XLSX_LABELS
                    tolerance = RATIO_TOLERANCE if non_money else MONEY_TOLERANCE
                    step = ROUNDED_XLSX_STEPS.get(str(header), ROUNDED_XLSX_STEPS.get(str(label)))
                    if allow_rounding_ties and step is not None:
                        tolerance = step + RATIO_TOLERANCE
                    yield from _number_diff(exp_cell, act_cell, tolerance, cell_path)
                elif type(exp_cell) is not type(act_cell) or exp_cell != act_cell:
                    yield f"{cell_path}: {exp_cell!r} → {act_cell!r}"


def fail_on_diffs(title: str, diffs: list[str]) -> None:
    """Роняет тест со списком расхождений, если они есть."""
    if diffs:
        shown = "\n".join(diffs[:MAX_REPORTED_DIFFS])
        more = len(diffs) - MAX_REPORTED_DIFFS
        tail = f"\n... и ещё {more}" if more > 0 else ""
        pytest.fail(f"{title}:\n{shown}{tail}")


def load_snapshot(name: str) -> Any:
    """Читает снимок `snapshots/<name>.json`."""
    return json.loads((SNAPSHOT_DIR / f"{name}.json").read_text(encoding="utf-8"))


def assert_matches_snapshot(name: str, actual: Any) -> None:
    """Сверяет выход ядра со снимком v0.3 с допуском (снимок не перезаписывается)."""
    fail_on_diffs(f"Ядро разошлось со снимком {name}", list(diff(load_snapshot(name), actual)))
