"""Именованные сценарии v0.3: контрольные расчёты, светофор, режим «Оба», краевые эффекты UI."""

from __future__ import annotations

from typing import Any

import pytest

from tests.characterization.legacy_runner import (
    calculate_estimate,
    dashboard_view,
    load_scenario,
)
from tests.characterization.snapshot import SnapshotCheck

pytestmark = pytest.mark.characterization

NAMED_SCENARIOS = (
    "legacy_billing_basic",
    "legacy_fixed_basic",
    "stage_flags_traffic_light",
    "both_mode_mixed_currency",
    "catalog_duties_are_zero",
    "duplicate_stage_names",
)


def run(name: str) -> tuple[dict[str, Any], list[str]]:
    """Прогоняет именованный сценарий через повтор страницы «Проект»."""
    return calculate_estimate(load_scenario(name))


@pytest.mark.parametrize("name", NAMED_SCENARIOS)
def test_named_scenario_snapshot(name: str, snapshot: SnapshotCheck) -> None:
    results, warnings = run(name)
    snapshot(
        f"scenarios/{name}",
        {"results": results, "warnings": warnings, "dashboard": dashboard_view(results)},
    )


def test_billing_basic_matches_hand_calculation() -> None:
    # Ручной расчёт из docstring legacy/rentab_v03/test_scenario.py.
    results, _ = run("legacy_billing_basic")
    assert results["gross_revenue"] == pytest.approx(165_000)
    assert results["direct_labor"] == pytest.approx(92_500)
    assert results["blended_rate"] == pytest.approx(8_250)
    assert results["leverage"] == pytest.approx(3.0)
    assert results["overhead_rate"] == pytest.approx(500)
    assert results["overheads_alloc"] == pytest.approx(10_000)
    assert results["tax"] == pytest.approx(9_900)
    assert results["nne"] == pytest.approx(52_600)


def test_fixed_basic_matches_hand_calculation() -> None:
    results, _ = run("legacy_fixed_basic")
    assert results["total_costs"] == pytest.approx(92_500)
    assert results["fixed_price"] == pytest.approx(144_531.25)
    assert results["tax"] == pytest.approx(8_671.875)
    assert results["nne"] == pytest.approx(43_359.375)
    assert results["actual_margin"] == pytest.approx(0.30)
    flags = [(f["stage_name"], f["flag"]) for f in results["stage_flags"]]
    assert flags == [("Подготовка", "yellow"), ("Регистрация", "green")]


def test_stage_flags_cover_all_colors() -> None:
    results, _ = run("stage_flags_traffic_light")
    assert [f["flag"] for f in results["stage_flags"]] == ["red", "yellow", "green", "green"]


def test_stage_flags_share_revenue_by_hours_and_compare_pre_tax_margin() -> None:
    # KNOWN-BUG L-04 → фаза 2: выручка этапа делится пропорционально часам,
    # маржа этапа берётся до налога и без собственных расходов.
    results, _ = run("stage_flags_traffic_light")
    total_hours = results["total_hours"]
    for flag, member in zip(results["stage_flags"], results["team_with_hours"], strict=True):
        hour_share = member["hours"] / total_hours
        assert flag["stage_revenue_share"] == pytest.approx(results["fixed_price"] * hour_share)
        expected_costs = (
            member["cost_rate"] * member["hours"] + results["overheads_alloc"] * hour_share
        )
        assert flag["stage_costs"] == pytest.approx(expected_costs)
    pre_tax_margin = sum(
        f["stage_revenue_share"] - f["stage_costs"] for f in results["stage_flags"]
    )
    assert pre_tax_margin == pytest.approx(results["nne"] + results["tax"])


def test_both_mode_sums_currencies_without_conversion() -> None:
    # KNOWN-BUG L-06 → фаза 2: 38 500 ₽ + 120 000 ₸ дают «158 500 ₽»,
    # накладные 120 000 ₽ + 300 000 ₸ дают «420 000 ₽» в месяц.
    results, _ = run("both_mode_mixed_currency")
    assert results["currency_code"] == "RUB"
    assert results["disbursements"] == pytest.approx(38_500 + 120_000)
    assert results["overhead_rate"] == pytest.approx((120_000 + 300_000) / 320)


def test_catalog_duties_are_skipped_because_amounts_are_zero() -> None:
    # KNOWN-BUG L-09 → фаза 3: каталоги пошлин v0.3 пустые.
    results, warnings = run("catalog_duties_are_zero")
    assert results["project_expenses"] == []
    assert results["disbursements"] == 0
    assert warnings == []


def test_duplicate_stage_names_double_count_hours_on_dashboard() -> None:
    # KNOWN-BUG L-05 → фаза 2: два исполнителя на этапе = два этапа с одним именем.
    results, _ = run("duplicate_stage_names")
    names = [f["stage_name"] for f in results["stage_flags"]]
    assert names.count("Ответ на уведомление экспертизы") == 2
    rows = dashboard_view(results)["stage_rows"]
    duplicated = [r["hours"] for r in rows if r["stage_name"] == "Ответ на уведомление экспертизы"]
    assert duplicated == [12.0, 12.0]


def test_stage_with_removed_executor_is_silently_skipped() -> None:
    scenario = load_scenario("legacy_billing_basic")
    scenario["stages"].append({"name": "Этап уволенного", "executor": "Удалённый", "hours": 40.0})
    results, _ = calculate_estimate(scenario)
    assert results["total_hours"] == 20.0
    assert results["gross_revenue"] == pytest.approx(165_000)
