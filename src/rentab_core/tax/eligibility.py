"""Применимость налоговых режимов (docs/methodology.md, раздел 15; L-08).

Валидатор не меняет расчёт: он сообщает, может ли фирма применять режим и
настройку НДС, а интерфейс показывает причину. Пороги — в реестре ставок,
ссылки на нормы — в `docs/rates_verification.md`.

Важность сообщения:

- `BLOCKER` — режим недоступен или настройка НДС противоречит закону;
- `WARNING` — режим доступен, но модель что-то упрощает или нужна проверка;
- `INFO` — не хватает данных для проверки или есть условие, которое нужно
  проверить самому (регион, срок эксперимента).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from enum import StrEnum
from typing import assert_never

from rentab_core.models import (
    AusnProfile,
    CoreModel,
    Country,
    Firm,
    FirmFacts,
    KzForm,
    KzOurProfile,
    KzVat,
    LegalForm,
    NpdProfile,
    OsnoProfile,
    TaxProfile,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import CURRENCY_SYMBOLS, format_number, round_to
from rentab_core.rates import REGISTRY_YEAR, amount, amount_entry, rate_entry

REGIME_NAMES: Mapping[str, str] = {
    "usn": "УСН",
    "ausn": "АУСН",
    "osno": "ОСНО",
    "npd": "НПД",
    "our": "ОУР РК",
}
# Сумма в сообщении округляется до рубля (тенге).
MESSAGE_QUANTUM = Decimal(1)


class Severity(StrEnum):
    """Важность сообщения валидатора."""

    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


class EligibilityIssue(CoreModel):
    """Сообщение валидатора о режиме.

    Attributes:
        country: Страна режима.
        regime: Ключ режима (`usn`, `ausn`, `osno`, `npd`, `our`).
        severity: Важность.
        message: Текст для пользователя.
        keys: Ключи реестра, на которых основана проверка.
    """

    country: Country
    regime: str
    severity: Severity
    message: str
    keys: tuple[str, ...] = ()

    @property
    def regime_name(self) -> str:
        """Название режима для интерфейса."""
        return REGIME_NAMES[self.regime]


def _money(value: Decimal, country: Country) -> str:
    return f"{format_number(round_to(value, MESSAGE_QUANTUM))} {CURRENCY_SYMBOLS[country.currency]}"


def _limit(country: Country, key: str, year: int) -> tuple[Decimal, str]:
    """Порог из реестра и ссылка на норму для сообщения."""
    entry = amount_entry(country, key)
    return amount(country, key, year), entry.source


def employee_headcount(firm: Firm, country: Country) -> int:
    """Численность работников по трудовым договорам в стране.

    Берётся из `FirmFacts.headcount`, иначе — по команде: сотрудники,
    трудоустроенные в стране; у ИП и физлица владелец работником не считается.
    """
    if firm.facts.headcount is not None:
        return firm.facts.headcount
    team = sum(1 for employee in firm.team if employee.employment_country == country)
    if country == Country.RF and firm.facts.rf_legal_form != LegalForm.ORGANIZATION:
        return max(team - 1, 0)
    return team


class _Checker:
    """Сборщик сообщений по одному режиму."""

    def __init__(self, country: Country, regime: str) -> None:
        self.country = country
        self.regime = regime
        self.issues: list[EligibilityIssue] = []

    def add(self, severity: Severity, message: str, *keys: str) -> None:
        self.issues.append(
            EligibilityIssue(
                country=self.country,
                regime=self.regime,
                severity=severity,
                message=message,
                keys=keys,
            )
        )

    def income_limit(self, income: Decimal | None, key: str, what: str, year: int) -> None:
        """Доход за год не выше порога из реестра."""
        if income is None:
            return
        limit, source = _limit(self.country, key, year)
        if income > limit:
            self.add(
                Severity.BLOCKER,
                f"Доход {_money(income, self.country)} больше лимита {what} "
                f"{_money(limit, self.country)} ({source})",
                key,
            )

    def headcount_limit(self, headcount: int, key: str, what: str, year: int) -> None:
        """Численность не выше порога из реестра."""
        limit, source = _limit(self.country, key, year)
        if headcount > limit:
            self.add(
                Severity.BLOCKER,
                f"Численность {headcount} чел. больше лимита {what} "
                f"{format_number(limit)} чел. ({source})",
                key,
            )

    def experiment(self, rate_key: str, year: int) -> None:
        """Срок эксперимента (АУСН, НПД) и условия, которые проверяет юрист."""
        entry = rate_entry(self.country, rate_key)
        if entry.valid_until is not None and entry.valid_until.year < year:
            self.add(
                Severity.BLOCKER,
                f"Эксперимент закончился {entry.valid_until:%d.%m.%Y}",
                rate_key,
            )
        elif entry.legal_review:
            self.add(Severity.INFO, entry.legal_review, rate_key)


def _usn_vat(check: _Checker, profile: UsnProfile, income: Decimal, year: int) -> None:
    exemption, exemption_source = _limit(Country.RF, "usn.vat_exemption_threshold", year)
    if income <= exemption and profile.vat != UsnVat.NONE:
        check.add(
            Severity.BLOCKER,
            f"При доходе до {_money(exemption, Country.RF)} плательщик УСН освобождён от НДС "
            f"автоматически ({exemption_source}): выберите «без НДС»",
            "usn.vat_exemption_threshold",
        )
    elif income > exemption and profile.vat == UsnVat.NONE:
        check.add(
            Severity.BLOCKER,
            f"Доход больше {_money(exemption, Country.RF)}: освобождения от НДС нет "
            f"({exemption_source}), нужен НДС 5%, 7% или 22%",
            "usn.vat_exemption_threshold",
        )
    vat_5_limit, vat_5_source = _limit(Country.RF, "usn.vat_5_income_limit", year)
    if profile.vat == UsnVat.VAT_5 and income > vat_5_limit:
        check.add(
            Severity.BLOCKER,
            f"НДС 5% — только при доходе до {_money(vat_5_limit, Country.RF)} ({vat_5_source}): "
            f"выберите 7%",
            "usn.vat_5_income_limit",
        )


def check_profile(
    profile: TaxProfile, facts: FirmFacts, headcount: int, year: int = REGISTRY_YEAR
) -> list[EligibilityIssue]:
    """Проверяет, может ли фирма применять налоговый режим.

    Args:
        profile: Налоговый режим с настройкой НДС.
        facts: Данные фирмы (форма, доход, агентские договоры).
        headcount: Численность работников в стране режима.
        year: Год реестра.

    Returns:
        Сообщения по порядку проверок; пустой список — режим доступен без оговорок.
    """
    check = _Checker(profile.country, profile.regime)
    rf_income = facts.annual_income_rf
    match profile:
        case UsnProfile():
            if facts.rf_legal_form == LegalForm.INDIVIDUAL:
                check.add(Severity.BLOCKER, "УСН применяют организации и ИП, а не физлица")
            check.income_limit(rf_income, "usn.income_limit", "УСН", year)
            check.headcount_limit(headcount, "usn.headcount_limit", "УСН", year)
            if rf_income is not None:
                _usn_vat(check, profile, rf_income, year)
        case AusnProfile():
            if facts.rf_legal_form == LegalForm.INDIVIDUAL:
                check.add(Severity.BLOCKER, "АУСН применяют организации и ИП, а не физлица")
            check.income_limit(rf_income, "ausn.income_limit", "АУСН", year)
            check.headcount_limit(headcount, "ausn.headcount_limit", "АУСН", year)
            check.experiment("ausn.income", year)
        case NpdProfile():
            if facts.rf_legal_form == LegalForm.ORGANIZATION:
                check.add(Severity.BLOCKER, "НПД применяют только физлица и ИП, а не организации")
            if headcount > 0:
                check.add(
                    Severity.BLOCKER,
                    "На НПД нельзя иметь работников по трудовым договорам "
                    "(422-ФЗ, ст. 4, ч. 2, п. 4)",
                )
            if facts.agency_contracts:
                check.add(
                    Severity.BLOCKER,
                    "На НПД нельзя работать по договорам поручения, комиссии и агентским "
                    "(422-ФЗ, ст. 4, ч. 2, п. 5): например, платить пошлины за клиента как агент",
                )
            check.income_limit(rf_income, "npd.income_limit", "НПД", year)
            check.experiment("npd.individual", year)
        case OsnoProfile():
            pass
        case KzOurProfile():
            _check_kz(check, profile, facts.annual_income_kz, year)
        case _:
            assert_never(profile)
    income = facts.annual_income_kz if isinstance(profile, KzOurProfile) else rf_income
    if income is None and not isinstance(profile, OsnoProfile):
        name = REGIME_NAMES[profile.regime]
        check.add(Severity.INFO, f"Укажите ожидаемый доход за год, чтобы проверить лимиты {name}")
    return check.issues


def _check_kz(check: _Checker, profile: KzOurProfile, income: Decimal | None, year: int) -> None:
    if income is None:
        return
    threshold, source = _limit(Country.KZ, "vat.registration_threshold", year)
    if profile.vat == KzVat.NONE and income > threshold:
        check.add(
            Severity.BLOCKER,
            f"Оборот {_money(income, Country.KZ)} больше {_money(threshold, Country.KZ)}: "
            f"постановка на учёт по НДС обязательна ({source}), выберите НДС 16%",
            "vat.registration_threshold",
        )
    iit_threshold, iit_source = _limit(Country.KZ, "our.iit_ip_threshold", year)
    if profile.form == KzForm.IP and income > iit_threshold:
        check.add(
            Severity.WARNING,
            f"Доход больше {_money(iit_threshold, Country.KZ)}: ИПН с превышения — 15% "
            f"({iit_source}), модель считает 10%",
            "our.iit_ip_threshold",
            "our.iit_ip_high",
        )


def check_firm(firm: Firm, year: int = REGISTRY_YEAR) -> list[EligibilityIssue]:
    """Проверяет режимы фирмы во всех странах её юрисдикции."""
    jurisdiction = firm.jurisdiction
    issues: list[EligibilityIssue] = []
    for country in jurisdiction.countries:
        profile = jurisdiction.tax_profile(country)
        issues.extend(check_profile(profile, firm.facts, employee_headcount(firm, country), year))
    return issues


def is_available(issues: Iterable[EligibilityIssue]) -> bool:
    """Режим доступен: среди сообщений нет блокирующих."""
    return all(issue.severity != Severity.BLOCKER for issue in issues)
