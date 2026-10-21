"""Почасовая модель и модель с потолком (docs/methodology.md, разделы 7.1–7.2).

    почасовая:   R = Σ_a b_i(a) × h_a
    с потолком:  R = min(Σ_a b_i(a) × h_a, cap)

Ставки и потолок указаны в режиме НДС проекта; R — без НДС.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from rentab_core.models import VatMode
from rentab_core.money import decimal_sum
from rentab_core.pricing import net_of_vat
from rentab_core.results import EstimateLine


def shadow_value(lines: Iterable[EstimateLine]) -> Decimal:
    """Теневая стоимость: Σ ставка для клиента × часы (в режиме НДС проекта)."""
    return decimal_sum(line.shadow_value for line in lines)


def hourly_revenue(shadow: Decimal, vat_mode: VatMode, vat_rate: Decimal) -> Decimal:
    """Выручка почасовой модели без НДС."""
    return net_of_vat(shadow, vat_mode, vat_rate)


def capped_revenue(shadow: Decimal, cap: Decimal, vat_mode: VatMode, vat_rate: Decimal) -> Decimal:
    """Выручка с потолком без НДС: min(теневая стоимость, потолок)."""
    return net_of_vat(min(shadow, cap), vat_mode, vat_rate)
