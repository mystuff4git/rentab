"""Калькулятор ФОТ, зарплатные налоги и ставки взносов работодателя v0.3."""

from __future__ import annotations

from typing import Any

import pytest

from tests.characterization.legacy import jurisdiction, team

pytestmark = pytest.mark.characterization


def test_employee_full_cost_uses_contract_hours() -> None:
    # KNOWN-BUG L-01 → фаза 2: знаменатель — контрактные, а не биллируемые часы.
    result = team.calculate_employee_full_cost(200_000, 168, 0.30)
    assert result == {
        "gross_salary": 200_000.0,
        "employer_taxes": pytest.approx(60_000.0),
        "total_monthly_cost": pytest.approx(260_000.0),
        "cost_rate_per_hour": pytest.approx(260_000 / 168),
    }
    assert team.DEFAULT_CONTRACT_HOURS_PER_MONTH == 168.0


@pytest.mark.parametrize(
    ("salary", "hours", "rate"),
    [(-1, 168, 0.3), (100_000, 0, 0.3), (100_000, 168, -0.01)],
)
def test_employee_full_cost_rejects_invalid_input(salary: float, hours: float, rate: float) -> None:
    with pytest.raises(ValueError, match=r"не может быть|должно быть"):
        team.calculate_employee_full_cost(salary, hours, rate)


def test_total_direct_labor() -> None:
    members = [{"cost_rate": 8000, "hours": 10}, {"cost_rate": 3500, "hours": 40}]
    assert team.total_direct_labor(members) == 220_000


@pytest.mark.parametrize(
    ("annual_salary", "expected_ndfl"),
    [
        (1_200_000, 156_000),  # 13% ниже порога
        (5_000_000, 650_000),  # ровно порог
        (6_000_000, 650_000 + 150_000),  # 15% со сверхпороговой части
    ],
)
def test_rf_payroll_flat_contributions_and_two_step_ndfl(
    annual_salary: float, expected_ndfl: float
) -> None:
    # KNOWN-BUG L-08 → фаза 3: НДФЛ по шкале 2021–2024, взносы плоские 30%
    # без единой предельной базы. На расчёт проекта не влияет: метод
    # add_payroll_taxes в v0.3 нигде не вызывается.
    calc = jurisdiction.TaxCalculator({"country": "RF", "regime": "USN"})
    result = calc.add_payroll_taxes(annual_salary)
    assert result["employee"]["НДФЛ"] == pytest.approx(expected_ndfl)
    assert result["employer"]["Страховые взносы"] == pytest.approx(annual_salary * 0.30)
    assert result["net_to_employee"] == pytest.approx(annual_salary - expected_ndfl)
    assert result["total_employer_cost"] == pytest.approx(annual_salary * 1.30)


def test_rf_payroll_zero_tariff_for_ausn() -> None:
    calc = jurisdiction.TaxCalculator(
        {"country": "RF", "regime": "AUSN", "social_contributions": "zero"}
    )
    assert calc.add_payroll_taxes(100_000)["employer_total"] == 0


def test_rf_payroll_rejects_unknown_tariff() -> None:
    calc = jurisdiction.TaxCalculator(
        {"country": "RF", "regime": "USN", "social_contributions": "reduced"}
    )
    with pytest.raises(ValueError, match="Неизвестный тариф"):
        calc.add_payroll_taxes(100_000)


def test_kz_payroll_flat_rates_without_deductions() -> None:
    # KNOWN-BUG L-08 → фаза 3: ставки РК плоские, ИПН без вычетов, нет социального налога.
    calc = jurisdiction.TaxCalculator({"country": "KZ", "regime": "OUR", "form": "too"})
    result = calc.add_payroll_taxes(500_000)
    assert result["employee"] == {
        "ОПВ": pytest.approx(50_000),
        "ИПН": pytest.approx(50_000),
        "ВОСМС": pytest.approx(10_000),
    }
    assert result["employer"] == {
        "ООСМС": pytest.approx(15_000),
        "СО": pytest.approx(25_000),
        "ОПВР": pytest.approx(17_500),
    }
    assert result["total_employer_cost"] == pytest.approx(557_500)


def test_payroll_rejects_negative_salary() -> None:
    calc = jurisdiction.TaxCalculator({"country": "RF", "regime": "USN"})
    with pytest.raises(ValueError, match="отрицательной"):
        calc.add_payroll_taxes(-1)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (None, 0.30),
        ({}, 0.30),
        ({"jurisdiction": "RF", "rf": {"regime": "USN", "social_contributions": "standard"}}, 0.30),
        ({"jurisdiction": "RF", "rf": {"regime": "USN", "social_contributions": "zero"}}, 0.0),
        ({"jurisdiction": "RF", "rf": {"regime": "AUSN"}}, 0.0),
        ({"jurisdiction": "RF", "rf": {"regime": "NPD"}}, 0.0),
        (
            {"jurisdiction": "RF", "rf": {"regime": "OSNO", "social_contributions": "standard"}},
            0.30,
        ),
        ({"jurisdiction": "RF", "rf": None}, 0.30),
        ({"jurisdiction": "KZ", "kz": {"form": "too"}}, 0.115),
        # Режим «Оба» сводится к РФ.
        ({"jurisdiction": "Both", "rf": {"regime": "NPD"}}, 0.0),
        ({"jurisdiction": "XX"}, 0.30),
    ],
)
def test_employer_contribution_rate(params: dict[str, Any] | None, expected: float) -> None:
    assert jurisdiction.get_employer_contribution_rate(params) == pytest.approx(expected)
