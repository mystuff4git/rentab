"""Взносы работодателя с оклада (docs/methodology.md, раздел 3.1.1; L-08).

Взносы считаются помесячно за календарный год при постоянном окладе: так
учитываются единая предельная база РФ (нарастающим итогом), тариф МСП с части
выплат сверх 1,5 МРОТ и пределы в МЗП и МРП в РК. Эффективная ставка —
годовые взносы / годовой оклад; она подставляется в карточку сотрудника
(`Salary.employer_contribution_rate`).

Зарплатные налоги работника (НДФЛ, ИПН, ОПВ, ВОСМС) удерживаются из оклада и
в стоимость сотрудника не входят; ОПВ и ВОСМС нужны только для баз СО и
социального налога РК.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import assert_never

from rentab_core.costing import MONTHS_PER_YEAR
from rentab_core.models import (
    DEFAULT_KZ_TAX_PROFILE,
    DEFAULT_RF_TAX_PROFILE,
    AusnProfile,
    CoreModel,
    Country,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    NpdProfile,
    OsnoProfile,
    SocialContributions,
    UsnProfile,
)
from rentab_core.money import ONE, ZERO, decimal_sum
from rentab_core.rates import REGISTRY_YEAR, amount, rate

MONTHS = int(MONTHS_PER_YEAR)


class RfTariff(StrEnum):
    """Тариф взносов РФ с учётом налогового режима."""

    STANDARD = "standard"  # общий: 30% до базы, 15,1% сверх
    SME = "sme"  # МСП: 15% с части выплат сверх 1,5 МРОТ
    ZERO = "zero"  # нулевой (особые категории)
    AUSN = "ausn"  # АУСН: 0%, травматизм — за организацию
    NONE = "none"  # НПД: работников нет


SOCIAL_CONTRIBUTION_TARIFFS: Mapping[SocialContributions, RfTariff] = {
    SocialContributions.STANDARD: RfTariff.STANDARD,
    SocialContributions.SME: RfTariff.SME,
    SocialContributions.ZERO: RfTariff.ZERO,
}
RF_TARIFF_LABELS: Mapping[RfTariff, str] = {
    RfTariff.STANDARD: "общий тариф",
    RfTariff.SME: "тариф МСП",
    RfTariff.ZERO: "нулевой тариф",
    RfTariff.AUSN: "АУСН",
    RfTariff.NONE: "НПД (без работников)",
}


class ContributionItem(CoreModel):
    """Вид взноса работодателя и его сумма за год."""

    name: str
    annual: Decimal


class ContributionBreakdown(CoreModel):
    """Взносы работодателя с оклада за год.

    Attributes:
        country: Страна трудоустройства.
        tariff_label: Тариф или режим, по которому считались взносы.
        gross_monthly: Оклад в месяц до удержаний.
        items: Виды взносов и их суммы за год.
        nominal_rate: Ставка без пределов и минимальных баз — для карточки без оклада.
    """

    country: Country
    tariff_label: str
    gross_monthly: Decimal
    items: tuple[ContributionItem, ...]
    nominal_rate: Decimal

    @property
    def annual_gross(self) -> Decimal:
        """Оклад за год."""
        return self.gross_monthly * MONTHS_PER_YEAR

    @property
    def annual_total(self) -> Decimal:
        """Взносы работодателя за год."""
        return decimal_sum(item.annual for item in self.items)

    @property
    def monthly_average(self) -> Decimal:
        """Взносы в среднем за месяц."""
        return self.annual_total / MONTHS_PER_YEAR

    @property
    def effective_rate(self) -> Decimal:
        """Годовые взносы / годовой оклад; без оклада — номинальная ставка."""
        if self.gross_monthly <= 0:
            return self.nominal_rate
        return self.annual_total / self.annual_gross


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(max(value, low), high)


def rf_tariff(jurisdiction: Jurisdiction | None) -> RfTariff:
    """Тариф взносов РФ по налоговому режиму фирмы (без юрисдикции — общий)."""
    if jurisdiction is None:
        return RfTariff.STANDARD
    profile = jurisdiction.rf or DEFAULT_RF_TAX_PROFILE
    match profile:
        case AusnProfile():
            return RfTariff.AUSN
        case NpdProfile():
            return RfTariff.NONE
        case UsnProfile() | OsnoProfile():
            return SOCIAL_CONTRIBUTION_TARIFFS[profile.social_contributions]
        case _:
            assert_never(profile)


def rf_contributions(
    gross_monthly: Decimal, tariff: RfTariff, year: int = REGISTRY_YEAR
) -> ContributionBreakdown:
    """Взносы работодателя РФ за год (помесячно, с единой предельной базой).

    Args:
        gross_monthly: Оклад в месяц до удержания НДФЛ.
        tariff: Тариф взносов.
        year: Год реестра (для МРОТ).
    """
    label = RF_TARIFF_LABELS[tariff]
    if tariff == RfTariff.NONE:
        return ContributionBreakdown(
            country=Country.RF,
            tariff_label=label,
            gross_monthly=gross_monthly,
            items=(),
            nominal_rate=ZERO,
        )
    if tariff == RfTariff.AUSN:
        ausn = rate(Country.RF, "ausn.social_contributions")
        return ContributionBreakdown(
            country=Country.RF,
            tariff_label=label,
            gross_monthly=gross_monthly,
            items=(ContributionItem(name="Взносы на АУСН", annual=ausn * gross_monthly * MONTHS),),
            nominal_rate=ausn,
        )

    injury = rate(Country.RF, "social_contributions.injury")
    injury_item = ContributionItem(
        name="Взносы на травматизм (НС и ПЗ)", annual=injury * gross_monthly * MONTHS
    )
    if tariff == RfTariff.ZERO:
        zero = rate(Country.RF, "social_contributions.zero")
        return ContributionBreakdown(
            country=Country.RF,
            tariff_label=label,
            gross_monthly=gross_monthly,
            items=(
                ContributionItem(
                    name="Взносы по нулевому тарифу", annual=zero * gross_monthly * MONTHS
                ),
                injury_item,
            ),
            nominal_rate=zero + injury,
        )

    standard = rate(Country.RF, "social_contributions.standard")
    above_base = rate(Country.RF, "social_contributions.above_base")
    base_limit = amount(Country.RF, "social_contributions.base_limit", year)
    if tariff == RfTariff.SME:
        threshold = amount(Country.RF, "social_contributions.sme_threshold", year)
        reduced = rate(Country.RF, "social_contributions.sme")
    else:
        threshold, reduced = gross_monthly, ZERO
    low = min(gross_monthly, threshold)
    high = gross_monthly - low
    main = ZERO
    for month in range(MONTHS):
        paid_before = gross_monthly * month
        within_base = _clamp(base_limit - paid_before, ZERO, low)
        main += standard * within_base + above_base * (low - within_base) + reduced * high
    name = "Взносы по тарифу МСП" if tariff == RfTariff.SME else "Взносы по единому тарифу"
    return ContributionBreakdown(
        country=Country.RF,
        tariff_label=label,
        gross_monthly=gross_monthly,
        items=(ContributionItem(name=name, annual=main), injury_item),
        nominal_rate=standard + injury,
    )


def kz_contributions(
    gross_monthly: Decimal, form: KzForm, year: int = REGISTRY_YEAR
) -> ContributionBreakdown:
    """Взносы работодателя РК за год: СО, ООСМС, ОПВР и социальный налог.

    Args:
        gross_monthly: Начисленный доход работника в месяц.
        form: ТОО (соцналог 6%) или ИП (соцналог 1 МРП за работника).
        year: Год реестра (для МЗП и МРП).
    """
    kz = Country.KZ
    opv_rate = rate(kz, "payroll.employee.opv")
    vosms_rate = rate(kz, "payroll.employee.vosms")
    so_rate = rate(kz, "payroll.employer.so")
    oosms_rate = rate(kz, "payroll.employer.oosms")
    opvr_rate = rate(kz, "payroll.employer.opvr")
    social_tax_rate = rate(kz, "social_tax")
    nominal = oosms_rate + so_rate * (ONE - opv_rate) + opvr_rate
    if form == KzForm.TOO:
        nominal += social_tax_rate * (ONE - opv_rate - vosms_rate)
    label = "ОУР, ТОО" if form == KzForm.TOO else "ОУР, ИП"

    gross = gross_monthly
    if gross <= 0:
        so = oosms = opvr = social_tax = ZERO
    else:
        opv = opv_rate * min(gross, amount(kz, "payroll.opv.base_max", year))
        vosms = vosms_rate * min(gross, amount(kz, "payroll.vosms.base_max", year))
        so = so_rate * _clamp(
            gross - opv,
            amount(kz, "payroll.so.base_min", year),
            amount(kz, "payroll.so.base_max", year),
        )
        oosms = oosms_rate * min(gross, amount(kz, "payroll.oosms.base_max", year))
        opvr = opvr_rate * _clamp(
            gross,
            amount(kz, "payroll.opvr.base_min", year),
            amount(kz, "payroll.opvr.base_max", year),
        )
        if form == KzForm.TOO:
            social_tax = social_tax_rate * max(
                gross - opv - vosms, amount(kz, "social_tax.base_min", year)
            )
        else:
            social_tax = amount(kz, "social_tax.ip_per_employee", year)
    return ContributionBreakdown(
        country=kz,
        tariff_label=label,
        gross_monthly=gross,
        items=(
            ContributionItem(name="Социальные отчисления (СО)", annual=so * MONTHS),
            ContributionItem(name="Отчисления на ОСМС (ООСМС)", annual=oosms * MONTHS),
            ContributionItem(name="Пенсионные взносы работодателя (ОПВР)", annual=opvr * MONTHS),
            ContributionItem(name="Социальный налог", annual=social_tax * MONTHS),
        ),
        nominal_rate=nominal,
    )


def contribution_country(jurisdiction: Jurisdiction | None, country: Country | None) -> Country:
    """Страна, по правилам которой считаются взносы сотрудника.

    В режимах «РФ» и «РК» — страна юрисдикции, в режиме «Оба» — страна
    трудоустройства (по умолчанию РФ), без юрисдикции — РФ.
    """
    if jurisdiction is None:
        return Country.RF
    if jurisdiction.mode == JurisdictionMode.BOTH:
        return country or Country.RF
    return Country(jurisdiction.mode.value)


def employer_contributions(
    gross_monthly: Decimal,
    jurisdiction: Jurisdiction | None,
    country: Country | None = None,
    year: int = REGISTRY_YEAR,
) -> ContributionBreakdown:
    """Взносы работодателя с оклада по юрисдикции фирмы.

    - РФ, УСН и ОСНО — по выбранному тарифу (общий, МСП или нулевой);
    - РФ, АУСН — тариф АУСН;
    - РФ, НПД — 0: у самозанятого нет работодателя;
    - РК — СО, ООСМС, ОПВР и социальный налог по форме (ТОО или ИП);
    - без юрисдикции — общий тариф РФ.

    Args:
        gross_monthly: Оклад в месяц до удержаний.
        jurisdiction: Юрисдикция фирмы.
        country: Страна трудоустройства сотрудника. Нужна в режиме «Оба»;
            по умолчанию — РФ.
        year: Год реестра.
    """
    if contribution_country(jurisdiction, country) == Country.KZ:
        kz_profile = (jurisdiction.kz if jurisdiction else None) or DEFAULT_KZ_TAX_PROFILE
        return kz_contributions(gross_monthly, kz_profile.form, year)
    return rf_contributions(gross_monthly, rf_tariff(jurisdiction), year)


def employer_contribution_rate(
    jurisdiction: Jurisdiction | None,
    country: Country | None = None,
    gross_salary: Decimal = ZERO,
) -> Decimal:
    """Эффективная ставка взносов работодателя с оклада, доля.

    При нулевом окладе — номинальная ставка (без пределов и минимальных баз).
    Аргументы — как у `employer_contributions`.
    """
    return employer_contributions(gross_salary, jurisdiction, country).effective_rate
