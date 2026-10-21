"""Модули calculator, expenses, jurisdiction и fixed_price v0.3 по отдельности."""

from __future__ import annotations

from typing import Any

import pytest

from tests.characterization.legacy import (
    LEGACY_DATA_DIR,
    calculator,
    expenses,
    fixed_price,
    jurisdiction,
    project,
)

pytestmark = pytest.mark.characterization

USN_INCOME = {"country": "RF", "regime": "USN", "object": "income", "vat": "none"}


# ---------------------------------------------------------------------------
# calculator
# ---------------------------------------------------------------------------
def test_blended_rate_and_gross_revenue() -> None:
    team = [{"billing_rate": 15000, "hours": 10}, {"billing_rate": 6000, "hours": 40}]
    assert calculator.blended_rate(team) == pytest.approx(7800)
    assert calculator.gross_revenue(team) == 390_000
    assert calculator.blended_rate([]) == 0.0


def test_leverage_counts_senior_lawyer_as_senior() -> None:
    team = [
        {"role": "Партнёр", "hours": 4},
        {"role": "Старший юрист", "hours": 6},
        {"role": "Младший юрист", "hours": 30},
        {"role": "Стажёр", "hours": 10},
    ]
    assert calculator.leverage(team) == pytest.approx(4.0)
    assert calculator.leverage([{"role": "Стажёр", "hours": 10}]) == 0.0


def test_module_level_tax_helpers() -> None:
    # KNOWN-BUG L-07 → фаза 1: дубль налоговой логики; модульные функции
    # calculate_tax и tax_base в v0.3 нигде не вызываются.
    assert calculator.overhead_rate(160_000, 160) == 1000
    assert calculator.overhead_rate(160_000, 0) == 0.0
    assert calculator.tax_base(250_000, 50_000) == 200_000
    assert calculator.calculate_tax(200_000, 80_000, {"rate": 0.06, "base": "revenue"}) == 12_000
    assert calculator.calculate_tax(200_000, 80_000, {"rate": 0.15, "base": "profit"}) == 18_000
    assert calculator.calculate_tax(50_000, 80_000, {"rate": 0.15, "base": "profit"}) == 0.0
    with pytest.raises(ValueError, match="Неизвестный тип"):
        calculator.calculate_tax(1, 1, {"rate": 0.1, "base": "turnover"})
    assert calculator.nne(200_000, 70_000, 20_000, 5_000, 12_000) == 93_000


# ---------------------------------------------------------------------------
# expenses
# ---------------------------------------------------------------------------
def overhead(amount: float, period: str = "monthly", currency: str = "RUB") -> Any:
    """Создаёт накладной расход v0.3."""
    return expenses.Expense(
        name="Статья", category="overhead", amount=amount, currency=currency, period=period
    )


@pytest.mark.parametrize(
    ("period", "expected"), [("monthly", 12_000), ("annual", 1_000), ("one-time", 0)]
)
def test_expense_monthly_amount(period: str, expected: float) -> None:
    assert overhead(12_000, period).monthly_amount() == pytest.approx(expected)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"category": "salary"}, "category="),
        ({"period": "weekly"}, "period="),
        ({"currency": "EUR"}, "currency="),
        ({"amount": -1.0}, "amount не может быть отрицательной"),
    ],
)
def test_expense_validation(kwargs: dict[str, object], message: str) -> None:
    base: dict[str, object] = {"name": "x", "category": "overhead"}
    with pytest.raises(ValueError, match=message):
        expenses.Expense(**{**base, **kwargs})


def test_expense_from_dict_fills_defaults() -> None:
    expense = expenses.Expense.from_dict({"name": "Аренда"})
    assert expense.to_dict() == {
        "name": "Аренда",
        "category": "overhead",
        "amount": 0.0,
        "currency": "RUB",
        "period": "one-time",
        "billable": False,
    }


def test_overheads_in_different_currencies_are_summed_as_is() -> None:
    # KNOWN-BUG L-06 → фаза 2: RUB, KZT и USD складываются без конвертации.
    manager = expenses.ExpenseManager(billable_hours_per_month=100)
    manager.add_firm_overhead(overhead(100_000))
    manager.add_firm_overhead(overhead(500_000, currency="KZT"))
    manager.add_firm_overhead(overhead(1_000, currency="USD"))
    assert manager.total_overheads_monthly() == 601_000
    assert manager.calculate_overhead_rate(100) == 6_010


def test_overheads_allocated_takes_hours_despite_parameter_name() -> None:
    # KNOWN-BUG L-07 → фаза 1: параметр назван direct_costs, а передаются часы.
    manager = expenses.ExpenseManager(billable_hours_per_month=160)
    manager.add_firm_overhead(overhead(80_000))
    assert manager.get_overheads_allocated(20) == pytest.approx(10_000)
    assert manager.get_overheads_allocated(0) == 0.0
    assert manager.get_overheads_allocated(-5) == 0.0
    assert manager.calculate_overhead_rate(0) == 0.0


def test_expense_manager_category_guards() -> None:
    with pytest.raises(ValueError, match="должно быть > 0"):
        expenses.ExpenseManager(billable_hours_per_month=0)
    manager = expenses.ExpenseManager()
    assert manager.billable_hours_per_month == 160.0
    with pytest.raises(ValueError, match="overhead"):
        manager.add_firm_overhead(expenses.Expense(name="x", category="disbursement_own"))
    with pytest.raises(ValueError, match="add_firm_overhead"):
        manager.add_project_expense(overhead(1))


def test_project_expense_totals_by_category() -> None:
    manager = expenses.ExpenseManager()
    for category, amount, currency in [
        ("disbursement_billable", 38_500, "RUB"),
        ("disbursement_billable", 120_000, "KZT"),
        ("disbursement_own", 3_000, "RUB"),
        ("project_extra", 7_000, "RUB"),
    ]:
        manager.add_project_expense(
            expenses.Expense(name="x", category=category, amount=amount, currency=currency)
        )
    assert manager.get_disbursements_billed() == 158_500
    assert manager.get_disbursements_own() == 3_000
    # Считается, но в NNE не участвует (KNOWN-BUG L-12).
    assert manager.get_project_extras() == 7_000


def test_firm_expenses_template_has_zero_amounts() -> None:
    loaded = expenses.load_firm_overheads(LEGACY_DATA_DIR / "firm_expenses.json")
    assert len(loaded) == 10
    assert {e.amount for e in loaded} == {0.0}
    assert {e.period for e in loaded} == {"monthly"}


@pytest.mark.parametrize(
    ("filename", "currency"),
    [("rospatent_duties.json", "RUB"), ("qazpatent_duties.json", "KZT")],
)
def test_duties_catalogs_are_empty(filename: str, currency: str) -> None:
    # KNOWN-BUG L-09 → фаза 3.
    catalog = project.load_duties_catalog(LEGACY_DATA_DIR / filename)
    assert len(catalog) == 12
    assert {d["amount"] for d in catalog} == {0}
    assert {d["currency"] for d in catalog} == {currency}
    as_expenses = expenses.load_duties_as_expenses(LEGACY_DATA_DIR / filename)
    assert all(e.billable and e.category == "disbursement_billable" for e in as_expenses)


def test_catalog_loaders_raise_for_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        project.load_duties_catalog(LEGACY_DATA_DIR / "missing.json")
    with pytest.raises(FileNotFoundError):
        expenses.load_firm_overheads(LEGACY_DATA_DIR / "missing.json")
    with pytest.raises(FileNotFoundError):
        expenses.load_duties_as_expenses(LEGACY_DATA_DIR / "missing.json")


# ---------------------------------------------------------------------------
# jurisdiction
# ---------------------------------------------------------------------------
def test_regime_presets_catalog() -> None:
    keys = {
        country: [p["key"] for p in jurisdiction.get_presets_by_country(country)]
        for country in ("RF", "KZ")
    }
    assert keys == {
        "RF": [
            "RF_USN_income",
            "RF_USN_income_expenses",
            "RF_OSNO",
            "RF_NPD_legal",
            "RF_NPD_individual",
        ],
        "KZ": ["KZ_OUR_too", "KZ_OUR_too_vat", "KZ_OUR_ip"],
    }
    assert jurisdiction.get_preset("RF_OSNO")["label"] == "ОСНО — прибыль 25% + НДС 22%"
    with pytest.raises(KeyError):
        jurisdiction.get_preset("RF_PSN")
    with pytest.raises(ValueError, match="Неизвестная страна"):
        jurisdiction.get_presets_by_country("BY")


def test_currency_helpers() -> None:
    assert jurisdiction.get_currency_symbol("KZ") == "₸"
    assert jurisdiction.get_currency_code("RF") == "RUB"
    with pytest.raises(ValueError, match="Неизвестная страна"):
        jurisdiction.get_currency_symbol("US")
    with pytest.raises(ValueError, match="Неизвестная страна"):
        jurisdiction.get_currency_code("US")


def test_tax_calculator_docstring_example() -> None:
    result = jurisdiction.TaxCalculator(USN_INCOME).calculate_tax(
        revenue=250_000, params={**USN_INCOME, "expenses": 80_000, "disbursements_billed": 50_000}
    )
    assert result["taxable_base"] == 200_000
    assert result["total_tax"] == pytest.approx(12_000)


def test_profit_base_tax_never_negative() -> None:
    params = {"country": "RF", "regime": "USN", "object": "income_minus_expenses", "vat": "none"}
    result = jurisdiction.TaxCalculator(params).calculate_tax(
        revenue=100_000, params={**params, "expenses": 150_000}
    )
    # KNOWN-BUG L-08 → фаза 3: минимальный налог 1% для УСН 15% не применяется.
    assert result["income_tax"] == 0.0


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"country": "RF"}, "должен содержать"),
        ({"country": "BY", "regime": "USN"}, "Неизвестная страна"),
    ],
)
def test_tax_calculator_rejects_bad_params(params: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        jurisdiction.TaxCalculator(params)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"country": "RF", "regime": "PSN"}, "Неизвестный режим РФ"),
        ({"country": "RF", "regime": "USN", "object": "turnover"}, "Неизвестный объект УСН"),
        ({"country": "RF", "regime": "USN", "object": "income", "vat": "10%"}, "НДС УСН"),
        ({"country": "RF", "regime": "NPD", "client_type": "foreign"}, "тип клиента для НПД"),
        ({"country": "KZ", "regime": "SNR"}, "только ОУР"),
        ({"country": "KZ", "regime": "OUR", "form": "ao"}, "форма организации"),
        ({"country": "KZ", "regime": "OUR", "form": "too", "vat": "12%"}, "НДС РК"),
    ],
)
def test_tax_calculator_rejects_unknown_options(params: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        jurisdiction.TaxCalculator(params).calculate_tax(revenue=100_000)


# ---------------------------------------------------------------------------
# fixed_price
# ---------------------------------------------------------------------------
def one_stage(cost: float) -> list[dict[str, object]]:
    """Один этап с одним исполнителем на 1 час — издержки равны cost."""
    member = {"name": "x", "role": "Партнёр", "billing_rate": 0.0, "cost_rate": cost, "hours": 1.0}
    return [{"name": "Этап", "assigned_members": [member]}]


@pytest.mark.parametrize("margin", [-0.1, 1.0, 1.5])
def test_fixed_price_rejects_margin_outside_unit_interval(margin: float) -> None:
    calc = fixed_price.FixedPriceCalculator()
    with pytest.raises(ValueError, match=r"\[0; 1\)"):
        calc.calculate(one_stage(1000), expenses.ExpenseManager(), USN_INCOME, margin)


def test_fixed_price_unreachable_margin() -> None:
    calc = fixed_price.FixedPriceCalculator()
    with pytest.raises(ValueError, match="не может быть прибыльным"):
        calc.calculate(one_stage(1000), expenses.ExpenseManager(), USN_INCOME, 0.95)


def test_stage_flags_before_calculate_ignore_overheads() -> None:
    calc = fixed_price.FixedPriceCalculator()
    flags = calc.get_stage_flags(one_stage(1000), fixed_price=2000, target_margin=0.3)
    assert flags == [
        {
            "stage_name": "Этап",
            "stage_costs": 1000.0,
            "stage_revenue_share": 2000.0,
            "stage_margin": 0.5,
            "flag": "green",
        }
    ]
    assert calc.get_stage_flags([{"name": "Пусто"}], 0, 0.3)[0]["flag"] == "red"


def test_fixed_price_iteration_constants() -> None:
    # KNOWN-BUG L-03 → фаза 2: заменяются закрытыми формулами.
    assert fixed_price.MAX_PRICE_ITERATIONS == 50
    assert fixed_price.PRICE_CONVERGENCE_EPS == 0.01
    assert fixed_price.RED_MARGIN_THRESHOLD == 0.0
