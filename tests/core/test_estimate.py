"""Расчёт сметы: оркестрация страницы «Проект» v0.3 в ядре."""

from __future__ import annotations

import pytest

from rentab_core.errors import FixedPriceError
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    Assignment,
    Country,
    DisbursementKind,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    OsnoProfile,
    Stage,
)
from rentab_core.money import Currency
from rentab_core.results import FixedPriceEstimate, HourlyEstimate, StageFlag, UnresolvedAssignment
from tests.core.factories import basic_scenario, d, disbursement, fixed, stage


def test_hourly_estimate_matches_v03_hand_calculation() -> None:
    # Сценарий 1 из legacy/rentab_v03/test_scenario.py.
    estimate = calculate_estimate(basic_scenario())
    assert isinstance(estimate, HourlyEstimate)
    assert estimate.gross_revenue == d(165_000)
    assert estimate.direct_labor == d(92_500)
    assert estimate.blended_rate == d(8_250)
    assert estimate.leverage == d(3)
    assert estimate.overhead_rate == d(500)
    assert estimate.overheads_alloc == d(10_000)
    assert estimate.tax.total_tax == d(9_900)
    assert estimate.nne == d(52_600)
    assert estimate.total_client == d(165_000)
    assert estimate.regime_label == "УСН income (6%)"
    assert estimate.currency == Currency.RUB


def test_hourly_estimate_with_disbursements() -> None:
    scenario = basic_scenario(
        disbursements=(
            disbursement(DisbursementKind.BILLABLE, 38_500),
            disbursement(DisbursementKind.OWN, 3_000),
            disbursement(DisbursementKind.EXTRA, 7_000),
        )
    )
    estimate = calculate_estimate(scenario)
    assert isinstance(estimate, HourlyEstimate)
    # Пошлины — транзит: в счёте есть, в базе налога нет.
    assert estimate.total_client == d(165_000 + 38_500)
    assert estimate.tax.taxable_base == d(165_000)
    # Собственные расходы уменьшают NNE; «Прочее» — нет (KNOWN-BUG L-12 → фаза 2).
    assert estimate.nne == d(52_600 - 3_000)


def test_fixed_estimate_totals() -> None:
    scenario = basic_scenario(
        fixed(), stages=(stage("Подготовка", "Иванов", 10), stage("Регистрация", "Петрова", 15))
    )
    estimate = calculate_estimate(scenario)
    assert isinstance(estimate, FixedPriceEstimate)
    # Себестоимость 8 000 × 10 + 3 500 × 15 = 132 500, накладные 500 × 25 = 12 500.
    assert estimate.total_costs == d(145_000)
    assert estimate.target_margin == d("0.30")
    assert estimate.total_client == estimate.fixed_price
    assert [s.stage_name for s in estimate.stages] == ["Подготовка", "Регистрация"]


def test_fixed_estimate_raises_when_unreachable() -> None:
    scenario = basic_scenario(fixed("0.80"), jurisdiction=Jurisdiction(rf=OsnoProfile()))
    with pytest.raises(FixedPriceError):
        calculate_estimate(scenario)


def test_unknown_executor_is_skipped_and_reported() -> None:
    scenario = basic_scenario(
        stages=(stage("Анализ", "Иванов", 5), stage("Этап уволенного", "Удалённый", 40))
    )
    estimate = calculate_estimate(scenario)
    assert estimate.total_hours == d(5)
    assert estimate.unresolved_assignments == (
        UnresolvedAssignment(stage_name="Этап уволенного", employee="Удалённый"),
    )


def test_stage_with_several_assignments() -> None:
    # Ядро допускает несколько исполнителей на этапе, как FixedPriceCalculator v0.3.
    pyramid = Stage(
        name="Ответ на уведомление",
        assignments=(
            Assignment(employee="Иванов", hours=d(2)),
            Assignment(employee="Петрова", hours=d(10)),
        ),
    )
    estimate = calculate_estimate(basic_scenario(fixed(), stages=(pyramid,)))
    assert isinstance(estimate, FixedPriceEstimate)
    assert len(estimate.lines) == 2
    (only,) = estimate.stages
    assert only.revenue_share == estimate.fixed_price
    assert only.flag == StageFlag.GREEN


def test_both_mode_uses_project_country() -> None:
    jurisdiction = Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile())
    rf = calculate_estimate(basic_scenario(jurisdiction=jurisdiction))
    assert rf.calc_country == Country.RF
    scenario = basic_scenario(jurisdiction=jurisdiction)
    kz_scenario = scenario.model_copy(
        update={"project": scenario.project.model_copy(update={"calc_country": Country.KZ})}
    )
    kz = calculate_estimate(kz_scenario)
    assert kz.calc_country == Country.KZ
    assert kz.currency == Currency.KZT
    assert kz.jurisdiction_mode == JurisdictionMode.BOTH
    assert kz.regime_label == "ОУР — ТОО (КПН) 20%"


def test_vat_is_treated_as_firm_expense() -> None:
    # KNOWN-BUG L-02 → фаза 2: НДС входит в налог и уменьшает NNE, а клиенту
    # выставляются только услуги и пошлины; в фикс-цене НДС стоит в знаменателе.
    jurisdiction = Jurisdiction(rf=OsnoProfile())
    hourly = calculate_estimate(basic_scenario(jurisdiction=jurisdiction))
    assert isinstance(hourly, HourlyEstimate)
    assert hourly.tax.vat == d(165_000) * d("0.22")
    assert hourly.tax.total_tax == hourly.tax.income_tax + hourly.tax.vat
    assert hourly.nne == d(165_000) - d(92_500) - d(10_000) - hourly.tax.total_tax
    assert hourly.total_client == hourly.gross_revenue

    priced = calculate_estimate(basic_scenario(fixed(), jurisdiction=jurisdiction))
    assert isinstance(priced, FixedPriceEstimate)
    costs = priced.total_costs
    closed_form = costs * (1 - d("0.25")) / (1 - d("0.25") - d("0.22") - d("0.30"))
    assert abs(priced.fixed_price - closed_form) < d("0.01")
