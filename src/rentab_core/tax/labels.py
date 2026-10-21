"""Форматирование ставок в подписях налоговых режимов."""

from __future__ import annotations

from decimal import Decimal

from rentab_core.money import PERCENT


def percent(rate: Decimal) -> str:
    """Ставка-доля как целый процент без знака «%»: 0.06 → «6» (как `:.0f` в v0.3)."""
    return f"{rate * PERCENT:.0f}"
