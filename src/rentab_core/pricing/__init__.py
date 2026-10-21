"""Модели ценообразования: почасовая, с потолком, фиксированная цена.

Каждая модель даёт выручку за услуги без НДС R (docs/methodology.md, раздел 7).
"""

from __future__ import annotations

from decimal import Decimal

from rentab_core.models import VatMode
from rentab_core.money import ONE


def net_of_vat(price: Decimal, vat_mode: VatMode, vat_rate: Decimal) -> Decimal:
    """Цена без НДС: при ценах «включая НДС» R = P / (1 + v), иначе R = P."""
    if vat_mode == VatMode.INCLUDED:
        return price / (ONE + vat_rate)
    return price
