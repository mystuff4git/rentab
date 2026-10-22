"""Налоги РК: общеустановленный режим — параметры для расчёта проекта."""

from __future__ import annotations

from collections.abc import Mapping

from rentab_core.models import Country, KzForm, KzOurProfile, KzVat
from rentab_core.money import ZERO
from rentab_core.rates import rate
from rentab_core.results import TaxBase, TaxTerms
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


def terms(profile: KzOurProfile) -> TaxTerms:
    """ОУР РК: КПН (ТОО) или ИПН (ИП) с прибыли, НДС сверху.

    Ставки — по новому НК РК 2026 (реестр `kz_2026.yaml`). ИПН ИП — 10%; ставка
    15% с дохода сверх 230 000 МРП не моделируется, об этом предупреждает
    `tax.eligibility`.
    """
    income_rate = rate(Country.KZ, FORM_RATE_KEYS[profile.form])
    vat_rate = rate(Country.KZ, VAT_KEYS[profile.vat]) if profile.vat != KzVat.NONE else ZERO
    label = f"ОУР — {FORM_LABELS[profile.form]} {percent(income_rate)}%"
    if vat_rate > 0:
        label += f" + НДС {percent(vat_rate)}%"
    return TaxTerms(regime_label=label, base=TaxBase.PROFIT, rate=income_rate, vat_rate=vat_rate)
