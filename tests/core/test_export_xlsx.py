"""Смета XLSX ядра: структура листов, строка НДС, цены этапов."""

from __future__ import annotations

import datetime as dt
import io
from typing import Any

from openpyxl import load_workbook

from rentab_core.estimate import calculate_estimate
from rentab_core.export import estimate_xlsx
from rentab_core.models import CappedPricing, DisbursementKind, Jurisdiction, UsnProfile, UsnVat
from rentab_core.results import Estimate
from tests.core.factories import basic_scenario, d, disbursement, fixed, stage

ISSUE_DATE = dt.date(2026, 3, 16)
VAT_5 = Jurisdiction(rf=UsnProfile(vat=UsnVat.VAT_5))


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


def row_with(rows: list[list[Any]], label: str) -> list[Any]:
    return next(row for row in rows if label in row)


def test_hourly_estimate_sheets() -> None:
    estimate = calculate_estimate(basic_scenario())
    book = sheets(estimate, firm_name="ООО «Фирма»")
    assert list(book) == ["Смета", "Внутренний расчёт"]
    client = book["Смета"]
    assert client[0] == ["Смета по проекту: Контрольный проект"]
    assert client[1] == ["Исполнитель: ООО «Фирма»"]
    assert client[2] == ["Дата: 16.03.2026"]
    assert [1, "Анализ", "Иванов (Партнёр)", 5, 15000, 75000] in client
    assert not any("Возмещаемые" in label for label in labels(client))
    assert [None, "НДС не облагается."] in client
    assert row_with(client, "Итого за услуги без НДС")[-1] == 165_000
    assert row_with(client, "Итого к оплате")[-1] == 165_000
    internal = {row[0]: row[1] for row in book["Внутренний расчёт"] if len(row) == 2}
    assert internal["Маржа, %"] == 31.9  # 52 600 / 165 000 = 31,88 %
    assert internal["Коэффициент рычага"] == 3
    assert internal["Ставка накладных, ₽/ч"] == 500
    assert internal["в т. ч. стоимость недозагрузки, ₽"] == 46_250
    assert ["Анализ этапов"] in book["Внутренний расчёт"]


def test_vat_line_and_total_to_pay() -> None:
    # L-02: к оплате = услуги без НДС + НДС + возмещаемые расходы.
    scenario = basic_scenario(
        jurisdiction=VAT_5,
        disbursements=(disbursement(DisbursementKind.BILLABLE, 38_500, "Пошлина"),),
    )
    estimate = calculate_estimate(scenario).model_copy(update={"client": "ООО «Ромашка»"})
    client = sheets(estimate)["Смета"]
    assert ["Заказчик: ООО «Ромашка»"] in client
    assert row_with(client, "НДС 5%")[-1] == 8_250
    assert [1, "Пошлина", None, None, None, 38_500] in client
    assert row_with(client, "Итого к оплате")[-1] == 165_000 + 8_250 + 38_500


def test_capped_estimate_shows_write_off() -> None:
    client = sheets(calculate_estimate(basic_scenario(CappedPricing(cap=d(150_000)))))["Смета"]
    assert row_with(client, "Стоимость по ставкам")[-1] == 165_000
    assert row_with(client, "Скидка до потолка стоимости")[-1] == -15_000
    assert row_with(client, "Итого за услуги без НДС")[-1] == 150_000


def test_rates_are_shown_without_vat() -> None:
    base = basic_scenario(jurisdiction=VAT_5)
    scenario = base.model_copy(
        update={"project": base.project.model_copy(update={"vat_mode": "included"})}
    )
    client = sheets(calculate_estimate(scenario))["Смета"]
    first = next(row for row in client if row and row[0] == 1)
    assert abs(first[4] - 15_000 / 1.05) < 1e-6
    assert abs(row_with(client, "Итого к оплате")[-1] - 165_000) < 1e-6


def test_fixed_estimate_lists_stage_prices_and_disbursements() -> None:
    scenario = basic_scenario(
        fixed(),
        jurisdiction=VAT_5,
        disbursements=(disbursement(DisbursementKind.BILLABLE, 38_500, "Пошлина"),),
    )
    estimate = calculate_estimate(scenario)
    book = sheets(estimate)
    client = book["Смета"]
    assert ["№", "Описание", "Стоимость, ₽"] in client
    assert [1, "Пошлина", 38_500] in client
    assert row_with(client, "Итого к оплате")[-1] == float(estimate.total_client)
    internal = book["Внутренний расчёт"]
    statuses = [
        row[-1] for row in internal if row and row[-1] in {"Норма", "Низкая маржа", "Убыточный"}
    ]
    assert len(statuses) == 2


def test_fixed_stage_price_follows_shadow_value() -> None:
    # L-17: цена этапа = доля по теневой стоимости (75 000 : 90 000), а не по часам.
    estimate = calculate_estimate(basic_scenario(fixed()))
    rows = sheets(estimate)["Смета"]
    stage_rows = [row for row in rows if row and isinstance(row[0], int)]
    for row, share in zip(stage_rows, [75 / 165, 90 / 165], strict=True):
        assert abs(row[2] - float(estimate.revenue) * share) < 0.01


def test_fixed_estimate_without_stages_has_single_service_row() -> None:
    scenario = basic_scenario(fixed(100_000), stages=(stage("Этап уволенного", ("Удалённый", 10)),))
    book = sheets(calculate_estimate(scenario))
    assert [1, "Услуги по проекту (фиксированная стоимость)", 100_000] in book["Смета"]
    assert ["Анализ этапов"] not in book["Внутренний расчёт"]
