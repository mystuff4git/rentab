"""Взносы работодателя с ФОТ по юрисдикции (перенос `get_employer_contribution_rate` v0.3).

Ставка подставляется по умолчанию в калькулятор себестоимости часа на
странице «Настройка». Зарплатные налоги работника (НДФЛ, ИПН и пр.) ядро
пока не считает: в v0.3 метод `add_payroll_taxes` нигде не вызывался (L-14),
полная модель — фаза 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import assert_never

from rentab_core.models import (
    DEFAULT_RF_TAX_PROFILE,
    AusnProfile,
    Country,
    Jurisdiction,
    JurisdictionMode,
    NpdProfile,
    OsnoProfile,
    SocialContributions,
    UsnProfile,
)
from rentab_core.money import ZERO, decimal_sum
from rentab_core.rates import rate

SOCIAL_CONTRIBUTION_KEYS: Mapping[SocialContributions, str] = {
    SocialContributions.STANDARD: "social_contributions.standard",
    SocialContributions.ZERO: "social_contributions.zero",
}
# Взносы работодателя РК сверх оклада: ООСМС + СО + ОПВР.
KZ_EMPLOYER_PAYROLL_KEYS: tuple[str, ...] = (
    "payroll.employer.oosms",
    "payroll.employer.so",
    "payroll.employer.opvr",
)


def employer_contribution_rate(jurisdiction: Jurisdiction | None) -> Decimal:
    """Суммарная ставка взносов работодателя с ФОТ, доля.

    - РФ, УСН и ОСНО — по выбранному тарифу (стандартный или нулевой);
    - РФ, АУСН — ставка взносов АУСН;
    - РФ, НПД — 0: у самозанятого нет работодателя;
    - РК — ООСМС + СО + ОПВР;
    - без юрисдикции — стандартный тариф РФ.

    В режиме «Оба» берётся РФ (KNOWN-BUG L-06 → фаза 2).
    """
    if jurisdiction is None:
        return rate(Country.RF, SOCIAL_CONTRIBUTION_KEYS[SocialContributions.STANDARD])
    if jurisdiction.mode == JurisdictionMode.KZ:
        return decimal_sum(rate(Country.KZ, key) for key in KZ_EMPLOYER_PAYROLL_KEYS)

    profile = jurisdiction.rf or DEFAULT_RF_TAX_PROFILE
    match profile:
        case AusnProfile():
            return rate(Country.RF, "ausn.social_contributions")
        case NpdProfile():
            return ZERO
        case UsnProfile() | OsnoProfile():
            return rate(Country.RF, SOCIAL_CONTRIBUTION_KEYS[profile.social_contributions])
        case _:
            assert_never(profile)
