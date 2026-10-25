"""Снимки ядра фазы 2 на characterization-сценариях v0.3.

Сценарии v0.3 переводятся в модели ядра правилами фазы 2
(`core_adapter.FINAL`), выход ядра сверяется со снимками
`snapshots/core/`. Снимки v0.3 (`snapshots/scenarios`, `snapshots/matrix`,
`snapshots/export`) остаются эталоном самого v0.3; как цифры v0.3 переходят
в эти снимки, показывает `docs/phase2_before_after.md`.

Перезапись — только осознанно: `uv run pytest --update-snapshots`.
"""

from __future__ import annotations

import pytest

from rentab_core.estimate import calculate_estimate
from rentab_core.export import estimate_xlsx
from tests.characterization.core_adapter import scenario_to_core
from tests.characterization.core_view import core_view
from tests.characterization.legacy_runner import load_scenario
from tests.characterization.snapshot import SnapshotCheck
from tests.characterization.test_legacy_export import FROZEN_TODAY as XLSX_DATE
from tests.characterization.test_legacy_export import dump_workbook
from tests.characterization.test_legacy_scenarios import NAMED_SCENARIOS
from tests.characterization.test_legacy_tax_matrix import PRICING, TAX_CONFIGS, build_scenario

pytestmark = pytest.mark.characterization


@pytest.mark.parametrize("name", NAMED_SCENARIOS)
def test_core_named_scenario(name: str, snapshot: SnapshotCheck) -> None:
    snapshot(f"core/scenarios/{name}", core_view(load_scenario(name)))


@pytest.mark.parametrize("pricing_id", PRICING)
@pytest.mark.parametrize("config_id", TAX_CONFIGS)
def test_core_matrix_cell(config_id: str, pricing_id: str, snapshot: SnapshotCheck) -> None:
    view = core_view(build_scenario(config_id, pricing_id))
    snapshot(f"core/matrix/{config_id}__{pricing_id}", view)


@pytest.mark.parametrize(
    ("case", "pricing_id", "firm_name", "drop_client"),
    [
        ("billing", "billing", "ООО «ИП-Консалт»", False),
        ("fixed", "fixed", "ООО «ИП-Консалт»", False),
        ("billing_no_firm_no_client", "billing", "", True),
    ],
)
def test_core_xlsx(
    case: str, pricing_id: str, firm_name: str, drop_client: bool, snapshot: SnapshotCheck
) -> None:
    # Те же входы, что у test_legacy_export: ячейка матрицы RF_USN_income.
    core_scenario, _ = scenario_to_core(build_scenario("RF_USN_income", pricing_id))
    estimate = calculate_estimate(core_scenario)
    if drop_client:
        estimate = estimate.model_copy(update={"client": ""})
    content = estimate_xlsx(
        estimate, scenario=core_scenario, firm_name=firm_name, issue_date=XLSX_DATE
    )
    snapshot(f"core/export/{case}", dump_workbook(content))
