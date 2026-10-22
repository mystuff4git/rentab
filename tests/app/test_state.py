"""Сборка моделей из значений сессии без Streamlit: «запись → значения → запись»."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from rentab_core.analysis.sensitivity import WhatIf
from rentab_core.demo import demo_workspace
from rentab_core.duties import ApplicantCategory, Quantity
from rentab_core.fx import RateSource, RatesQuote
from rentab_core.io import DutySelection, ProjectRecord
from rentab_core.models import (
    DEFAULT_KZ_TAX_PROFILE,
    DEFAULT_RF_TAX_PROFILE,
    ActualHours,
    AusnProfile,
    Country,
    ExchangeRates,
    Firm,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    Project,
    SocialContributions,
)
from rentab_core.money import Currency
from tests.core.factories import d
from tests.core.test_io import full_featured_workspace
from ui import state


@pytest.mark.parametrize(
    "record",
    [
        *demo_workspace().projects,
        *full_featured_workspace().projects,
        ProjectRecord(
            project=Project(name="Ставка с НДС", pricing=FixedPricing(price=d(120_000))),
            duties={Country.KZ: DutySelection(category=ApplicantCategory.INDIVIDUAL)},
        ),
    ],
)
def test_project_values_round_trip(record: ProjectRecord) -> None:
    assert state.project_record(state.project_values(record)) == record


def test_project_values_for_widgets() -> None:
    record = demo_workspace().projects[1]
    values = state.project_values(record)
    assert values[state.PRICING_MODEL] == state.PRICING_HOURLY
    assert values[state.TARGET_MARGIN_PCT] == 30
    assert values[state.duty_quantity_key(Country.RF, Quantity.CLASSES)] == 3
    assert values[state.duty_quantity_key(Country.RF, Quantity.ITEMS_OVER_LIMIT)] == 0
    assert values[state.duty_category_key(Country.KZ)] == ApplicantCategory.LEGAL_ENTITY
    assert values[state.duty_multiselect_key(Country.RF)] == list(record.duties[Country.RF].actions)
    assert values[state.duty_multiselect_key(Country.KZ)] == []
    # Каждый ключ виджета задан явно, даже если модель цены его не использует.
    assert values[state.CALC_COUNTRY] is None
    assert values[state.CAP_AMOUNT] == values[state.AGREED_PRICE] == 0.0
    assert values[state.MARGIN_SLIDER] == 30


def test_full_values_fill_keys_missing_from_snapshot() -> None:
    values = {
        state.PROJECT_NAME: "Черновик",
        state.PRICING_MODEL: state.PRICING_CAPPED,
        state.TARGET_MARGIN_PCT: 90,
        state.SELECTED_DUTIES: {"RF": ["Пошлина"], "KZ": []},
    }
    full = state.full_values(values)
    assert full[state.PRICING_MODEL] == state.PRICING_CAPPED
    assert full[state.CAP_AMOUNT] == 0.0
    # 90% шире слайдера: слайдер — на значении по умолчанию, поле — 90.
    assert full[state.TARGET_MARGIN_PCT] == 90
    assert full[state.MARGIN_SLIDER] == state.DEFAULT_TARGET_MARGIN_PCT
    assert full[state.duty_multiselect_key(Country.RF)] == ["Пошлина"]
    assert set(state.full_values({})) == set(state.project_values(ProjectRecord()))


def test_incomplete_price_is_kept_as_hourly_draft() -> None:
    values = {state.PRICING_MODEL: state.PRICING_AGREED, state.AGREED_PRICE: 0.0}
    assert not state.pricing_complete(values)
    assert state.project_record(values, strict=False).project.pricing == HourlyPricing()
    with pytest.raises(ValueError, match="greater than 0"):
        state.project_record(values)


@pytest.mark.parametrize(
    ("margin", "pct"),
    [("0.30", 30), ("0.325", 33), ("0.3249", 32), ("0", 0), ("0.996", 99)],
)
def test_margin_pct(margin: str, pct: int) -> None:
    assert state.margin_pct(Decimal(margin)) == pct


@pytest.mark.parametrize(
    "jurisdiction",
    [
        Jurisdiction(rf=AusnProfile()),
        Jurisdiction(rf=NpdProfile(client_type=NpdClientType.INDIVIDUAL)),
        Jurisdiction(rf=OsnoProfile(social_contributions=SocialContributions.SME)),
        full_featured_workspace().firm.jurisdiction,
        Jurisdiction(mode=JurisdictionMode.KZ, kz=DEFAULT_KZ_TAX_PROFILE),
    ],
)
def test_firm_values_round_trip(jurisdiction: Jurisdiction) -> None:
    firm = full_featured_workspace().firm.model_copy(update={"jurisdiction": jurisdiction})
    quote = RatesQuote(
        rates=firm.exchange_rates,
        source=RateSource.CBR,
        rate_date=dt.date(2026, 10, 22),
        requested=dt.date(2026, 10, 22),
    )
    values = state.firm_values(firm, quote)
    assert state.firm(values) == firm
    assert state.fx_quote(values) == quote
    assert values[state.FX_DATE] == quote.requested


def test_firm_without_rates_and_defaults() -> None:
    values = state.firm_values(Firm())
    assert values[state.RUB_PER_KZT] == state.NO_RATE
    assert state.FX_QUOTE not in values
    assert state.firm(values) == Firm(jurisdiction=Jurisdiction(rf=DEFAULT_RF_TAX_PROFILE))
    assert state.exchange_rates(values) == ExchangeRates()


def test_normalized_jurisdiction_matches_setup_page() -> None:
    both = state.normalized_jurisdiction(Jurisdiction(mode=JurisdictionMode.BOTH))
    assert both == Jurisdiction(
        mode=JurisdictionMode.BOTH, rf=DEFAULT_RF_TAX_PROFILE, kz=DEFAULT_KZ_TAX_PROFILE
    )
    kz_only = state.normalized_jurisdiction(
        Jurisdiction(mode=JurisdictionMode.KZ, rf=OsnoProfile())
    )
    assert kz_only == Jurisdiction(mode=JurisdictionMode.KZ, kz=DEFAULT_KZ_TAX_PROFILE)


def test_snapshot_copies_mutable_values() -> None:
    values = state.project_values(demo_workspace().projects[1])
    values["unrelated"] = 1
    copy = state.snapshot(values)
    assert "unrelated" not in copy
    assert copy[state.PROJECT_STAGES] == values[state.PROJECT_STAGES]
    assert copy[state.PROJECT_STAGES] is not values[state.PROJECT_STAGES]
    assert copy[state.SELECTED_DUTIES]["RF"] is not values[state.SELECTED_DUTIES]["RF"]


def test_project_values_set_fact_and_analytics_keys() -> None:
    record = demo_workspace().projects[0]
    values = state.project_values(record)
    assert record.project.actuals is not None
    assert values[state.ACTUAL_HOURS] == list(record.project.actuals.hours)
    assert values[state.ACTUAL_BILLED_MANUAL] is False
    assert values[state.ACTUAL_BILLED] == 0.0
    # «Что если», торнадо и потолок для сравнения задаются явно для каждого проекта.
    assert all(values[key] == 0 for key in state.WHATIF_KEYS)
    assert values[state.TORNADO_PCT] == 10
    assert values[state.COMPARE_CAP] == 0.0
    empty = state.project_values(ProjectRecord())
    assert empty[state.ACTUAL_HOURS] is None
    assert state.project_record(empty).project.actuals is None


def test_manual_billed_round_trip_and_draft() -> None:
    record = demo_workspace().projects[1]
    assert record.project.actuals is not None
    billed = record.project.actuals.model_copy(update={"billed": d(120_000)})
    manual = record.model_copy(
        update={"project": record.project.model_copy(update={"actuals": billed})}
    )
    values = state.project_values(manual)
    assert values[state.ACTUAL_BILLED_MANUAL] is True
    assert values[state.ACTUAL_BILLED] == 120_000.0
    assert state.project_record(values) == manual
    # «Вручную» без суммы — по модели цены.
    draft = {**values, state.ACTUAL_BILLED: 0.0}
    actuals = state.project_record(draft).project.actuals
    assert actuals is not None
    assert actuals.billed is None


def test_fact_of_deleted_stage_is_not_kept() -> None:
    values = state.project_values(demo_workspace().projects[1])
    values[state.PROJECT_STAGES] = values[state.PROJECT_STAGES][:1]
    values[state.CUSTOM_EXPENSES] = []
    actuals = state.project_record(values).project.actuals
    assert actuals is not None
    assert {row.stage for row in actuals.hours} == {"Анализ охраноспособности"}


def test_what_if_from_percent_sliders() -> None:
    values = {
        state.WHATIF_HOURS: 100,
        state.WHATIF_UTILIZATION: -10,
        state.whatif_fx_key(Currency.KZT): 5,
        state.whatif_fx_key(Currency.USD): 0,
        state.TORNADO_PCT: 25,
    }
    assert state.what_if(values) == WhatIf(
        hours=d(1), utilization=d("-0.1"), fx={Currency.KZT: d("0.05")}
    )
    assert state.what_if({}) == WhatIf()
    assert state.tornado_deviation(values) == d("0.25")
    assert state.compare_cap({state.COMPARE_CAP: 90_000.0}) == d(90_000)
    assert state.compare_cap({}) is None


def test_actuals_editor_rows_round_trip() -> None:
    project = demo_workspace().projects[1].project
    assert project.actuals is not None
    rows = list(project.actuals.hours)
    # Пара плана без факта: юрист на подаче заявки.
    without_lawyer = [
        row for row in rows if (row.stage, row.employee) != ("Подготовка и подача заявки", "Юрист")
    ]
    table = state.actuals_rows(list(project.stages), without_lawyer)
    assert [
        (
            row[state.ACTUALS_EMPLOYEE],
            row[state.ACTUALS_PLAN],
            row[state.ACTUALS_FACT],
            row[state.ACTUALS_NOTE],
        )
        for row in table
    ] == [
        ("Партнёр", 2.0, 3.0, ""),
        ("Юрист", 8.0, 10.0, ""),
        ("Юрист", 4.0, None, ""),
        ("Стажёр", 10.0, 12.0, ""),
        ("Партнёр", 0.0, 1.0, state.UNPLANNED_NOTE),
    ]
    assert state.parse_actuals_rows(table) == [
        ActualHours(stage="Анализ охраноспособности", employee="Партнёр", hours=d(3)),
        ActualHours(stage="Анализ охраноспособности", employee="Юрист", hours=d(10)),
        ActualHours(stage="Подготовка и подача заявки", employee="Стажёр", hours=d(12)),
        ActualHours(stage="Подготовка и подача заявки", employee="Партнёр", hours=d(1)),
    ]
    # Работа вне плана с нулём часов убирается, план с нулём — остаётся.
    table[-1][state.ACTUALS_FACT] = 0.0
    table[0][state.ACTUALS_FACT] = float("nan")
    table[1][state.ACTUALS_FACT] = 0.0
    parsed = state.parse_actuals_rows(table)
    assert [(row.employee, row.hours) for row in parsed] == [("Юрист", d(0)), ("Стажёр", d(12))]
