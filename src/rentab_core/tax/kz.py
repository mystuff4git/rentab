"""Налоги РК: общеустановленный режим (перенос `TaxCalculator._calc_kz` v0.3)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from rentab_core.models import Country, KzForm, KzOurProfile, KzVat
from rentab_core.money import ZERO
from rentab_core.rates import rate
from rentab_core.results import TaxResult
from rentab_core.tax.labels import percent

FORM_RATE_KEYS: Mapping[KzForm, str] = {
    KzForm.TOO: "our.cit_too",
    KzForm.IP: "our.iit_ip",
}
FORM_LABELS: Mapping[KzForm, str] = {
    KzForm.TOO: "ТОО (КПН)",
    KzForm.IP: "ИП (ИПН)",
}
VAT_KEYS: Mapping[KzVat, str] = {
    KzVat.VAT_16: "vat_16",
}


def calculate(
    profile: KzOurProfile,
    *,
    revenue: Decimal,
    taxable_base: Decimal,
    expenses: Decimal,
) -> TaxResult:
    """Налоги по ОУР РК: КПН (ТОО) или ИПН (ИП) с прибыли и НДС с базы.

    KNOWN-BUG L-08 → фаза 3: ставки v0.3, новый НК РК 2026 не учтён.
    """
    income_rate = rate(Country.KZ, FORM_RATE_KEYS[profile.form])
    profit = max(taxable_base - expenses, ZERO)
    income_tax = profit * income_rate
    vat_rate = rate(Country.KZ, VAT_KEYS[profile.vat]) if profile.vat != KzVat.NONE else ZERO
    vat = taxable_base * vat_rate

    label = f"ОУР — {FORM_LABELS[profile.form]} {percent(income_rate)}%"
    if vat_rate > 0:
        label += f" + НДС {percent(vat_rate)}%"

    return TaxResult(
        regime_label=label,
        revenue=revenue,
        taxable_base=taxable_base,
        income_tax_base=profit,
        income_tax_rate=income_rate,
        income_tax=income_tax,
        vat_rate=vat_rate,
        vat=vat,
        total_tax=income_tax + vat,
    )
