"""Смета XLSX ядра: структура листов и ветки, не покрытые снимками v0.3."""

from __future__ import annotations

import datetime as dt
import io
from typing import Any

from openpyxl import load_workbook

from rentab_core.estimate import calculate_estimate
from rentab_core.export import estimate_xlsx
from rentab_core.models import DisbursementKind
from rentab_core.results import Estimate, FixedPriceEstimate
from tests.core.factories import basic_scenario, disbursement, fixed, stage

ISSUE_DATE = dt.date(2026, 3, 16)


def sheets(estimate: Estimate, firm_name: str = "") -> dict[str, list[list[Any]]]:
    """Значения ячеек по листам, без пустых хвостов строк."""
    workbook = load_workbook(
        io.BytesIO(estimate_xlsx(estimate, firm_name=firm_name, issue_date=ISSUE_DATE))
    )
    result: dict[str, list[list[Any]]] = {}
    for sheet in workbook.worksheets:
        rows = []
        for row in sheet.iter_rows(values_only=True):
            values = list(row)
            while values and values[-1] is None:
                values.pop()
            rows.append(values)
        result[sheet.title] = rows
    return result


def labels(rows: list[list[Any]]) -> list[str]:
    return [str(value) for row in rows for value in row if isinstance(value, str)]


def test_hourly_estimate_sheets() -> None:
    estimate = calculate_estimate(basic_scenario())
    book = sheets(estimate, firm_name="ООО «Фирма»")
    assert list(book) == ["Смета", "Внутренний расчёт"]
    client = book["Смета"]
    assert client[0] == ["Смета по проекту: Контрольный проект"]
    assert client[1] == ["Исполнитель: ООО «Фирма»"]
    assert client[2] == ["Дата: 16.03.2026"]
    assert [1, "Анализ", "Иванов (Партнёр)", 5, 15000, 75000] in client
    # Без пошлин блока перевыставляемых расходов нет.
    assert not any("Перевыставляемые" in label for label in labels(client))
    total = next(row for row in client if "Итого к оплате" in row)
    assert total[-1] == 165_000
    internal = {row[0]: row[1] for row in book["Внутренний расчёт"] if len(row) == 2}
    assert internal["Маржа, %"] == 31.9  # 52 600 / 165 000 = 31,88 %
    assert internal["Коэффициент рычага"] == 3


def test_estimate_has_no_vat_line() -> None:
    # KNOWN-BUG L-02 → фаза 7: строки НДС в смете нет.
    estimate = calculate_estimate(basic_scenario())
    assert not any("НДС" in label for label in labels(sheets(estimate)["Смета"]))


def test_fixed_estimate_lists_stage_shares_and_disbursements() -> None:
    scenario = basic_scenario(
        fixed(), disbursements=(disbursement(DisbursementKind.BILLABLE, 38_500, "Пошлина"),)
    )
    estimate = calculate_estimate(scenario)
    assert isinstance(estimate, FixedPriceEstimate)
    book = sheets(estimate)
    client = book["Смета"]
    assert ["№", "Описание", "Стоимость, ₽"] in client
    assert [1, "Пошлина", 38_500] in client
    total = next(row for row in client if "Итого к оплате" in row)
    assert total[-1] == float(estimate.fixed_price + 38_500)
    internal = book["Внутренний расчёт"]
    assert ["Анализ этапов"] in internal
    statuses = [
        row[-1] for row in internal if row and row[-1] in {"Норма", "Низкая маржа", "Убыточный"}
    ]
    assert len(statuses) == 2


def test_fixed_estimate_without_stages_has_single_service_row() -> None:
    scenario = basic_scenario(fixed(), stages=(stage("Этап уволенного", "Удалённый", 10),))
    estimate = calculate_estimate(scenario)
    book = sheets(estimate)
    assert [1, "Услуги по проекту (фиксированная стоимость)", 0] in book["Смета"]
    assert ["Анализ этапов"] not in book["Внутренний расчёт"]


def test_fixed_estimate_stage_price_is_hour_share() -> None:
    # KNOWN-BUG L-17 → фаза 7: цена этапа в смете = доля фикс-цены по часам.
    estimate = calculate_estimate(basic_scenario(fixed()))
    assert isinstance(estimate, FixedPriceEstimate)
    rows = sheets(estimate)["Смета"]
    stage_rows = [row for row in rows if row and isinstance(row[0], int)]
    hours_share = [5 / 20, 15 / 20]
    for row, share in zip(stage_rows, hours_share, strict=True):
        assert abs(row[2] - float(estimate.fixed_price) * share) < 0.01
