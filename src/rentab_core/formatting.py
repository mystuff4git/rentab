"""Числа для людей: «1 234 567 ₽», «58,80%», «40 ч» — для интерфейса и документов.

Разряды и знак валюты отделяются неразрывным пробелом (число не переносится
на узком экране и в таблице документа), отрицательные значения — с минусом
«−». Округление — «половина вверх» (`money.round_to`) с заданным шагом:
интерфейс показывает суммы до рубля (тенге), КП и смета — до копейки
(`KOPECK`). Числа без округления для подписей и замечаний — `number_text`.
"""

from __future__ import annotations

from decimal import Decimal

from rentab_core.money import MONEY_QUANTUM, PERCENT, format_number, round_to

NBSP = " "
MINUS = "−"
DASH = "—"
# Шаг округления сумм в подписях интерфейса: рубль (тенге).
LABEL_QUANTUM = Decimal(1)
# Шаг округления сумм в документах (КП, смета): копейка (тиын).
KOPECK = MONEY_QUANTUM
# Шаг округления часов и процентов.
HOURS_QUANTUM = Decimal("0.1")
PCT_QUANTUM = Decimal("0.01")


def number_text(value: Decimal) -> str:
    """Число без округления: «1 234 567», «−2 200», «0,5»."""
    if value == 0:
        value = abs(value)
    return format_number(value).replace(" ", NBSP).replace("-", MINUS)


def number_label(value: Decimal, quantum: Decimal = LABEL_QUANTUM) -> str:
    """Число с округлением «половина вверх» и знаками шага: «1 234 567», «7,00», «−0,73»."""
    rounded = round_to(value, quantum)
    if rounded == 0:
        rounded = abs(rounded)
    integer, _, fraction = f"{rounded:f}".partition(".")
    sign = MINUS if integer.startswith("-") else ""
    digits = integer.lstrip("-")
    groups = [digits[max(i - 3, 0) : i] for i in range(len(digits), 0, -3)][::-1]
    text = sign + NBSP.join(groups)
    return f"{text},{fraction}" if fraction else text


def money(value: Decimal, symbol: str, quantum: Decimal = LABEL_QUANTUM) -> str:
    """Сумма: «1 234 567 ₽» (по умолчанию до рубля), «40 669,93 ₽» с шагом `KOPECK`."""
    return f"{number_label(value, quantum)}{NBSP}{symbol}"


def money_rate(value: Decimal, symbol: str, quantum: Decimal = LABEL_QUANTUM) -> str:
    """Сумма за час: «4 125 ₽/ч»."""
    return f"{money(value, symbol, quantum)}/ч"


def sign_prefix(rounded: Decimal, minus: str = MINUS) -> str:
    """Знак округлённого значения: «+», минус или пусто для нуля."""
    return "+" if rounded > 0 else (minus if rounded < 0 else "")


def hours_label(value: Decimal) -> str:
    """Часы: «159,6 ч», «40 ч» (без лишнего нуля)."""
    return f"{number_text(round_to(value, HOURS_QUANTUM))}{NBSP}ч"


def signed_hours(value: Decimal) -> str:
    """Часы со знаком: «+55 ч», «−0,5 ч»."""
    rounded = round_to(value, HOURS_QUANTUM)
    return f"{sign_prefix(rounded)}{number_text(abs(rounded))}{NBSP}ч"


def signed_money(value: Decimal, symbol: str) -> str:
    """Сумма со знаком: «+5 650 ₽», «−227 700 ₽»."""
    rounded = round_to(value, LABEL_QUANTUM)
    return f"{sign_prefix(rounded)}{number_text(abs(rounded))}{NBSP}{symbol}"


def pct_label(share: Decimal | None, quantum: Decimal = PCT_QUANTUM) -> str:
    """Доля в процентах: «75,17%», «−0,73%»; None — «—»."""
    if share is None:
        return DASH
    return f"{number_label(share * PERCENT, quantum)}%"


def signed_pct(share: Decimal, quantum: Decimal = Decimal(1)) -> str:
    """Доля в процентах со знаком: «+399%»."""
    rounded = round_to(share * PERCENT, quantum)
    return f"{sign_prefix(rounded)}{number_label(abs(rounded), quantum)}%"


def points_label(delta: Decimal) -> str:
    """Разница долей в процентных пунктах: «−75,90 п. п.»."""
    rounded = round_to(delta * PERCENT, PCT_QUANTUM)
    return f"{sign_prefix(rounded)}{number_label(abs(rounded), PCT_QUANTUM)}{NBSP}п.{NBSP}п."


def rate_text(share: Decimal) -> str:
    """Ставка-доля процентом без лишних нулей: 0.05 → «5», 0.151 → «15,1»."""
    return number_text(share * PERCENT)


def plural(count: int, forms: tuple[str, str, str]) -> str:
    """Форма слова после числа: (1 «день», 2 «дня», 5 «дней») → «21 день», «14 дней»."""
    tail = count % 100
    if 11 <= tail <= 14:
        form = forms[2]
    elif count % 10 == 1:
        form = forms[0]
    elif 2 <= count % 10 <= 4:
        form = forms[1]
    else:
        form = forms[2]
    return f"{count} {form}"
