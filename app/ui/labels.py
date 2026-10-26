"""Русские подписи перечислений ядра и формат чисел интерфейса.

Проценты в подписях берутся из реестра ставок ядра, а не пишутся вручную.
Числа — «1 234 567 ₽» и подпись режима (L-18) — функции ядра
(`rentab_core.formatting`, `rentab_core.labels`): КП и смета пишут суммы теми
же функциями. Исключение — дельты `st.metric`: Streamlit выбирает стрелку и
цвет по ведущему ASCII-минусу.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from rentab_core.analysis.compare import PricingKind
from rentab_core.analysis.dashboard import Profitability
from rentab_core.analysis.plan_fact import BillingRule
from rentab_core.analysis.sensitivity import Factor, TaxBranch, TornadoNote
from rentab_core.errors import FixedPriceError
from rentab_core.formatting import DASH as DASH
from rentab_core.formatting import HOURS_QUANTUM as HOURS_QUANTUM
from rentab_core.formatting import LABEL_QUANTUM, PCT_QUANTUM, rate_text, sign_prefix
from rentab_core.formatting import MINUS as MINUS
from rentab_core.formatting import NBSP as NBSP
from rentab_core.formatting import hours_label as hours_label
from rentab_core.formatting import money as money
from rentab_core.formatting import money_rate as money_rate
from rentab_core.formatting import number_label as number_label
from rentab_core.formatting import number_text as number_text
from rentab_core.formatting import pct_label as pct_label
from rentab_core.formatting import points_label as points_label
from rentab_core.formatting import signed_hours as signed_hours
from rentab_core.formatting import signed_money as signed_money
from rentab_core.formatting import signed_pct as signed_pct
from rentab_core.labels import employee_title as employee_title
from rentab_core.labels import kz_rate_pct as kz_rate_pct
from rentab_core.labels import kz_vat_suffix as kz_vat_suffix
from rentab_core.labels import regime_label as regime_label
from rentab_core.labels import rf_rate_pct as rf_rate_pct
from rentab_core.models import (
    Country,
    DisbursementKind,
    JurisdictionMode,
    KzForm,
    KzVat,
    LegalForm,
    NpdClientType,
    SocialContributions,
    TaxProfile,
    UsnObject,
    UsnVat,
    VatMode,
)
from rentab_core.money import PERCENT, round_to
from rentab_core.rates import amount, rate
from rentab_core.results import StageFlag
from rentab_core.tax.eligibility import Severity

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
# Те же категории в таблице расходов «Результата».
EXPENSE_TYPE_LABELS: Mapping[DisbursementKind, str] = {
    DisbursementKind.BILLABLE: "Возмещается клиентом",
    DisbursementKind.OWN: "За счёт фирмы",
}
# Светофор: цвет всегда вместе со значком и подписью.
STAGE_FLAG_LABELS: Mapping[StageFlag, str] = {
    StageFlag.GREEN: "Норма",
    StageFlag.YELLOW: "Низкая маржа",
    StageFlag.RED: "Убыточный",
}
STAGE_FLAG_ICONS: Mapping[StageFlag, str] = {
    StageFlag.GREEN: "●",
    StageFlag.YELLOW: "▲",
    StageFlag.RED: "✕",
}
# Цвет бейджа Streamlit (`:green-badge[…]`): тема задаёт его для светлой и тёмной.
STAGE_FLAG_BADGE_COLORS: Mapping[StageFlag, str] = {
    StageFlag.GREEN: "green",
    StageFlag.YELLOW: "orange",
    StageFlag.RED: "red",
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
PROFITABILITY_MESSAGES: Mapping[Profitability, str] = {
    Profitability.PROFITABLE: "Проект рентабелен",
    Profitability.BREAKEVEN: "Проект в точке безубыточности",
    Profitability.LOSS: "Проект убыточен",
}


def rate_pct(country: Country, key: str) -> str:
    """Ставка из реестра как процент без лишних нулей: «15,1»."""
    return rate_text(rate(country, key))


def registry_amount(country: Country, key: str) -> str:
    """Порог из реестра числом для подписи: «60 000 000», «5»."""
    return number_text(amount(country, key))


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


def fixed_price_error(exc: FixedPriceError, profile: TaxProfile) -> str:
    """Почему цену «издержки + маржа» не рассчитать — с подписью режима интерфейса (L-18)."""
    if exc.target_margin is None or exc.tax_rate is None:
        return str(exc)
    one_decimal = Decimal("0.1")
    total = exc.target_margin + exc.tax_rate
    return (
        f"целевая маржа {pct_label(exc.target_margin, one_decimal)} недостижима при режиме "
        f"«{regime_label(profile)}»: маржа + ставка налога = {pct_label(total, one_decimal)} "
        "≥ 100%."
    )


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


def metric_delta(value: Decimal, symbol: str) -> str | None:
    """Дельта суммы для `st.metric`: «-69 056 ₽»; None — без изменения.

    Streamlit выбирает стрелку и цвет по ведущему ASCII-минусу, поэтому здесь не «−».
    """
    rounded = round_to(value, LABEL_QUANTUM)
    if rounded == 0:
        return None
    return f"{sign_prefix(rounded, '-')}{number_text(abs(rounded))}{NBSP}{symbol}"


def metric_points(delta: Decimal) -> str | None:
    """Дельта доли для `st.metric` в процентных пунктах; None — без изменения."""
    rounded = round_to(delta * PERCENT, PCT_QUANTUM)
    if rounded == 0:
        return None
    return f"{sign_prefix(rounded, '-')}{number_label(abs(rounded), PCT_QUANTUM)}{NBSP}п.{NBSP}п."


def flag_badge(flag: StageFlag) -> str:
    """Статус светофора для Markdown: бейдж темы со значком и подписью."""
    color = STAGE_FLAG_BADGE_COLORS[flag]
    return f":{color}-badge[{STAGE_FLAG_ICONS[flag]} {STAGE_FLAG_LABELS[flag]}]"
