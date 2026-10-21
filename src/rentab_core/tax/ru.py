"""Налоги РФ: УСН, АУСН, ОСНО, НПД (перенос `TaxCalculator._calc_rf` v0.3)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import assert_never

from rentab_core.models import (
    AusnProfile,
    Country,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import ZERO
from rentab_core.rates import rate
from rentab_core.results import TaxResult
from rentab_core.tax.labels import percent

USN_RATE_KEYS: Mapping[UsnObject, str] = {
    UsnObject.INCOME: "usn.income",
    UsnObject.INCOME_MINUS_EXPENSES: "usn.income_minus_expenses",
}
USN_VAT_KEYS: Mapping[UsnVat, str] = {
    UsnVat.VAT_5: "usn.vat_5",
    UsnVat.VAT_7: "usn.vat_7",
}
NPD_RATE_KEYS: Mapping[NpdClientType, str] = {
    NpdClientType.INDIVIDUAL: "npd.individual",
    NpdClientType.LEGAL_ENTITY: "npd.legal_entity",
}


def _rate(key: str) -> Decimal:
    return rate(Country.RF, key)


def calculate(
    profile: UsnProfile | AusnProfile | OsnoProfile | NpdProfile,
    *,
    revenue: Decimal,
    taxable_base: Decimal,
    expenses: Decimal,
) -> TaxResult:
    """Налоги по режиму РФ.

    Args:
        profile: Режим РФ.
        revenue: Сумма, полученная от клиента.
        taxable_base: revenue за вычетом перевыставленных пошлин.
        expenses: Расходы для базы «доходы − расходы».
    """
    vat_rate = ZERO
    match profile:
        case UsnProfile():
            income_rate = _rate(USN_RATE_KEYS[profile.tax_object])
            if profile.tax_object == UsnObject.INCOME:
                income_tax_base = taxable_base
            else:
                # KNOWN-BUG L-08 → фаза 3: минимальный налог 1% не применяется.
                income_tax_base = max(taxable_base - expenses, ZERO)
            if profile.vat != UsnVat.NONE:
                vat_rate = _rate(USN_VAT_KEYS[profile.vat])
            # KNOWN-BUG L-18 → фаза 6: в подпись попадает ключ объекта («УСН income (6%)»).
            label = f"УСН {profile.tax_object.value} ({percent(income_rate)}%)"
        case OsnoProfile():
            income_rate = _rate("osno.profit")
            income_tax_base = max(taxable_base - expenses, ZERO)
            vat_rate = _rate("osno.vat")
            label = f"ОСНО (прибыль {percent(income_rate)}% + НДС {percent(vat_rate)}%)"
        case AusnProfile():
            income_rate = _rate("ausn.income")
            income_tax_base = taxable_base
            label = (
                f"АУСН — Автоматизированная УСН ({percent(income_rate)}%, без страховых взносов)"
            )
        case NpdProfile():
            income_rate = _rate(NPD_RATE_KEYS[profile.client_type])
            income_tax_base = taxable_base
            label = f"НПД ({percent(income_rate)}%)"
        case _:
            assert_never(profile)

    income_tax = income_tax_base * income_rate
    vat = taxable_base * vat_rate
    return TaxResult(
        regime_label=label,
        revenue=revenue,
        taxable_base=taxable_base,
        income_tax_base=income_tax_base,
        income_tax_rate=income_rate,
        income_tax=income_tax,
        vat_rate=vat_rate,
        vat=vat,
        total_tax=income_tax + vat,
    )
