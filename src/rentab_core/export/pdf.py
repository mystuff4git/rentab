"""КП в PDF (reportlab, чистый Python): оформление «классический бланк» (ADR 0009).

Белый лист A4, шапка с логотипом и реквизитами под бордовой линией,
заголовки PT Serif, текст и таблицы PT Sans; шрифты встраиваются в файл
подмножеством, системные шрифты не нужны. Документ собирается в памяти и
возвращается байтами — на диск ничего не пишется. Вывод детерминирован
(`invariant`): одинаковые данные дают одинаковый файл.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from rentab_core.documents import logo_size
from rentab_core.export.offer import (
    DISBURSEMENTS_TITLE,
    DISBURSEMENTS_TOTAL,
    NO_VAT,
    SERVICES_GROSS,
    SERVICES_NET,
    SHADOW_NET,
    TITLE,
    TOTAL,
    WRITE_OFF,
    OfferDocument,
    PriceKind,
)
from rentab_core.export.style import BORDO, INK, MUTED, PARCHMENT, RULE, FontFace, font_bytes
from rentab_core.formatting import KOPECK, NBSP, hours_label, money, money_rate

PAGE_MARGIN = 18 * mm
FOOTER_Y = 12 * mm
BOTTOM_MARGIN = 22 * mm
LOGO_MAX_WIDTH = 54 * mm
LOGO_MAX_HEIGHT = 18 * mm
HEADER_LOGO_COLUMN = 70 * mm
AMOUNT_COLUMN = 38 * mm
NUMBER_COLUMN = 10 * mm
HOURS_COLUMN = 18 * mm
RATE_COLUMN = 34 * mm
META_LABEL_COLUMN = 28 * mm
LINE_INDENT = 4 * mm
BODY_SIZE = 10
SMALL_SIZE = 8.5
FOOTER_SIZE = 8
TITLE_SIZE = 20
HEADING_SIZE = 12.5
RULE_WIDTH = 0.5
ACCENT_WIDTH = 1.2
CELL_PADDING = 4
LINE_PADDING = 1.5  # строки исполнителей под этапом — плотнее
TOTALS_PADDING = 2
CREATOR = "Rentab"

# Имена шрифтов в PDF (без пробелов) и файлы в пакете.
PDF_FONTS: dict[FontFace, str] = {
    FontFace.SANS: "PTSans-Regular",
    FontFace.SANS_BOLD: "PTSans-Bold",
    FontFace.SERIF_BOLD: "PTSerif-Bold",
}
SANS = PDF_FONTS[FontFace.SANS]
SANS_BOLD = PDF_FONTS[FontFace.SANS_BOLD]
SERIF_BOLD = PDF_FONTS[FontFace.SERIF_BOLD]


def register_fonts() -> None:
    """Регистрирует шрифты ParaType в reportlab (повторный вызов ничего не делает)."""
    registered = set(pdfmetrics.getRegisteredFontNames())
    for face, name in PDF_FONTS.items():
        if name not in registered:
            pdfmetrics.registerFont(TTFont(name, io.BytesIO(font_bytes(face))))


def _color(value: str) -> colors.Color:
    return colors.HexColor(f"#{value}")


def _style(**overrides: Any) -> ParagraphStyle:
    base: dict[str, Any] = {
        "fontName": SANS,
        "fontSize": BODY_SIZE,
        "leading": BODY_SIZE * 1.35,
        "textColor": _color(INK),
    }
    return ParagraphStyle("rentab", **(base | overrides))


BODY = _style()
BOLD = _style(fontName=SANS_BOLD)
SMALL = _style(fontSize=SMALL_SIZE, leading=SMALL_SIZE * 1.3, textColor=_color(MUTED))
RIGHT = _style(alignment=TA_RIGHT)
BOLD_RIGHT = _style(fontName=SANS_BOLD, alignment=TA_RIGHT)
SMALL_RIGHT = _style(
    fontSize=SMALL_SIZE, leading=SMALL_SIZE * 1.3, textColor=_color(MUTED), alignment=TA_RIGHT
)
FIRM_RIGHT = _style(fontName=SANS_BOLD, alignment=TA_RIGHT)
TITLE_STYLE = _style(
    fontName=SERIF_BOLD, fontSize=TITLE_SIZE, leading=TITLE_SIZE * 1.2, textColor=_color(BORDO)
)
SUBTITLE = _style(fontSize=11, textColor=_color(MUTED))
HEADING = _style(
    fontName=SERIF_BOLD,
    fontSize=HEADING_SIZE,
    leading=HEADING_SIZE * 1.3,
    textColor=_color(BORDO),
    spaceBefore=8,
    spaceAfter=3,
)
BULLET = _style(leftIndent=8, firstLineIndent=-8, spaceAfter=2)
TABLE_HEADER = _style(fontName=SANS_BOLD, fontSize=9, textColor=_color(BORDO))
TABLE_HEADER_RIGHT = _style(
    fontName=SANS_BOLD, fontSize=9, textColor=_color(BORDO), alignment=TA_RIGHT
)


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    """Абзац с пользовательским текстом: разметка экранируется, переносы строк сохраняются."""
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _amount(document: OfferDocument, value: Decimal, style: ParagraphStyle = RIGHT) -> Paragraph:
    return _p(money(value, document.symbol, KOPECK), style)


def _logo(document: OfferDocument) -> Image | None:
    logo = document.letterhead.logo
    if logo is None:
        return None
    width, height = logo_size(logo)
    scale = min(LOGO_MAX_WIDTH / width, LOGO_MAX_HEIGHT / height)
    return Image(io.BytesIO(logo.data), width=width * scale, height=height * scale)


def _header(document: OfferDocument) -> list[Flowable]:
    """Логотип слева, название и реквизиты справа, бордовая линия снизу."""
    letterhead = document.letterhead
    lines: list[Flowable] = []
    if document.firm_name:
        lines.append(_p(document.firm_name, FIRM_RIGHT))
    for text in (letterhead.requisites, letterhead.address, letterhead.contacts):
        if text:
            lines.append(_p(text, SMALL_RIGHT))
    logo = _logo(document)
    if logo is None and not lines:
        return []
    table = Table([[logo or "", lines]], colWidths=[HEADER_LOGO_COLUMN, None])
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), SANS, BODY_SIZE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, 0), ACCENT_WIDTH, _color(BORDO)),
            ]
        )
    )
    return [table, Spacer(1, 8)]


def _meta(document: OfferDocument) -> Table:
    rows = [("Заказчик", document.client), ("Проект", document.project_name)]
    rows.append(("Цена", document.price_terms))
    table = Table(
        [[_p(label, SMALL), _p(value)] for label, value in rows if value],
        colWidths=[META_LABEL_COLUMN, None],
    )
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), SANS, BODY_SIZE),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _stage_cell(name: str, description: str) -> list[Flowable]:
    cell: list[Flowable] = [_p(name, BOLD)]
    if description:
        cell.append(_p(description, SMALL))
    return cell


def _works_style(header_rows: int = 1) -> list[tuple[Any, ...]]:
    return [
        ("FONT", (0, 0), (-1, -1), SANS, BODY_SIZE),
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), _color(PARCHMENT)),
        ("LINEBELOW", (0, 0), (-1, header_rows - 1), 1, _color(BORDO)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), CELL_PADDING),
        ("BOTTOMPADDING", (0, 0), (-1, -1), CELL_PADDING),
    ]


def _works_fixed(document: OfferDocument) -> Table:
    rows: list[list[Any]] = [
        [
            _p("№", TABLE_HEADER),
            _p("Этап и результат", TABLE_HEADER),
            _p("Стоимость без НДС", TABLE_HEADER_RIGHT),
        ]
    ]
    for stage in document.stages:
        rows.append(
            [
                _p(str(stage.number)),
                _stage_cell(stage.name, stage.description),
                _amount(document, stage.amount),
            ]
        )
    table = Table(rows, colWidths=[NUMBER_COLUMN, None, AMOUNT_COLUMN], repeatRows=1)
    table.setStyle(
        TableStyle([*_works_style(), ("LINEBELOW", (0, 1), (-1, -1), RULE_WIDTH, _color(RULE))])
    )
    return table


def _works_hourly(document: OfferDocument) -> Table:
    symbol = document.symbol
    rows: list[list[Any]] = [
        [
            _p("№", TABLE_HEADER),
            _p("Этап, исполнитель", TABLE_HEADER),
            _p("Часы", TABLE_HEADER_RIGHT),
            _p("Ставка без НДС", TABLE_HEADER_RIGHT),
            _p("Сумма без НДС", TABLE_HEADER_RIGHT),
        ]
    ]
    style = _works_style()
    for stage in document.stages:
        rows.append(
            [
                _p(str(stage.number)),
                _stage_cell(stage.name, stage.description),
                _p(hours_label(stage.hours), BOLD_RIGHT),
                "",
                _amount(document, stage.amount, BOLD_RIGHT),
            ]
        )
        for line in stage.lines:
            rows.append(
                [
                    "",
                    _p(line.title, _style(leftIndent=LINE_INDENT)),
                    _p(hours_label(line.hours), RIGHT),
                    _p(money_rate(line.rate, symbol, KOPECK), RIGHT),
                    _amount(document, line.amount),
                ]
            )
            row = len(rows) - 1
            style += [
                ("TOPPADDING", (0, row), (-1, row), LINE_PADDING),
                ("BOTTOMPADDING", (0, row), (-1, row), LINE_PADDING),
            ]
        style.append(
            ("LINEBELOW", (0, len(rows) - 1), (-1, len(rows) - 1), RULE_WIDTH, _color(RULE))
        )
    table = Table(
        rows,
        colWidths=[NUMBER_COLUMN, None, HOURS_COLUMN, RATE_COLUMN, AMOUNT_COLUMN],
        repeatRows=1,
    )
    table.setStyle(TableStyle(style))
    return table


def _totals(document: OfferDocument) -> Table:
    """Итоговый блок: услуги без НДС, НДС, возмещаемые расходы, итого к оплате."""
    rows: list[list[Any]] = []
    style: list[tuple[Any, ...]] = [
        ("FONT", (0, 0), (-1, -1), SANS, BODY_SIZE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), TOTALS_PADDING),
        ("BOTTOMPADDING", (0, 0), (-1, -1), TOTALS_PADDING),
    ]
    if document.price_kind == PriceKind.CAPPED and document.write_off > 0:
        rows.append([_p(SHADOW_NET), _amount(document, document.shadow_net)])
        rows.append([_p(WRITE_OFF), _amount(document, -document.write_off)])
    rows.append([_p(SERVICES_NET), _amount(document, document.services_net)])
    if document.vat_rate > 0:
        rows.append([_p(document.vat_label), _amount(document, document.vat)])
        rows.append([_p(SERVICES_GROSS), _amount(document, document.services_gross)])
    else:
        rows.append([_p("НДС"), _p(NO_VAT, RIGHT)])
    if document.disbursements:
        style.append(("LINEABOVE", (0, len(rows)), (-1, len(rows)), RULE_WIDTH, _color(RULE)))
        rows.append([_p(f"{DISBURSEMENTS_TITLE}:"), ""])
        for item in document.disbursements:
            rows.append(
                [_p(item.label, _style(leftIndent=LINE_INDENT)), _amount(document, item.amount)]
            )
        style.append(("LINEABOVE", (0, len(rows)), (-1, len(rows)), RULE_WIDTH, _color(RULE)))
        rows.append(
            [
                _p(DISBURSEMENTS_TOTAL, BOLD),
                _amount(document, document.disbursements_total, BOLD_RIGHT),
            ]
        )
    total_row = len(rows)
    rows.append(
        [
            _p(TOTAL, _style(fontName=SANS_BOLD, textColor=_color(BORDO))),
            _amount(
                document,
                document.total,
                _style(fontName=SANS_BOLD, textColor=_color(BORDO), alignment=TA_RIGHT),
            ),
        ]
    )
    style += [
        ("LINEABOVE", (0, total_row), (-1, total_row), ACCENT_WIDTH, _color(BORDO)),
        ("BACKGROUND", (0, total_row), (-1, total_row), _color(PARCHMENT)),
    ]
    table = Table(rows, colWidths=[None, AMOUNT_COLUMN])
    table.setStyle(TableStyle(style))
    return table


def _signature(document: OfferDocument) -> list[Flowable]:
    story: list[Flowable] = []
    if document.signature:
        story += [Spacer(1, 10), _p("С уважением,"), _p(document.signature, BOLD)]
    bank = document.letterhead.bank_details.strip()
    if bank:
        story += [Spacer(1, 10), _p("Банковские реквизиты", SMALL), _p(bank, SMALL)]
    return story


def story(document: OfferDocument) -> list[Flowable]:
    """Содержимое PDF по порядку разделов КП."""
    flow: list[Flowable] = [
        *_header(document),
        _p(TITLE, TITLE_STYLE),
        Spacer(1, 2),
        _p(document.subtitle, SUBTITLE),
        Spacer(1, 8),
        _meta(document),
    ]
    if document.description:
        flow += [_p("Задача", HEADING), _p(document.description)]
    flow.append(_p("Состав работ и стоимость", HEADING))
    if document.price_kind == PriceKind.FIXED:
        flow.append(_works_fixed(document))
    else:
        flow.append(_works_hourly(document))
    flow += [Spacer(1, 8), KeepTogether([_totals(document)])]
    flow.append(_p("Условия", HEADING))
    bullets: list[Flowable] = [_p(f"•{NBSP}{item}", BULLET) for item in document.conditions]
    # Подпись не уходит на новую страницу одна: с ней переносится последнее условие.
    flow += [*bullets[:-1], KeepTogether([*bullets[-1:], *_signature(document)])]
    return flow


def _build(document: OfferDocument, total_pages: int | None) -> tuple[bytes, int]:
    """Собирает PDF; колонтитул «стр. N из M», если число страниц уже известно."""
    buffer = io.BytesIO()
    width, height = A4

    def footer(canvas: Canvas, doc: BaseDocTemplate) -> None:
        page = f"стр. {doc.page}" + (f" из {total_pages}" if total_pages else "")
        canvas.saveState()
        canvas.setFont(SANS, FOOTER_SIZE)
        canvas.setFillColor(_color(MUTED))
        canvas.drawString(PAGE_MARGIN, FOOTER_Y, document.footer)
        canvas.drawRightString(width - PAGE_MARGIN, FOOTER_Y, page)
        canvas.restoreState()

    frame = Frame(
        PAGE_MARGIN,
        BOTTOM_MARGIN,
        width - 2 * PAGE_MARGIN,
        height - PAGE_MARGIN - BOTTOM_MARGIN,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title=f"{TITLE} {document.subtitle} — {document.project_name}",
        author=document.firm_name,
        subject=document.project_name,
        creator=CREATOR,
        invariant=True,
        initialFontName=SANS,
        initialFontSize=BODY_SIZE,
    )
    doc.addPageTemplates([PageTemplate(id="offer", frames=[frame], onPage=footer)])
    doc.build(story(document))
    return buffer.getvalue(), doc.page


def offer_pdf(document: OfferDocument) -> bytes:
    """КП в PDF: байты файла.

    Документ собирается дважды: первый проход считает страницы для
    колонтитула «стр. N из M» (несколько десятков миллисекунд).
    """
    register_fonts()
    _, pages = _build(document, None)
    data, _ = _build(document, pages)
    return data
