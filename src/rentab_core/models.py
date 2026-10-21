"""Входные модели ядра: фирма, команда, накладные, налоговый режим, проект.

Модели неизменяемые (`frozen`) и не принимают лишних полей. Суммы, ставки и
часы — Decimal; float на входе переводится через строку. Доли (маржа, ставка
взносов) хранятся долями, а не процентами: 30% — это Decimal("0.30").

Структура повторяет данные v0.3 (`session_state` страниц «Настройка» и
«Проект»), но без словарей и русских подписей вместо ключей.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from rentab_core.money import ZERO, Currency

NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
PositiveDecimal = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]

# Норма контрактных часов в месяц: 8 ч × 21 рабочий день (v0.3, team.py:372).
DEFAULT_CONTRACT_HOURS_PER_MONTH = Decimal(168)
# Биллируемые часы фирмы в месяц по умолчанию (v0.3, expenses.py:149).
DEFAULT_BILLABLE_HOURS_PER_MONTH = Decimal(160)
# Целевая маржа фикс-прайса по умолчанию (v0.3, 02_Проект.py:69).
DEFAULT_TARGET_MARGIN = Decimal("0.30")


class CoreModel(BaseModel):
    """База моделей ядра: неизменяемые, без лишних полей."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Юрисдикция
# ---------------------------------------------------------------------------
class Country(StrEnum):
    """Страна расчёта налогов и валюты сметы."""

    RF = "RF"
    KZ = "KZ"

    @property
    def currency(self) -> Currency:
        """Национальная валюта страны."""
        return COUNTRY_CURRENCY[self]


COUNTRY_CURRENCY: Mapping[Country, Currency] = {
    Country.RF: Currency.RUB,
    Country.KZ: Currency.KZT,
}


class JurisdictionMode(StrEnum):
    """Где работает фирма: только РФ, только РК или в обеих странах."""

    RF = "RF"
    KZ = "KZ"
    BOTH = "BOTH"


# ---------------------------------------------------------------------------
# Налоговые профили: размеченное объединение по полю regime
# ---------------------------------------------------------------------------
class UsnObject(StrEnum):
    """Объект налогообложения УСН."""

    INCOME = "income"
    INCOME_MINUS_EXPENSES = "income_minus_expenses"


class UsnVat(StrEnum):
    """Ставка НДС на УСН."""

    NONE = "none"
    VAT_5 = "5%"
    VAT_7 = "7%"


class SocialContributions(StrEnum):
    """Тариф страховых взносов работодателя в РФ."""

    STANDARD = "standard"
    ZERO = "zero"


class NpdClientType(StrEnum):
    """Тип клиентов самозанятого: от него зависит ставка НПД."""

    INDIVIDUAL = "individual"
    LEGAL_ENTITY = "legal_entity"


class KzForm(StrEnum):
    """Организационная форма в РК."""

    TOO = "too"
    IP = "ip"


class KzVat(StrEnum):
    """Ставка НДС в РК."""

    NONE = "none"
    VAT_16 = "16%"


class UsnProfile(CoreModel):
    """РФ, упрощённая система налогообложения."""

    regime: Literal["usn"] = "usn"
    tax_object: UsnObject = UsnObject.INCOME
    vat: UsnVat = UsnVat.NONE
    social_contributions: SocialContributions = SocialContributions.STANDARD

    @property
    def country(self) -> Country:
        """Страна режима."""
        return Country.RF


class AusnProfile(CoreModel):
    """РФ, автоматизированная УСН: ставка фиксирована, взносы нулевые."""

    regime: Literal["ausn"] = "ausn"

    @property
    def country(self) -> Country:
        """Страна режима."""
        return Country.RF


class OsnoProfile(CoreModel):
    """РФ, общая система налогообложения."""

    regime: Literal["osno"] = "osno"
    social_contributions: SocialContributions = SocialContributions.STANDARD

    @property
    def country(self) -> Country:
        """Страна режима."""
        return Country.RF


class NpdProfile(CoreModel):
    """РФ, налог на профессиональный доход (самозанятость)."""

    regime: Literal["npd"] = "npd"
    client_type: NpdClientType = NpdClientType.LEGAL_ENTITY

    @property
    def country(self) -> Country:
        """Страна режима."""
        return Country.RF


class KzOurProfile(CoreModel):
    """РК, общеустановленный режим."""

    regime: Literal["our"] = "our"
    form: KzForm = KzForm.TOO
    vat: KzVat = KzVat.NONE

    @property
    def country(self) -> Country:
        """Страна режима."""
        return Country.KZ


RfTaxProfile = Annotated[
    UsnProfile | AusnProfile | OsnoProfile | NpdProfile, Field(discriminator="regime")
]
TaxProfile = Annotated[
    UsnProfile | AusnProfile | OsnoProfile | NpdProfile | KzOurProfile,
    Field(discriminator="regime"),
]

# Режимы по умолчанию, если для страны параметры не заданы (v0.3, 02_Проект.py:513–525).
DEFAULT_RF_TAX_PROFILE = UsnProfile()
DEFAULT_KZ_TAX_PROFILE = KzOurProfile()


class Jurisdiction(CoreModel):
    """Юрисдикция фирмы и налоговые режимы по странам.

    Attributes:
        mode: РФ, РК или обе страны.
        rf: Режим в РФ; None — режим по умолчанию (УСН «Доходы» без НДС).
        kz: Режим в РК; None — ОУР для ТОО без НДС.
        exchange_rate_rub_per_kzt: Курс «1 ₸ = x ₽» для режима «Оба». Только
            хранится и используется для показа сумм: в расчёт сметы не входит
            (KNOWN-BUG L-06 → фаза 2).
    """

    mode: JurisdictionMode = JurisdictionMode.RF
    rf: RfTaxProfile | None = None
    kz: KzOurProfile | None = None
    exchange_rate_rub_per_kzt: PositiveDecimal | None = None

    @property
    def countries(self) -> tuple[Country, ...]:
        """Страны, в которых работает фирма (в режиме «Оба» — обе)."""
        if self.mode == JurisdictionMode.BOTH:
            return (Country.RF, Country.KZ)
        return (Country(self.mode.value),)

    def calc_country(self, requested: Country | None = None) -> Country:
        """Страна расчёта налога сметы.

        В режиме «Оба» страну выбирают на проекте (по умолчанию РФ, как первая
        кнопка в v0.3); в остальных режимах она определена юрисдикцией.
        """
        if self.mode == JurisdictionMode.BOTH:
            return requested or Country.RF
        return Country(self.mode.value)

    def tax_profile(self, country: Country) -> TaxProfile:
        """Налоговый режим страны или режим по умолчанию, если он не задан."""
        if country == Country.RF:
            return self.rf or DEFAULT_RF_TAX_PROFILE
        return self.kz or DEFAULT_KZ_TAX_PROFILE


# ---------------------------------------------------------------------------
# Команда и накладные
# ---------------------------------------------------------------------------
class Role(StrEnum):
    """Роль сотрудника в иерархии фирмы (от старшей к младшей)."""

    PARTNER = "partner"
    SENIOR = "senior"
    ASSOCIATE = "associate"
    INTERN = "intern"


ROLE_LABELS: Mapping[Role, str] = {
    Role.PARTNER: "Партнёр",
    Role.SENIOR: "Старший юрист",
    Role.ASSOCIATE: "Младший юрист",
    Role.INTERN: "Стажёр",
}

# Деление ролей для коэффициента рычага (leverage).
SENIOR_ROLES: frozenset[Role] = frozenset({Role.PARTNER, Role.SENIOR})
JUNIOR_ROLES: frozenset[Role] = frozenset({Role.ASSOCIATE, Role.INTERN})


class Payroll(CoreModel):
    """Параметры ФОТ сотрудника для калькулятора себестоимости часа."""

    gross_salary: NonNegativeDecimal
    contract_hours_per_month: PositiveDecimal = DEFAULT_CONTRACT_HOURS_PER_MONTH
    employer_contribution_rate: NonNegativeDecimal


class Employee(CoreModel):
    """Сотрудник, чьи часы продаются клиенту.

    Attributes:
        name: Имя; по нему этапы проекта ссылаются на исполнителя.
        role: Роль (для коэффициента рычага).
        billing_rate: Ставка для клиента, валюта/ч.
        cost_rate: Себестоимость часа, валюта/ч. Как в v0.3, вводится
            пользователем; калькулятор ФОТ (`costing.employee_full_cost`)
            только подсказывает значение.
        payroll: Параметры ФОТ, если пользователь их заполнил.
    """

    name: str
    role: Role
    billing_rate: NonNegativeDecimal = ZERO
    cost_rate: NonNegativeDecimal = ZERO
    payroll: Payroll | None = None


class Period(StrEnum):
    """Периодичность накладного расхода."""

    MONTHLY = "monthly"
    ANNUAL = "annual"
    ONE_TIME = "one_time"


class Overhead(CoreModel):
    """Накладной расход фирмы (аренда, ПО, бухгалтерия)."""

    name: str
    amount: NonNegativeDecimal
    currency: Currency = Currency.RUB
    period: Period


class Firm(CoreModel):
    """Фирма: юрисдикция, команда, накладные и часы.

    Attributes:
        name: Название для шапки сметы.
        jurisdiction: Юрисдикция и налоговые режимы.
        team: Сотрудники. Имена не обязаны быть уникальными: как в v0.3,
            при совпадении исполнителем считается последний сотрудник.
        overheads: Накладные расходы фирмы.
        billable_hours_per_month: Биллируемые часы всей фирмы в месяц —
            знаменатель ставки накладных.
        contract_hours_per_month: Контрактные часы всей команды в месяц;
            нужны только для показа утилизации.
    """

    name: str = ""
    jurisdiction: Jurisdiction = Field(default_factory=Jurisdiction)
    team: tuple[Employee, ...] = ()
    overheads: tuple[Overhead, ...] = ()
    billable_hours_per_month: PositiveDecimal = DEFAULT_BILLABLE_HOURS_PER_MONTH
    contract_hours_per_month: NonNegativeDecimal | None = None

    def team_by_name(self) -> dict[str, Employee]:
        """Сотрудники по имени; при совпадении имён побеждает последний (как в v0.3)."""
        return {employee.name: employee for employee in self.team}


# ---------------------------------------------------------------------------
# Проект
# ---------------------------------------------------------------------------
class Assignment(CoreModel):
    """Назначение сотрудника на этап с плановыми часами."""

    employee: str = Field(description="Имя сотрудника из Firm.team")
    hours: NonNegativeDecimal


class Stage(CoreModel):
    """Этап проекта.

    Модель допускает несколько назначений, как `FixedPriceCalculator` v0.3.
    Интерфейс фазы 1 по-прежнему даёт одного исполнителя на этап
    (KNOWN-BUG L-05 → фаза 2).
    """

    name: str
    assignments: tuple[Assignment, ...] = ()


class DisbursementKind(StrEnum):
    """Тип прямого расхода проекта."""

    BILLABLE = "billable"  # перевыставляется клиенту (пошлины): транзит, не доход
    OWN = "own"  # за счёт фирмы: уменьшает NNE
    EXTRA = "extra"  # «Прочее»: в v0.3 ни на что не влияет (KNOWN-BUG L-12 → фаза 2)


class Disbursement(CoreModel):
    """Прямой расход проекта: пошлина, курьер, перевод и т. п."""

    name: str
    kind: DisbursementKind
    amount: NonNegativeDecimal
    currency: Currency = Currency.RUB

    @property
    def billable(self) -> bool:
        """Перевыставляется ли расход клиенту."""
        return self.kind == DisbursementKind.BILLABLE


class HourlyPricing(CoreModel):
    """Почасовая модель: выручка = Σ ставка × часы."""

    kind: Literal["hourly"] = "hourly"


class FixedPricing(CoreModel):
    """Фиксированная цена «издержки + маржа»."""

    kind: Literal["fixed"] = "fixed"
    target_margin: Annotated[Decimal, Field(ge=0, lt=1)] = DEFAULT_TARGET_MARGIN


PricingModel = Annotated[HourlyPricing | FixedPricing, Field(discriminator="kind")]


class Project(CoreModel):
    """Проект для сметы.

    Attributes:
        name: Название проекта.
        client: Клиент.
        description: Краткое описание.
        pricing: Модель ценообразования.
        stages: Этапы в порядке выполнения.
        disbursements: Прямые расходы: пошлины из каталога, затем
            произвольные расходы (порядок v0.3, важен для сметы).
        calc_country: Страна расчёта налога; учитывается только в режиме «Оба».
    """

    name: str = ""
    client: str = ""
    description: str = ""
    pricing: PricingModel = Field(default_factory=HourlyPricing)
    stages: tuple[Stage, ...] = ()
    disbursements: tuple[Disbursement, ...] = ()
    calc_country: Country | None = None


class Scenario(CoreModel):
    """Полный вход одного расчёта сметы: фирма и проект."""

    firm: Firm
    project: Project
