"""Русские подписи перечислений ядра для интерфейса (как в v0.3).

Проценты в подписях берутся из реестра ставок ядра, а не пишутся вручную.
"""

from __future__ import annotations

from collections.abc import Mapping

from rentab_core.analysis.dashboard import Profitability
from rentab_core.models import (
    Country,
    DisbursementKind,
    JurisdictionMode,
    KzForm,
    KzVat,
    NpdClientType,
    SocialContributions,
    UsnObject,
    UsnVat,
)
from rentab_core.rates import rate
from rentab_core.results import StageFlag
from rentab_core.tax.labels import percent

JURISDICTION_LABELS: Mapping[JurisdictionMode, str] = {
    JurisdictionMode.RF: "Только РФ",
    JurisdictionMode.KZ: "Только РК",
    JurisdictionMode.BOTH: "Оба (РФ + РК)",
}
COUNTRY_LABELS: Mapping[Country, str] = {
    Country.RF: "🇷🇺 Россия",
    Country.KZ: "🇰🇿 Казахстан",
}
PATENT_OFFICE_LABELS: Mapping[Country, str] = {
    Country.RF: "🇷🇺 Роспатент",
    Country.KZ: "🇰🇿 Казпатент",
}
RF_REGIME_LABELS: Mapping[str, str] = {
    "usn": "УСН",
    "ausn": "АУСН",
    "osno": "ОСНО",
    "npd": "НПД (самозанятость)",
}
PRICING_LABELS: Mapping[str, str] = {
    "hourly": "Биллинговая (часы × ставка)",
    "fixed": "Фиксированная (издержки + маржа)",
}
# Категории произвольного расхода в форме «Проекта» (02_Проект.py:82–86).
CUSTOM_EXPENSE_LABELS: Mapping[DisbursementKind, str] = {
    DisbursementKind.OWN: "Собственный расход (не перевыставляется клиенту)",
    DisbursementKind.BILLABLE: "Перевыставляемый клиенту (помимо пошлин)",
    DisbursementKind.EXTRA: "Прочее",
}
# Те же категории в таблице расходов «Дашборда» (03_Дашборд.py:64–69).
EXPENSE_TYPE_LABELS: Mapping[DisbursementKind, str] = {
    DisbursementKind.BILLABLE: "Перевыставляемый клиенту",
    DisbursementKind.OWN: "За счёт фирмы",
    DisbursementKind.EXTRA: "Прочие проектные расходы",
}
STAGE_FLAG_LABELS: Mapping[StageFlag, str] = {
    StageFlag.GREEN: "🟢 Норма",
    StageFlag.YELLOW: "🟡 Низкая маржа",
    StageFlag.RED: "🔴 Убыточный",
}
STAGE_FLAG_BG_COLORS: Mapping[StageFlag, str] = {
    StageFlag.GREEN: "",
    StageFlag.YELLOW: "background-color: #fef3c7",
    StageFlag.RED: "background-color: #fee2e2",
}
PROFITABILITY_MESSAGES: Mapping[Profitability, str] = {
    Profitability.PROFITABLE: "✅ Проект рентабелен",
    Profitability.BREAKEVEN: "⚠️ Проект в точке безубыточности",
}


def rf_rate_pct(key: str) -> str:
    """Ставка РФ из реестра как целый процент: «6»."""
    return percent(rate(Country.RF, key))


def kz_rate_pct(key: str) -> str:
    """Ставка РК из реестра как целый процент: «20»."""
    return percent(rate(Country.KZ, key))


def usn_object_label(value: UsnObject) -> str:
    """Объект УСН со ставкой."""
    if value == UsnObject.INCOME:
        return f"Доходы ({rf_rate_pct('usn.income')}%)"
    return f"Доходы − расходы ({rf_rate_pct('usn.income_minus_expenses')}%)"


def vat_label(value: UsnVat | KzVat) -> str:
    """Вариант НДС."""
    return "Без НДС" if value.value == "none" else value.value


def social_contributions_label(value: SocialContributions) -> str:
    """Тариф страховых взносов."""
    if value == SocialContributions.STANDARD:
        return f"Стандарт ({rf_rate_pct('social_contributions.standard')}%)"
    return f"АУСН ({rf_rate_pct('social_contributions.zero')}%)"


def npd_client_label(value: NpdClientType) -> str:
    """Тип клиентов самозанятого со ставкой."""
    if value == NpdClientType.INDIVIDUAL:
        return f"Физлица ({rf_rate_pct('npd.individual')}%)"
    return f"Юрлица / ИП ({rf_rate_pct('npd.legal_entity')}%)"


def kz_form_label(value: KzForm) -> str:
    """Форма организации в РК со ставкой."""
    if value == KzForm.TOO:
        return f"ТОО (КПН {kz_rate_pct('our.cit_too')}%)"
    return f"ИП (ИПН {kz_rate_pct('our.iit_ip')}%)"
