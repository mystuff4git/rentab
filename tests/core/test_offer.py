"""Модель КП: суммы сходятся со сметой до копейки, цены этапов — по теневой стоимости (L-17)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentab_core.demo import demo_workspace
from rentab_core.documents import Letterhead, OfferTerms
from rentab_core.errors import CurrencyConversionError, FixedPriceError
from rentab_core.estimate import calculate_estimate
from rentab_core.export.offer import (
    NOTE_DISBURSEMENTS,
    NOTE_FIXED,
    NOTE_HOURLY,
    OfferDocument,
    PriceKind,
    allocate,
    offer_document,
)
from rentab_core.models import (
    CappedPricing,
    DisbursementKind,
    Jurisdiction,
    Scenario,
    UsnProfile,
    UsnVat,
    VatMode,
)
from rentab_core.money import round_to
from rentab_core.results import Estimate
from tests.core.factories import basic_scenario, d, disbursement, fixed, stage
from tests.core.strategies import scenarios

ISSUE_DATE = dt.date(2026, 10, 25)
VAT_5 = Jurisdiction(rf=UsnProfile(vat=UsnVat.VAT_5))
KOPECK = d("0.01")


def document(scenario: Scenario, terms: OfferTerms | None = None) -> tuple[OfferDocument, Estimate]:
    estimate = calculate_estimate(scenario)
    return offer_document(scenario, estimate, issue_date=ISSUE_DATE, terms=terms), estimate


def assert_reconciles(doc: OfferDocument, estimate: Estimate) -> None:
    """Строки КП — значения сметы до копейки; итог — сумма строк."""
    assert doc.services_net == round_to(estimate.revenue)
    assert doc.vat == round_to(estimate.vat)
    billable = [item for item in estimate.disbursements if item.billable and item.amount > 0]
    assert [item.amount for item in doc.disbursements] == [round_to(i.amount) for i in billable]
    assert doc.total == doc.services_net + doc.vat + doc.disbursements_total
    # Итог отличается от сметы только округлением слагаемых — меньше копейки на каждое.
    assert abs(doc.total - estimate.total_client) <= KOPECK / 2 * (2 + len(billable))
    assert sum(stage.amount for stage in doc.stages) == (
        doc.shadow_net if doc.price_kind != PriceKind.FIXED else doc.services_net
    )
    assert doc.shadow_net - doc.write_off == doc.services_net
    for item in doc.stages:
        assert sum(line.amount for line in item.lines) == (item.amount if item.lines else 0)


@pytest.mark.parametrize("index", range(4))
def test_demo_offers_reconcile_with_estimate(index: int) -> None:
    workspace = demo_workspace()
    doc, estimate = document(workspace.projects[index].scenario(workspace.firm))
    assert_reconciles(doc, estimate)
    assert doc.total == round_to(estimate.total_client)


def test_fixed_stage_prices_follow_shadow_value() -> None:
    # L-17: «Патентование изобретения» — 163 750 ₽ по теневой стоимости 76 : 180 : 50.
    workspace = demo_workspace()
    doc, estimate = document(workspace.projects[2].scenario(workspace.firm))
    assert doc.price_kind == PriceKind.FIXED
    assert [item.amount for item in doc.stages] == [d("40669.93"), d("96323.53"), d("26756.54")]
    for item, result in zip(doc.stages, estimate.stages, strict=True):
        assert abs(item.amount - result.revenue) < KOPECK
        assert item.lines == ()
    assert doc.services_net == d("163750.00")
    assert doc.vat == d("8187.50")
    assert doc.disbursements_total == d("33000.00")
    assert doc.total == d("204937.50")


def test_hourly_offer_lists_executors_with_rates() -> None:
    workspace = demo_workspace()
    doc, _ = document(workspace.projects[1].scenario(workspace.firm))
    first = doc.stages[0]
    assert (first.name, first.amount, first.hours) == ("Анализ охраноспособности", d(60_000), d(10))
    assert [(line.title, line.hours, line.rate, line.amount) for line in first.lines] == [
        ("Партнёр", d(2), d(10_000), d(20_000)),
        ("Юрист (Младший юрист)", d(8), d(5_000), d(40_000)),
    ]
    assert doc.price_terms == "почасовая, НДС 5% начисляется сверху"
    assert doc.vat_label == "НДС 5%"
    assert doc.conditions[:2] == (NOTE_HOURLY, NOTE_DISBURSEMENTS)


def test_capped_offer_shows_write_off_and_cap_note() -> None:
    doc, estimate = document(basic_scenario(CappedPricing(cap=d(150_000)), jurisdiction=VAT_5))
    assert (doc.shadow_net, doc.write_off, doc.services_net) == (d(165_000), d(15_000), d(150_000))
    assert doc.price_kind == PriceKind.CAPPED
    assert doc.conditions[0].replace(" ", " ") == (
        "Оплачиваются фактически затраченные часы по указанным ставкам, но не более "
        "150 000,00 ₽ без НДС."
    )
    assert_reconciles(doc, estimate)


def test_prices_including_vat_are_shown_net() -> None:
    base = basic_scenario(jurisdiction=VAT_5)
    scenario = base.model_copy(
        update={"project": base.project.model_copy(update={"vat_mode": VatMode.INCLUDED})}
    )
    doc, estimate = document(scenario)
    line = doc.stages[0].lines[0]
    assert line.rate == round_to(d(15_000) / d("1.05"))  # 14 285,71
    assert doc.services_net == round_to(d(165_000) / d("1.05"))
    assert doc.total == d(165_000)
    assert_reconciles(doc, estimate)


def test_offer_without_vat_and_without_stages() -> None:
    scenario = basic_scenario(fixed(100_000), stages=(stage("Этап уволенного", ("Удалённый", 10)),))
    doc, _ = document(scenario)
    assert doc.vat_rate == 0
    assert doc.price_terms == "фиксированная, без НДС"
    assert [(s.number, s.name, s.amount) for s in doc.stages] == [
        (1, "Услуги по проекту", d(100_000))
    ]
    assert doc.conditions[0] == NOTE_FIXED
    assert NOTE_DISBURSEMENTS not in doc.conditions


def test_terms_letterhead_and_descriptions() -> None:
    base = basic_scenario(
        fixed(),
        disbursements=(
            disbursement(DisbursementKind.BILLABLE, 38_500, "Пошлина"),
            disbursement(DisbursementKind.BILLABLE, 0, "Пошлина без суммы"),
            disbursement(DisbursementKind.OWN, 1_000, "Курьер"),
        ),
    )
    stages = tuple(
        item.model_copy(update={"description": "  Отчёт о поиске  "}) if index == 0 else item
        for index, item in enumerate(base.project.stages)
    )
    scenario = base.model_copy(
        update={
            "firm": base.firm.model_copy(update={"name": "ООО «Фирма»"}),
            "project": base.project.model_copy(update={"stages": stages, "client": "АО «Клиент»"}),
        }
    )
    terms = OfferTerms(
        number="7",
        validity_days=14,
        payment_terms=" 50% — аванс. ",
        execution_term="10 рабочих дней",
        extra_terms="Первое условие.\n\n  Второе условие.",
    )
    estimate = calculate_estimate(scenario)
    letterhead = Letterhead(signatory_name="Иванов И. И.", signatory_position="партнёр")
    doc = offer_document(
        scenario, estimate, issue_date=ISSUE_DATE, letterhead=letterhead, terms=terms
    )
    assert doc.subtitle == "№ 7 от 25.10.2026"
    assert doc.valid_until == dt.date(2026, 11, 8)
    assert doc.client == "АО «Клиент»"
    assert doc.footer == "ООО «Фирма» · Коммерческое предложение № 7 от 25.10.2026"
    assert doc.signature == "Иванов И. И., партнёр"
    assert [s.description for s in doc.stages] == ["Отчёт о поиске", ""]
    assert [item.label for item in doc.disbursements] == ["Пошлина"]  # нулевые и свои — нет
    assert doc.conditions == (
        NOTE_FIXED,
        NOTE_DISBURSEMENTS,
        "Порядок оплаты: 50% — аванс.",
        "Срок выполнения: 10 рабочих дней.",
        "Первое условие.",
        "Второе условие.",
        "Предложение действительно до 08.11.2026.",
    )


def test_defaults_without_terms_and_names() -> None:
    base = basic_scenario()
    untitled = base.project.model_copy(update={"name": " "})
    doc, _ = document(base.model_copy(update={"project": untitled}))
    assert doc.project_name == "Без названия"
    assert doc.subtitle == "от 25.10.2026"
    assert doc.valid_until == ISSUE_DATE + dt.timedelta(days=30)
    assert doc.footer == "Коммерческое предложение от 25.10.2026"
    assert doc.signature == ""
    assert doc.letterhead == Letterhead()


# ---------------------------------------------------------------------------
# Распределение копеек
# ---------------------------------------------------------------------------
def test_allocate_gives_missing_kopecks_to_largest_remainders() -> None:
    parts = [d(100) / 3] * 3  # 33,333…
    assert allocate(d(100), parts) == [d("33.34"), d("33.33"), d("33.33")]
    assert allocate(d("0.02"), [d("0.004"), d("0.006"), d("0.01")]) == [0, d("0.01"), d("0.01")]
    assert allocate(d(0), [d(0), d(0)]) == [0, 0]
    assert allocate(d(5), []) == []


@given(
    parts=st.lists(st.integers(0, 10**9).map(lambda v: Decimal(v) / 997), min_size=1, max_size=8)
)
def test_allocate_sums_exactly_and_stays_within_a_kopeck(parts: list[Decimal]) -> None:
    total = round_to(sum(parts, Decimal(0)))
    result = allocate(total, parts)
    assert sum(result, Decimal(0)) == total
    for value, exact in zip(result, parts, strict=True):
        assert value >= 0
        assert abs(value - exact) < KOPECK
        assert value == round_to(value)


@settings(max_examples=150, deadline=None)
@given(scenario=scenarios())
def test_random_offers_reconcile_with_estimate(scenario: Scenario) -> None:
    try:
        doc, estimate = document(scenario)
    except (FixedPriceError, CurrencyConversionError):
        return
    assert_reconciles(doc, estimate)
    if doc.price_kind == PriceKind.FIXED:
        for stage_, result in zip(doc.stages, estimate.stages, strict=True):
            assert abs(stage_.amount - result.revenue) < KOPECK
