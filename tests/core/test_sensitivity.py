"""Чувствительность: запас часов, торнадо, «что если» (docs/methodology.md, разделы 16–17).

Golden-тесты повторяют разобранные примеры разделов 16–17; property-тесты
сверяют закрытую формулу запаса часов с полным пересчётом сметы ядром.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings

from rentab_core.analysis.sensitivity import (
    BreakEven,
    BreakEvenStatus,
    Factor,
    PriceRule,
    TaxBranch,
    TornadoNote,
    WhatIf,
    apply_what_if,
    break_even,
    foreign_currencies,
    held_revenue,
    tornado,
    what_if,
)
from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    Assignment,
    CappedPricing,
    Country,
    Disbursement,
    DisbursementKind,
    Employee,
    ExchangeRates,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    Overhead,
    Period,
    PricingModel,
    Project,
    Role,
    Salary,
    Scenario,
    Stage,
    UsnProfile,
)
from rentab_core.money import Currency, round_to
from tests.core.factories import d, fixed, stage
from tests.core.strategies import scenarios
from tests.core.test_methodology_examples import (
    TEAM,
    USN_6_VAT_5,
    USN_15_VAT_5,
    example_firm,
    example_project,
)

SLIDE3_HOURS = (("Партнёр", 5), ("Юрист", 15), ("Стажёр", 20))
SLIDE3_FACT = (("Партнёр", 62), ("Юрист", 15), ("Стажёр", 10))
HOURS_QUANTUM = d("0.01")


def slide3(hours: tuple[tuple[str, int], ...] = SLIDE3_HOURS) -> Scenario:
    """Слайд 3 (раздел 12): согласованная цена 300 000 ₽, УСН 6% + НДС 5%."""
    return Scenario(
        firm=example_firm(USN_6_VAT_5),
        project=Project(name="Слайд 3", pricing=fixed(300_000), stages=(stage("Проект", *hours),)),
    )


def example1(pricing: PricingModel, profile: UsnProfile = USN_6_VAT_5) -> Scenario:
    """Пример 1 (раздел 10): товарный знак, два этапа."""
    return Scenario(firm=example_firm(profile), project=example_project(pricing))


def rows(scenario: Scenario, *, written_off: bool = False) -> dict[str | None, BreakEven]:
    report = break_even(scenario)
    return {row.employee: row for row in (report.written_off if written_off else report.rows)}


def hours(row: BreakEven) -> Decimal:
    assert row.extra_hours is not None
    return round_to(row.extra_hours, HOURS_QUANTUM)


# ---------------------------------------------------------------------------
# Запас часов: разобранные примеры
# ---------------------------------------------------------------------------
def test_slide3_break_even() -> None:
    # Раздел 16.7: партнёр может потратить ещё 55 ч.
    result = rows(slide3())
    assert break_even(slide3()).rule == PriceRule.FIXED
    expected = {
        None: ("1412.5", "159.65"),
        "Партнёр": ("4100", "55.00"),
        "Юрист": ("1600", "140.94"),
        "Стажёр": ("600", "375.83"),
    }
    for employee, (cost, extra) in expected.items():
        row = result[employee]
        assert row.status == BreakEvenStatus.HEADROOM
        assert row.cost_per_hour == d(cost)
        assert hours(row) == d(extra)
        assert row.branch == TaxBranch.INCOME
        assert not row.capped
    partner = result["Партнёр"]
    assert partner.role == Role.PARTNER
    assert partner.plan_hours == d(5)
    assert partner.total_hours == d(60)
    assert partner.share_of_plan == d(11)
    assert partner.nne_per_hour == d(-4100)
    assert result[None].plan_hours == d(40)


def test_slide3_fact_is_loss_by_partner_hours() -> None:
    # NNE факта −2 200: в ноль — если партнёр потратит на 2 200 / 4 100 ч меньше.
    result = rows(slide3(SLIDE3_FACT))
    partner = result["Партнёр"]
    assert partner.status == BreakEvenStatus.LOSS
    assert hours(partner) == d("-0.54")
    assert result["Стажёр"].status == BreakEvenStatus.LOSS
    assert hours(result["Стажёр"]) == d("-3.67")


def test_hourly_break_even_is_not_reached() -> None:
    # Раздел 16.8: каждый оплаченный час добавляет к NNE.
    report = break_even(example1(HourlyPricing()))
    assert report.rule == PriceRule.BILLED
    per_hour = {row.employee: row.nne_per_hour for row in report.rows}
    assert {key: round_to(value) for key, value in per_hour.items()} == {
        None: d(2525),
        "Партнёр": d(5300),
        "Юрист": d(3100),
        "Стажёр": d(1280),
    }
    assert all(row.status == BreakEvenStatus.NOT_REACHED for row in report.rows)
    assert all(row.extra_hours is None and row.total_hours is None for row in report.rows)


@pytest.mark.parametrize(
    ("profile", "expected", "branch"),
    [
        (
            USN_6_VAT_5,
            {None: "42.25", "Партнёр": "14.34", "Юрист": "36.75", "Стажёр": "98.00"},
            TaxBranch.INCOME,
        ),
        (
            USN_15_VAT_5,
            {None: "45.84", "Партнёр": "15.56", "Юрист": "39.88", "Стажёр": "106.33"},
            TaxBranch.MIN_TAX,
        ),
    ],
)
def test_hourly_written_off_break_even(
    profile: UsnProfile, expected: dict[str | None, str], branch: TaxBranch
) -> None:
    # Часы сверх плана не выставляются: выручка остаётся 100 000 ₽.
    result = rows(example1(HourlyPricing(), profile), written_off=True)
    assert {key: hours(row) for key, row in result.items()} == {
        key: d(value) for key, value in expected.items()
    }
    assert {row.branch for row in result.values()} == {branch}


def test_min_tax_shortens_headroom() -> None:
    # Без минимального налога запас партнёра — 64 800 / 4 100 = 15,80 ч.
    partner = rows(example1(HourlyPricing(), USN_15_VAT_5), written_off=True)["Партнёр"]
    profit_line = next(line for line in partner.lines if line.branch == TaxBranch.PROFIT)
    assert round_to(profit_line.alpha / profit_line.beta, HOURS_QUANTUM) == d("15.80")


def test_capped_break_even_bills_hours_up_to_cap() -> None:
    # Потолок 120 000 ₽: первые 2 ч партнёра оплачиваются, запас 18,93 ч.
    partner = rows(example1(CappedPricing(cap=d(120_000))))["Партнёр"]
    assert partner.status == BreakEvenStatus.HEADROOM
    assert hours(partner) == d("18.93")
    assert partner.capped
    assert partner.nne_per_hour == d(5300)
    assert round_to(partner.nne_at(d(2))) == d(69_400)
    # Потолок 90 000 ₽ уже сработал: 49 400 / 4 100.
    capped90 = rows(example1(CappedPricing(cap=d(90_000))))["Партнёр"]
    assert hours(capped90) == d("12.05")
    assert capped90.nne_per_hour == d(-4100)


def test_cost_plus_price_holds() -> None:
    # Раздел 16.6: цена 55 000 ₽ держится.
    result = rows(example1(fixed()))
    assert hours(result["Партнёр"]) == d("4.02")
    assert hours(result[None]) == d("11.86")
    assert break_even(example1(fixed())).written_off == ()


def test_contributions_deduction_cap_binds() -> None:
    # Юрист с окладом 120 000 ₽ и взносами 25% (раздел 11.6): s = 300 ₽/ч. Налог
    # упёрся в предел 50%, взносы с новых часов его уже не уменьшают.
    team = (
        TEAM[0],
        TEAM[1].model_copy(
            update={
                "compensation": Salary(
                    gross_salary=d(120_000), employer_contribution_rate=d("0.25")
                )
            }
        ),
        TEAM[2],
    )
    scenario = example1(fixed(100_000))
    scenario = scenario.model_copy(update={"firm": scenario.firm.model_copy(update={"team": team})})
    lawyer = rows(scenario)["Юрист"]
    assert round_to(lawyer.nne) == d(61_800)
    assert lawyer.extra_hours == d("38.625")
    assert lawyer.branch == TaxBranch.DEDUCTION_CAP
    contributions = next(line for line in lawyer.lines if line.branch == TaxBranch.CONTRIBUTIONS)
    assert contributions.alpha == d(62_400)
    assert contributions.beta == d(1_300)


def test_loss_at_any_hours_and_no_hours() -> None:
    scenario = slide3()
    expensive = scenario.project.model_copy(
        update={
            "disbursements": (
                Disbursement(name="Командировка", kind=DisbursementKind.OWN, amount=d(400_000)),
            )
        }
    )
    result = rows(scenario.model_copy(update={"project": expensive}))
    assert {row.status for row in result.values()} == {BreakEvenStatus.LOSS_ANY_HOURS}
    assert all(row.extra_hours is None for row in result.values())
    empty = scenario.model_copy(update={"project": Project(pricing=fixed(1_000))})
    mix = rows(empty)[None]
    assert mix.status == BreakEvenStatus.NO_HOURS
    assert mix.share_of_plan is None
    assert rows(empty)["Партнёр"].status == BreakEvenStatus.HEADROOM


def test_hourly_loss_needs_more_hours() -> None:
    # Часы прибыльны, но собственный расход больше прибыли плана: в ноль — при
    # бо́льших оплачиваемых часах.
    scenario = example1(HourlyPricing())
    project = scenario.project.model_copy(
        update={
            "disbursements": (
                Disbursement(name="Перевод", kind=DisbursementKind.OWN, amount=d(70_000)),
            )
        }
    )
    partner = rows(scenario.model_copy(update={"project": project}))["Партнёр"]
    assert partner.status == BreakEvenStatus.LOSS
    # Пошлина и перевод заменены расходом 70 000 ₽: NNE = 100 000 − 103 400 − 6 000 =
    # −9 400, каждый час партнёра +5 300.
    assert hours(partner) == d("1.77")


def test_employee_without_rate_is_skipped() -> None:
    usd = Employee(
        name="Консультант",
        role=Role.SENIOR,
        compensation=TEAM[0].compensation,
        currency=Currency.USD,
    )
    scenario = slide3()
    scenario = scenario.model_copy(
        update={"firm": scenario.firm.model_copy(update={"team": (*TEAM, usd)})}
    )
    assert "Консультант" not in rows(scenario)


# ---------------------------------------------------------------------------
# Торнадо и «что если»
# ---------------------------------------------------------------------------
def deltas(scenario: Scenario) -> dict[Factor, tuple[Decimal, Decimal]]:
    result = tornado(scenario)
    return {bar.factor: (round_to(bar.low_delta), round_to(bar.high_delta)) for bar in result.bars}


def test_slide3_tornado() -> None:
    # Раздел 17.2.
    result = tornado(slide3())
    assert [bar.factor for bar in result.bars] == [
        Factor.UTILIZATION,
        Factor.HOURS,
        Factor.OVERHEADS,
        Factor.BILLING_RATES,
    ]
    assert deltas(slide3()) == {
        Factor.UTILIZATION: (d("-6277.78"), d("5136.36")),
        Factor.HOURS: (d(5650), d(-5650)),
        Factor.OVERHEADS: (d(400), d(-400)),
        Factor.BILLING_RATES: (d(0), d(0)),
    }
    notes = {bar.factor: bar.note for bar in result.bars}
    assert notes[Factor.BILLING_RATES] == TornadoNote.FIXED_PRICE
    assert notes[Factor.HOURS] is None
    assert result.bars[0].spread == result.bars[0].low_delta.copy_abs()


def test_example1_tornado_hourly_and_cost_plus() -> None:
    # Раздел 17.3: в почасовой модели часы — источник выручки, в фиксе — риск.
    assert deltas(example1(HourlyPricing())) == {
        Factor.BILLING_RATES: (d(-9400), d(9400)),
        Factor.HOURS: (d(-6060), d(6060)),
        Factor.UTILIZATION: (d("-3711.11"), d("3036.36")),
        Factor.OVERHEADS: (d(240), d(-240)),
    }
    cost_plus = deltas(example1(fixed()))
    assert cost_plus[Factor.HOURS] == (d(3340), d(-3340))
    assert cost_plus[Factor.BILLING_RATES] == (d(0), d(0))


def test_tornado_notes_cap_and_utilization() -> None:
    capped = tornado(example1(CappedPricing(cap=d(50_000))))
    notes = {bar.factor: bar.note for bar in capped.bars}
    assert notes[Factor.BILLING_RATES] == TornadoNote.CAP_REACHED
    # Утилизация 62,5% × 1,6 = 100%: упирается в предел.
    high = tornado(slide3(), d("0.7"))
    utilization = next(bar for bar in high.bars if bar.factor == Factor.UTILIZATION)
    assert utilization.note == TornadoNote.UTILIZATION_CAPPED
    with pytest.raises(ValueError, match="в \\(0; 1\\)"):
        tornado(slide3(), d(0))


def both_mode(project_currency_kz: bool) -> Scenario:
    """Фирма «Оба»: аренда в Алматы в тенге, пошлина Казпатента в тенге."""
    scenario = slide3()
    firm = scenario.firm.model_copy(
        update={
            "jurisdiction": Jurisdiction(
                mode=JurisdictionMode.BOTH, rf=USN_6_VAT_5, kz=KzOurProfile()
            ),
            "overheads": (
                *scenario.firm.overheads,
                Overhead(
                    name="Алматы", amount=d(50_000), currency=Currency.KZT, period=Period.MONTHLY
                ),
            ),
            "exchange_rates": ExchangeRates(rub_per_kzt=d("0.2"), rub_per_usd=d(90)),
        }
    )
    project = scenario.project.model_copy(
        update={
            "calc_country": Country.KZ if project_currency_kz else Country.RF,
            "disbursements": (
                Disbursement(
                    name="Пошлина",
                    kind=DisbursementKind.BILLABLE,
                    amount=d(10),
                    currency=Currency.USD,
                ),
            ),
        }
    )
    return Scenario(firm=firm, project=project)


def test_fx_factor_in_rub_project() -> None:
    scenario = both_mode(project_currency_kz=False)
    base = calculate_estimate(scenario)
    assert foreign_currencies(scenario, base) == {Currency.KZT: True, Currency.USD: False}
    bars = {bar.currency: bar for bar in tornado(scenario).bars if bar.factor == Factor.FX}
    # Накладные 50 000 ₸ × 0,2 = 10 000 ₽; тенге +10% — ещё 1 000 ₽ накладных в месяц.
    # Ставка накладных растёт на 1 000 / 300 ч, на 40 ч проекта — 133,33 ₽.
    assert round_to(bars[Currency.KZT].high_delta) == d("-133.33")
    assert bars[Currency.USD].note == TornadoNote.TRANSIT_ONLY
    assert bars[Currency.USD].spread == 0


def test_fx_factor_in_kzt_project_moves_rub_amounts() -> None:
    scenario = both_mode(project_currency_kz=True)
    base = calculate_estimate(scenario)
    assert set(foreign_currencies(scenario, base)) == {Currency.RUB, Currency.USD}
    changed = apply_what_if(scenario, WhatIf(fx={Currency.RUB: d("0.25")}), base.currency)
    rates = changed.firm.exchange_rates
    # Рубль дорожает к тенге на 25%: тенге и доллар в рублях дешевеют, доллар к тенге — нет.
    assert rates.rub_per_kzt == d("0.16")
    assert rates.rub_per_usd == d(72)
    result = what_if(scenario, WhatIf(fx={Currency.RUB: d("0.25")}))
    assert result.changed.direct_labor == base.direct_labor * d("1.25")


def test_what_if_combines_factors_and_keeps_project() -> None:
    # Раздел 17.4: часы +100% и утилизация −10%.
    scenario = slide3()
    result = what_if(scenario, WhatIf(hours=d(1), utilization=d("-0.1")))
    assert round_to(result.changed.nne) == d("156444.44")
    assert round_to(result.changed.margin * 100) == d("52.15")
    assert result.base == calculate_estimate(scenario)
    assert result.repriced is None
    assert scenario == slide3()


def test_what_if_price_and_repriced_cost_plus() -> None:
    scenario = example1(fixed())
    result = what_if(scenario, WhatIf(hours=d("0.1")))
    # Цена держится на 55 000 ₽, пересчитанная — 60 218,75 ₽.
    assert result.changed.revenue == d(55_000)
    assert round_to(result.changed.nne) == d(13_160)
    assert result.repriced == d("60218.75")
    cheaper = what_if(scenario, WhatIf(price=d("-0.1")))
    assert cheaper.changed.revenue == d(49_500)
    agreed = what_if(slide3(), WhatIf(price=d("-0.1")))
    assert agreed.changed.revenue == d(270_000)
    capped = what_if(example1(CappedPricing(cap=d(90_000))), WhatIf(price=d("0.1")))
    assert capped.changed.revenue == d(99_000)
    hourly = what_if(example1(HourlyPricing()), WhatIf(price=d("0.5")))
    assert hourly.changed.revenue == d(100_000)


def test_what_if_utilization_is_capped_at_one() -> None:
    result = what_if(slide3(), WhatIf(utilization=d(1)))
    assert result.utilization_capped == ("Партнёр", "Юрист", "Стажёр")
    # u = 100%: себестоимость часа — контрактная, 400 000 / 160 = 2 500 ₽ у партнёра.
    partner = next(line for line in result.changed.lines if line.employee_name == "Партнёр")
    assert partner.cost_rate == d(2500)


# ---------------------------------------------------------------------------
# Property-тесты: закрытая формула против полного пересчёта сметы
# ---------------------------------------------------------------------------
def shifted(scenario: Scenario, employee: str | None, extra: Decimal) -> Scenario:
    """Сценарий с x дополнительными часами направления (микс или сотрудник)."""
    project = scenario.project
    total = sum(
        (
            item.hours
            for stage_ in project.stages
            for item in stage_.assignments
            if employee is None or item.employee == employee
        ),
        d(0),
    )
    if total == 0:
        assert employee is not None
        assert extra >= 0
        first = project.stages[0]
        stages = (
            first.model_copy(
                update={
                    "assignments": (*first.assignments, Assignment(employee=employee, hours=extra))
                }
            ),
            *project.stages[1:],
        )
    else:
        factor = 1 + extra / total
        stages = tuple(
            Stage(
                name=stage_.name,
                assignments=tuple(
                    item.model_copy(update={"hours": item.hours * factor})
                    if employee is None or item.employee == employee
                    else item
                    for item in stage_.assignments
                ),
            )
            for stage_ in project.stages
        )
    return scenario.model_copy(update={"project": project.model_copy(update={"stages": stages})})


def full_nne(
    scenario: Scenario, row: BreakEven, extra: Decimal, revenue: Decimal | None
) -> Decimal:
    return calculate_estimate(shifted(scenario, row.employee, extra), revenue=revenue).nne


@settings(max_examples=150, deadline=None)
@given(scenario=scenarios())
def test_break_even_matches_full_estimate(scenario: Scenario) -> None:
    base = calculate_estimate(scenario)
    report = break_even(scenario)
    held = held_revenue(scenario, base)
    for written_off, group in ((False, report.rows), (True, report.written_off)):
        revenue = base.revenue if written_off else held
        for row in group:
            if row.status == BreakEvenStatus.NO_HOURS:
                continue
            assert abs(row.nne_at(d(0)) - base.nne) < d("1e-9")
            for extra in (-row.plan_hours / 2, row.plan_hours, d(37)):
                if row.plan_hours + extra < 0 or (row.plan_hours == 0 and extra < 0):
                    continue
                expected = full_nne(scenario, row, extra, revenue)
                assert abs(row.nne_at(extra) - expected) < d("1e-6")
            if row.extra_hours is not None:
                assert row.extra_hours >= -row.plan_hours
                assert abs(full_nne(scenario, row, row.extra_hours, revenue)) < d("0.005")
            if row.status == BreakEvenStatus.HEADROOM:
                assert row.extra_hours is not None
                assert full_nne(scenario, row, row.extra_hours + 1, revenue) < 0
                if row.extra_hours > 0:
                    middle = full_nne(scenario, row, row.extra_hours / 2, revenue)
                    assert middle > d("-0.005")
            if row.status == BreakEvenStatus.NOT_REACHED:
                assert full_nne(scenario, row, d(50), revenue) >= base.nne - d("1e-6")


@settings(max_examples=60, deadline=None)
@given(scenario=scenarios())
def test_tornado_signs(scenario: Scenario) -> None:
    result = tornado(scenario)
    bars = {(bar.factor, bar.currency): bar for bar in result.bars}
    overheads = bars[(Factor.OVERHEADS, None)]
    assert overheads.high_delta <= d("1e-9")
    assert overheads.low_delta >= d("-1e-9")
    if isinstance(scenario.project.pricing, FixedPricing):
        assert bars[(Factor.BILLING_RATES, None)].spread == 0
        assert bars[(Factor.HOURS, None)].high_delta <= d("1e-9")
    spreads = [bar.spread for bar in result.bars]
    assert spreads == sorted(spreads, reverse=True)


@settings(max_examples=60, deadline=None)
@given(scenario=scenarios())
def test_neutral_what_if_is_the_estimate(scenario: Scenario) -> None:
    result = what_if(scenario, WhatIf())
    assert result.changed == result.base == calculate_estimate(scenario)
    assert apply_what_if(scenario, WhatIf(), result.base.currency) == scenario
