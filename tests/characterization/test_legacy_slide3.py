"""Слайд 3 защиты ВКР: план и факт «партнёр тянет проект» в логике v0.3.

В UI v0.3 выручку нельзя задать числом, поэтому сценарий собирается из функций
модулей v0.3 так же, как это делает биллинговая ветка страницы «Проект»:
ФОТ через total_direct_labor, накладные через ExpenseManager, налог через
TaxCalculator, NNE через calculator.nne.

Эти цифры — эталон старой логики. В фазе 2 (НДС сверху, а не из выручки)
они осознанно изменятся; golden-тест новой методологии появится там же.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.characterization.legacy import calculator, expenses, jurisdiction, team
from tests.characterization.legacy_runner import load_scenario

pytestmark = pytest.mark.characterization

SLIDE3 = load_scenario("slide3")


def run_variant(variant: str) -> dict[str, float]:
    """Считает вариант слайда 3 (plan / fact) модулями v0.3."""
    data: dict[str, Any] = SLIDE3["variants"][variant]
    members = [
        {"cost_rate": SLIDE3["cost_rates"][role], "hours": hours}
        for role, hours in data["hours"].items()
    ]
    total_hours = sum(m["hours"] for m in members)
    direct_labor = team.total_direct_labor(members)

    manager = expenses.ExpenseManager(billable_hours_per_month=SLIDE3["billable_hours_per_month"])
    manager.add_firm_overhead(
        expenses.Expense(
            name="Накладные фирмы",
            category="overhead",
            amount=SLIDE3["overheads_monthly"],
            period="monthly",
        )
    )
    overheads_alloc = manager.get_overheads_allocated(total_hours)

    tax_params = SLIDE3["tax_params"]
    tax = jurisdiction.TaxCalculator(tax_params).calculate_tax(
        revenue=SLIDE3["revenue"],
        params={
            **tax_params,
            "expenses": direct_labor + overheads_alloc,
            "disbursements_billed": 0.0,
        },
    )
    nne = calculator.nne(
        gross=SLIDE3["revenue"],
        direct_labor=direct_labor,
        overheads_alloc=overheads_alloc,
        disbursements_own=0.0,
        tax=tax["total_tax"],
    )
    return {
        "overhead_rate": manager.calculate_overhead_rate(SLIDE3["billable_hours_per_month"]),
        "direct_labor": direct_labor,
        "overheads_alloc": overheads_alloc,
        "income_tax": tax["income_tax"],
        "vat": tax["vat"],
        "nne": nne,
    }


@pytest.mark.parametrize("variant", ["plan", "fact"])
def test_slide3_reproduces_v03_numbers(variant: str) -> None:
    actual = run_variant(variant)
    expected = SLIDE3["variants"][variant]["expected_v03"]
    assert actual["overhead_rate"] == pytest.approx(100.0)
    for key, value in expected.items():
        assert actual[key] == pytest.approx(value), key


def test_slide3_vat_is_deducted_from_firm_revenue() -> None:
    # KNOWN-BUG L-02 → фаза 2: НДС 5% (15 000 ₽) вычтен из NNE как расход фирмы.
    plan = run_variant("plan")
    services_only = SLIDE3["revenue"] - plan["direct_labor"] - plan["overheads_alloc"]
    assert plan["nne"] == pytest.approx(services_only - plan["income_tax"] - plan["vat"])
    assert plan["vat"] == pytest.approx(15_000)
