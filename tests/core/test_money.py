"""money: перевод в Decimal, округление на выводе, пересчёт валют для показа."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.money import (
    ONE,
    ZERO,
    Currency,
    decimal_sum,
    display_conversion_factor,
    round_to,
    to_decimal,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.1, Decimal("0.1")),
        (15000.0, Decimal("15000.0")),
        (7, Decimal(7)),
        ("0.30", Decimal("0.30")),
        (Decimal("1.5"), Decimal("1.5")),
    ],
)
def test_to_decimal_avoids_binary_float_artifacts(
    value: float | int | str | Decimal, expected: Decimal
) -> None:
    assert to_decimal(value) == expected
    assert str(to_decimal(0.1)) == "0.1"


def test_to_decimal_rejects_bool_and_non_finite() -> None:
    with pytest.raises(TypeError):
        to_decimal(True)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="конечным"):
            to_decimal(bad)


@pytest.mark.parametrize(
    ("value", "quantum", "expected"),
    [
        ("2.675", "0.01", "2.68"),  # «половина вверх», а не к чётному
        ("2.665", "0.01", "2.67"),
        ("-2.675", "0.01", "-2.68"),
        ("31.85", "0.1", "31.9"),
        ("1234.4999", "0.01", "1234.50"),
    ],
)
def test_round_to_rounds_half_up(value: str, quantum: str, expected: str) -> None:
    assert round_to(Decimal(value), Decimal(quantum)) == Decimal(expected)


def test_round_to_defaults_to_kopecks() -> None:
    assert round_to(Decimal("10.005")) == Decimal("10.01")


def test_decimal_sum_of_empty_is_decimal_zero() -> None:
    total = decimal_sum([])
    assert isinstance(total, Decimal)
    assert total == ZERO
    assert decimal_sum([Decimal("0.1")] * 3) == Decimal("0.3")


@pytest.mark.parametrize(
    ("source", "target", "rate", "expected"),
    [
        (Currency.RUB, Currency.RUB, "0.18", ONE),
        (Currency.KZT, Currency.RUB, "0.18", Decimal("0.18")),
        (Currency.RUB, Currency.KZT, "0.2", Decimal(5)),
        (Currency.RUB, Currency.KZT, "0", ONE),  # нулевой курс — без пересчёта
        (Currency.USD, Currency.RUB, "0.18", ONE),
    ],
)
def test_display_conversion_factor(
    source: Currency, target: Currency, rate: str, expected: Decimal
) -> None:
    assert display_conversion_factor(source, target, Decimal(rate)) == expected
