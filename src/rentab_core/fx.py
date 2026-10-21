"""Пересчёт сумм между валютами по курсам фирмы (L-06).

Курс задаётся как цена единицы валюты в рублях (`ExchangeRates`), поэтому
пересчёт идёт через рубль:

    amount_target = amount_source × rub_per(source) / rub_per(target)

Если нужного курса нет, ядро не складывает суммы как есть, а бросает
`CurrencyConversionError`. Загрузка курсов ЦБ РФ и НБРК — фаза 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from rentab_core.errors import CurrencyConversionError
from rentab_core.models import ExchangeRates
from rentab_core.money import CURRENCY_SYMBOLS, ONE, Currency

# Поле `ExchangeRates` с курсом каждой валюты, кроме рубля.
RATE_FIELDS: Mapping[Currency, str] = {
    Currency.KZT: "rub_per_kzt",
    Currency.USD: "rub_per_usd",
}


def rub_per(rates: ExchangeRates, currency: Currency) -> Decimal:
    """Цена единицы валюты в рублях.

    Raises:
        CurrencyConversionError: Курс валюты не задан.
    """
    if currency == Currency.RUB:
        return ONE
    value: Decimal | None = getattr(rates, RATE_FIELDS[currency])
    if value is None:
        raise CurrencyConversionError(
            f"Укажите курс ₽ за 1 {CURRENCY_SYMBOLS[currency]}: "
            f"без него суммы в {currency.value} нельзя пересчитать"
        )
    return value


def convert(amount: Decimal, source: Currency, target: Currency, rates: ExchangeRates) -> Decimal:
    """Пересчитывает сумму из валюты source в валюту target.

    Raises:
        CurrencyConversionError: Не задан курс одной из валют (если валюты различаются).
    """
    if source == target:
        return amount
    return amount * rub_per(rates, source) / rub_per(rates, target)
