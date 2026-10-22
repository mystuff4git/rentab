"""Загрузка реестра ставок из YAML.

Реестр страны — один файл с четырьмя разделами:

    country: RF
    rates:                        # доли: 0.06 = 6%
      usn.income:
        value: 0.06
        effective_from: 2026-01-01
        source: "НК РФ, ст. 346.20, п. 1"   # норма или документ
        source_url: https://...             # где её прочитать
        needs_review: false                 # true, пока значение не сверено с источником
        legal_review: "..."                 # что проверить юристу; null — не нужно
        valid_until: null                   # последний день действия, если известен
        note: "..."
    amounts:                      # денежные пороги и численность
      usn.income_limit: {value: 490500000, unit: RUB, ...}
      vat.registration_threshold: {value: 10000, unit: MRP, ...}
    indices:                      # расчётные показатели по годам
      mrp:
        unit: KZT
        values:
          2026: {value: 4325, ...}
    scales:                       # прогрессивные шкалы
      ndfl.resident:
        brackets: [{up_to: 2400000, rate: 0.13}, ..., {up_to: null, rate: 0.22}]
        ...

`effective_from` — начало периода, на который значение проверено (для
реестра 2026 года — 1 января 2026 года). Суммы в МРП, МЗП и МРОТ переводятся
в национальную валюту по показателю нужного года (`amount`).

Ставка с `needs_review: false` обязана ссылаться на источник: так реестр
не пропустит значение, которое никто не сверял (CLAUDE.md).

Файлы читаются один раз за процесс и кэшируются.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from functools import cache
from typing import Annotated, TypeVar

from pydantic import Field, HttpUrl, model_validator

from rentab_core.errors import ReferenceDataError
from rentab_core.models import CoreModel, Country
from rentab_core.reference import check_country, load_reference

RATES_PACKAGE = "rentab_core.rates"
RATE_FILES: Mapping[Country, str] = {
    Country.RF: "ru_2026.yaml",
    Country.KZ: "kz_2026.yaml",
}
# Год, на который составлен реестр: по нему берутся МРП, МЗП и МРОТ.
REGISTRY_YEAR = 2026

Fraction = Annotated[Decimal, Field(ge=0, le=1)]
EntryT = TypeVar("EntryT")


class SourcedEntry(CoreModel):
    """Общие поля записи реестра: источник, дата, флаги проверки.

    Attributes:
        effective_from: Начало периода, на который значение проверено.
        source: Норма или документ («НК РФ, ст. 346.20, п. 1»).
        source_url: Где прочитать источник.
        needs_review: True, пока значение не сверено с текстом источника.
        legal_review: Что стоит проверить юристу; None — проверка не нужна.
        valid_until: Последний день действия нормы, если он известен.
        note: Пояснение.
    """

    effective_from: dt.date
    source: str = ""
    source_url: HttpUrl | None = None
    needs_review: bool
    legal_review: str | None = None
    valid_until: dt.date | None = None
    note: str = ""

    @model_validator(mode="after")
    def _confirmed_needs_source(self) -> SourcedEntry:
        """Сверенное значение (needs_review: false) ссылается на источник."""
        if not self.needs_review and (self.source_url is None or not self.source):
            raise ValueError("needs_review: false допустим только с source и source_url")
        return self


class Rate(SourcedEntry):
    """Ставка-доля."""

    value: Fraction


class AmountUnit(StrEnum):
    """Единица суммы в реестре."""

    RUB = "RUB"
    KZT = "KZT"
    MRP = "MRP"  # месячный расчётный показатель РК
    MZP = "MZP"  # минимальная заработная плата РК
    MROT = "MROT"  # минимальный размер оплаты труда РФ
    COUNT = "COUNT"  # человек


# Единицы, которые переводятся в деньги по показателю года, и ключ показателя.
INDEX_UNITS: Mapping[AmountUnit, str] = {
    AmountUnit.MRP: "mrp",
    AmountUnit.MZP: "mzp",
    AmountUnit.MROT: "mrot",
}


class Amount(SourcedEntry):
    """Денежный порог, кратность показателя или численность."""

    value: Annotated[Decimal, Field(ge=0)]
    unit: AmountUnit


class IndexValue(SourcedEntry):
    """Значение расчётного показателя за год."""

    value: Annotated[Decimal, Field(gt=0)]


class Index(CoreModel):
    """Расчётный показатель (МРП, МЗП, МРОТ) по годам."""

    name: str
    unit: AmountUnit
    values: dict[int, IndexValue]


class Bracket(CoreModel):
    """Ступень прогрессивной шкалы: ставка для части базы до up_to."""

    up_to: Annotated[Decimal, Field(gt=0)] | None
    rate: Fraction


class Scale(SourcedEntry):
    """Прогрессивная шкала: ставки по частям годовой базы."""

    brackets: tuple[Bracket, ...]

    @model_validator(mode="after")
    def _check_brackets(self) -> Scale:
        """Пороги растут, последняя ступень — без верхней границы."""
        bounds = [bracket.up_to for bracket in self.brackets]
        if not bounds or bounds[-1] is not None or None in bounds[:-1]:
            raise ValueError("шкала должна заканчиваться ступенью без верхней границы")
        finite = [bound for bound in bounds[:-1] if bound is not None]
        if finite != sorted(set(finite)):
            raise ValueError("пороги шкалы должны строго возрастать")
        return self


class RateTable(CoreModel):
    """Реестр страны: ставки, суммы, показатели и шкалы по ключам."""

    country: Country
    rates: dict[str, Rate]
    amounts: dict[str, Amount] = Field(default_factory=dict)
    indices: dict[str, Index] = Field(default_factory=dict)
    scales: dict[str, Scale] = Field(default_factory=dict)


@cache
def load_rates(country: Country) -> RateTable:
    """Читает и проверяет реестр ставок страны.

    Raises:
        ReferenceDataError: Файл не найден, не разбирается или не проходит
            проверку схемы, либо в нём указана другая страна.
    """
    filename = RATE_FILES[country]
    table = load_reference(RATES_PACKAGE, filename, RateTable)
    check_country(table.country, country, filename)
    return table


def _lookup(section: Mapping[str, EntryT], country: Country, kind: str, key: str) -> EntryT:
    if key not in section:
        raise ReferenceDataError(f"В реестре {kind} {country} нет ключа {key!r}")
    return section[key]


def rate_entry(country: Country, key: str) -> Rate:
    """Запись ставки целиком (с источником и флагами).

    Raises:
        ReferenceDataError: Ставки с таким ключом в реестре нет.
    """
    return _lookup(load_rates(country).rates, country, "ставок", key)


def rate(country: Country, key: str) -> Decimal:
    """Возвращает значение ставки из реестра.

    Args:
        country: Страна реестра.
        key: Ключ ставки, например `"usn.income"`.

    Raises:
        ReferenceDataError: Ставки с таким ключом в реестре нет.
    """
    return rate_entry(country, key).value


def index_value(country: Country, key: str, year: int = REGISTRY_YEAR) -> Decimal:
    """Значение расчётного показателя (`"mrp"`, `"mzp"`, `"mrot"`) за год.

    Raises:
        ReferenceDataError: Показателя или его значения за год в реестре нет.
    """
    index = _lookup(load_rates(country).indices, country, "показателей", key)
    if year not in index.values:
        raise ReferenceDataError(f"В реестре {country} нет значения {index.name} за {year} год")
    return index.values[year].value


def amount_entry(country: Country, key: str) -> Amount:
    """Запись суммы целиком (с единицей, источником и флагами).

    Raises:
        ReferenceDataError: Суммы с таким ключом в реестре нет.
    """
    return _lookup(load_rates(country).amounts, country, "сумм", key)


def amount(country: Country, key: str, year: int = REGISTRY_YEAR) -> Decimal:
    """Сумма из реестра в национальной валюте (или численность).

    Кратность МРП, МЗП или МРОТ умножается на показатель года.

    Raises:
        ReferenceDataError: Суммы или нужного показателя в реестре нет.
    """
    entry = amount_entry(country, key)
    index_key = INDEX_UNITS.get(entry.unit)
    if index_key is None:
        return entry.value
    return entry.value * index_value(country, index_key, year)


def scale(country: Country, key: str) -> Scale:
    """Прогрессивная шкала из реестра.

    Raises:
        ReferenceDataError: Шкалы с таким ключом в реестре нет.
    """
    return _lookup(load_rates(country).scales, country, "шкал", key)
