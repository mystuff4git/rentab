"""Сводка сверки ставок и пошлин с первоисточниками: `docs/rates_verification.md`.

Документ собирается из реестров ставок и каталогов пошлин, поэтому не
расходится с ними. Перегенерировать:

    uv run python -m rentab_core.rates.verification docs/rates_verification.md

Уверенность выводится из записи: `needs_review: false` — высокая (значение
видно в тексте нормы или разъяснении ведомства), источник есть, но
`needs_review: true` — средняя, источника нет — низкая.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from rentab_core.duties.catalog import (
    APPLICANT_CATEGORY_LABELS,
    QUANTITY_LABELS,
    Duty,
    DutyCatalog,
    load_catalog,
)
from rentab_core.models import Country
from rentab_core.money import CURRENCY_SYMBOLS, PERCENT, format_number
from rentab_core.rates.loader import (
    INDEX_UNITS,
    REGISTRY_YEAR,
    Amount,
    AmountUnit,
    SourcedEntry,
    amount,
    load_rates,
)

COUNTRY_TITLES: Mapping[Country, str] = {Country.RF: "РФ", Country.KZ: "РК"}
OFFICE_TITLES: Mapping[Country, str] = {Country.RF: "Роспатент", Country.KZ: "Казпатент"}
UNIT_LABELS: Mapping[AmountUnit, str] = {
    AmountUnit.RUB: "₽",
    AmountUnit.KZT: "₸",
    AmountUnit.MRP: "МРП",
    AmountUnit.MZP: "МЗП",
    AmountUnit.MROT: "МРОТ",
    AmountUnit.COUNT: "чел.",
}
NO = "нет"


class Confidence(StrEnum):
    """Уверенность в значении записи."""

    HIGH = "высокая"
    MEDIUM = "средняя"
    LOW = "низкая"


def confidence(needs_review: bool, has_source: bool) -> Confidence:
    """Уверенность по флагам записи."""
    if not needs_review:
        return Confidence.HIGH
    return Confidence.MEDIUM if has_source else Confidence.LOW


def format_percent(value: Decimal) -> str:
    """Доля как процент: 0.151 → «15,1%»."""
    return f"{format_number(value * PERCENT)}%"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _source(entry_source: str, url: object) -> str:
    title = _cell(entry_source) if entry_source else "источник"
    return f"[{title}]({url})" if url else (title if entry_source else "—")


def _period(entry: SourcedEntry) -> str:
    start = entry.effective_from.strftime("%d.%m.%Y")
    if entry.valid_until is None:
        return f"с {start}"
    return f"с {start} до {entry.valid_until.strftime('%d.%m.%Y')}"


def _entry_cells(entry: SourcedEntry) -> list[str]:
    level = confidence(entry.needs_review, entry.source_url is not None)
    return [
        _period(entry),
        _source(entry.source, entry.source_url),
        level.value,
        _cell(f"да: {entry.legal_review}") if entry.legal_review else NO,
        _cell(entry.note),
    ]


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


ENTRY_HEADER = ("Действует", "Источник", "Уверенность", "Нужна проверка юристом", "Пояснение")


def _amount_value(country: Country, key: str, entry: Amount) -> str:
    unit = UNIT_LABELS[entry.unit]
    text = f"{format_number(entry.value)} {unit}"
    if entry.unit in INDEX_UNITS:
        resolved = amount(country, key, REGISTRY_YEAR)
        currency = CURRENCY_SYMBOLS[country.currency]
        text += f" = {format_number(resolved)} {currency} в {REGISTRY_YEAR} году"
    return text


def country_section(country: Country) -> list[str]:
    """Раздел документа по реестру страны."""
    table = load_rates(country)
    lines = [f"## {COUNTRY_TITLES[country]}", "", "### Ставки", ""]
    lines += _table(
        ("Ключ", "Значение", *ENTRY_HEADER),
        (
            [f"`{key}`", format_percent(entry.value), *_entry_cells(entry)]
            for key, entry in table.rates.items()
        ),
    )
    if table.amounts:
        lines += ["", "### Пороги и суммы", ""]
        lines += _table(
            ("Ключ", "Значение", *ENTRY_HEADER),
            (
                [f"`{key}`", _amount_value(country, key, entry), *_entry_cells(entry)]
                for key, entry in table.amounts.items()
            ),
        )
    if table.indices:
        lines += ["", "### Расчётные показатели", ""]
        lines += _table(
            ("Показатель", "Год", "Значение", *ENTRY_HEADER),
            (
                [
                    index.name,
                    str(year),
                    f"{format_number(value.value)} {UNIT_LABELS[index.unit]}",
                    *_entry_cells(value),
                ]
                for index in table.indices.values()
                for year, value in sorted(index.values.items())
            ),
        )
    if table.scales:
        lines += ["", "### Шкалы", ""]
        rows = []
        for key, entry in table.scales.items():
            steps = []
            lower = Decimal(0)
            for bracket in entry.brackets:
                rate_text = format_percent(bracket.rate)
                if bracket.up_to is None:
                    steps.append(f"{rate_text} свыше {format_number(lower)}")
                else:
                    steps.append(f"{rate_text} до {format_number(bracket.up_to)}")
                    lower = bracket.up_to
            rows.append([f"`{key}`", "; ".join(steps), *_entry_cells(entry)])
        lines += _table(("Ключ", "Ступени", *ENTRY_HEADER), rows)
    return lines


def _duty_amount(duty: Duty, symbol: str) -> str:
    if duty.amount == 0:
        return "не установлена"
    text = f"{format_number(duty.amount)} {symbol}"
    if duty.amount_by_category:
        others = "; ".join(
            f"{APPLICANT_CATEGORY_LABELS[category].lower()} {format_number(value)}"
            for category, value in duty.amount_by_category.items()
        )
        text += f" ({others})"
    return text


def _duty_extras(duty: Duty, symbol: str) -> str:
    if not duty.extras:
        return "—"
    return "; ".join(
        f"+{format_number(extra.per_unit)} {symbol} за единицу сверх {extra.included}: "
        f"{QUANTITY_LABELS[extra.quantity].lower()}"
        for extra in duty.extras
    )


def catalog_section(catalog: DutyCatalog) -> list[str]:
    """Раздел документа по каталогу пошлин."""
    symbol = CURRENCY_SYMBOLS[catalog.currency]
    lines = [f"## Пошлины: {OFFICE_TITLES[catalog.country]}", ""]
    source = _source(catalog.source, catalog.source_url)
    start = catalog.effective_from.strftime("%d.%m.%Y") if catalog.effective_from else "—"
    vat = "с НДС" if catalog.vat_included else "без НДС"
    lines += [f"Документ: {source}. Суммы действуют с {start}, {vat}.", ""]
    rows = []
    for duty in catalog.duties:
        level = confidence(duty.needs_review, duty.source_url is not None)
        rows.append(
            [
                duty.code or "—",
                _cell(duty.action),
                _duty_amount(duty, symbol),
                _duty_extras(duty, symbol),
                _source("источник" if duty.source_url else "", duty.source_url),
                level.value,
                _cell(f"да: {duty.legal_review}") if duty.legal_review else NO,
                _cell(duty.note),
            ]
        )
    lines += _table(
        (
            "Пункт",
            "Действие",
            "Сумма",
            "Надбавки",
            "Источник",
            "Уверенность",
            "Нужна проверка юристом",
            "Пояснение",
        ),
        rows,
    )
    return lines


def _summary() -> list[str]:
    counts = dict.fromkeys(Confidence, 0)
    legal = 0
    for country in Country:
        table = load_rates(country)
        entries: list[SourcedEntry] = [
            *table.rates.values(),
            *table.amounts.values(),
            *table.scales.values(),
            *(value for index in table.indices.values() for value in index.values.values()),
        ]
        for entry in entries:
            counts[confidence(entry.needs_review, entry.source_url is not None)] += 1
            legal += entry.legal_review is not None
        for duty in load_catalog(country).duties:
            counts[confidence(duty.needs_review, duty.source_url is not None)] += 1
            legal += duty.legal_review is not None
    return [
        "| Уверенность | Записей |",
        "|---|---|",
        *(f"| {level.value} | {counts[level]} |" for level in Confidence),
        "",
        f"Записей с пометкой «нужна проверка юристом»: {legal}.",
    ]


HEADER = f"""# Сверка ставок и пошлин с первоисточниками

Ставки, пороги и пошлины, которые использует ядро, на {REGISTRY_YEAR} год. Документ
генерируется из реестров `src/rentab_core/rates/*.yaml` и каталогов
`src/rentab_core/duties/data/*.yaml`, вручную его не правят:

    uv run python -m rentab_core.rates.verification docs/rates_verification.md

**Уверенность.** Высокая — значение видно в тексте нормы или в разъяснении
ведомства (ФНС, КГД, ЕНПФ, ГФСС, ФСМС, Роспатент), в реестре `needs_review: false`.
Средняя — источник найден, но значение подтверждено не полностью: вторичные
источники, расхождения или неясная редакция. Низкая — первоисточник не найден;
пошлина с такой пометкой имеет сумму 0 и в смету не попадает.

**Как сверяли.** В фазе 3 полные тексты на consultant.ru, garant.ru,
adilet.zan.kz, rospatent.gov.ru и kazpatent.kz из окружения разработки были
недоступны (сетевая политика). Значения сверены по выдержкам этих страниц в
поисковой выдаче и по разъяснениям ведомств; ссылки ведут на первоисточник.

**Нужна проверка юристом** — значение верное, но применимость зависит от
обстоятельств фирмы (регион, ОКВЭД, класс риска) или от толкования нормы.
"""


def verification_markdown() -> str:
    """Текст `docs/rates_verification.md`."""
    lines = [HEADER, "## Итог", "", *_summary(), ""]
    for country in Country:
        lines += [*country_section(country), ""]
    for country in Country:
        lines += [*catalog_section(load_catalog(country)), ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str]) -> None:
    """Пишет документ в путь из аргументов или выводит его в stdout."""
    text = verification_markdown()
    if argv:
        Path(argv[0]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
