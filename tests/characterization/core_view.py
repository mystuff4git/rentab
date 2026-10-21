"""Выход ядра в виде снимка: числа сметы, этапы, дашборд, предупреждения.

Снимки ядра лежат в `snapshots/core/` и фиксируют цифры новой методологии
(фаза 2): любое их изменение — изменение цифр, которое объясняется в PR.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rentab_core.analysis.dashboard import dashboard
from rentab_core.errors import FixedPriceError
from rentab_core.estimate import calculate_estimate
from rentab_core.results import Estimate
from tests.characterization.core_adapter import FINAL, LegacyDict, LegacyMapping, scenario_to_core


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def estimate_view(estimate: Estimate) -> dict[str, Any]:
    """Числа сметы ядра для снимка."""
    view = dashboard(estimate)
    return {
        "currency": estimate.currency.value,
        "regime_label": estimate.regime_label,
        "pricing": estimate.pricing.kind,
        "target_margin": _f(estimate.target_margin),
        "revenue": _f(estimate.revenue),
        "shadow_value": _f(estimate.shadow_value),
        "vat": _f(estimate.vat),
        "disbursements_billed": _f(estimate.disbursements_billed),
        "total_client": _f(estimate.total_client),
        "direct_labor": _f(estimate.direct_labor),
        "underutilization_cost": _f(estimate.underutilization_cost),
        "overhead_rate": _f(estimate.overhead_rate),
        "overheads_alloc": _f(estimate.overheads_alloc),
        "disbursements_own": _f(estimate.disbursements_own),
        "total_costs": _f(estimate.total_costs),
        "tax": _f(estimate.tax.tax),
        "regular_tax": _f(estimate.tax.regular_tax),
        "min_tax": _f(estimate.tax.min_tax),
        "nne": _f(estimate.nne),
        "margin": _f(estimate.margin),
        "blended_rate": _f(estimate.blended_rate),
        "leverage": _f(estimate.leverage),
        "total_hours": _f(estimate.total_hours),
        "lines": [
            {
                "stage": line.stage_name,
                "employee": line.employee_name,
                "hours": _f(line.hours),
                "billing_rate": _f(line.billing_rate),
                "cost_rate": _f(line.cost_rate),
                "contract_cost_rate": _f(line.contract_cost_rate),
            }
            for line in estimate.lines
        ],
        "disbursements": [
            {
                "name": item.name,
                "kind": item.kind.value,
                "original": f"{item.original_amount} {item.original_currency.value}",
                "amount": _f(item.amount),
            }
            for item in estimate.disbursements
        ],
        "stages": [
            {
                "stage": stage.stage_name,
                "hours": _f(stage.hours),
                "shadow_value": _f(stage.shadow_value),
                "revenue": _f(stage.revenue),
                "costs": _f(stage.costs),
                "own": _f(stage.own),
                "tax": _f(stage.tax),
                "nne": _f(stage.nne),
                "margin": _f(stage.margin),
                "flag": stage.flag.value,
            }
            for stage in estimate.stages
        ],
        "unresolved": [
            f"{item.stage_name}: {item.employee}" for item in estimate.unresolved_assignments
        ],
        "dashboard": {
            "margin_pct": _f(view.margin_pct),
            "effective_tax_rate_pct": _f(view.effective_tax_rate_pct),
            "underutilization_share_pct": _f(view.underutilization_share_pct),
            "profitability": view.profitability.value,
        },
    }


def core_view(scenario: LegacyDict, mapping: LegacyMapping = FINAL) -> dict[str, Any]:
    """Сценарий v0.3 → снимок ядра (или текст ошибки фикс-цены)."""
    core_scenario, warnings = scenario_to_core(scenario, mapping)
    try:
        estimate = calculate_estimate(core_scenario)
    except FixedPriceError as exc:
        return {"warnings": warnings, "error": str(exc)}
    return {"warnings": warnings, "estimate": estimate_view(estimate)}
