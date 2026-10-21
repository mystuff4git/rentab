"""Golden-сценарий: слайд 3 защиты ВКР в новой методологии (docs/methodology.md, раздел 12).

Данные слайда: выручка 300 000 ₽, УСН 6% + НДС 5%, накладные 100 ₽/ч,
себестоимость биллируемого часа Партнёр 4 000, Юрист 1 500, Стажёр 500 ₽.
Как на слайде, 300 000 ₽ — выручка без НДС, НДС 5% (15 000 ₽) начисляется
сверху: клиент платит 315 000 ₽. В v0.3 НДС вычитался из NNE (L-02), поэтому
NNE плана был 210 500 ₽ вместо 225 500 ₽, а факта — −17 200 ₽ вместо −2 200 ₽.

Нижняя таблица слайда — сравнение режимов на контрольном сценарии v0.3
(R = 165 000 ₽, C = 102 500 ₽): меняется только строка «ОСНО + НДС 22%».
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.estimate import calculate_estimate
from rentab_core.models import (
    AusnProfile,
    Firm,
    Jurisdiction,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Period,
    Project,
    RfTaxProfile,
    Scenario,
    UsnObject,
    UsnProfile,
    UsnVat,
    VatMode,
)
from rentab_core.money import round_to
from rentab_core.results import Estimate
from tests.core.factories import basic_scenario, d, fixed, stage
from tests.core.test_methodology_examples import TEAM

SLIDE3_PRICE = d(300_000)
SLIDE3_VAT_PROFILE = UsnProfile(vat=UsnVat.VAT_5)
# 30 000 ₽ накладных на ёмкость 3 × 100 ч = 100 ₽/ч, как на слайде.
SLIDE3_OVERHEADS = Overhead(name="Накладные фирмы", amount=d(30_000), period=Period.MONTHLY)
SLIDE3_HOURS = {
    "plan": {"Партнёр": 5, "Юрист": 15, "Стажёр": 20},
    "fact": {"Партнёр": 62, "Юрист": 15, "Стажёр": 10},
}
# v0.3: tests/characterization/scenarios/slide3.json.
V03_NNE = {"plan": d(210_500), "fact": d(-17_200)}


def slide3(variant: str, vat_mode: VatMode = VatMode.ON_TOP) -> Estimate:
    """Проект слайда 3: согласованная цена 300 000 ₽, один этап с тремя исполнителями."""
    hours = SLIDE3_HOURS[variant]
    return calculate_estimate(
        Scenario(
            firm=Firm(
                jurisdiction=Jurisdiction(rf=SLIDE3_VAT_PROFILE),
                team=TEAM,
                overheads=(SLIDE3_OVERHEADS,),
            ),
            project=Project(
                name="Слайд 3",
                pricing=fixed(SLIDE3_PRICE),
                vat_mode=vat_mode,
                stages=(stage("Проект", *hours.items()),),
            ),
        )
    )


@pytest.mark.parametrize(
    ("variant", "labor", "overheads", "nne", "margin_pct"),
    [
        ("plan", 52_500, 4_000, 225_500, "75.17"),
        ("fact", 275_500, 8_700, -2_200, "-0.73"),
    ],
)
def test_slide3_plan_and_fact(
    variant: str, labor: int, overheads: int, nne: int, margin_pct: str
) -> None:
    estimate = slide3(variant)
    assert estimate.overhead_rate == d(100)
    assert estimate.revenue == d(300_000)
    assert estimate.vat == d(15_000)
    assert estimate.total_client == d(315_000)
    assert estimate.direct_labor == d(labor)
    assert estimate.overheads_alloc == d(overheads)
    assert estimate.tax.tax == d(18_000)
    assert estimate.nne == d(nne)
    assert round_to(estimate.margin * 100) == Decimal(margin_pct)
    # Разница с v0.3 — ровно НДС, который платит клиент, а не фирма (L-02).
    assert estimate.nne - V03_NNE[variant] == estimate.vat


@pytest.mark.parametrize(("variant", "nne"), [("plan", "212071.43"), ("fact", "-15628.57")])
def test_slide3_if_price_included_vat(variant: str, nne: str) -> None:
    # Альтернатива: 300 000 ₽ уже включают НДС 5% (ROADMAP, открытый вопрос фазы 2).
    estimate = slide3(variant, VatMode.INCLUDED)
    assert round_to(estimate.revenue) == d("285714.29")
    assert round_to(estimate.vat) == d("14285.71")
    assert round_to(estimate.tax.tax) == d("17142.86")
    assert round_to(estimate.nne) == Decimal(nne)
    assert estimate.total_client == SLIDE3_PRICE


USN_15 = UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES)


@pytest.mark.parametrize(
    ("profile", "tax", "nne", "margin_pct", "vat", "v03_nne"),
    [
        (UsnProfile(), "9900", "52600", "31.88", "0", "52600"),
        (USN_15, "9375", "53125", "32.20", "0", "53125"),
        (AusnProfile(), "13200", "49300", "29.88", "0", "49300"),
        (NpdProfile(), "9900", "52600", "31.88", "0", "52600"),
        # ОСНО с НДС: NNE как у ОСНО без НДС (на слайде отдельной строкой — 46 875),
        # НДС 36 300 ₽ платит клиент. В v0.3 — 10 575 ₽ (L-02).
        (OsnoProfile(), "15625", "46875", "28.41", "36300", "10575"),
    ],
)
def test_slide3_regime_table(
    profile: RfTaxProfile, tax: str, nne: str, margin_pct: str, vat: str, v03_nne: str
) -> None:
    estimate = calculate_estimate(basic_scenario(jurisdiction=Jurisdiction(rf=profile)))
    assert estimate.revenue == d(165_000)
    assert estimate.total_costs == d(102_500)
    assert estimate.tax.tax == Decimal(tax)
    assert estimate.nne == Decimal(nne)
    assert round_to(estimate.margin * 100) == Decimal(margin_pct)
    assert estimate.vat == Decimal(vat)
    assert estimate.nne - Decimal(v03_nne) == estimate.vat
    if isinstance(profile, UsnProfile) and profile.tax_object == UsnObject.INCOME_MINUS_EXPENSES:
        assert estimate.tax.min_tax == d(1_650)
        assert not estimate.tax.min_tax_applied
