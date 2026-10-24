"""Смета в XLSX (оформление v0.3, цифры — по методологии фазы 2).

Файл содержит два листа:

1. «Смета» — документ для клиента. Для почасовой модели и модели с потолком —
   строки с часами и ставками, для фиксированной цены — этапы без часов и
   ставок с ценой этапа по теневой стоимости. Суммы — без НДС, затем строка
   НДС, возмещаемые расходы и «Итого к оплате» = R + НДС + D.
2. «Внутренний расчёт» — показатели для фирмы: издержки, налог, NNE, маржа и
   анализ этапов со светофором.

Числа пишутся без округления, формат ячейки показывает целые; проценты и
рычаг округляются до 0,1 и 0,01 («половина вверх»). Оформление КП и живые
формулы Excel — фаза 7.
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Mapping, Sequence
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from rentab_core.models import ROLE_LABELS, FixedPricing, VatMode
from rentab_core.money import CURRENCY_SYMBOLS, ONE, PERCENT, round_to
from rentab_core.results import DisbursementLine, Estimate, StageFlag
from rentab_core.tax.labels import percent

CellValue = str | int | float | None

# ---------------------------------------------------------------------------
# Оформление (как в v0.3). Цвета — без «#», в формате openpyxl.
# ---------------------------------------------------------------------------
HEADER_FILL_COLOR = "1F497D"  # шапка документа
TABLE_HEADER_FILL_COLOR = "D9E1F2"  # шапка таблицы
HEADER_FONT_COLOR = "FFFFFF"
DEFAULT_FONT_NAME = "Arial"
HEADER_FONT_SIZE = 12
DEFAULT_FONT_SIZE = 10
HEADER_ROW_HEIGHT = 24
# «1 234 567»: пробел между тысячами, без копеек.
NUMBER_FORMAT_INT = "# ##0"
COLUMN_WIDTH_PADDING = 2
COLUMN_WIDTH_MIN = 10
COLUMN_WIDTH_MAX = 60

FLAG_FILL_COLORS: Mapping[StageFlag, str] = {
    StageFlag.GREEN: "D5F5E3",
    StageFlag.YELLOW: "FEF3C7",
    StageFlag.RED: "FEE2E2",
}
FLAG_LABELS: Mapping[StageFlag, str] = {
    StageFlag.GREEN: "Норма",
    StageFlag.YELLOW: "Низкая маржа",
    StageFlag.RED: "Убыточный",
}

CLIENT_SHEET_TITLE = "Смета"
INTERNAL_SHEET_TITLE = "Внутренний расчёт"
UNTITLED_PROJECT = "Без названия"
DATE_FORMAT = "%d.%m.%Y"
VALIDITY_NOTE = "Смета действительна 30 дней с даты составления."
FIXED_PRICE_NOTE = "* Стоимость работ зафиксирована и не зависит от фактически затраченных часов."
DISBURSEMENTS_TITLE = "Возмещаемые расходы (пошлины и иные платежи)"
NO_VAT_NOTE = "НДС не облагается."

PERCENT_QUANTUM = Decimal("0.1")
LEVERAGE_QUANTUM = Decimal("0.01")


# ---------------------------------------------------------------------------
# Низкоуровневые помощники
# ---------------------------------------------------------------------------
def _number(value: Decimal) -> float:
    """Decimal → float для ячейки: Excel хранит числа как double."""
    return float(value)


def _rounded(value: Decimal, quantum: Decimal) -> float:
    return float(round_to(value, quantum))


def _font(*, bold: bool = False) -> Font:
    return Font(name=DEFAULT_FONT_NAME, size=DEFAULT_FONT_SIZE, bold=bold)


def _solid_fill(color: str) -> PatternFill:
    return PatternFill(start_color=color, end_color=color, fill_type="solid")


def _write_document_header(ws: Worksheet, row: int, title: str, n_cols: int) -> int:
    """Шапка документа: объединённая строка с заливкой. Возвращает следующую строку."""
    ws.merge_cells(f"A{row}:{get_column_letter(n_cols)}{row}")
    cell = ws.cell(row=row, column=1, value=title)
    cell.font = Font(
        name=DEFAULT_FONT_NAME, size=HEADER_FONT_SIZE, bold=True, color=HEADER_FONT_COLOR
    )
    cell.fill = _solid_fill(HEADER_FILL_COLOR)
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[row].height = HEADER_ROW_HEIGHT
    return row + 1


def _write_table_header(ws: Worksheet, row: int, headers: Sequence[str]) -> int:
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=text)
        cell.font = _font(bold=True)
        cell.fill = _solid_fill(TABLE_HEADER_FILL_COLOR)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    return row + 1


def _write_row(
    ws: Worksheet,
    row: int,
    values: Sequence[CellValue],
    number_columns: frozenset[int],
    *,
    total: bool = False,
) -> int:
    """Строка данных или итогов; колонки number_columns — в числовом формате."""
    border = Border(top=Side(style="thin", color="000000")) if total else None
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col, value=value)
        cell.font = _font(bold=total)
        if border is not None:
            cell.border = border
        if col in number_columns:
            cell.number_format = NUMBER_FORMAT_INT
            cell.alignment = Alignment(horizontal="right")
    return row + 1


def _write_plain(
    ws: Worksheet, row: int, column: int, value: CellValue, *, bold: bool = False
) -> None:
    ws.cell(row=row, column=column, value=value).font = _font(bold=bold)


def _autosize_columns(ws: Worksheet, n_cols: int) -> None:
    """Ширина колонок по самой длинной ячейке, без учёта объединённой шапки."""
    merged_top_left = {str(rng).split(":")[0] for rng in ws.merged_cells.ranges}
    for col in range(1, n_cols + 1):
        letter = get_column_letter(col)
        max_len = COLUMN_WIDTH_MIN
        for cell in ws[letter]:
            if cell.coordinate in merged_top_left or cell.value is None:
                continue
            value = cell.value
            if isinstance(value, int | float):
                text = f"{value:,.0f}".replace(",", " ")
            else:
                text = str(value)
            max_len = max(max_len, len(text))
        ws.column_dimensions[letter].width = max(
            min(max_len + COLUMN_WIDTH_PADDING, COLUMN_WIDTH_MAX), COLUMN_WIDTH_MIN
        )


def _write_meta(
    ws: Worksheet,
    row: int,
    estimate: Estimate,
    firm_name: str,
    issue_date: dt.date,
    regime_label: str,
) -> int:
    """Исполнитель, заказчик, дата, режим и пустая строка-разделитель."""
    if firm_name:
        _write_plain(ws, row, 1, f"Исполнитель: {firm_name}")
        row += 1
    if estimate.client:
        _write_plain(ws, row, 1, f"Заказчик: {estimate.client}")
        row += 1
    _write_plain(ws, row, 1, f"Дата: {issue_date.strftime(DATE_FORMAT)}")
    row += 1
    if regime_label:
        _write_plain(ws, row, 1, f"Налоговый режим: {regime_label}")
        row += 1
    return row + 1


def _billable(estimate: Estimate) -> list[DisbursementLine]:
    return [item for item in estimate.disbursements if item.billable]


def _write_disbursements(
    ws: Worksheet, row: int, estimate: Estimate, n_cols: int, number_columns: frozenset[int]
) -> int:
    """Блок перевыставляемых расходов, если они есть."""
    if estimate.disbursements_billed <= 0:
        return row
    row += 1
    _write_plain(ws, row, 1, DISBURSEMENTS_TITLE, bold=True)
    row += 1
    padding: list[CellValue] = [""] * (n_cols - 3)
    for idx, item in enumerate(_billable(estimate), start=1):
        values: list[CellValue] = [idx, item.name, *padding, _number(item.amount)]
        row = _write_row(ws, row, values, number_columns)
    totals: list[CellValue] = [
        "",
        "Итого возмещаемых расходов",
        *padding,
        _number(estimate.disbursements_billed),
    ]
    return _write_row(ws, row, totals, number_columns, total=True)


def _project_title(estimate: Estimate) -> str:
    return estimate.project_name.strip() or UNTITLED_PROJECT


def _net_factor(estimate: Estimate) -> Decimal:
    """Множитель «цена в режиме проекта → без НДС» для ставок в смете."""
    if estimate.vat_mode == VatMode.INCLUDED:
        return ONE / (ONE + estimate.tax.terms.vat_rate)
    return ONE


def _write_totals(
    ws: Worksheet,
    row: int,
    estimate: Estimate,
    n_cols: int,
    number_columns: frozenset[int],
    services_label: str,
) -> int:
    """Итог за услуги без НДС, НДС, возмещаемые расходы и «Итого к оплате»."""
    padding: list[CellValue] = [""] * (n_cols - 3)
    row = _write_row(
        ws,
        row,
        ["", services_label, *padding, _number(estimate.revenue)],
        number_columns,
        total=True,
    )
    vat_rate = estimate.tax.terms.vat_rate
    if vat_rate > 0:
        vat_label = f"НДС {percent(vat_rate)}%"
        row = _write_row(ws, row, ["", vat_label, *padding, _number(estimate.vat)], number_columns)
    else:
        _write_plain(ws, row, 2, NO_VAT_NOTE)
        row += 1
    row = _write_disbursements(ws, row, estimate, n_cols, number_columns)
    row += 1
    return _write_row(
        ws,
        row,
        ["", "Итого к оплате", *padding, _number(estimate.total_client)],
        number_columns,
        total=True,
    )


# ---------------------------------------------------------------------------
# Лист «Смета»
# ---------------------------------------------------------------------------
def _client_sheet_hourly(
    ws: Worksheet, estimate: Estimate, firm_name: str, issue_date: dt.date, regime_label: str
) -> None:
    symbol = CURRENCY_SYMBOLS[estimate.currency]
    headers = ["№", "Этап", "Исполнитель", "Часы", f"Ставка, {symbol}/ч", f"Стоимость, {symbol}"]
    n_cols = len(headers)
    number_columns = frozenset({4, 5, 6})
    net = _net_factor(estimate)

    row = _write_document_header(ws, 1, f"Смета по проекту: {_project_title(estimate)}", n_cols)
    row = _write_meta(ws, row, estimate, firm_name, issue_date, regime_label)
    row = _write_table_header(ws, row, headers)
    for idx, line in enumerate(estimate.lines, start=1):
        values: list[CellValue] = [
            idx,
            line.stage_name,
            f"{line.employee_name} ({ROLE_LABELS[line.role]})",
            _number(line.hours),
            _number(line.billing_rate * net),
            _number(line.shadow_value * net),
        ]
        row = _write_row(ws, row, values, number_columns)
    if estimate.write_off > 0:
        row = _write_row(
            ws,
            row,
            ["", "Стоимость по ставкам", "", "", "", _number(estimate.shadow_value_net)],
            number_columns,
        )
        row = _write_row(
            ws,
            row,
            ["", "Скидка до потолка стоимости", "", "", "", -_number(estimate.write_off)],
            number_columns,
        )
    row = _write_totals(ws, row, estimate, n_cols, number_columns, "Итого за услуги без НДС")
    _write_plain(ws, row + 2, 1, VALIDITY_NOTE)
    _autosize_columns(ws, n_cols)


def _client_sheet_fixed(
    ws: Worksheet, estimate: Estimate, firm_name: str, issue_date: dt.date, regime_label: str
) -> None:
    symbol = CURRENCY_SYMBOLS[estimate.currency]
    headers = ["№", "Описание", f"Стоимость, {symbol}"]
    n_cols = len(headers)
    number_columns = frozenset({3})

    row = _write_document_header(ws, 1, f"Смета по проекту: {_project_title(estimate)}", n_cols)
    row = _write_meta(ws, row, estimate, firm_name, issue_date, regime_label)
    row = _write_table_header(ws, row, headers)
    if estimate.stages:
        for idx, stage in enumerate(estimate.stages, start=1):
            values: list[CellValue] = [idx, stage.stage_name, _number(stage.revenue)]
            row = _write_row(ws, row, values, number_columns)
    else:
        row = _write_row(
            ws,
            row,
            [1, "Услуги по проекту (фиксированная стоимость)", _number(estimate.revenue)],
            number_columns,
        )
    row = _write_totals(
        ws, row, estimate, n_cols, number_columns, "Фиксированная стоимость работ без НДС"
    )
    _write_plain(ws, row + 1, 1, FIXED_PRICE_NOTE)
    _write_plain(ws, row + 3, 1, VALIDITY_NOTE)
    _autosize_columns(ws, n_cols)


# ---------------------------------------------------------------------------
# Лист «Внутренний расчёт»
# ---------------------------------------------------------------------------
IndicatorRow = tuple[str, CellValue, bool]  # подпись, значение, денежный формат


def _write_indicators(ws: Worksheet, title: str, rows: Sequence[IndicatorRow]) -> int:
    """Шапка и таблица «Показатель — Значение». Возвращает следующую строку."""
    row = _write_document_header(ws, 1, title, 2) + 1
    row = _write_table_header(ws, row, ["Показатель", "Значение"])
    for label, value, is_money in rows:
        if is_money:
            row = _write_row(ws, row, [label, value], frozenset({2}))
        else:
            _write_plain(ws, row, 1, label)
            _write_plain(ws, row, 2, value)
            row += 1
    return row


def _pct(value: Decimal) -> float:
    return _rounded(value * PERCENT, PERCENT_QUANTUM)


def _internal_sheet(ws: Worksheet, estimate: Estimate, regime_label: str) -> None:
    symbol = CURRENCY_SYMBOLS[estimate.currency]
    rows: list[IndicatorRow] = [
        (f"Выручка за услуги без НДС, {symbol}", _number(estimate.revenue), True),
        (f"НДС (сверху), {symbol}", _number(estimate.vat), True),
        (f"Возмещаемые расходы, {symbol}", _number(estimate.disbursements_billed), True),
        (f"Итого к оплате клиентом, {symbol}", _number(estimate.total_client), True),
        (f"Прямые трудозатраты, {symbol}", _number(estimate.direct_labor), True),
        (
            f"в т. ч. стоимость недозагрузки, {symbol}",
            _number(estimate.underutilization_cost),
            True,
        ),
        (f"Накладные (распределённые), {symbol}", _number(estimate.overheads_alloc), True),
        (f"Собственные расходы, {symbol}", _number(estimate.disbursements_own), True),
        (f"Все издержки проекта, {symbol}", _number(estimate.total_costs), True),
        (f"Налог на доход или прибыль, {symbol}", _number(estimate.tax.tax), True),
        (f"Чистая прибыль (NNE), {symbol}", _number(estimate.nne), True),
        ("Маржа, %", _pct(estimate.margin), False),
        ("Целевая маржа, %", _pct(estimate.target_margin), False),
        (f"Ставка накладных, {symbol}/ч", _number(estimate.overhead_rate), True),
        (f"Средневзвешенная ставка, {symbol}/ч", _number(estimate.blended_rate), True),
        ("Коэффициент рычага", _rounded(estimate.leverage, LEVERAGE_QUANTUM), False),
        ("Налоговый режим", regime_label, False),
    ]
    row = _write_indicators(ws, "Внутренний расчёт проекта", rows)
    if not estimate.stages:
        _autosize_columns(ws, 2)
        return

    row += 2
    _write_plain(ws, row, 1, "Анализ этапов", bold=True)
    headers = [
        "Этап",
        "Часы",
        f"Выручка этапа, {symbol}",
        f"Издержки этапа, {symbol}",
        f"Налог этапа, {symbol}",
        f"NNE этапа, {symbol}",
        "Маржа, %",
        "Статус",
    ]
    money_columns = frozenset({3, 4, 5, 6})
    row = _write_table_header(ws, row + 1, headers)
    for stage in estimate.stages:
        values: list[CellValue] = [
            stage.stage_name,
            _number(stage.hours),
            _number(stage.revenue),
            _number(stage.costs),
            _number(stage.tax),
            _number(stage.nne),
            _pct(stage.margin),
            FLAG_LABELS[stage.flag],
        ]
        fill = _solid_fill(FLAG_FILL_COLORS[stage.flag])
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.font = _font()
            cell.fill = fill
            if col in money_columns:
                cell.number_format = NUMBER_FORMAT_INT
                cell.alignment = Alignment(horizontal="right")
        row += 1
    _autosize_columns(ws, len(headers))


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------
def estimate_xlsx(
    estimate: Estimate,
    *,
    firm_name: str = "",
    issue_date: dt.date,
    regime_label: str | None = None,
) -> bytes:
    """Собирает XLSX-смету и возвращает байты файла.

    Args:
        estimate: Смета ядра.
        firm_name: Название фирмы-исполнителя для шапки; пустое — строки нет.
        issue_date: Дата составления сметы.
        regime_label: Подпись налогового режима для сметы; None — подпись ядра
            (`Estimate.regime_label`, как в v0.3). Интерфейс передаёт свою
            человекочитаемую подпись (L-18).

    Returns:
        Содержимое .xlsx-файла.
    """
    workbook = Workbook()
    client_sheet = workbook.active
    assert client_sheet is not None  # у новой книги всегда есть активный лист
    client_sheet.title = CLIENT_SHEET_TITLE
    internal_sheet = workbook.create_sheet(INTERNAL_SHEET_TITLE)

    label = estimate.regime_label if regime_label is None else regime_label
    if isinstance(estimate.pricing, FixedPricing):
        _client_sheet_fixed(client_sheet, estimate, firm_name, issue_date, label)
    else:
        _client_sheet_hourly(client_sheet, estimate, firm_name, issue_date, label)
    _internal_sheet(internal_sheet, estimate, label)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
