"""Мост «было/стало» (`bridge.py`): каждое расхождение с v0.3 объяснено.

- При нейтральном переводе входов ядро видит те же издержки, что и v0.3, и
  отличается только методологией НДС (L-02): для почасовой модели NNE растёт
  ровно на НДС v0.3, для фикс-цены НДС уходит из знаменателя формулы.
- Слияние одноимённых этапов (L-05) не меняет итогов проекта.
- Последний шаг моста — это снимок ядра фазы 2.
- `docs/phase2_before_after.md` совпадает с расчётом (перезапись —
  `--update-snapshots` или `python -m tests.characterization.bridge`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.money import ONE
from rentab_core.results import TaxBase
from tests.characterization.bridge import (
    DOC_PATH,
    FX_STEP,
    MERGE_STEP,
    METRICS,
    RESULT_KEY,
    ScenarioBridge,
    all_bridges,
    chain,
    render,
)
from tests.characterization.core_view import estimate_view
from tests.characterization.snapshot import normalize

pytestmark = pytest.mark.characterization

# Снимки v0.3 округлены до 6 знаков; издержки v0.3 — до копейки.
KOPECK = Decimal("0.01")
BRIDGES = all_bridges()
IDS = [b.scenario_id for b in BRIDGES]


def _d(value: object) -> Decimal:
    return Decimal(str(value))


@pytest.mark.parametrize("bridge", BRIDGES, ids=IDS)
def test_neutral_mapping_reproduces_v03_costs(bridge: ScenarioBridge) -> None:
    n, v03 = bridge.neutral, bridge.v03
    assert abs(n.direct_labor - _d(v03["direct_labor"])) <= KOPECK
    assert abs(n.overheads_alloc - _d(v03["overheads_alloc"])) <= KOPECK
    assert abs(n.disbursements_own - _d(v03["disbursements_own"])) <= KOPECK
    assert abs(n.disbursements_billed - _d(v03["disbursements"])) <= KOPECK
    assert abs(n.overhead_rate - _d(v03["overhead_rate"])) <= KOPECK
    assert abs(n.blended_rate - _d(v03["blended_rate"])) <= KOPECK
    assert abs(n.leverage - _d(v03["leverage"])) <= Decimal("1e-6")


@pytest.mark.parametrize("bridge", [b for b in BRIDGES if not b.fixed], ids=lambda b: b.scenario_id)
def test_hourly_l02_is_exactly_vat(bridge: ScenarioBridge) -> None:
    # NNE без минимального налога = NNE v0.3 + НДС, который v0.3 вычитал из NNE.
    values = chain(bridge, METRICS[2])
    assert abs(values["L-02"] - (values["v0.3"] + _d(bridge.v03["vat"]))) <= KOPECK
    assert abs(bridge.neutral.revenue - _d(bridge.v03["gross_revenue"])) <= KOPECK
    assert abs(bridge.neutral.tax.regular_tax - _d(bridge.v03["income_tax"])) <= KOPECK


@pytest.mark.parametrize("bridge", [b for b in BRIDGES if b.fixed], ids=lambda b: b.scenario_id)
def test_fixed_l02_removes_vat_from_denominator(bridge: ScenarioBridge) -> None:
    n = bridge.neutral
    terms, m, costs = n.tax.terms, n.target_margin, _d(bridge.v03["total_costs"])
    assert abs(n.total_costs - costs) <= KOPECK
    t, v = terms.rate, terms.vat_rate
    numerator = costs if terms.base == TaxBase.INCOME else costs * (ONE - t)
    # v0.3: НДС в знаменателе; L-02: без него.
    assert abs(_d(bridge.v03["fixed_price"]) - numerator / (ONE - m - t - v)) <= KOPECK
    assert abs(chain(bridge, METRICS[0])["L-02"] - numerator / (ONE - m - t)) <= KOPECK


@pytest.mark.parametrize("bridge", BRIDGES, ids=IDS)
def test_stage_merge_does_not_change_totals(bridge: ScenarioBridge) -> None:
    before, after = bridge.steps[FX_STEP], bridge.steps[MERGE_STEP]
    for metric in METRICS:
        assert metric.core(before) == metric.core(after)


@pytest.mark.parametrize("bridge", BRIDGES, ids=IDS)
def test_bridge_ends_at_phase2_snapshot(bridge: ScenarioBridge) -> None:
    view = normalize(estimate_view(bridge.final))
    for metric, key in zip(METRICS, ("revenue", "total_client", "nne", "margin"), strict=True):
        assert normalize(float(chain(bridge, metric)[RESULT_KEY])) == view[key]


def test_before_after_document_is_up_to_date(request: pytest.FixtureRequest) -> None:
    text = render(BRIDGES)
    if request.config.getoption("--update-snapshots"):
        DOC_PATH.write_text(text, encoding="utf-8")
        return
    assert DOC_PATH.read_text(encoding="utf-8") == text, (
        "docs/phase2_before_after.md устарел: uv run python -m tests.characterization.bridge"
    )
