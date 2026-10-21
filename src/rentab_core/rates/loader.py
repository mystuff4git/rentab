"""Загрузка реестра ставок из YAML.

Формат файла:

    country: RF
    rates:
      usn.income:
        value: 0.06               # доля, а не процент
        effective_from: 2026-01-01
        source_url: null          # ссылка на первоисточник
        needs_review: true        # true, пока ставка не сверена с источником
        note: "..."

Файлы читаются один раз за процесс и кэшируются.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from functools import cache
from typing import Annotated

from pydantic import Field, HttpUrl

from rentab_core.errors import ReferenceDataError
from rentab_core.models import CoreModel, Country
from rentab_core.reference import check_country, load_reference

RATES_PACKAGE = "rentab_core.rates"
RATE_FILES: Mapping[Country, str] = {
    Country.RF: "ru_2026.yaml",
    Country.KZ: "kz_2026.yaml",
}


class Rate(CoreModel):
    """Одна ставка реестра."""

    value: Annotated[Decimal, Field(ge=0, le=1)]
    effective_from: dt.date
    source_url: HttpUrl | None
    needs_review: bool
    note: str = ""


class RateTable(CoreModel):
    """Ставки одной страны по ключам вида `usn.income`."""

    country: Country
    rates: dict[str, Rate]


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


def rate(country: Country, key: str) -> Decimal:
    """Возвращает значение ставки из реестра.

    Args:
        country: Страна реестра.
        key: Ключ ставки, например `"usn.income"`.

    Raises:
        ReferenceDataError: Ставки с таким ключом в реестре нет.
    """
    rates = load_rates(country).rates
    if key not in rates:
        raise ReferenceDataError(f"В реестре ставок {country} нет ключа {key!r}")
    return rates[key].value
