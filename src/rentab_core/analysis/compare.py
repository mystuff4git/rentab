"""Сравнение моделей цены и налоговых режимов (docs/methodology.md, раздел 19).

Матрица: строки — налоговые режимы стран юрисдикции фирмы, столбцы — модели
цены (почасовая, с потолком, «издержки + маржа» и согласованная цена, если она
есть в проекте). Ячейка — смета проекта с этим режимом и этой моделью.
Доступность режима — `tax.eligibility.check_profile`: недоступный режим
помечается причиной, но смета для него считается.

Ставка взносов сотрудника с окладом, совпадающая с расчётной для текущего
режима, в строке другого режима пересчитывается по его правилам (раздел 19.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

from rentab_core.errors import CurrencyConversionError, FixedPriceError
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    AusnProfile,
    CappedPricing,
    CoreModel,
    Country,
    Employee,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    KzForm,
    KzOurProfile,
    KzVat,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    PricingModel,
    Project,
    Salary,
    Scenario,
    SocialContributions,
    TaxProfile,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import Currency
from rentab_core.results import Estimate
from rentab_core.tax.eligibility import (
    EligibilityIssue,
    Severity,
    check_profile,
    employee_headcount,
    is_available,
)
from rentab_core.tax.payroll import employer_contribution_rate

# Ставка взносов в карточке хранится с точностью до 0,01 п. п.: совпадение с
# расчётной в этих пределах значит, что её не меняли вручную (раздел 19.3).
CONTRIBUTION_RATE_TOLERANCE = Decimal("0.0001")
CAP_MISSING = "Задайте потолок для сравнения"


class PricingKind(StrEnum):
    """Столбец матрицы — модель цены."""

    HOURLY = "hourly"
    CAPPED = "capped"
    COST_PLUS = "cost_plus"
    AGREED = "agreed"


def pricing_kind(pricing: PricingModel) -> PricingKind:
    """Столбец, к которому относится модель цены проекта."""
    if isinstance(pricing, HourlyPricing):
        return PricingKind.HOURLY
    if isinstance(pricing, CappedPricing):
        return PricingKind.CAPPED
    return PricingKind.COST_PLUS if pricing.price is None else PricingKind.AGREED


class PricingColumn(CoreModel):
    """Столбец матрицы.

    Attributes:
        pricing: Модель цены; None — потолок не задан.
        is_current: Модель цены проекта.
    """

    kind: PricingKind
    pricing: PricingModel | None
    is_current: bool = False


class RegimeOption(CoreModel):
    """Строка матрицы: налоговый режим страны."""

    country: Country
    profile: TaxProfile
    is_current: bool = False


class ComparisonCell(CoreModel):
    """Ячейка: смета или причина, по которой её нет."""

    estimate: Estimate | None = None
    error: str | None = None


class ComparisonRow(CoreModel):
    """Строка матрицы с сообщениями валидатора применимости."""

    option: RegimeOption
    issues: tuple[EligibilityIssue, ...]
    cells: tuple[ComparisonCell, ...]

    @property
    def available(self) -> bool:
        """Режим доступен: блокирующих замечаний нет."""
        return is_available(self.issues)

    @property
    def blockers(self) -> tuple[EligibilityIssue, ...]:
        """Причины, по которым режим недоступен."""
        return tuple(issue for issue in self.issues if issue.severity == Severity.BLOCKER)


class Comparison(CoreModel):
    """Матрица «модель цены × налоговый режим».

    Attributes:
        currency: Валюта проекта: в ней сумма строк другой страны («Оба»)
            пересчитывается для сравнения.
    """

    currency: Currency
    columns: tuple[PricingColumn, ...]
    rows: tuple[ComparisonRow, ...]

    @property
    def current(self) -> tuple[ComparisonRow, int] | None:
        """Текущая строка и номер текущего столбца."""
        column = next(i for i, item in enumerate(self.columns) if item.is_current)
        row = next((row for row in self.rows if row.option.is_current), None)
        return None if row is None else (row, column)


# ---------------------------------------------------------------------------
# Строки: режимы и их варианты
# ---------------------------------------------------------------------------
def _rf_contributions(firm: Firm) -> SocialContributions:
    """Тариф взносов фирмы в РФ: из УСН или ОСНО, иначе общий."""
    profile = firm.jurisdiction.tax_profile(Country.RF)
    if isinstance(profile, UsnProfile | OsnoProfile):
        return profile.social_contributions
    return SocialContributions.STANDARD


def regime_families(firm: Firm, country: Country) -> list[list[TaxProfile]]:
    """Режимы страны и их варианты в порядке раздела 19.1."""
    if country == Country.KZ:
        return [[KzOurProfile(form=form, vat=vat) for vat in KzVat] for form in KzForm]
    tariff = _rf_contributions(firm)
    usn: list[list[TaxProfile]] = [
        [UsnProfile(tax_object=item, vat=vat, social_contributions=tariff) for vat in UsnVat]
        for item in UsnObject
    ]
    return [
        *usn,
        [AusnProfile()],
        [
            NpdProfile(client_type=NpdClientType.LEGAL_ENTITY),
            NpdProfile(client_type=NpdClientType.INDIVIDUAL),
        ],
        [OsnoProfile(social_contributions=tariff)],
    ]


def _issues(firm: Firm, profile: TaxProfile) -> tuple[EligibilityIssue, ...]:
    country = profile.country
    return tuple(check_profile(profile, firm.facts, employee_headcount(firm, country)))


def _blocker_count(firm: Firm, profile: TaxProfile) -> int:
    return sum(issue.severity == Severity.BLOCKER for issue in _issues(firm, profile))


def regime_options(firm: Firm, *, all_variants: bool = False) -> list[RegimeOption]:
    """Строки матрицы: режимы стран юрисдикции фирмы (раздел 19.1).

    По умолчанию — одна строка на режим: вариант фирмы, если режим текущий;
    иначе первый вариант без блокирующих замечаний; иначе вариант с
    наименьшим числом блокирующих замечаний.
    """
    options: list[RegimeOption] = []
    for country in firm.jurisdiction.countries:
        current = firm.jurisdiction.tax_profile(country)
        for family in regime_families(firm, country):
            if all_variants:
                chosen = family
            elif current in family:
                chosen = [current]
            else:
                chosen = [min(family, key=lambda profile: _blocker_count(firm, profile))]
            options += [
                RegimeOption(country=country, profile=profile, is_current=profile == current)
                for profile in chosen
            ]
    return options


def _with_profile(jurisdiction: Jurisdiction, profile: TaxProfile) -> Jurisdiction:
    if isinstance(profile, KzOurProfile):
        return jurisdiction.model_copy(update={"kz": profile})
    return jurisdiction.model_copy(update={"rf": profile})


def _employee_for(employee: Employee, current: Jurisdiction, target: Jurisdiction) -> Employee:
    """Сотрудник со ставкой взносов по правилам режима строки (раздел 19.3)."""
    compensation = employee.compensation
    if not isinstance(compensation, Salary):
        return employee
    country = employee.employment_country
    gross = compensation.gross_salary
    auto = employer_contribution_rate(current, country, gross)
    if abs(compensation.employer_contribution_rate - auto) > CONTRIBUTION_RATE_TOLERANCE:
        return employee
    rate = employer_contribution_rate(target, country, gross)
    return employee.model_copy(
        update={
            "compensation": compensation.model_copy(update={"employer_contribution_rate": rate})
        }
    )


def firm_for_regime(firm: Firm, profile: TaxProfile) -> Firm:
    """Фирма с режимом строки; ставки взносов, рассчитанные автоматически, — по нему."""
    current = firm.jurisdiction
    target = _with_profile(current, profile)
    team = tuple(_employee_for(employee, current, target) for employee in firm.team)
    return firm.model_copy(update={"jurisdiction": target, "team": team})


# ---------------------------------------------------------------------------
# Матрица
# ---------------------------------------------------------------------------
def pricing_columns(project: Project, cap: Decimal | None = None) -> list[PricingColumn]:
    """Столбцы матрицы: почасовая, с потолком, «издержки + маржа», согласованная цена.

    Args:
        project: Проект.
        cap: Потолок для сравнения, если в проекте его нет.
    """
    pricing = project.pricing
    current = pricing_kind(pricing)
    project_cap = pricing.cap if isinstance(pricing, CappedPricing) else cap
    columns = [
        PricingColumn(kind=PricingKind.HOURLY, pricing=HourlyPricing()),
        PricingColumn(
            kind=PricingKind.CAPPED,
            pricing=None if project_cap is None else CappedPricing(cap=project_cap),
        ),
        PricingColumn(kind=PricingKind.COST_PLUS, pricing=FixedPricing()),
    ]
    if current == PricingKind.AGREED:
        columns.append(PricingColumn(kind=PricingKind.AGREED, pricing=pricing))
    return [column.model_copy(update={"is_current": column.kind == current}) for column in columns]


def _cell(firm: Firm, project: Project, pricing: PricingModel | None) -> ComparisonCell:
    if pricing is None:
        return ComparisonCell(error=CAP_MISSING)
    try:
        scenario = Scenario(firm=firm, project=project.model_copy(update={"pricing": pricing}))
        return ComparisonCell(estimate=calculate_estimate(scenario))
    except (FixedPriceError, CurrencyConversionError) as exc:
        return ComparisonCell(error=str(exc))


def compare(
    scenario: Scenario, *, cap: Decimal | None = None, all_variants: bool = False
) -> Comparison:
    """Матрица «модель цены × налоговый режим» для проекта (раздел 19).

    Args:
        scenario: Фирма и проект; строка текущего режима и столбец модели
            цены проекта совпадают со сметой проекта.
        cap: Потолок для сравнения, если в проекте его нет.
        all_variants: По строке на каждый вариант НДС и ставки НПД.
    """
    firm, project = scenario.firm, scenario.project
    columns = pricing_columns(project, cap)
    rows = []
    for option in regime_options(firm, all_variants=all_variants):
        row_firm = firm_for_regime(firm, option.profile)
        row_project = project.model_copy(update={"calc_country": option.country})
        is_current = option.is_current and option.country == firm.jurisdiction.calc_country(
            project.calc_country
        )
        rows.append(
            ComparisonRow(
                option=option.model_copy(update={"is_current": is_current}),
                issues=_issues(firm, option.profile),
                cells=tuple(_cell(row_firm, row_project, column.pricing) for column in columns),
            )
        )
    currency = firm.jurisdiction.calc_country(project.calc_country).currency
    return Comparison(currency=currency, columns=tuple(columns), rows=tuple(rows))


def best_cells(comparison: Comparison, metric: str) -> set[tuple[int, int]]:
    """Ячейки с наибольшим значением показателя в столбце среди доступных режимов.

    Args:
        comparison: Матрица.
        metric: Атрибут сметы (`nne`, `margin`) — больше значит лучше.

    Returns:
        Пары (строка, столбец); в «Оба» строки сравниваются внутри своей страны.
    """
    best: set[tuple[int, int]] = set()
    for column in range(len(comparison.columns)):
        for country in Country:
            values = _values(comparison.rows, column, country, metric)
            if values:
                top = max(value for _, value in values)
                best |= {(row, column) for row, value in values if value == top}
    return best


def _values(
    rows: Sequence[ComparisonRow], column: int, country: Country, metric: str
) -> list[tuple[int, Decimal]]:
    result = []
    for index, row in enumerate(rows):
        estimate = row.cells[column].estimate
        if row.available and row.option.country == country and estimate is not None:
            result.append((index, getattr(estimate, metric)))
    return result
