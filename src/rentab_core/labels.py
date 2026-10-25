"""Человекочитаемые подписи налоговых режимов (L-18) для документов и интерфейса.

Подпись ядра `Estimate.regime_label` осталась как в v0.3 («УСН income (6%)»):
она входит в снимки ядра и мост «было/стало». Документы (КП, смета) и
интерфейс показывают подпись `regime_label` по налоговому профилю:
«УСН «доходы» 6% + НДС 5%». Ставки берутся из реестра, а не пишутся вручную.
"""

from __future__ import annotations

from rentab_core.models import (
    AusnProfile,
    Country,
    KzForm,
    KzOurProfile,
    KzVat,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    TaxProfile,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.rates import rate
from rentab_core.tax.labels import percent
from rentab_core.tax.ru import USN_VAT_KEYS


def rf_rate_pct(key: str) -> str:
    """Ставка РФ из реестра как целый процент: «6»."""
    return percent(rate(Country.RF, key))


def kz_rate_pct(key: str) -> str:
    """Ставка РК из реестра как целый процент: «20»."""
    return percent(rate(Country.KZ, key))


def kz_vat_suffix(value: KzVat) -> str:
    """НДС в подписи режима РК."""
    if value == KzVat.NONE:
        return " без НДС"
    return f" + НДС {kz_rate_pct('vat_16')}%"


def regime_label(profile: TaxProfile) -> str:
    """Режим с вариантом НДС: «УСН «доходы» 6% + НДС 5%», «ОСНО 25% + НДС 22%»."""
    if isinstance(profile, UsnProfile):
        if profile.tax_object == UsnObject.INCOME:
            name = f"УСН «доходы» {rf_rate_pct('usn.income')}%"
        else:
            name = f"УСН «доходы − расходы» {rf_rate_pct('usn.income_minus_expenses')}%"
        if profile.vat == UsnVat.NONE:
            return f"{name} без НДС"
        return f"{name} + НДС {rf_rate_pct(USN_VAT_KEYS[profile.vat])}%"
    if isinstance(profile, AusnProfile):
        return f"АУСН {rf_rate_pct('ausn.income')}%"
    if isinstance(profile, NpdProfile):
        if profile.client_type == NpdClientType.INDIVIDUAL:
            return f"НПД {rf_rate_pct('npd.individual')}% (физлица)"
        return f"НПД {rf_rate_pct('npd.legal_entity')}% (юрлица)"
    if isinstance(profile, OsnoProfile):
        return f"ОСНО {rf_rate_pct('osno.profit')}% + НДС {rf_rate_pct('osno.vat')}%"
    assert isinstance(profile, KzOurProfile)
    if profile.form == KzForm.TOO:
        name = f"ОУР РК, ТОО (КПН {kz_rate_pct('our.cit_too')}%)"
    else:
        name = f"ОУР РК, ИП (ИПН {kz_rate_pct('our.iit_ip')}%)"
    return name + kz_vat_suffix(profile.vat)
