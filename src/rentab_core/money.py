"""Деньги: Decimal, валюты и округление.

Внутри ядра все суммы и ставки — `Decimal` без округления. Округляют только
на выводе (смета, интерфейс) функцией `round_to`. Входные float переводятся
в Decimal через строку, чтобы 0.1 оставалось 0.1, а не двоичным приближением.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

ZERO = Decimal(0)
ONE = Decimal(1)
PERCENT = Decimal(100)

# Округление на выводе: «половина вверх», как принято в бухгалтерии РФ и РК.
ROUNDING = ROUND_HALF_UP
# Шаг округления денежных сумм: копейка (тиын, цент).
MONEY_QUANTUM = Decimal("0.01")


class Currency(StrEnum):
    """Валюта суммы (ISO 4217)."""

    RUB = "RUB"
    KZT = "KZT"
    USD = "USD"


CURRENCY_SYMBOLS: Mapping[Currency, str] = {
    Currency.RUB: "₽",
    Currency.KZT: "₸",
    Currency.USD: "$",
}


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    """Переводит число в Decimal без двоичных артефактов float.

    Args:
        value: Decimal, int, конечный float или строка с числом.

    Returns:
        Значение как Decimal; float переводится через `repr`.

    Raises:
        TypeError: Если передан bool (почти всегда это ошибка вызова).
        ValueError: Если float не конечен (NaN, ±inf).
    """
    if isinstance(value, bool):
        raise TypeError("bool не является денежной суммой")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Сумма должна быть конечным числом, получено {value!r}")
        return Decimal(repr(value))
    return Decimal(value)


def round_to(value: Decimal, quantum: Decimal = MONEY_QUANTUM) -> Decimal:
    """Округляет значение для вывода: по умолчанию до копеек, «половина вверх»."""
    return value.quantize(quantum, rounding=ROUNDING)


def decimal_sum(values: Iterable[Decimal]) -> Decimal:
    """Сумма Decimal; для пустой последовательности — Decimal(0), а не int 0."""
    return sum(values, ZERO)


def format_number(value: Decimal) -> str:
    """Число для текста: «1 234 567», «88 359,38», «0,5» — без округления."""
    integer, _, fraction = f"{value.normalize():f}".partition(".")
    sign = "-" if integer.startswith("-") else ""
    digits = integer.lstrip("-")
    groups = [digits[max(i - 3, 0) : i] for i in range(len(digits), 0, -3)][::-1]
    text = sign + " ".join(groups)
    return f"{text},{fraction}" if fraction else text
