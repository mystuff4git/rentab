"""Входные модели ядра: фирма, команда, накладные, налоговый режим, проект.

Модели неизменяемые (`frozen`) и не принимают лишних полей. Суммы, ставки и
часы — Decimal; float на входе переводится через строку. Доли (маржа,
утилизация, ставка взносов) хранятся долями, а не процентами: 30% — это
Decimal("0.30"). Обозначения и формулы — docs/methodology.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rentab_core.money import ZERO, Currency

NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
PositiveDecimal = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
# Утилизация — доля контрактных часов, которую фирма продаёт клиентам.
Utilization = Annotated[Decimal, Field(gt=0, le=1, allow_inf_nan=False)]
# Целевая маржа NNE — доля выручки без НДС.
Margin = Annotated[Decimal, Field(ge=0, lt=1, allow_inf_nan=False)]

# Норма контрактных часов в месяц: 8 ч × 21 рабочий день (v0.3, team.py:372).
DEFAULT_CONTRACT_HOURS_PER_MONTH = Decimal(168)
# Целевая утилизация по умолчанию (docs/methodology.md, раздел 3.2).
DEFAULT_TARGET_UTILIZATION = Decimal("0.65")
# Целевая маржа NNE по умолчанию (v0.3, 02_Проект.py:69).
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
    """

    mode: JurisdictionMode = JurisdictionMode.RF
    rf: RfTaxProfile | None = None
    kz: KzOurProfile | None = None

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


class Salary(CoreModel):
    """Полная стоимость сотрудника из оклада и взносов работодателя.

    Взносы — плоская ставка от оклада; предельная база и пониженные тарифы —
    фаза 3 (L-08).
    """

    kind: Literal["salary"] = "salary"
    gross_salary: NonNegativeDecimal
    employer_contribution_rate: NonNegativeDecimal

    @property
    def employer_contributions(self) -> Decimal:
        """Взносы работодателя в месяц."""
        return self.gross_salary * self.employer_contribution_rate

    @property
    def monthly_cost(self) -> Decimal:
        """W = оклад + взносы."""
        return self.gross_salary + self.employer_contributions


class MonthlyCost(CoreModel):
    """Полная стоимость сотрудника в месяц, введённая напрямую.

    Так задаётся нормативное вознаграждение партнёра по Майстеру — рыночная
    цена его труда, отделённая от прибыли фирмы, — и стоимость подрядчика.
    """

    kind: Literal["monthly"] = "monthly"
    amount: NonNegativeDecimal

    @property
    def monthly_cost(self) -> Decimal:
        """W — введённая сумма."""
        return self.amount


Compensation = Annotated[Salary | MonthlyCost, Field(discriminator="kind")]


class Employee(CoreModel):
    """Сотрудник, чьи часы продаются клиенту.

    Себестоимость часа не вводится, а выводится (`costing.employee_cost`):
    c = W / (H × u), где W — полная стоимость в месяц, H — контрактные часы,
    u — целевая утилизация.

    Attributes:
        name: Имя; по нему этапы проекта ссылаются на исполнителя.
        role: Роль (для коэффициента рычага).
        compensation: Полная стоимость в месяц: оклад и взносы или сумма.
        currency: Валюта оклада и ставки для клиента.
        billing_rate: Ставка для клиента за час.
        contract_hours_per_month: Контрактные часы в месяц.
        target_utilization: Доля контрактных часов, которая продаётся клиентам.
    """

    name: str
    role: Role
    compensation: Compensation
    currency: Currency = Currency.RUB
    billing_rate: NonNegativeDecimal = ZERO
    contract_hours_per_month: PositiveDecimal = DEFAULT_CONTRACT_HOURS_PER_MONTH
    target_utilization: Utilization = DEFAULT_TARGET_UTILIZATION


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


class ExchangeRates(CoreModel):
    """Курсы валют: цена единицы валюты в рублях, как публикует ЦБ РФ.

    В фазе 2 курсы вводятся вручную; загрузка курсов ЦБ РФ и НБРК — фаза 3.
    None — курс не задан: пересчёт через эту валюту невозможен.
    """

    rub_per_kzt: PositiveDecimal | None = None
    rub_per_usd: PositiveDecimal | None = None


class Firm(CoreModel):
    """Фирма: юрисдикция, команда, накладные и курсы валют.

    Биллируемая ёмкость фирмы (знаменатель ставки накладных) выводится из
    команды: Σ H_i × u_i.

    Attributes:
        name: Название для шапки сметы.
        jurisdiction: Юрисдикция и налоговые режимы.
        team: Сотрудники, чьи часы продаются клиентам. Имена не обязаны быть
            уникальными: как в v0.3, при совпадении исполнителем этапа
            считается последний сотрудник.
        overheads: Накладные расходы фирмы.
        exchange_rates: Курсы для пересчёта сумм в валюту проекта.
    """

    name: str = ""
    jurisdiction: Jurisdiction = Field(default_factory=Jurisdiction)
    team: tuple[Employee, ...] = ()
    overheads: tuple[Overhead, ...] = ()
    exchange_rates: ExchangeRates = Field(default_factory=ExchangeRates)

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
    """Этап проекта: набор назначений «сотрудник × часы».

    Пирамида «партнёр проверяет 2 ч, юрист делает 8 ч» — один этап с двумя
    назначениями (L-05).
    """

    name: str
    assignments: tuple[Assignment, ...] = ()


class DisbursementKind(StrEnum):
    """Тип прямого расхода проекта."""

    BILLABLE = "billable"  # возмещается клиентом (пошлины): транзит, вне выручки
    OWN = "own"  # за счёт фирмы, включая «Прочее» v0.3 (L-12): входит в издержки


class Disbursement(CoreModel):
    """Прямой расход проекта: пошлина, курьер, перевод и т. п.

    Attributes:
        name: Название.
        kind: Возмещаемый клиентом или собственный.
        amount: Сумма в валюте `currency`.
        currency: Валюта расхода.
        stage: Этап, к которому относится собственный расход; None —
            расход распределяется по этапам пропорционально теневой стоимости.
    """

    name: str
    kind: DisbursementKind
    amount: NonNegativeDecimal
    currency: Currency = Currency.RUB
    stage: str | None = None

    @property
    def billable(self) -> bool:
        """Перевыставляется ли расход клиенту."""
        return self.kind == DisbursementKind.BILLABLE


class VatMode(StrEnum):
    """Как указаны цены проекта (ставки, потолок, согласованная цена)."""

    ON_TOP = "on_top"  # без НДС, НДС начисляется сверху
    INCLUDED = "included"  # включая НДС: R = P / (1 + v)


class HourlyPricing(CoreModel):
    """Почасовая модель: R = Σ ставка × часы."""

    kind: Literal["hourly"] = "hourly"


class CappedPricing(CoreModel):
    """Почасовая модель с потолком (capped fee): R = min(Σ ставка × часы, cap)."""

    kind: Literal["capped"] = "capped"
    cap: PositiveDecimal


class FixedPricing(CoreModel):
    """Фиксированная цена.

    Attributes:
        price: Согласованная цена (в режиме НДС проекта). None — цена
            «издержки + маржа» по закрытой формуле от целевой маржи проекта.
    """

    kind: Literal["fixed"] = "fixed"
    price: PositiveDecimal | None = None


PricingModel = Annotated[HourlyPricing | CappedPricing | FixedPricing, Field(discriminator="kind")]


class Project(CoreModel):
    """Проект для сметы.

    Attributes:
        name: Название проекта.
        client: Клиент.
        description: Краткое описание.
        pricing: Модель ценообразования.
        target_margin: Целевая маржа NNE: определяет цену «издержки + маржа»
            и служит порогом светофора этапов во всех моделях.
        vat_mode: Указаны ли цены проекта без НДС или включая НДС.
        stages: Этапы в порядке выполнения; названия уникальны.
        disbursements: Прямые расходы: пошлины из каталога, затем
            произвольные расходы (порядок важен для сметы).
        calc_country: Страна расчёта налога; учитывается только в режиме «Оба».
    """

    name: str = ""
    client: str = ""
    description: str = ""
    pricing: PricingModel = Field(default_factory=HourlyPricing)
    target_margin: Margin = DEFAULT_TARGET_MARGIN
    vat_mode: VatMode = VatMode.ON_TOP
    stages: tuple[Stage, ...] = ()
    disbursements: tuple[Disbursement, ...] = ()
    calc_country: Country | None = None

    @model_validator(mode="after")
    def _check_stages(self) -> Project:
        """Названия этапов уникальны; расходы ссылаются на существующие этапы."""
        names = [stage.name for stage in self.stages]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"Названия этапов должны быть уникальны, повторяются: {', '.join(duplicates)}"
            )
        known = set(names)
        for item in self.disbursements:
            if item.stage is not None and item.stage not in known:
                raise ValueError(
                    f"Расход «{item.name}» привязан к неизвестному этапу «{item.stage}»"
                )
        return self


class Scenario(CoreModel):
    """Полный вход одного расчёта сметы: фирма и проект."""

    firm: Firm
    project: Project
