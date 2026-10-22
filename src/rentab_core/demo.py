"""Демо-фирма: данные слайда 3 защиты ВКР и три типовых IP-проекта.

Фирма — из `docs/methodology.md`, раздел 10.1: партнёр, юрист и стажёр с
себестоимостью биллируемого часа 4 000 / 1 500 / 500 ₽, накладные 100 ₽/ч,
УСН «доходы» 6% + НДС 5%. Стоимость сотрудников задана суммой в месяц: с
окладом налог УСН уменьшался бы на взносы (решение 2Б), и цифры слайда
разошлись бы с golden-тестом.

Проекты:

- «Слайд 3: план» и «Слайд 3: факт» — golden-сценарий (раздел 12): NNE
  225 500 и −2 200 ₽, к оплате 315 000 ₽;
- «Регистрация товарного знака» — пример 1 (раздел 10), почасовая модель;
  пошлины Роспатента за три класса МКТУ — из каталога;
- «Патентование изобретения» — фикс «издержки + маржа» 30%;
- «Ответ на уведомление экспертизы» — почасовая модель с потолком.
"""

from __future__ import annotations

from decimal import Decimal

from rentab_core.duties import Quantity
from rentab_core.io.schema import DutySelection, ProjectRecord, Workspace
from rentab_core.models import (
    Assignment,
    CappedPricing,
    Country,
    Disbursement,
    DisbursementKind,
    Employee,
    Firm,
    FirmFacts,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    MonthlyCost,
    Overhead,
    Period,
    PricingModel,
    Project,
    Role,
    Stage,
    UsnProfile,
    UsnVat,
)

DEMO_FIRM_NAME = "Демо-фирма «Слайд 3»"
# Раздел 10.1: H = 160 ч, u = 62,5% — ёмкость 100 ч в месяц у каждого.
DEMO_CONTRACT_HOURS = Decimal(160)
DEMO_UTILIZATION = Decimal("0.625")
# Ожидаемый доход за год: больше порога освобождения от НДС на УСН (20 млн ₽),
# поэтому НДС 5% у демо-фирмы обязателен и валидатор режима не возражает.
DEMO_ANNUAL_INCOME_RF = Decimal(24_000_000)

PARTNER = "Партнёр"
LAWYER = "Юрист"
INTERN = "Стажёр"

# Пошлины Роспатента (каталог rospatent_2026.yaml).
TM_APPLICATION = "Регистрация заявки на товарный знак и формальная экспертиза"
TM_EXAMINATION = "Экспертиза заявленного обозначения (товарный знак)"
INVENTION_APPLICATION = "Регистрация заявки на изобретение и формальная экспертиза"
INVENTION_EXAMINATION = "Экспертиза заявки на изобретение по существу"


def _employee(name: str, role: Role, monthly: int, billing: int) -> Employee:
    return Employee(
        name=name,
        role=role,
        compensation=MonthlyCost(amount=Decimal(monthly)),
        billing_rate=Decimal(billing),
        contract_hours_per_month=DEMO_CONTRACT_HOURS,
        target_utilization=DEMO_UTILIZATION,
    )


def demo_firm() -> Firm:
    """Фирма слайда 3: три сотрудника, накладные 30 000 ₽ в месяц (100 ₽/ч)."""
    return Firm(
        name=DEMO_FIRM_NAME,
        jurisdiction=Jurisdiction(rf=UsnProfile(vat=UsnVat.VAT_5)),
        team=(
            # Нормативное вознаграждение партнёра по Майстеру.
            _employee(PARTNER, Role.PARTNER, 400_000, 10_000),
            _employee(LAWYER, Role.ASSOCIATE, 150_000, 5_000),
            _employee(INTERN, Role.INTERN, 50_000, 2_000),
        ),
        overheads=(
            Overhead(name="Аренда офиса", amount=Decimal(18_000), period=Period.MONTHLY),
            Overhead(name="Правовые базы и ПО", amount=Decimal(7_000), period=Period.MONTHLY),
            Overhead(name="Бухгалтерия на аутсорсе", amount=Decimal(5_000), period=Period.MONTHLY),
        ),
        facts=FirmFacts(annual_income_rf=DEMO_ANNUAL_INCOME_RF),
    )


def _stage(name: str, *assignments: tuple[str, int]) -> Stage:
    return Stage(
        name=name,
        assignments=tuple(
            Assignment(employee=who, hours=Decimal(hours)) for who, hours in assignments
        ),
    )


def _slide3(name: str, description: str, partner: int, lawyer: int, intern: int) -> Project:
    """Проект слайда 3: цена 300 000 ₽ без НДС согласована, один этап."""
    return Project(
        name=name,
        client="Клиент слайда 3",
        description=description,
        pricing=FixedPricing(price=Decimal(300_000)),
        stages=(_stage("Проект", (PARTNER, partner), (LAWYER, lawyer), (INTERN, intern)),),
    )


def _project(
    name: str,
    client: str,
    description: str,
    pricing: PricingModel,
    *stages: Stage,
    disbursements: tuple[Disbursement, ...] = (),
) -> Project:
    return Project(
        name=name,
        client=client,
        description=description,
        pricing=pricing,
        stages=stages,
        disbursements=disbursements,
    )


def demo_projects() -> tuple[ProjectRecord, ...]:
    """Пять проектов демо-фирмы."""
    trademark = _project(
        "Регистрация товарного знака",
        "ООО «Ромашка»",
        "Пример 1 из методологии: классы МКТУ 05, 29, 30.",
        HourlyPricing(),
        _stage("Анализ охраноспособности", (PARTNER, 2), (LAWYER, 8)),
        _stage("Подготовка и подача заявки", (LAWYER, 4), (INTERN, 10)),
        disbursements=(
            Disbursement(
                name="Нотариальный перевод доверенности",
                kind=DisbursementKind.OWN,
                amount=Decimal(1_800),
                stage="Подготовка и подача заявки",
            ),
        ),
    )
    invention = _project(
        "Патентование изобретения",
        "АО «Технологии»",
        "Заявка на изобретение: поиск, подготовка, ответ на запрос экспертизы.",
        FixedPricing(),
        _stage("Патентный поиск", (LAWYER, 12), (INTERN, 8)),
        _stage("Подготовка заявки", (PARTNER, 6), (LAWYER, 24)),
        _stage("Ответ на запрос экспертизы", (PARTNER, 2), (LAWYER, 6)),
    )
    office_action = _project(
        "Ответ на уведомление экспертизы",
        "ИП Смирнов",
        "Потолок согласован с клиентом; часы сверх потолка — списание.",
        CappedPricing(cap=Decimal(60_000)),
        _stage("Анализ уведомления и ответ", (PARTNER, 2), (LAWYER, 10)),
    )
    return (
        ProjectRecord(
            project=_slide3(
                "Слайд 3: план", "Golden-сценарий защиты ВКР: план по часам.", 5, 15, 20
            )
        ),
        ProjectRecord(
            project=_slide3(
                "Слайд 3: факт — партнёр тянет проект",
                "Тот же проект по факту: партнёр сделал работу юриста и стажёра.",
                62,
                15,
                10,
            )
        ),
        ProjectRecord(
            project=trademark,
            duties={
                Country.RF: DutySelection(
                    actions=(TM_APPLICATION, TM_EXAMINATION),
                    quantities={Quantity.CLASSES: 3},
                )
            },
        ),
        ProjectRecord(
            project=invention,
            duties={
                Country.RF: DutySelection(
                    actions=(INVENTION_APPLICATION, INVENTION_EXAMINATION),
                    quantities={Quantity.CLAIMS: 15, Quantity.INDEPENDENT_CLAIMS: 2},
                )
            },
        ),
        ProjectRecord(project=office_action),
    )


def demo_workspace() -> Workspace:
    """Демо-фирма с проектами: то, что загружает кнопка «Загрузить демо-фирму»."""
    return Workspace(firm=demo_firm(), projects=demo_projects())
