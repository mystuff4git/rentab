"""Матрица «налоговый режим × модель ценообразования» на типовом проекте v0.3."""

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

MATRIX = load_scenario("matrix")
TAX_CONFIGS: dict[str, dict[str, Any]] = {c["id"]: c for c in MATRIX["tax_configs"]}
PRICING: dict[str, dict[str, Any]] = {p["id"]: p for p in MATRIX["pricing"]}

# Режимы с базой «доходы»: налог считается с выручки за услуги.
REVENUE_BASE_CONFIGS = (
    "RF_USN_income",
    "RF_USN_income_vat5",
    "RF_USN_income_vat7",
    "RF_AUSN",
    "RF_NPD_legal",
    "RF_NPD_individual",
)


def build_scenario(config_id: str, pricing_id: str) -> dict[str, Any]:
    """Собирает сценарий матрицы: база проекта страны + режим + модель."""
    config = TAX_CONFIGS[config_id]
    country = config["jurisdiction_params"]["jurisdiction"]
    pricing = {k: v for k, v in PRICING[pricing_id].items() if k != "id"}
    return {
        **MATRIX["base_projects"][country],
        "id": f"{config_id}__{pricing_id}",
        "jurisdiction_params": config["jurisdiction_params"],
        **pricing,
    }


def run(config_id: str, pricing_id: str) -> dict[str, Any]:
    """Прогоняет ячейку матрицы и возвращает results страницы «Проект»."""
    results, _ = calculate_estimate(build_scenario(config_id, pricing_id))
    return results


@pytest.mark.parametrize("pricing_id", PRICING)
@pytest.mark.parametrize("config_id", TAX_CONFIGS)
def test_matrix_snapshot(config_id: str, pricing_id: str, snapshot: SnapshotCheck) -> None:
    results, warnings = calculate_estimate(build_scenario(config_id, pricing_id))
    snapshot(
        f"matrix/{config_id}__{pricing_id}",
        {"results": results, "warnings": warnings, "dashboard": dashboard_view(results)},
    )


@pytest.mark.parametrize("config_id", TAX_CONFIGS)
def test_billing_nne_subtracts_vat_as_firm_expense(config_id: str) -> None:
    # KNOWN-BUG L-02 → фаза 2: НДС входит в tax и уменьшает NNE,
    # а к оплате клиенту выставляются только услуги и пошлины, без НДС.
    r = run(config_id, "billing")
    assert r["tax"] == pytest.approx(r["income_tax"] + r["vat"])
    expected_nne = (
        r["gross_revenue"]
        - r["direct_labor"]
        - r["overheads_alloc"]
        - r["disbursements_own"]
        - r["tax"]
    )
    assert r["nne"] == pytest.approx(expected_nne)
    assert r["total_client"] == pytest.approx(r["gross_revenue"] + r["disbursements"])
    assert r["vat"] == pytest.approx(r["taxable_base"] * vat_rate(config_id))


@pytest.mark.parametrize("config_id", TAX_CONFIGS)
def test_billing_taxable_base_excludes_disbursements(config_id: str) -> None:
    r = run(config_id, "billing")
    assert r["disbursements"] > 0
    assert r["taxable_base"] == pytest.approx(r["gross_revenue"])


@pytest.mark.parametrize("pricing_id", PRICING)
@pytest.mark.parametrize("config_id", TAX_CONFIGS)
def test_project_extra_expense_is_lost(config_id: str, pricing_id: str) -> None:
    # KNOWN-BUG L-12 → фаза 1: расход категории «Прочее» есть в project_expenses,
    # но не попадает ни в NNE, ни в фикс-цену.
    with_extra = run(config_id, pricing_id)
    assert any(e["category"] == "project_extra" for e in with_extra["project_expenses"])
    scenario = build_scenario(config_id, pricing_id)
    scenario["custom_expenses"] = [
        e for e in scenario["custom_expenses"] if e["category"] != "project_extra"
    ]
    without_extra, _ = calculate_estimate(scenario)
    assert with_extra["nne"] == pytest.approx(without_extra["nne"])


@pytest.mark.parametrize("config_id", REVENUE_BASE_CONFIGS)
def test_fixed_price_revenue_base_closed_form(config_id: str) -> None:
    # Для базы «доходы» v0.3 сходится к P = C / (1 − m − t − vat),
    # т. е. НДС заложен в цену как налог фирмы (KNOWN-BUG L-02).
    r = run(config_id, "fixed")
    t = income_rate(config_id) + vat_rate(config_id)
    expected = r["total_costs"] / (1 - r["target_margin"] - t)
    assert r["fixed_price"] == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("config_id", [c for c in TAX_CONFIGS if c not in REVENUE_BASE_CONFIGS])
def test_fixed_price_profit_base_iterations_match_closed_form(config_id: str) -> None:
    # KNOWN-BUG L-03 → фаза 2: итерации v0.3 совпадают с закрытой формулой
    # P = C(1 − t) / (1 − t − vat − m) с точностью до копейки.
    r = run(config_id, "fixed")
    t = income_rate(config_id)
    v = vat_rate(config_id)
    expected = r["total_costs"] * (1 - t) / (1 - t - v - r["target_margin"])
    assert r["fixed_price"] == pytest.approx(expected, abs=0.01)
    assert r["actual_margin"] == pytest.approx(r["target_margin"], abs=1e-6)


def income_rate(config_id: str) -> float:
    """Ставка налога на доход/прибыль для конфигурации матрицы (из TAX_RATES v0.3)."""
    rates = {
        "RF_USN_income": 0.06,
        "RF_USN_income_vat5": 0.06,
        "RF_USN_income_vat7": 0.06,
        "RF_USN_income_expenses": 0.15,
        "RF_USN_income_expenses_vat5": 0.15,
        "RF_AUSN": 0.08,
        "RF_OSNO": 0.25,
        "RF_NPD_legal": 0.06,
        "RF_NPD_individual": 0.04,
        "KZ_OUR_too": 0.20,
        "KZ_OUR_too_vat16": 0.20,
        "KZ_OUR_ip": 0.10,
        "KZ_OUR_ip_vat16": 0.10,
    }
    return rates[config_id]


def vat_rate(config_id: str) -> float:
    """Ставка НДС для конфигурации матрицы (из TAX_RATES v0.3)."""
    rates = {
        "RF_USN_income_vat5": 0.05,
        "RF_USN_income_vat7": 0.07,
        "RF_USN_income_expenses_vat5": 0.05,
        "RF_OSNO": 0.22,
        "KZ_OUR_too_vat16": 0.16,
        "KZ_OUR_ip_vat16": 0.16,
    }
    return rates.get(config_id, 0.0)
