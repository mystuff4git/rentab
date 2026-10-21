"""Пересчёт валют по курсам фирмы (L-06)."""

from __future__ import annotations

import pytest

from rentab_core.errors import CurrencyConversionError, RentabError
from rentab_core.fx import convert, rub_per
from rentab_core.models import ExchangeRates
from rentab_core.money import Currency
from tests.core.factories import d

RATES = ExchangeRates(rub_per_kzt=d("0.18"), rub_per_usd=d(90))


@pytest.mark.parametrize(
    ("amount", "source", "target", "expected"),
    [
        ("100", Currency.RUB, Currency.RUB, "100"),
        ("120000", Currency.KZT, Currency.RUB, "21600"),
        ("21600", Currency.RUB, Currency.KZT, "120000"),
        ("10", Currency.USD, Currency.RUB, "900"),
        ("10", Currency.USD, Currency.KZT, "5000"),
    ],
)
def test_convert(amount: str, source: Currency, target: Currency, expected: str) -> None:
    assert convert(d(amount), source, target, RATES) == d(expected)


def test_same_currency_needs_no_rate() -> None:
    assert convert(d(5), Currency.KZT, Currency.KZT, ExchangeRates()) == d(5)
    assert rub_per(ExchangeRates(), Currency.RUB) == 1


def test_missing_rate_is_an_explicit_error() -> None:
    # v0.3 складывал тенге с рублями как есть; ядро требует курс.
    with pytest.raises(CurrencyConversionError, match="Укажите курс ₽ за 1 ₸") as error:
        convert(d(1), Currency.KZT, Currency.RUB, ExchangeRates(rub_per_usd=d(90)))
    assert isinstance(error.value, RentabError)
    with pytest.raises(CurrencyConversionError, match=r"₽ за 1 \$"):
        convert(d(1), Currency.RUB, Currency.USD, RATES.model_copy(update={"rub_per_usd": None}))
