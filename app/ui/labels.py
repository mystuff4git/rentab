"""Русские подписи перечислений ядра для интерфейса (как в v0.3).

Проценты в подписях берутся из реестра ставок ядра, а не пишутся вручную.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from rentab_core.analysis.compare import PricingKind
from rentab_core.analysis.dashboard import Profitability
from rentab_core.analysis.plan_fact import BillingRule
from rentab_core.analysis.sensitivity import Factor, TaxBranch, TornadoNote
from rentab_core.models import (
    AusnProfile,
    Country,
    DisbursementKind,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    LegalForm,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    SocialContributions,
    TaxProfile,
    UsnObject,
    UsnProfile,
    UsnVat,
    VatMode,
)
from rentab_core.money import PERCENT, format_number, round_to
from rentab_core.rates import amount, rate
from rentab_core.results import StageFlag
from rentab_core.tax.eligibility import Severity
from rentab_core.tax.labels import percent
from rentab_core.tax.ru import USN_VAT_KEYS

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
# Модели цены (ключи — ui.state.PRICING_*).
PRICING_LABELS: Mapping[str, str] = {
    "hourly": "Почасовая (часы × ставка)",
    "capped": "Почасовая с потолком",
    "fixed": "Фикс: издержки + маржа",
    "agreed": "Фикс: согласованная цена",
}
VAT_MODE_LABELS: Mapping[VatMode, str] = {
    VatMode.ON_TOP: "Без НДС (НДС начисляется сверху)",
    VatMode.INCLUDED: "Включая НДС",
}
# Способ задать полную стоимость сотрудника в месяц (ключи — Compensation.kind).
COMPENSATION_LABELS: Mapping[str, str] = {
    "salary": "Оклад + взносы",
    "monthly": "Сумма в месяц (нормативное вознаграждение партнёра)",
}
# Категории произвольного расхода в форме «Проекта». «Прочее» v0.3 — собственный
# расход (L-12).
CUSTOM_EXPENSE_LABELS: Mapping[DisbursementKind, str] = {
    DisbursementKind.OWN: "Собственный расход фирмы (курьер, перевод, командировка…)",
    DisbursementKind.BILLABLE: "Возмещается клиентом (помимо пошлин)",
}
# Те же категории в таблице расходов «Дашборда».
EXPENSE_TYPE_LABELS: Mapping[DisbursementKind, str] = {
    DisbursementKind.BILLABLE: "Возмещается клиентом",
    DisbursementKind.OWN: "За счёт фирмы",
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
LEGAL_FORM_LABELS: Mapping[LegalForm, str] = {
    LegalForm.ORGANIZATION: "Организация (ООО и др.)",
    LegalForm.ENTREPRENEUR: "ИП",
    LegalForm.INDIVIDUAL: "Физлицо без статуса ИП",
}
SEVERITY_ICONS: Mapping[Severity, str] = {
    Severity.BLOCKER: "⛔",
    Severity.WARNING: "⚠️",
    Severity.INFO: "ℹ️",
}
# Пометка позиции справочника, не сверенной с первоисточником полностью.
NEEDS_REVIEW_MARK = "⚠️"
# Шаг округления сумм в подписях: рубль (тенге); часов — десятая.
LABEL_QUANTUM = Decimal(1)
HOURS_QUANTUM = Decimal("0.1")
PROFITABILITY_MESSAGES: Mapping[Profitability, str] = {
    Profitability.PROFITABLE: "✅ Проект рентабелен",
    Profitability.BREAKEVEN: "⚠️ Проект в точке безубыточности",
}


def rf_rate_pct(key: str) -> str:
    """Ставка РФ из реестра как целый процент: «6»."""
    return percent(rate(Country.RF, key))


def rate_pct(country: Country, key: str) -> str:
    """Ставка из реестра как процент без лишних нулей: «15,1»."""
    return format_number(rate(country, key) * PERCENT)


def money(value: Decimal, symbol: str) -> str:
    """Сумма для подписи: «1 234 567 ₽», округление до рубля (тенге)."""
    return f"{format_number(round_to(value, LABEL_QUANTUM))} {symbol}"


def registry_amount(country: Country, key: str) -> str:
    """Порог из реестра числом для подписи: «60 000 000», «5»."""
    return format_number(amount(country, key))


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
    rf = Country.RF
    if value == SocialContributions.STANDARD:
        return (
            f"Общий ({rate_pct(rf, 'social_contributions.standard')}%, сверх базы "
            f"{rate_pct(rf, 'social_contributions.above_base')}%)"
        )
    if value == SocialContributions.SME:
        return f"МСП из перечня ({rate_pct(rf, 'social_contributions.sme')}% сверх 1,5 МРОТ)"
    return "Нулевой (особые категории)"


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


def kz_vat_suffix(value: KzVat) -> str:
    """НДС в подписи режима РК."""
    if value == KzVat.NONE:
        return " без НДС"
    return f" + НДС {kz_rate_pct('vat_16')}%"


def regime_label(profile: TaxProfile) -> str:
    """Режим с вариантом НДС для матрицы сравнения: «УСН «доходы» 6% + НДС 5%»."""
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


# Аналитика (страницы «Чувствительность», «Сравнение», «План–факт»).
TAX_BRANCH_LABELS: Mapping[TaxBranch, str] = {
    TaxBranch.INCOME: "налог с выручки",
    TaxBranch.CONTRIBUTIONS: "налог за вычетом взносов",
    TaxBranch.DEDUCTION_CAP: "уменьшение на взносы упёрлось в предел",
    TaxBranch.PROFIT: "налог на прибыль",
    TaxBranch.MIN_TAX: "минимальный налог",
}
FACTOR_LABELS: Mapping[Factor, str] = {
    Factor.HOURS: "Часы",
    Factor.BILLING_RATES: "Ставки для клиента",
    Factor.UTILIZATION: "Утилизация",
    Factor.OVERHEADS: "Накладные",
    Factor.FX: "Курс",
    Factor.PRICE: "Цена",
}
TORNADO_NOTE_LABELS: Mapping[TornadoNote, str] = {
    TornadoNote.FIXED_PRICE: "фикс: ставки влияют только на распределение выручки по этапам",
    TornadoNote.CAP_REACHED: "выручка упирается в потолок",
    TornadoNote.UTILIZATION_CAPPED: "утилизация части сотрудников упирается в 100%",
    TornadoNote.TRANSIT_ONLY: "в этой валюте только возмещаемые расходы",
}
BILLING_RULE_LABELS: Mapping[BillingRule, str] = {
    BillingRule.BY_FACT: "почасовая модель: выставляются фактические часы по ставкам",
    BillingRule.CAPPED: "фактические часы по ставкам, но не больше потолка",
    BillingRule.AGREED: "согласованная фикс-цена",
    BillingRule.PLAN_PRICE: "цена «издержки + маржа» по плану — она уже предложена клиенту",
    BillingRule.MANUAL: "сумма, введённая вручную",
}
PRICING_KIND_LABELS: Mapping[PricingKind, str] = {
    PricingKind.HOURLY: "Почасовая",
    PricingKind.CAPPED: "С потолком",
    PricingKind.COST_PLUS: "Издержки + маржа",
    PricingKind.AGREED: "Согласованная цена",
}
# Показатели матрицы сравнения: атрибут сметы → подпись.
COMPARE_METRICS: Mapping[str, str] = {
    "nne": "NNE",
    "margin": "Маржа",
    "revenue": "Цена без НДС",
    "total_client": "К оплате",
}


def hours_label(value: Decimal) -> str:
    """Часы для подписи: «159,6 ч»."""
    return f"{format_number(round_to(value, HOURS_QUANTUM))} ч"


def signed_hours(value: Decimal) -> str:
    """Часы со знаком: «+55 ч», «−0,5 ч»."""
    rounded = round_to(value, HOURS_QUANTUM)
    sign = "+" if rounded > 0 else ("−" if rounded < 0 else "")
    return f"{sign}{format_number(abs(rounded))} ч"


def signed_money(value: Decimal, symbol: str) -> str:
    """Сумма со знаком: «+5 650 ₽», «−227 700 ₽»."""
    rounded = round_to(value, LABEL_QUANTUM)
    sign = "+" if rounded > 0 else ("−" if rounded < 0 else "")
    return f"{sign}{format_number(abs(rounded))} {symbol}"


def pct_label(share: Decimal | None, quantum: Decimal = Decimal("0.01")) -> str:
    """Доля в процентах: «75,17%»; None — «—»."""
    if share is None:
        return "—"
    return f"{format_number(round_to(share * PERCENT, quantum))}%"


def signed_pct(share: Decimal, quantum: Decimal = Decimal(1)) -> str:
    """Доля в процентах со знаком: «+399%»."""
    rounded = round_to(share * PERCENT, quantum)
    sign = "+" if rounded > 0 else ("−" if rounded < 0 else "")
    return f"{sign}{format_number(abs(rounded))}%"


def points_label(delta: Decimal) -> str:
    """Разница долей в процентных пунктах: «−75,90 п. п.»."""
    rounded = round_to(delta * PERCENT, Decimal("0.01"))
    sign = "+" if rounded > 0 else ("−" if rounded < 0 else "")
    return f"{sign}{format_number(abs(rounded))} п. п."
