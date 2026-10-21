"""Показатели команды проекта по Майстеру: средневзвешенная ставка и рычаг."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from rentab_core.models import JUNIOR_ROLES, SENIOR_ROLES
from rentab_core.money import ZERO, decimal_sum
from rentab_core.results import EstimateLine


def blended_rate(lines: Sequence[EstimateLine]) -> Decimal:
    """Средневзвешенная ставка: Σ ставка × часы / Σ часы; 0, если часов нет."""
    total_hours = decimal_sum(line.hours for line in lines)
    if total_hours == 0:
        return ZERO
    return decimal_sum(line.shadow_value for line in lines) / total_hours


def leverage(lines: Sequence[EstimateLine]) -> Decimal:
    """Коэффициент рычага: часы младших / часы старших; 0, если старших нет."""
    senior_hours = decimal_sum(line.hours for line in lines if line.role in SENIOR_ROLES)
    junior_hours = decimal_sum(line.hours for line in lines if line.role in JUNIOR_ROLES)
    if senior_hours == 0:
        return ZERO
    return junior_hours / senior_hours
