"""
Rentab v0.3 — сквозные тесты обеих моделей ценообразования.

Проверяет два сценария:
  1. Биллинговая модель (как в v0.2) — связка
     calculator.py + expenses.py + jurisdiction.py.
  2. Фикс-прайс модель (новая в v0.3) — модуль fixed_price.py.

Оба прогоняются на контрольных входных данных, ручной расчёт зафиксирован
в docstring соответствующих секций.

Запуск:
    python test_scenario.py

Скрипт печатает обе сводки (ручную и автоматическую), сравнивает ключевые
показатели и возвращает код выхода 0 при совпадении, 1 при расхождении.

Сценарий:
    Команда:
      - Партнёр:           billing 15 000 ₽/ч,  cost 8 000 ₽/ч,  5 часов
      - Младший юрист:     billing  6 000 ₽/ч,  cost 3 500 ₽/ч,  15 часов
    Этап: 20 часов суммарно.
    Юрисдикция: РФ, УСН «Доходы» 6%, НДС «none», взносы standard.
    Накладные фирмы: 80 000 ₽/мес, 160 оплачиваемых часов в месяц.
    Пошлины и собственные расходы: нет.

Ручные расчёты (все цифры округлены до ₽):
    Gross Revenue     = 15 000 × 5 + 6 000 × 15                = 165 000 ₽
    Direct Labor      =  8 000 × 5 + 3 500 × 15                =  92 500 ₽
    Blended Rate      = 165 000 / 20                           =   8 250 ₽/ч
    Leverage          = 15 / 5                                 =    3.00
    Overhead Rate     = 80 000 / 160                           =     500 ₽/ч
    Overheads alloc   = 500 × 20                               =  10 000 ₽
    Tax (УСН 6%)      = 165 000 × 0.06                         =   9 900 ₽
    NNE               = 165 000 − 92 500 − 10 000 − 0 − 9 900  =  52 600 ₽
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from modules.calculator import blended_rate, gross_revenue, leverage, nne
from modules.expenses import Expense, ExpenseManager
from modules.fixed_price import FixedPriceCalculator
from modules.jurisdiction import TaxCalculator
from modules.team import total_direct_labor


# ---------------------------------------------------------------------------
# Ожидаемые эталонные значения (рассчитаны руками — см. docstring модуля)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Expected:
    """Эталонные показатели сценария — считаем «руками» и зашиваем сюда."""

    gross_revenue: float = 165_000.0
    direct_labor: float = 92_500.0
    blended_rate: float = 8_250.0
    leverage: float = 3.0
    overhead_rate: float = 500.0
    overheads_alloc: float = 10_000.0
    tax: float = 9_900.0
    nne: float = 52_600.0


# Погрешность сравнения — копеечный хвост float-арифметики нас не интересует.
EPS = 0.01


def _approx_equal(actual: float, expected: float, *, eps: float = EPS) -> bool:
    """Проверяет равенство float'ов с абсолютной погрешностью eps."""
    return abs(actual - expected) <= eps


def run_scenario() -> dict[str, float]:
    """Прогоняет сценарий через модули calculator/expenses/jurisdiction.

    Returns:
        Словарь с теми же ключами, что и Expected, для сравнения.
    """
    # --- команда с часами ---
    team_with_hours = [
        {
            "name": "Иванов",
            "role": "Партнёр",
            "billing_rate": 15_000.0,
            "cost_rate": 8_000.0,
            "hours": 5.0,
        },
        {
            "name": "Петрова",
            "role": "Младший юрист",
            "billing_rate": 6_000.0,
            "cost_rate": 3_500.0,
            "hours": 15.0,
        },
    ]
    total_hours = sum(m["hours"] for m in team_with_hours)

    # --- показатели команды ---
    gr = gross_revenue(team_with_hours)
    dl = total_direct_labor(team_with_hours)
    br = blended_rate(team_with_hours)
    lev = leverage(team_with_hours)

    # --- накладные фирмы ---
    manager = ExpenseManager(billable_hours_per_month=160.0)
    manager.add_firm_overhead(
        Expense(
            name="Аренда + ПО",
            category="overhead",
            amount=80_000.0,
            currency="RUB",
            period="monthly",
        )
    )
    oh_rate = manager.calculate_overhead_rate(160.0)
    oh_alloc = manager.get_overheads_allocated(total_hours)

    # --- налог: РФ УСН «Доходы» 6% ---
    tax_params = {
        "country": "RF",
        "regime": "USN",
        "object": "income",
        "vat": "none",
        "social_contributions": "standard",
    }
    tc = TaxCalculator(tax_params)
    tax_result = tc.calculate_tax(
        revenue=gr,  # пошлин нет → revenue == gross
        params={**tax_params, "expenses": dl + oh_alloc, "disbursements_billed": 0.0},
    )
    tax = float(tax_result["total_tax"])

    # --- NNE ---
    nne_val = nne(
        gross=gr,
        direct_labor=dl,
        overheads_alloc=oh_alloc,
        disbursements_own=0.0,
        tax=tax,
    )

    return {
        "gross_revenue": gr,
        "direct_labor": dl,
        "blended_rate": br,
        "leverage": lev,
        "overhead_rate": oh_rate,
        "overheads_alloc": oh_alloc,
        "tax": tax,
        "nne": nne_val,
    }


def compare_and_report(actual: dict[str, float], expected: Expected) -> bool:
    """Печатает таблицу сравнения и возвращает True, если всё сходится."""
    header = f"{'Показатель':<22} {'Ожидаем':>12} {'Факт':>12}  Статус"
    print(header)
    print("-" * len(header))

    all_ok = True
    for key in (
        "gross_revenue",
        "direct_labor",
        "blended_rate",
        "leverage",
        "overhead_rate",
        "overheads_alloc",
        "tax",
        "nne",
    ):
        exp = getattr(expected, key)
        act = actual[key]
        ok = _approx_equal(act, exp)
        all_ok = all_ok and ok
        mark = "OK" if ok else "FAIL"
        print(f"{key:<22} {exp:>12,.2f} {act:>12,.2f}  {mark}")

    return all_ok


# ---------------------------------------------------------------------------
# Сценарий v0.3 — fixed_price_basic
# ---------------------------------------------------------------------------
# Команда: партнёр (cost 5 000 ₽/ч) + младший (cost 2 000 ₽/ч).
# Этапы:
#   "Подготовка"   — 10 часов, партнёр
#   "Регистрация"  — 15 часов, младший юрист
# target_margin = 0.30, юрисдикция РФ УСН «Доходы» 6%.
# Накладные: 80 000 ₽/мес, billable 160 ч/мес.
#
# Ручной расчёт (для контроля):
#   Direct Labor       = 5 000 × 10 + 2 000 × 15            =  80 000 ₽
#   Total hours        = 25
#   Overhead Rate      = 80 000 / 160                       =     500 ₽/ч
#   Overheads alloc    = 500 × 25                           =  12 500 ₽
#   Total Costs        = 80 000 + 12 500 + 0                =  92 500 ₽
#   Fixed Price        = 92 500 / (1 − 0.30 − 0.06)         = 144 531.25 ₽
#   Tax (УСН 6%)       = 144 531.25 × 0.06                  =   8 671.88 ₽
#   NNE                = 144 531.25 − 92 500 − 8 671.88     =  43 359.38 ₽
#   actual_margin      = 43 359.38 / 144 531.25             =       0.30
# ---------------------------------------------------------------------------

# Допустимое расхождение фактической маржи от целевой (доля).
FIXED_PRICE_MARGIN_TOLERANCE: float = 0.01


def run_fixed_price_basic() -> dict[str, float]:
    """Прогоняет сценарий fixed_price_basic через FixedPriceCalculator.

    Returns:
        Словарь — точно тот, что возвращает FixedPriceCalculator.calculate(),
        плюс ключи "stages" со списком dict-этапов и "stage_flags" с выводом
        get_stage_flags() для печати.
    """
    # --- этапы проекта ---
    stages: list[dict] = [
        {
            "name": "Подготовка",
            "assigned_members": [
                {
                    "name": "Иванов",
                    "role": "Партнёр",
                    # billing_rate в фикс-прайс модели не используется — ставим 0
                    "billing_rate": 0.0,
                    "cost_rate": 5_000.0,
                    "hours": 10.0,
                },
            ],
        },
        {
            "name": "Регистрация",
            "assigned_members": [
                {
                    "name": "Петрова",
                    "role": "Младший юрист",
                    "billing_rate": 0.0,
                    "cost_rate": 2_000.0,
                    "hours": 15.0,
                },
            ],
        },
    ]

    # --- накладные фирмы ---
    manager = ExpenseManager(billable_hours_per_month=160.0)
    manager.add_firm_overhead(
        Expense(
            name="Аренда + ПО",
            category="overhead",
            amount=80_000.0,
            currency="RUB",
            period="monthly",
        )
    )

    # --- налоговый режим: РФ УСН «Доходы» 6% ---
    jurisdiction_params = {
        "country": "RF",
        "regime": "USN",
        "object": "income",
        "vat": "none",
        "social_contributions": "standard",
    }
    target_margin = 0.30

    # --- расчёт ---
    calculator = FixedPriceCalculator()
    result = calculator.calculate(
        team_stages=stages,
        expense_manager=manager,
        jurisdiction_params=jurisdiction_params,
        target_margin=target_margin,
    )
    flags = calculator.get_stage_flags(
        stages=stages,
        fixed_price=result["fixed_price"],
        target_margin=target_margin,
    )
    return {**result, "stages": stages, "stage_flags": flags, "target_margin": target_margin}


def report_fixed_price_basic(result: dict) -> bool:
    """Печатает результаты сценария fixed_price_basic и проверяет инварианты.

    Проверяет:
      1. fixed_price > total_costs — у проекта есть положительная маржа;
      2. |actual_margin − target_margin| <= FIXED_PRICE_MARGIN_TOLERANCE.

    Args:
        result: Выход run_fixed_price_basic().

    Returns:
        True, если оба инварианта выполнены.
    """
    fixed_price = float(result["fixed_price"])
    total_costs = float(result["total_costs"])
    direct_labor = float(result["direct_labor"])
    overheads_alloc = float(result["overheads_alloc"])
    disbursements_own = float(result["disbursements_own"])
    tax_amount = float(result["tax_amount"])
    nne_value = float(result["nne"])
    actual_margin = float(result["actual_margin"])
    target_margin = float(result["target_margin"])

    print("Сценарий: fixed_price_basic")
    print("-" * 56)
    print(f"  Direct Labor        : {direct_labor:>14,.2f} RUB")
    print(f"  Overheads alloc     : {overheads_alloc:>14,.2f} RUB")
    print(f"  Disbursements own   : {disbursements_own:>14,.2f} RUB")
    print(f"  Total Costs         : {total_costs:>14,.2f} RUB")
    print(f"  Fixed Price         : {fixed_price:>14,.2f} RUB")
    print(f"  Tax                 : {tax_amount:>14,.2f} RUB")
    print(f"  NNE                 : {nne_value:>14,.2f} RUB")
    print(f"  Target margin       : {target_margin:>14.2%}")
    print(f"  Actual margin       : {actual_margin:>14.2%}")
    print()

    print("  Этапы (флаги):")
    for stage in result["stage_flags"]:
        print(
            f"    [{stage['flag']:>6}] "
            f"{stage['stage_name']:<14} "
            f"costs={stage['stage_costs']:>10,.2f}  "
            f"revenue={stage['stage_revenue_share']:>10,.2f}  "
            f"margin={stage['stage_margin']:>6.2%}"
        )
    print()

    price_ok = fixed_price > total_costs
    margin_ok = abs(actual_margin - target_margin) <= FIXED_PRICE_MARGIN_TOLERANCE

    print(
        f"  Проверка fixed_price > total_costs : "
        f"{'OK' if price_ok else 'FAIL'}"
    )
    print(
        f"  Проверка |actual - target| <= {FIXED_PRICE_MARGIN_TOLERANCE:.2f} : "
        f"{'OK' if margin_ok else 'FAIL'}"
    )

    return price_ok and margin_ok


def main() -> int:
    """Точка входа скрипта. Возвращает 0 при успехе, 1 при расхождениях."""
    print("Rentab v0.3 — сквозные тесты расчётов\n")

    print("=" * 56)
    print("[1/2] Биллинговая модель — расчёт NNE")
    print("=" * 56)
    actual = run_scenario()
    ok_billing = compare_and_report(actual, Expected())
    print()

    print("=" * 56)
    print("[2/2] Фикс-прайс модель — базовый сценарий")
    print("=" * 56)
    fp_result = run_fixed_price_basic()
    ok_fixed = report_fixed_price_basic(fp_result)
    print()

    if ok_billing and ok_fixed:
        # Без emoji: Windows-консоль (cp1251) не умеет их печатать
        # и падает с UnicodeEncodeError на ✅/❌.
        print("[OK] Все сценарии сходятся с ручным расчётом.")
        return 0
    print("[FAIL] Есть расхождения — см. строки со статусом FAIL выше.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
