"""Расчёт сметы: оркестрация ядра по методологии фазы 2."""

from __future__ import annotations

import pytest

from rentab_core.errors import CurrencyConversionError, FixedPriceError
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    AusnProfile,
    CappedPricing,
    Country,
    Disbursement,
    DisbursementKind,
    Employee,
    ExchangeRates,
    Firm,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    MonthlyCost,
    OsnoProfile,
    Overhead,
    Period,
    PricingModel,
    Project,
    Role,
    Salary,
    Scenario,
    UsnProfile,
    UsnVat,
    VatMode,
)
from rentab_core.money import Currency
from rentab_core.results import StageFlag, UnresolvedAssignment
from tests.core.factories import basic_scenario, d, disbursement, fixed, stage


def test_hourly_estimate_control_scenario() -> None:
    # Сценарий 1 из legacy/rentab_v03/test_scenario.py (нижняя таблица слайда 3).
    estimate = calculate_estimate(basic_scenario())
    assert estimate.shadow_value == d(165_000)
    assert estimate.revenue == d(165_000)
    assert estimate.direct_labor == d(92_500)
    assert estimate.blended_rate == d(8_250)
    assert estimate.leverage == d(3)
    assert estimate.overhead_rate == d(500)
    assert estimate.overheads_alloc == d(10_000)
    assert estimate.total_costs == d(102_500)
    assert estimate.tax.tax == d(9_900)
    assert estimate.vat == 0
    assert estimate.nne == d(52_600)
    assert estimate.margin == d(52_600) / d(165_000)
    assert estimate.total_client == d(165_000)
    assert estimate.regime_label == "УСН income (6%)"
    assert estimate.currency == Currency.RUB


def test_underutilization_cost() -> None:
    # Факторные сотрудники: H = 160, u = 0,5 → c = 2ĉ, недозагрузка = половина трудозатрат.
    estimate = calculate_estimate(basic_scenario())
    assert estimate.underutilization_cost == d(92_500) / 2
    line = estimate.lines[0]
    assert line.cost_rate == d(8_000)
    assert line.contract_cost_rate == d(4_000)


def test_disbursements_are_transit_or_own_costs() -> None:
    scenario = basic_scenario(
        jurisdiction=Jurisdiction(rf=UsnProfile(vat=UsnVat.VAT_5)),
        disbursements=(
            disbursement(DisbursementKind.BILLABLE, 38_500),
            disbursement(DisbursementKind.OWN, 3_000),
            # «Прочее» v0.3 (нотариальный перевод) — тоже собственный расход (L-12).
            disbursement(DisbursementKind.OWN, 7_000),
        ),
    )
    estimate = calculate_estimate(scenario)
    # Пошлины — транзит: в счёте есть, в выручке и базе налога нет.
    assert estimate.revenue == d(165_000)
    assert estimate.disbursements_billed == d(38_500)
    assert estimate.disbursements_own == d(10_000)
    assert estimate.tax.tax == d(9_900)
    # НДС сверху: к оплате R + НДС + D (L-02).
    assert estimate.vat == d(8_250)
    assert estimate.price_with_vat == d(173_250)
    assert estimate.total_client == d(165_000 + 8_250 + 38_500)
    assert estimate.nne == d(52_600 - 10_000)


def test_vat_does_not_reduce_nne() -> None:
    # L-02: ОСНО + НДС 22% — NNE как у ОСНО без НДС; клиент платит НДС сверху.
    estimate = calculate_estimate(basic_scenario(jurisdiction=Jurisdiction(rf=OsnoProfile())))
    assert estimate.tax.tax == d(15_625)
    assert estimate.vat == d(36_300)
    assert estimate.nne == d(46_875)
    assert estimate.total_client == d(201_300)


def test_prices_including_vat() -> None:
    base = basic_scenario(jurisdiction=Jurisdiction(rf=UsnProfile(vat=UsnVat.VAT_5)))
    scenario = base.model_copy(
        update={"project": base.project.model_copy(update={"vat_mode": VatMode.INCLUDED})}
    )
    estimate = calculate_estimate(scenario)
    assert estimate.shadow_value == d(165_000)
    assert estimate.revenue == d(165_000) / d("1.05")
    assert estimate.price_with_vat == d(165_000)
    assert estimate.write_off == 0


def test_cost_plus_fixed_price() -> None:
    scenario = basic_scenario(
        fixed(), stages=(stage("Подготовка", ("Иванов", 10)), stage("Регистрация", ("Петрова", 15)))
    )
    estimate = calculate_estimate(scenario)
    # Себестоимость 8 000 × 10 + 3 500 × 15 = 132 500, накладные 500 × 25 = 12 500.
    assert estimate.total_costs == d(145_000)
    assert estimate.revenue == d(145_000) / d("0.64")
    assert estimate.margin == d("0.30")
    assert estimate.target_margin == d("0.30")
    assert estimate.total_client == estimate.revenue
    assert [s.stage_name for s in estimate.stages] == ["Подготовка", "Регистрация"]


def test_cost_plus_hits_target_on_ausn_with_salary() -> None:
    # L-23: ставка взносов 30% осталась после смены режима на АУСН; налог АУСН на
    # взносы не уменьшается, поэтому цена их не вычитает и маржа равна цели.
    salaried = Employee(
        name="Юрист",
        role=Role.ASSOCIATE,
        compensation=Salary(gross_salary=d(100_000), employer_contribution_rate=d("0.30")),
        billing_rate=d(3_000),
    )
    scenario = Scenario(
        firm=Firm(jurisdiction=Jurisdiction(rf=AusnProfile()), team=(salaried,)),
        project=Project(pricing=fixed(), stages=(stage("Работа", ("Юрист", 10)),)),
    )
    estimate = calculate_estimate(scenario)
    assert abs(estimate.margin - d("0.30")) < d("1e-20")  # до исправления — 11,4%
    assert estimate.tax.deduction == 0


def test_agreed_fixed_price() -> None:
    estimate = calculate_estimate(basic_scenario(fixed(150_000)))
    assert estimate.revenue == d(150_000)
    assert estimate.nne == d(150_000) - d(102_500) - d(9_000)


@pytest.mark.parametrize("pricing", [fixed(), CappedPricing(cap=d(10_000))])
def test_revenue_given_from_outside(pricing: PricingModel) -> None:
    # Цена держится (раздел 16.3) или выставлена вручную (раздел 18.2): смета —
    # как у согласованной цены, модель цены проекта сохраняется.
    scenario = basic_scenario(pricing)
    estimate = calculate_estimate(scenario, revenue=d(150_000))
    agreed = calculate_estimate(basic_scenario(fixed(150_000)))
    assert estimate.pricing == scenario.project.pricing
    assert estimate.revenue == d(150_000)
    assert estimate.tax == agreed.tax
    assert estimate.nne == agreed.nne
    assert estimate.stages == agreed.stages


def test_capped_fee() -> None:
    estimate = calculate_estimate(basic_scenario(CappedPricing(cap=d(150_000))))
    assert estimate.revenue == d(150_000)
    assert estimate.write_off == d(15_000)
    uncapped = calculate_estimate(basic_scenario(CappedPricing(cap=d(200_000))))
    assert uncapped.revenue == d(165_000)
    assert uncapped.write_off == 0


def test_capped_fee_including_vat() -> None:
    # Ставки и потолок «включая НДС»: списание считается без НДС.
    base = basic_scenario(
        CappedPricing(cap=d(157_500)), jurisdiction=Jurisdiction(rf=UsnProfile(vat=UsnVat.VAT_5))
    )
    scenario = base.model_copy(
        update={"project": base.project.model_copy(update={"vat_mode": VatMode.INCLUDED})}
    )
    estimate = calculate_estimate(scenario)
    assert estimate.revenue == d(150_000)
    assert estimate.shadow_value_net == d(165_000) / d("1.05")
    assert estimate.write_off == d(165_000) / d("1.05") - d(150_000)


def test_fixed_estimate_raises_when_unreachable() -> None:
    scenario = basic_scenario(
        fixed(), jurisdiction=Jurisdiction(rf=OsnoProfile()), target_margin="0.80"
    )
    with pytest.raises(FixedPriceError, match="недостижима"):
        calculate_estimate(scenario)


def test_unknown_executor_is_skipped_and_reported() -> None:
    scenario = basic_scenario(
        stages=(stage("Анализ", ("Иванов", 5)), stage("Этап уволенного", ("Удалённый", 40)))
    )
    estimate = calculate_estimate(scenario)
    assert estimate.total_hours == d(5)
    assert estimate.unresolved_assignments == (
        UnresolvedAssignment(stage_name="Этап уволенного", employee="Удалённый"),
    )
    assert [s.stage_name for s in estimate.stages] == ["Анализ"]


def test_own_expense_of_skipped_stage_is_spread() -> None:
    scenario = basic_scenario(
        stages=(stage("Анализ", ("Иванов", 5)), stage("Этап уволенного", ("Удалённый", 40))),
        disbursements=(disbursement(DisbursementKind.OWN, 3_000, stage="Этап уволенного"),),
    )
    (only,) = calculate_estimate(scenario).stages
    assert only.own == d(3_000)


def test_pyramid_on_one_stage() -> None:
    # L-05: «партнёр проверяет 2 ч, юрист делает 10 ч» — один этап.
    pyramid = stage("Ответ на уведомление", ("Иванов", 2), ("Петрова", 10))
    estimate = calculate_estimate(basic_scenario(fixed(), stages=(pyramid,)))
    assert len(estimate.lines) == 2
    (only,) = estimate.stages
    assert only.hours == d(12)
    assert only.revenue == estimate.revenue
    assert only.flag == StageFlag.GREEN


def _both_firm(rates: ExchangeRates) -> Firm:
    team = (
        Employee(
            name="Сидоров",
            role=Role.ASSOCIATE,
            compensation=MonthlyCost(amount=d(280_000)),
            billing_rate=d(6_000),
            contract_hours_per_month=d(160),
            target_utilization=d("0.5"),
        ),
    )
    return Firm(
        jurisdiction=Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile()),
        team=team,
        overheads=(
            Overhead(name="Москва", amount=d(120_000), period=Period.MONTHLY),
            Overhead(
                name="Алматы", amount=d(300_000), currency=Currency.KZT, period=Period.MONTHLY
            ),
        ),
        exchange_rates=rates,
    )


def _both_project(country: Country) -> Project:
    return Project(
        stages=(stage("Регистрация", ("Сидоров", 10)),),
        disbursements=(
            Disbursement(name="Роспатент", kind=DisbursementKind.BILLABLE, amount=d(38_500)),
            Disbursement(
                name="Казпатент",
                kind=DisbursementKind.BILLABLE,
                amount=d(120_000),
                currency=Currency.KZT,
            ),
        ),
        calc_country=country,
    )


def test_both_mode_converts_currencies() -> None:
    # L-06 и пример раздела 9 методологии: курс 0,18 ₽ за 1 ₸.
    firm = _both_firm(ExchangeRates(rub_per_kzt=d("0.18")))
    rf = calculate_estimate(Scenario(firm=firm, project=_both_project(Country.RF)))
    assert rf.currency == Currency.RUB
    assert rf.overhead_rate == d(174_000) / d(80)
    assert rf.disbursements_billed == d(60_100)
    kazpatent = rf.disbursements[1]
    assert (kazpatent.original_amount, kazpatent.original_currency) == (d(120_000), Currency.KZT)
    assert kazpatent.amount == d(21_600)

    kz = calculate_estimate(Scenario(firm=firm, project=_both_project(Country.KZ)))
    assert kz.currency == Currency.KZT
    assert kz.regime_label == "ОУР — ТОО (КПН) 20%"
    # Ставка и себестоимость сотрудника из рублей переведены в тенге.
    assert kz.lines[0].billing_rate == d(6_000) / d("0.18")
    assert kz.lines[0].cost_rate == d(3_500) / d("0.18")
    assert kz.disbursements_billed == d(38_500) / d("0.18") + d(120_000)
    assert kz.revenue == rf.revenue / d("0.18")


def test_missing_exchange_rate_is_reported() -> None:
    firm = _both_firm(ExchangeRates())
    with pytest.raises(CurrencyConversionError, match="₽ за 1 ₸"):
        calculate_estimate(Scenario(firm=firm, project=_both_project(Country.RF)))
