"""Демо-фирма: данные слайда 3 защиты ВКР и три типовых IP-проекта.

Фирма — из `docs/methodology.md`, раздел 10.1: партнёр, юрист и стажёр с
себестоимостью биллируемого часа 4 000 / 1 500 / 500 ₽, накладные 100 ₽/ч,
УСН «доходы» 6% + НДС 5%. Стоимость сотрудников задана суммой в месяц: с
окладом налог УСН уменьшался бы на взносы (решение 2Б), и цифры слайда
разошлись бы с golden-тестом.

Проекты:

- «Слайд 3: план и факт» — golden-сценарий (раздел 12) одним проектом: план
  5 / 15 / 20 ч, факт 62 / 15 / 10 ч; NNE 225 500 → −2 200 ₽, к оплате
  315 000 ₽ (раздел 18.5);
- «Регистрация товарного знака» — пример 1 (раздел 10), почасовая модель;
  пошлины Роспатента за три класса МКТУ — из каталога; факт с работой
  партнёра вне плана (раздел 18.6);
- «Патентование изобретения» — фикс «издержки + маржа» 30%;
- «Ответ на уведомление экспертизы» — почасовая модель с потолком.

Для КП (фаза 7) у демо-фирмы есть бланк с вымышленными реквизитами (нули,
адрес example.com) и логотипом `demo_logo.png` (нарисован шрифтами ParaType,
OFL), у типовых проектов — условия КП и описания этапов. Цифры смет от них
не зависят.
"""

from __future__ import annotations

from decimal import Decimal
from importlib import resources

from rentab_core.documents import Letterhead, OfferTerms, read_logo
from rentab_core.duties import Quantity
from rentab_core.io.schema import DutySelection, ProjectRecord, Workspace
from rentab_core.models import (
    ActualHours,
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
    ProjectActuals,
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


DEMO_LOGO = "demo_logo.png"


def demo_letterhead() -> Letterhead:
    """Бланк демо-фирмы: реквизиты заведомо вымышленные."""
    logo = read_logo((resources.files("rentab_core") / DEMO_LOGO).read_bytes())
    return Letterhead(
        requisites="ИНН 0000000000 · КПП 000000000 · ОГРН 0000000000000",
        address="Москва, ул. Примерная, д. 1, офис 10",
        phone="+7 000 000-00-00",
        email="info@example.com",
        website="example.com",
        signatory_name="Иванов Иван Иванович",
        signatory_position="управляющий партнёр",
        logo=logo,
    )


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


def _stage(name: str, *assignments: tuple[str, int], description: str = "") -> Stage:
    return Stage(
        name=name,
        assignments=tuple(
            Assignment(employee=who, hours=Decimal(hours)) for who, hours in assignments
        ),
        description=description,
    )


def _actuals(*rows: tuple[str, str, int]) -> ProjectActuals:
    return ProjectActuals(
        hours=tuple(
            ActualHours(stage=stage, employee=who, hours=Decimal(hours))
            for stage, who, hours in rows
        )
    )


SLIDE3_STAGE = "Проект"
TM_ANALYSIS = "Анализ охраноспособности"
TM_FILING = "Подготовка и подача заявки"


def _slide3() -> Project:
    """Проект слайда 3: цена 300 000 ₽ без НДС согласована, один этап; план и факт."""
    return Project(
        name="Слайд 3: план и факт",
        client="Клиент слайда 3",
        description=(
            "Golden-сценарий защиты ВКР: план по часам и факт — партнёр сделал работу "
            "юриста и стажёра."
        ),
        pricing=FixedPricing(price=Decimal(300_000)),
        stages=(_stage(SLIDE3_STAGE, (PARTNER, 5), (LAWYER, 15), (INTERN, 20)),),
        actuals=_actuals(
            (SLIDE3_STAGE, PARTNER, 62), (SLIDE3_STAGE, LAWYER, 15), (SLIDE3_STAGE, INTERN, 10)
        ),
    )


def _project(
    name: str,
    client: str,
    description: str,
    pricing: PricingModel,
    *stages: Stage,
    disbursements: tuple[Disbursement, ...] = (),
    actuals: ProjectActuals | None = None,
) -> Project:
    return Project(
        name=name,
        client=client,
        description=description,
        pricing=pricing,
        stages=stages,
        disbursements=disbursements,
        actuals=actuals,
    )


def demo_projects() -> tuple[ProjectRecord, ...]:
    """Четыре проекта демо-фирмы."""
    trademark = _project(
        "Регистрация товарного знака",
        "ООО «Ромашка»",
        "Регистрация товарного знака в Роспатенте по классам МКТУ 05, 29, 30.",
        HourlyPricing(),
        _stage(
            TM_ANALYSIS,
            (PARTNER, 2),
            (LAWYER, 8),
            description="Поиск сходных обозначений, заключение о регистрируемости",
        ),
        _stage(
            TM_FILING,
            (LAWYER, 4),
            (INTERN, 10),
            description="Заявка по трём классам МКТУ, подача и контроль формальной экспертизы",
        ),
        disbursements=(
            Disbursement(
                name="Нотариальный перевод доверенности",
                kind=DisbursementKind.OWN,
                amount=Decimal(1_800),
                stage=TM_FILING,
            ),
        ),
        # Раздел 18.6: на подаче заявки партнёр поработал вне плана.
        actuals=_actuals(
            (TM_ANALYSIS, PARTNER, 3),
            (TM_ANALYSIS, LAWYER, 10),
            (TM_FILING, LAWYER, 4),
            (TM_FILING, INTERN, 12),
            (TM_FILING, PARTNER, 1),
        ),
    )
    invention = _project(
        "Патентование изобретения",
        "АО «Технологии»",
        "Подготовка и подача заявки на изобретение, ответ на запрос экспертизы.",
        FixedPricing(),
        _stage(
            "Патентный поиск",
            (LAWYER, 12),
            (INTERN, 8),
            description="Отчёт о поиске по базам ФИПС и ЕАПО, оценка патентоспособности",
        ),
        _stage(
            "Подготовка заявки",
            (PARTNER, 6),
            (LAWYER, 24),
            description="Описание, формула и реферат, подача заявки в Роспатент",
        ),
        _stage(
            "Ответ на запрос экспертизы",
            (PARTNER, 2),
            (LAWYER, 6),
            description="Анализ запроса, ответ и уточнённая формула",
        ),
    )
    office_action = _project(
        "Ответ на уведомление экспертизы",
        "ИП Смирнов",
        "Ответ на уведомление экспертизы по заявке на товарный знак.",
        CappedPricing(cap=Decimal(60_000)),
        _stage(
            "Анализ уведомления и ответ",
            (PARTNER, 2),
            (LAWYER, 10),
            description="Доводы и ответ на уведомление экспертизы о результатах проверки",
        ),
    )
    return (
        ProjectRecord(project=_slide3()),
        ProjectRecord(
            project=trademark,
            duties={
                Country.RF: DutySelection(
                    actions=(TM_APPLICATION, TM_EXAMINATION),
                    quantities={Quantity.CLASSES: 3},
                )
            },
            offer=OfferTerms(
                number="КП-2",
                payment_terms="50% — аванс, 50% — после подачи заявки",
                execution_term="подача заявки — 10 рабочих дней после аванса",
            ),
        ),
        ProjectRecord(
            project=invention,
            duties={
                Country.RF: DutySelection(
                    actions=(INVENTION_APPLICATION, INVENTION_EXAMINATION),
                    quantities={Quantity.CLAIMS: 15, Quantity.INDEPENDENT_CLAIMS: 2},
                )
            },
            offer=OfferTerms(
                number="КП-3",
                payment_terms="50% — аванс, 50% — после подачи заявки",
                execution_term="подача заявки — 20 рабочих дней после аванса",
            ),
        ),
        ProjectRecord(
            project=office_action,
            offer=OfferTerms(
                number="КП-4",
                validity_days=14,
                payment_terms="100% — после отправки ответа экспертизе",
                execution_term="до срока ответа, указанного в уведомлении",
            ),
        ),
    )


def demo_workspace() -> Workspace:
    """Демо-фирма с проектами: то, что загружает кнопка «Загрузить демо-фирму»."""
    return Workspace(firm=demo_firm(), letterhead=demo_letterhead(), projects=demo_projects())
