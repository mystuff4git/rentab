"""Ядро rentab_core воспроизводит снимки v0.3 (фаза 1, ADR 0003).

Те же сценарии, что у characterization-тестов v0.3, проходят через адаптер
в модели ядра; результат сравнивается с теми же снимками с допуском
(`equivalence.py`). Снимки здесь не перезаписываются никогда: расхождение —
это изменение поведения, которое в фазе 1 недопустимо.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from rentab_core.analysis.dashboard import dashboard
from rentab_core.costing import allocate_overheads, direct_labor, overhead_rate
from rentab_core.estimate import calculate_estimate
from rentab_core.export import estimate_xlsx
from rentab_core.models import Overhead, Period, Role
from rentab_core.money import ZERO, to_decimal
from rentab_core.pricing.hourly import net_net_earnings
from rentab_core.results import EstimateLine
from rentab_core.tax import calculate_tax
from tests.characterization.core_adapter import (
    dashboard_to_legacy,
    estimate_to_results,
    scenario_to_core,
    tax_profile_from_params,
)
from tests.characterization.equivalence import (
    MONEY_TOLERANCE,
    assert_matches_snapshot,
    diff_workbook,
    fail_on_diffs,
    load_snapshot,
)
from tests.characterization.legacy_runner import load_scenario
from tests.characterization.test_legacy_export import FROZEN_TODAY as XLSX_DATE
from tests.characterization.test_legacy_export import dump_workbook
from tests.characterization.test_legacy_scenarios import NAMED_SCENARIOS
from tests.characterization.test_legacy_tax_matrix import PRICING, TAX_CONFIGS, build_scenario

pytestmark = pytest.mark.characterization


def core_view(scenario: dict[str, Any]) -> dict[str, Any]:
    """Выход ядра в формате снимка: results, warnings и dashboard v0.3."""
    core_scenario, warnings = scenario_to_core(scenario)
    estimate = calculate_estimate(core_scenario)
    return {
        "results": estimate_to_results(estimate),
        "warnings": warnings,
        "dashboard": dashboard_to_legacy(dashboard(estimate)),
    }


@pytest.mark.parametrize("name", NAMED_SCENARIOS)
def test_core_reproduces_named_scenario(name: str) -> None:
    assert_matches_snapshot(f"scenarios/{name}", core_view(load_scenario(name)))


@pytest.mark.parametrize("pricing_id", PRICING)
@pytest.mark.parametrize("config_id", TAX_CONFIGS)
def test_core_reproduces_matrix_cell(config_id: str, pricing_id: str) -> None:
    view = core_view(build_scenario(config_id, pricing_id))
    assert_matches_snapshot(f"matrix/{config_id}__{pricing_id}", view)


SLIDE3 = load_scenario("slide3")


@pytest.mark.parametrize("variant", ["plan", "fact"])
def test_core_reproduces_slide3(variant: str) -> None:
    # Как в test_legacy_slide3: выручка задана числом, поэтому сценарий
    # собирается из функций ядра. Роль на расчёт не влияет.
    data = SLIDE3["variants"][variant]
    lines = [
        EstimateLine(
            stage_name=role,
            employee_name=role,
            role=Role.ASSOCIATE,
            billing_rate=ZERO,
            cost_rate=to_decimal(SLIDE3["cost_rates"][role]),
            hours=to_decimal(hours),
        )
        for role, hours in data["hours"].items()
    ]
    total_hours = sum((line.hours for line in lines), ZERO)
    labor = direct_labor(lines)
    overheads = [
        Overhead(
            name="Накладные фирмы",
            amount=to_decimal(SLIDE3["overheads_monthly"]),
            period=Period.MONTHLY,
        )
    ]
    rate = overhead_rate(overheads, to_decimal(SLIDE3["billable_hours_per_month"]))
    overheads_alloc = allocate_overheads(rate, total_hours)
    profile = tax_profile_from_params(SLIDE3["tax_params"])
    assert profile is not None
    revenue = to_decimal(SLIDE3["revenue"])
    tax = calculate_tax(profile, revenue, expenses=labor + overheads_alloc)
    actual = {
        "direct_labor": labor,
        "overheads_alloc": overheads_alloc,
        "income_tax": tax.income_tax,
        "vat": tax.vat,
        "nne": net_net_earnings(revenue, labor, overheads_alloc, ZERO, tax.total_tax),
    }
    assert rate == Decimal(100)
    for key, expected in data["expected_v03"].items():
        assert abs(float(actual[key]) - expected) <= MONEY_TOLERANCE, key


@pytest.mark.parametrize(
    ("case", "pricing_id", "firm_name", "drop_client"),
    [
        ("billing", "billing", "ООО «ИП-Консалт»", False),
        ("fixed", "fixed", "ООО «ИП-Консалт»", False),
        ("billing_no_firm_no_client", "billing", "", True),
    ],
)
def test_core_reproduces_xlsx(
    case: str, pricing_id: str, firm_name: str, drop_client: bool
) -> None:
    # Те же входы, что у test_legacy_export: ячейка матрицы RF_USN_income.
    core_scenario, _ = scenario_to_core(build_scenario("RF_USN_income", pricing_id))
    estimate = calculate_estimate(core_scenario)
    if drop_client:
        estimate = estimate.model_copy(update={"client": ""})
    content = estimate_xlsx(estimate, firm_name=firm_name, issue_date=XLSX_DATE)
    diffs = list(diff_workbook(load_snapshot(f"export/{case}"), dump_workbook(content)))
    fail_on_diffs(f"XLSX ядра разошёлся со снимком export/{case}", diffs)
