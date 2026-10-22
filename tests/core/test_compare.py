"""Сравнение моделей цены и налоговых режимов (docs/methodology.md, раздел 19)."""

from __future__ import annotations

from hypothesis import given, settings

from rentab_core.analysis.compare import (
    CAP_MISSING,
    Comparison,
    PricingKind,
    best_cells,
    compare,
    firm_for_regime,
    regime_options,
)
from rentab_core.demo import demo_firm
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    AusnProfile,
    CappedPricing,
    Country,
    ExchangeRates,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    Project,
    Salary,
    Scenario,
    SocialContributions,
    UsnObject,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import Currency, round_to
from rentab_core.tax.eligibility import check_profile, employee_headcount, is_available
from rentab_core.tax.payroll import employer_contribution_rate
from tests.core.factories import d, fixed, stage
from tests.core.strategies import scenarios
from tests.core.test_methodology_examples import example_project

USN_6 = UsnProfile(vat=UsnVat.VAT_5)
USN_15 = UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES, vat=UsnVat.VAT_5)
NPD_LEGAL = NpdProfile(client_type=NpdClientType.LEGAL_ENTITY)


def trademark(project: Project | None = None) -> Scenario:
    """Пример 1 у демо-фирмы, потолок 90 000 ₽ задан на экране сравнения."""
    return Scenario(firm=demo_firm(), project=project or example_project(HourlyPricing()))


def table(comparison: Comparison, metric: str) -> dict[object, list[object]]:
    """Строки матрицы: профиль → значения показателя по столбцам (или текст ошибки)."""
    return {
        row.option.profile: [
            cell.error if cell.estimate is None else round_to(getattr(cell.estimate, metric))
            for cell in row.cells
        ]
        for row in comparison.rows
    }


def test_example1_matrix_for_demo_firm() -> None:
    # Раздел 19.5.
    result = compare(trademark(), cap=d(90_000))
    assert [column.kind for column in result.columns] == [
        PricingKind.HOURLY,
        PricingKind.CAPPED,
        PricingKind.COST_PLUS,
    ]
    assert [column.is_current for column in result.columns] == [True, False, False]
    assert [row.option.profile for row in result.rows] == [
        USN_6,
        USN_15,
        AusnProfile(),
        NPD_LEGAL,
        OsnoProfile(),
    ]
    assert table(result, "nne") == {
        USN_6: [d(58_800), d(49_400), d(16_500)],
        USN_15: [d(55_080), d(46_580), d(16_320)],
        AusnProfile(): [d(56_800), d(47_600), d("17032.26")],
        NPD_LEGAL: [d(58_800), d(49_400), d(16_500)],
        OsnoProfile(): [d(48_600), d(41_100), d(17_600)],
    }
    assert table(result, "total_client") == {
        USN_6: [d(120_000), d(109_500), d(72_750)],
        USN_15: [d(120_000), d(109_500), d(72_120)],
        AusnProfile(): [d(115_000), d(105_000), d("71774.19")],
        NPD_LEGAL: [d(115_000), d(105_000), d(70_000)],
        OsnoProfile(): [d(137_000), d(124_800), d("86573.33")],
    }
    available = {row.option.profile: row.available for row in result.rows}
    assert available == {
        USN_6: True,
        USN_15: True,
        AusnProfile(): True,
        NPD_LEGAL: False,
        OsnoProfile(): True,
    }
    npd = next(row for row in result.rows if row.option.profile == NPD_LEGAL)
    assert [issue.message.split(" (")[0] for issue in npd.blockers] == [
        "НПД применяют только физлица и ИП, а не организации",
        "На НПД нельзя иметь работников по трудовым договорам",
        "Доход 24 000 000 ₽ больше лимита НПД 2 400 000 ₽",
    ]
    ausn = next(row for row in result.rows if row.option.profile == AusnProfile())
    assert ausn.issues
    assert not ausn.blockers
    current = result.current
    assert current is not None
    row, column = current
    assert row.option.profile == USN_6
    assert column == 0
    assert row.cells[0].estimate == calculate_estimate(trademark())


def test_all_variants_show_blocked_vat_settings() -> None:
    result = compare(trademark(), cap=d(90_000), all_variants=True)
    profiles = [row.option.profile for row in result.rows]
    assert len(profiles) == 10
    usn_none = next(row for row in result.rows if row.option.profile == UsnProfile())
    assert not usn_none.available
    assert "освобождения от НДС нет" in usn_none.blockers[0].message
    # НДС не меняет NNE при ценах без НДС.
    nne = table(result, "nne")
    assert nne[UsnProfile()] == nne[USN_6] == nne[UsnProfile(vat=UsnVat.VAT_7)]
    assert NpdProfile(client_type=NpdClientType.INDIVIDUAL) in profiles


def test_slide3_agreed_price_column() -> None:
    project = Project(
        name="Слайд 3",
        pricing=fixed(300_000),
        stages=(stage("Проект", ("Партнёр", 5), ("Юрист", 15), ("Стажёр", 20)),),
    )
    result = compare(trademark(project))
    assert [column.kind for column in result.columns][-1] == PricingKind.AGREED
    assert result.columns[-1].is_current
    agreed = {profile: values[-1] for profile, values in table(result, "nne").items()}
    assert agreed[USN_6] == d(225_500)
    assert agreed[USN_15] == d(206_975)
    assert agreed[AusnProfile()] == d(219_500)
    assert agreed[OsnoProfile()] == d(182_625)
    osno = next(row for row in result.rows if row.option.profile == OsnoProfile())
    assert osno.cells[-1].estimate is not None
    assert osno.cells[-1].estimate.total_client == d(366_000)
    # Потолок не задан: столбец есть, ячейки — с подсказкой.
    assert all(row.cells[1].error == CAP_MISSING for row in result.rows)
    assert result.columns[1].pricing is None


def test_project_cap_and_unreachable_target() -> None:
    project = example_project(CappedPricing(cap=d(90_000)), target_margin="0.8")
    result = compare(trademark(project))
    assert result.columns[1].is_current
    assert result.columns[1].pricing == CappedPricing(cap=d(90_000))
    cost_plus = {row.option.profile: row.cells[2] for row in result.rows}
    assert cost_plus[OsnoProfile()].estimate is None
    assert "недостижима" in (cost_plus[OsnoProfile()].error or "")
    assert cost_plus[USN_6].estimate is not None


def test_best_cells_among_available_rows() -> None:
    result = compare(trademark(), cap=d(90_000))
    best = best_cells(result, "nne")
    profiles = {(result.rows[row].option.profile, column) for row, column in best}
    # НПД (58 800) недоступен: лучший в почасовой — УСН 6%; в «издержки + маржа» — ОСНО.
    assert (USN_6, 0) in profiles
    assert (NPD_LEGAL, 0) not in profiles
    assert (OsnoProfile(), 2) in profiles


def test_contributions_are_recalculated_for_regime() -> None:
    # Раздел 19.3: ставка, рассчитанная автоматически, пересчитывается; ручная — нет.
    firm = demo_firm()
    gross = d(120_000)
    jurisdiction = firm.jurisdiction
    auto = employer_contribution_rate(jurisdiction, Country.RF, gross)
    team = (
        firm.team[0],
        firm.team[1].model_copy(
            update={"compensation": Salary(gross_salary=gross, employer_contribution_rate=auto)}
        ),
        firm.team[2].model_copy(
            update={"compensation": Salary(gross_salary=gross, employer_contribution_rate=d("0.1"))}
        ),
    )
    firm = firm.model_copy(update={"team": team})
    ausn = firm_for_regime(firm, AusnProfile())
    lawyer, intern = ausn.team[1].compensation, ausn.team[2].compensation
    assert isinstance(lawyer, Salary)
    assert isinstance(intern, Salary)
    assert lawyer.employer_contribution_rate == employer_contribution_rate(
        ausn.jurisdiction, Country.RF, gross
    )
    assert lawyer.employer_contribution_rate < auto
    assert intern.employer_contribution_rate == d("0.1")
    assert ausn.jurisdiction.rf == AusnProfile()
    # Строка текущего режима — та же смета, что у проекта.
    scenario = Scenario(firm=firm, project=example_project(HourlyPricing()))
    current = compare(scenario).current
    assert current is not None
    assert current[0].cells[0].estimate == calculate_estimate(scenario)
    sme = firm.model_copy(
        update={
            "jurisdiction": Jurisdiction(
                rf=USN_6.model_copy(update={"social_contributions": SocialContributions.SME})
            )
        }
    )
    usn15 = next(o for o in regime_options(sme) if o.profile.regime == "usn" and not o.is_current)
    assert isinstance(usn15.profile, UsnProfile)
    assert usn15.profile.social_contributions == SocialContributions.SME


def test_both_mode_has_two_country_groups() -> None:
    firm = demo_firm().model_copy(
        update={
            "jurisdiction": Jurisdiction(mode=JurisdictionMode.BOTH, rf=USN_6, kz=KzOurProfile()),
            "exchange_rates": ExchangeRates(rub_per_kzt=d("0.2")),
        }
    )
    result = compare(Scenario(firm=firm, project=example_project(HourlyPricing())))
    assert result.currency == Currency.RUB
    countries = [row.option.country for row in result.rows]
    assert countries == [Country.RF] * 5 + [Country.KZ] * 2
    kz_rows = [row for row in result.rows if row.option.country == Country.KZ]
    assert [row.option.profile for row in kz_rows] == [
        KzOurProfile(),
        KzOurProfile(form=KzForm.IP),
    ]
    estimate = kz_rows[0].cells[0].estimate
    assert estimate is not None
    assert estimate.currency == Currency.KZT
    # 100 000 ₽ по курсу 0,2 ₽ за 1 ₸.
    assert estimate.revenue == d(500_000)
    assert not kz_rows[0].option.is_current
    assert sum(row.option.is_current for row in result.rows) == 1
    kz_all = [
        row.option.profile
        for row in compare(
            Scenario(firm=firm, project=example_project(HourlyPricing())), all_variants=True
        ).rows
        if row.option.country == Country.KZ
    ]
    assert kz_all == [KzOurProfile(form=form, vat=vat) for form in KzForm for vat in KzVat]


def test_missing_rate_is_reported_in_cell() -> None:
    firm = demo_firm().model_copy(
        update={
            "jurisdiction": Jurisdiction(mode=JurisdictionMode.BOTH, rf=USN_6, kz=KzOurProfile()),
            "exchange_rates": ExchangeRates(),
        }
    )
    result = compare(Scenario(firm=firm, project=example_project(HourlyPricing())))
    kz = next(row for row in result.rows if row.option.country == Country.KZ)
    assert kz.cells[0].estimate is None
    assert "курс" in (kz.cells[0].error or "")


@settings(max_examples=60, deadline=None)
@given(scenario=scenarios())
def test_current_cell_is_project_estimate_and_availability(scenario: Scenario) -> None:
    result = compare(scenario, cap=d(50_000))
    current = result.current
    assert current is not None
    row, column = current
    cell = row.cells[column]
    assert cell.estimate == calculate_estimate(scenario)
    firm = scenario.firm
    for item in result.rows:
        profile = item.option.profile
        issues = check_profile(profile, firm.facts, employee_headcount(firm, profile.country))
        assert item.available == is_available(issues)
    assert isinstance(result.columns[1].pricing, CappedPricing)
