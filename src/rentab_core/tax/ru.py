"""Налоги РФ: УСН, АУСН, ОСНО, НПД — параметры для расчёта проекта."""

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
from rentab_core.results import TaxBase, TaxTerms
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
# Минимальный налог УСН «доходы − расходы», доля доходов (п. 6 ст. 346.18 НК РФ).
USN_MIN_TAX_KEY = "usn.min_tax"
# Предел уменьшения налога УСН «доходы» на взносы (п. 3.1 ст. 346.21 НК РФ).
USN_DEDUCTION_CAP_KEY = "usn.contributions_deduction_cap"


def _rate(key: str) -> Decimal:
    return rate(Country.RF, key)


def terms(profile: UsnProfile | AusnProfile | OsnoProfile | NpdProfile) -> TaxTerms:
    """Параметры режима РФ: база, ставка, минимальный налог и НДС."""
    match profile:
        case UsnProfile():
            income_rate = _rate(USN_RATE_KEYS[profile.tax_object])
            vat_rate = _rate(USN_VAT_KEYS[profile.vat]) if profile.vat != UsnVat.NONE else ZERO
            # KNOWN-BUG L-18 → фаза 6: в подпись попадает ключ объекта («УСН income (6%)»).
            label = f"УСН {profile.tax_object.value} ({percent(income_rate)}%)"
            if vat_rate > 0:
                label += f" + НДС {percent(vat_rate)}%"
            if profile.tax_object == UsnObject.INCOME:
                return TaxTerms(
                    regime_label=label,
                    base=TaxBase.INCOME,
                    rate=income_rate,
                    vat_rate=vat_rate,
                    contributions_deduction_cap=_rate(USN_DEDUCTION_CAP_KEY),
                )
            return TaxTerms(
                regime_label=label,
                base=TaxBase.PROFIT,
                rate=income_rate,
                min_rate=_rate(USN_MIN_TAX_KEY),
                vat_rate=vat_rate,
            )
        case OsnoProfile():
            income_rate = _rate("osno.profit")
            vat_rate = _rate("osno.vat")
            return TaxTerms(
                regime_label=f"ОСНО (прибыль {percent(income_rate)}% + НДС {percent(vat_rate)}%)",
                base=TaxBase.PROFIT,
                rate=income_rate,
                vat_rate=vat_rate,
            )
        case AusnProfile():
            income_rate = _rate("ausn.income")
            return TaxTerms(
                regime_label=(
                    f"АУСН — Автоматизированная УСН ({percent(income_rate)}%, "
                    "без страховых взносов)"
                ),
                base=TaxBase.INCOME,
                rate=income_rate,
            )
        case NpdProfile():
            income_rate = _rate(NPD_RATE_KEYS[profile.client_type])
            return TaxTerms(
                regime_label=f"НПД ({percent(income_rate)}%)", base=TaxBase.INCOME, rate=income_rate
            )
        case _:
            assert_never(profile)
