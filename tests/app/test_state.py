"""Сборка моделей из значений сессии без Streamlit: «запись → значения → запись»."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from rentab_core.demo import demo_workspace
from rentab_core.duties import ApplicantCategory, Quantity
from rentab_core.fx import RateSource, RatesQuote
from rentab_core.io import DutySelection, ProjectRecord
from rentab_core.models import (
    DEFAULT_KZ_TAX_PROFILE,
    DEFAULT_RF_TAX_PROFILE,
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
    record = demo_workspace().projects[2]
    values = state.project_values(record)
    assert values[state.PRICING_MODEL] == state.PRICING_HOURLY
    assert values[state.TARGET_MARGIN_PCT] == 30
    assert values[state.duty_quantity_key(Country.RF, Quantity.CLASSES)] == 3
    assert values[state.duty_quantity_key(Country.RF, Quantity.ITEMS_OVER_LIMIT)] == 0
    assert values[state.duty_category_key(Country.KZ)] == ApplicantCategory.LEGAL_ENTITY
    assert state.CALC_COUNTRY not in values


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
    values = state.project_values(demo_workspace().projects[2])
    values["unrelated"] = 1
    copy = state.snapshot(values)
    assert "unrelated" not in copy
    assert copy[state.PROJECT_STAGES] == values[state.PROJECT_STAGES]
    assert copy[state.PROJECT_STAGES] is not values[state.PROJECT_STAGES]
    assert copy[state.SELECTED_DUTIES]["RF"] is not values[state.SELECTED_DUTIES]["RF"]
