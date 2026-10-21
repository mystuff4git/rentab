"""
Rentab v0.3 — модуль расчёта фиксированной стоимости проекта.

В отличие от биллинговой модели (выручка = часы × ставка клиента),
фикс-прайс считает цену снизу вверх: от издержек к нужной марже.
Fixed Price = Total Costs / (1 - target_margin - effective_tax_rate)
"""

from __future__ import annotations

from typing import Any

from modules.expenses import ExpenseManager
from modules.jurisdiction import TaxCalculator
from modules.team import total_direct_labor


# ---------------------------------------------------------------------------
# Параметры классификации этапов по марже.
# Эти границы — единственное место для правки правил «светофора»; никаких
# хардкодных значений в логике get_stage_flags не используется.
# ---------------------------------------------------------------------------
# Этап считается убыточным, если маржа меньше или равна этой границе.
RED_MARGIN_THRESHOLD: float = 0.0

# ---------------------------------------------------------------------------
# Параметры численного решения уравнения фикс-прайса.
# Для режимов с налоговой базой "revenue" (УСН Доходы, НПД, АУСН) задача
# линейна и сходится за один шаг. Для базы "profit" (УСН Доходы−Расходы,
# ОСНО, ОУР) effective_tax_rate зависит от самой цены, поэтому требуется
# пара итераций — обычно 2–3, но запас берём с большим избытком.
# ---------------------------------------------------------------------------
MAX_PRICE_ITERATIONS: int = 50

# Допустимая абсолютная погрешность сходимости цены (валюта проекта).
# 1 копейка достаточна — выше точности входных ставок и часов.
PRICE_CONVERGENCE_EPS: float = 0.01


class FixedPriceCalculator:
    """Калькулятор фиксированной цены проекта по методологии «снизу вверх».

    В фикс-прайс модели цена клиенту определяется не часами × ставкой
    (как в биллинговой), а решением уравнения:

        Fixed Price = Total Costs / (1 - target_margin - effective_tax_rate)

    где
        Total Costs        = Direct Labor + Overheads_alloc + Disbursements_own
        Direct Labor       = Σ(cost_rate_i × hours_i)
        Overheads_alloc    = expense_manager.get_overheads_allocated(total_hours)
        Disbursements_own  = expense_manager.get_disbursements_own()
        effective_tax_rate = total_tax / Fixed Price (из TaxCalculator)

    Для режимов с базой "revenue" effective_tax_rate совпадает с номинальной
    ставкой и формула решается за один шаг. Для режимов с базой "profit"
    налог зависит от прибыли, поэтому используется численная фиксированная
    точка (см. _solve_fixed_price).

    После расчёта `calculate()` объект кэширует промежуточные величины
    (распределённые накладные и суммарные часы), которые используются
    в `get_stage_flags()` для пропорционального разнесения накладных
    и выручки по этапам.
    """

    def __init__(self) -> None:
        """Создаёт калькулятор без состояния расчёта.

        Промежуточные величины заполняются методом `calculate` и затем
        используются в `get_stage_flags`. До первого вызова `calculate`
        они хранят нули — `get_stage_flags` корректно отработает на
        пустых данных и вернёт нулевые издержки.
        """
        self._last_overheads_alloc: float = 0.0
        self._last_total_hours: float = 0.0

    # ------------------------------------------------------------------ #
    #  Публичный API                                                     #
    # ------------------------------------------------------------------ #

    def calculate(
        self,
        team_stages: list[dict],
        expense_manager: ExpenseManager,
        jurisdiction_params: dict,
        target_margin: float,
    ) -> dict:
        """Рассчитывает фиксированную цену проекта и связанные показатели.

        Args:
            team_stages: Список этапов проекта в виде словарей. Каждый
                этап ожидает ключи:
                  - "name" (str)               — название этапа;
                  - "assigned_members" (list[dict]) — назначенные сотрудники
                    с полями name, role, billing_rate, cost_rate, hours.
                Дополнительные поля игнорируются.
            expense_manager: Менеджер расходов фирмы и проекта. Источник
                накладных (через get_overheads_allocated) и собственных
                disbursements (через get_disbursements_own).
            jurisdiction_params: Параметры налогового режима в формате
                REGIME_PRESETS[...]["params"] из modules/jurisdiction.py.
            target_margin: Целевая маржа фирмы после налога, доля от 0 до 1.

        Returns:
            Словарь со следующими ключами:
              - fixed_price       (float) — итоговая цена для клиента;
              - total_costs       (float) — все издержки проекта;
              - direct_labor      (float) — Σ(cost_rate × hours);
              - overheads_alloc   (float) — распределённые накладные фирмы;
              - disbursements_own (float) — расходы за счёт фирмы;
              - tax_amount        (float) — налог по выбранному режиму;
              - actual_margin     (float) — фактическая маржа после налога,
                доля от 0 до 1 (для revenue-base совпадает с target_margin);
              - nne               (float) — чистая прибыль фирмы.

        Raises:
            ValueError: Если target_margin вне [0, 1) либо сумма
                target_margin + effective_tax_rate ≥ 1 (тогда формула
                не имеет положительного решения и проект убыточен
                при любой цене).
        """
        if not 0.0 <= target_margin < 1.0:
            raise ValueError(
                f"target_margin должна быть в [0; 1), получено {target_margin!r}"
            )

        # --- агрегируем команду по всем этапам ---
        all_members: list[dict] = []
        total_hours: float = 0.0
        for stage in team_stages:
            for member in stage.get("assigned_members", []):
                all_members.append(member)
                total_hours += float(member["hours"])

        # --- издержки ---
        direct_labor = total_direct_labor(all_members)
        overheads_alloc = expense_manager.get_overheads_allocated(total_hours)
        disbursements_own = expense_manager.get_disbursements_own()
        total_costs = direct_labor + overheads_alloc + disbursements_own

        # --- решаем уравнение для fixed_price ---
        tax_calculator = TaxCalculator(jurisdiction_params)
        fixed_price = self._solve_fixed_price(
            tax_calculator=tax_calculator,
            jurisdiction_params=jurisdiction_params,
            total_costs=total_costs,
            target_margin=target_margin,
        )

        # --- финальный налог при найденной цене ---
        tax_result = tax_calculator.calculate_tax(
            revenue=fixed_price,
            params={
                **jurisdiction_params,
                "expenses": total_costs,
                "disbursements_billed": 0.0,
            },
        )
        tax_amount = float(tax_result["total_tax"])
        nne_value = fixed_price - total_costs - tax_amount
        actual_margin = nne_value / fixed_price if fixed_price > 0 else 0.0

        # --- кэшируем для get_stage_flags ---
        self._last_overheads_alloc = overheads_alloc
        self._last_total_hours = total_hours

        return {
            "fixed_price": fixed_price,
            "total_costs": total_costs,
            "direct_labor": direct_labor,
            "overheads_alloc": overheads_alloc,
            "disbursements_own": disbursements_own,
            "tax_amount": tax_amount,
            "actual_margin": actual_margin,
            "nne": nne_value,
        }

    def get_stage_flags(
        self,
        stages: list[dict],
        fixed_price: float,
        target_margin: float,
    ) -> list[dict]:
        """Классифицирует этапы по их маржинальности.

        Доля выручки и доля накладных распределяются по этапам
        пропорционально часам. Маржа этапа считается по той же логике,
        что и общая: (revenue_share − stage_costs) / revenue_share.
        Накладные на этапах берутся из последнего вызова calculate(),
        поэтому метод корректно работает только после него (до — вернёт
        этапы без накладных).

        Args:
            stages: Этапы проекта в том же формате, что и в calculate.
            fixed_price: Итоговая цена проекта (выход calculate).
            target_margin: Целевая маржа, доля от 0 до 1. Используется
                для классификации флагов (см. правила ниже).

        Returns:
            Список словарей по одному на этап со следующими ключами:
              - stage_name           (str)   — название этапа;
              - stage_costs          (float) — трудозатраты этапа +
                                               его доля накладных;
              - stage_revenue_share  (float) — доля fixed_price пропорц. часам;
              - stage_margin         (float) — маржа этапа, доля 0..1;
              - flag                 (str)   — "green" / "yellow" / "red".

            Правила «светофора»:
              - "green":  stage_margin >= target_margin      (этап тянет план);
              - "yellow": 0 < stage_margin < target_margin   (этап в плюс,
                                                              но ниже цели);
              - "red":    stage_margin <= 0                  (этап убыточен).
        """
        total_hours: float = 0.0
        for stage in stages:
            for member in stage.get("assigned_members", []):
                total_hours += float(member["hours"])

        result: list[dict] = []
        for stage in stages:
            stage_hours = sum(
                float(m["hours"]) for m in stage.get("assigned_members", [])
            )
            stage_labor = sum(
                float(m["cost_rate"]) * float(m["hours"])
                for m in stage.get("assigned_members", [])
            )

            if total_hours > 0:
                hour_share = stage_hours / total_hours
            else:
                hour_share = 0.0

            stage_overheads_share = self._last_overheads_alloc * hour_share
            stage_revenue_share = fixed_price * hour_share
            stage_costs = stage_labor + stage_overheads_share

            if stage_revenue_share > 0:
                stage_margin = (stage_revenue_share - stage_costs) / stage_revenue_share
            else:
                stage_margin = 0.0

            if stage_margin <= RED_MARGIN_THRESHOLD:
                flag = "red"
            elif stage_margin >= target_margin:
                flag = "green"
            else:
                flag = "yellow"

            result.append(
                {
                    "stage_name": str(stage.get("name", "")),
                    "stage_costs": stage_costs,
                    "stage_revenue_share": stage_revenue_share,
                    "stage_margin": stage_margin,
                    "flag": flag,
                }
            )
        return result

    # ------------------------------------------------------------------ #
    #  Внутренние методы                                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _solve_fixed_price(
        tax_calculator: TaxCalculator,
        jurisdiction_params: dict,
        total_costs: float,
        target_margin: float,
    ) -> float:
        """Решает уравнение Fixed Price = Total Costs / (1 − margin − tax_rate).

        Уравнение нелинейно для режимов с базой "profit" (тогда tax_rate
        зависит от самой цены), поэтому решается итерационно методом
        фиксированной точки. Для базы "revenue" сходится за один шаг.

        Args:
            tax_calculator: Налоговый калькулятор с настроенным режимом.
            jurisdiction_params: Параметры режима, которые также передаются
                в calculate_tax (нужны для ключа "expenses").
            total_costs: Сумма всех издержек проекта (Direct Labor +
                Overheads_alloc + Disbursements_own).
            target_margin: Целевая маржа, доля 0..1.

        Returns:
            Найденная цена fixed_price (валюта проекта).

        Raises:
            ValueError: Если на каком-либо шаге знаменатель
                (1 − target_margin − effective_tax_rate) ≤ 0 — тогда
                проект не может быть прибыльным при заданной цели.
        """
        # Стартовое приближение: цена при нулевом налоге.
        denominator = 1.0 - target_margin
        if denominator <= 0:
            raise ValueError(
                f"target_margin={target_margin} оставляет нулевую долю на издержки"
            )
        fixed_price = total_costs / denominator

        for _ in range(MAX_PRICE_ITERATIONS):
            tax_result = tax_calculator.calculate_tax(
                revenue=fixed_price,
                params={
                    **jurisdiction_params,
                    "expenses": total_costs,
                    "disbursements_billed": 0.0,
                },
            )
            tax_amount = float(tax_result["total_tax"])
            effective_tax_rate = tax_amount / fixed_price if fixed_price > 0 else 0.0

            denominator = 1.0 - target_margin - effective_tax_rate
            if denominator <= 0:
                raise ValueError(
                    f"Сумма target_margin + effective_tax_rate "
                    f"= {target_margin + effective_tax_rate:.4f} ≥ 1: "
                    f"проект не может быть прибыльным при такой цели."
                )

            new_price = total_costs / denominator
            if abs(new_price - fixed_price) < PRICE_CONVERGENCE_EPS:
                return new_price
            fixed_price = new_price

        return fixed_price
