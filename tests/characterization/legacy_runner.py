"""Повтор расчётной части страниц v0.3 поверх модулей legacy.

В v0.3 оркестрация расчёта живёт не в модулях, а прямо в странице
`pages/02_Проект.py` (блоки 2, 4 и 5, строки 277–299 и 494–751), а часть
производных показателей считается в `pages/03_Дашборд.py`. Страницы
импортируют streamlit и исполняются целиком при импорте, поэтому вызвать их из
тестов нельзя. Здесь та же логика повторена построчно; комментарии `02:NNN`
и `03:NNN` указывают на строки исходника.

Отклонения от исходника недопустимы: при сомнении сверяться с legacy/.
Единственное отличие: вместо `st.warning` / `st.error` предупреждения
собираются в список, а `st.stop()` после ошибки фикс-прайса заменён
пробросом исключения.

Формат сценария описан в tests/characterization/scenarios/README.md.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.characterization.legacy import LEGACY_DATA_DIR, calculator, expenses, jurisdiction
from tests.characterization.legacy import fixed_price as fixed_price_module
from tests.characterization.legacy import project as project_module
from tests.characterization.legacy import team as team_module

Scenario = Mapping[str, Any]

SCENARIO_DIR = Path(__file__).parent / "scenarios"

# 02:67–69 — дефолт целевой маржи в процентах.
TARGET_MARGIN_DEFAULT_PCT = 30

# 02:75–79 — пути к каталогам пошлин.
DUTIES_CATALOGS = {
    "RF": LEGACY_DATA_DIR / "rospatent_duties.json",
    "KZ": LEGACY_DATA_DIR / "qazpatent_duties.json",
}

# 02:513–525 — параметры режима по умолчанию, если в jurisdiction_params пусто.
DEFAULT_TAX_PARAMS = {
    "RF": {"country": "RF", "regime": "USN", "object": "income", "vat": "none"},
    "KZ": {"country": "KZ", "regime": "OUR", "form": "too", "vat": "none"},
}


def load_scenario(name: str) -> dict[str, Any]:
    """Читает сценарий `scenarios/<name>.json`."""
    path = SCENARIO_DIR / f"{name}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def build_team_with_hours(scenario: Scenario) -> list[dict[str, Any]]:
    """Собирает team_with_hours так же, как блок 2 страницы «Проект» (02:281–295)."""
    team_by_name = {m["name"]: m for m in scenario["team"]}
    team_with_hours: list[dict[str, Any]] = []
    for stage in scenario["stages"]:
        member = team_by_name.get(stage["executor"])
        if member is None:
            continue
        team_with_hours.append(
            {
                "stage_name": stage["name"],
                "name": member["name"],
                "role": member["role"],
                "billing_rate": float(member["billing_rate"]),
                "cost_rate": float(member["cost_rate"]),
                "hours": float(stage["hours"]),
            }
        )
    return team_with_hours


def calculate_estimate(scenario: Scenario) -> tuple[dict[str, Any], list[str]]:
    """Повторяет нажатие «Рассчитать смету» на странице «Проект» v0.3.

    Args:
        scenario: Входные данные в формате scenarios/README.md.

    Returns:
        Пара (results, warnings): results — словарь, который v0.3 кладёт в
        `st.session_state["results"]`; warnings — тексты `st.warning`.

    Raises:
        ValueError: Если FixedPriceCalculator не нашёл цену (в v0.3 — st.error).
    """
    warnings: list[str] = []
    team = scenario["team"]
    jurisdiction_params = scenario["jurisdiction_params"]
    jurisdiction_mode = jurisdiction_params.get("jurisdiction", "RF")  # 02:142
    is_fixed_price = scenario["pricing_model"] == "fixed"  # 02:121
    team_by_name = {m["name"]: m for m in team}
    stages_data = scenario["stages"]
    project = scenario.get("project", {})

    # --- 02:281–299: показатели команды ---
    team_with_hours = build_team_with_hours(scenario)
    br_current = calculator.blended_rate(team_with_hours)
    lev_current = calculator.leverage(team_with_hours)

    # --- 02:499–528: юрисдикция расчёта и параметры налога ---
    calc_country = scenario["calc_country"] if jurisdiction_mode == "Both" else jurisdiction_mode
    params_key = "rf" if calc_country == "RF" else "kz"
    tax_params = jurisdiction_params.get(params_key) or DEFAULT_TAX_PARAMS[calc_country]
    calc_symbol = jurisdiction.CURRENCY_SYMBOLS[calc_country]
    calc_code = jurisdiction.CURRENCY_CODES[calc_country]

    # --- 02:553–563: накладные фирмы ---
    billable_hours_month = float(scenario.get("billable_hours_per_month", 160.0))
    manager = expenses.ExpenseManager(billable_hours_per_month=billable_hours_month)
    for item in scenario.get("firm_expenses", []):
        try:
            manager.add_firm_overhead(expenses.Expense.from_dict(item))
        except ValueError as exc:
            warnings.append(f"Пропущен накладной расход: {exc}")

    # --- 02:567–589: пошлины из каталогов ---
    disb_countries = ["RF", "KZ"] if jurisdiction_mode == "Both" else [jurisdiction_mode]
    selected_duties = scenario.get("selected_duties", {})
    for cc in disb_countries:
        catalog_path = DUTIES_CATALOGS.get(cc)
        if catalog_path is None or not catalog_path.exists():
            continue
        duties_list = project_module.load_duties_catalog(catalog_path)
        duties_map = project_module.duties_display_options(duties_list)
        for label in selected_duties.get(cc, []):
            amount = float(duties_map.get(label, 0.0))
            if amount <= 0:
                continue
            manager.add_project_expense(
                expenses.Expense(
                    name=label,
                    category="disbursement_billable",
                    amount=amount,
                    currency=jurisdiction.CURRENCY_CODES[cc],
                    period="one-time",
                    billable=True,
                )
            )

    # --- 02:592–596: произвольные расходы проекта ---
    for item in scenario.get("custom_expenses", []):
        try:
            manager.add_project_expense(expenses.Expense.from_dict(item))
        except ValueError as exc:
            warnings.append(f"Пропущен произвольный расход: {exc}")

    # --- 02:599–622: общие поля результата ---
    project_expenses_dump = [e.to_dict() for e in manager.project_expenses]
    overhead_rate_val = manager.calculate_overhead_rate(billable_hours_month)
    total_hours_val = sum(m["hours"] for m in team_with_hours)
    base_results: dict[str, Any] = {
        "project_name": project.get("name", ""),
        "client": project.get("client", ""),
        "description": project.get("description", ""),
        "calc_country": calc_country,
        "jurisdiction_mode": jurisdiction_mode,
        "currency": calc_symbol,
        "currency_code": calc_code,
        "blended_rate": br_current,
        "leverage": lev_current,
        "total_hours": total_hours_val,
        "overhead_rate": overhead_rate_val,
        "team_with_hours": team_with_hours,
        "project_expenses": project_expenses_dump,
    }

    if is_fixed_price:
        # --- 02:629–697: фикс-прайс ---
        team_stages_for_fp: list[dict[str, Any]] = []
        for stage in stages_data:
            member = team_by_name.get(stage["executor"])
            if member is None:
                continue
            team_stages_for_fp.append(
                {
                    "name": stage["name"],
                    "assigned_members": [
                        {
                            "name": member["name"],
                            "role": member["role"],
                            "billing_rate": float(member["billing_rate"]),
                            "cost_rate": float(member["cost_rate"]),
                            "hours": float(stage["hours"]),
                        }
                    ],
                }
            )
        target_margin_pct = int(scenario.get("target_margin_pct", TARGET_MARGIN_DEFAULT_PCT))
        target_margin_decimal = target_margin_pct / 100.0

        fp_calc = fixed_price_module.FixedPriceCalculator()
        fp_result = fp_calc.calculate(
            team_stages=team_stages_for_fp,
            expense_manager=manager,
            jurisdiction_params=tax_params,
            target_margin=target_margin_decimal,
        )
        stage_flags = fp_calc.get_stage_flags(
            stages=team_stages_for_fp,
            fixed_price=fp_result["fixed_price"],
            target_margin=target_margin_decimal,
        )
        regime_label = jurisdiction.TaxCalculator(tax_params).calculate_tax(
            revenue=fp_result["fixed_price"],
            params={**tax_params, "expenses": fp_result["total_costs"]},
        )["regime_label"]
        results = {
            **base_results,
            "pricing_model": "fixed",
            "regime_label": regime_label,
            "fixed_price": float(fp_result["fixed_price"]),
            "total_costs": float(fp_result["total_costs"]),
            "direct_labor": float(fp_result["direct_labor"]),
            "overheads_alloc": float(fp_result["overheads_alloc"]),
            "disbursements_own": float(fp_result["disbursements_own"]),
            "tax": float(fp_result["tax_amount"]),
            "nne": float(fp_result["nne"]),
            "actual_margin": float(fp_result["actual_margin"]),
            "target_margin": target_margin_decimal,
            "stage_flags": stage_flags,
            "disbursements": manager.get_disbursements_billed(),
        }
    else:
        # --- 02:700–746: биллинговая модель ---
        gr_val = calculator.gross_revenue(team_with_hours)
        direct_labor_val = team_module.total_direct_labor(team_with_hours)
        overheads_alloc = manager.get_overheads_allocated(total_hours_val)
        disbursements_billed = manager.get_disbursements_billed()
        disbursements_own = manager.get_disbursements_own()
        tax_result = jurisdiction.TaxCalculator(tax_params).calculate_tax(
            revenue=gr_val + disbursements_billed,
            params={
                **tax_params,
                "expenses": direct_labor_val + overheads_alloc + disbursements_own,
                "disbursements_billed": disbursements_billed,
            },
        )
        tax_total = float(tax_result["total_tax"])
        nne_val = calculator.nne(
            gross=gr_val,
            direct_labor=direct_labor_val,
            overheads_alloc=overheads_alloc,
            disbursements_own=disbursements_own,
            tax=tax_total,
        )
        results = {
            **base_results,
            "pricing_model": "billing",
            "regime_label": tax_result["regime_label"],
            "gross_revenue": gr_val,
            "direct_labor": direct_labor_val,
            "overheads_alloc": overheads_alloc,
            "disbursements": disbursements_billed,
            "disbursements_own": disbursements_own,
            "income_tax": float(tax_result["income_tax"]),
            "vat": float(tax_result["vat"]),
            "tax": tax_total,
            "taxable_base": float(tax_result["taxable_base"]),
            "nne": nne_val,
            "total_client": gr_val + disbursements_billed,
        }
    return results, warnings


def _profitability(nne_value: float) -> str:
    """Индикатор рентабельности дашборда (03:326–334, 03:563–569)."""
    if nne_value > 0:
        return "рентабелен"
    if nne_value == 0:
        return "безубыточность"
    return "убыточен"


def dashboard_view(results: Mapping[str, Any]) -> dict[str, Any]:
    """Производные показатели страницы «Дашборд» v0.3 без форматирования.

    Валюта отображения не меняется (conv_factor = 1, 03:137): конвертация в
    режиме «Оба» — чисто отображение и в снимки не входит.
    """
    if results.get("pricing_model") == "fixed":
        # 03:209–334 — ветка фикс-прайса.
        hours_by_stage: dict[str, float] = {}
        for member_row in results.get("team_with_hours", []):
            stage_label = str(member_row.get("stage_name", ""))
            hours_by_stage[stage_label] = hours_by_stage.get(stage_label, 0.0) + float(
                member_row.get("hours", 0.0)
            )
        return {
            "target_margin_pct": float(results.get("target_margin", 0.0)) * 100.0,
            "actual_margin_pct": float(results.get("actual_margin", 0.0)) * 100.0,
            "price_structure": {
                "direct_labor": results["direct_labor"],
                "indirect": results["overheads_alloc"] + results["disbursements_own"],
                "tax": results["tax"],
                "profit": results["nne"],
            },
            "stage_rows": [
                {
                    "stage_name": flag["stage_name"],
                    "hours": hours_by_stage.get(str(flag["stage_name"]), 0.0),
                    "stage_costs": flag["stage_costs"],
                    "stage_revenue_share": flag["stage_revenue_share"],
                    "stage_margin_pct": float(flag["stage_margin"]) * 100,
                    "flag": flag["flag"],
                }
                for flag in results.get("stage_flags", [])
            ],
            "profitability": _profitability(float(results["nne"])),
        }

    # 03:348–569 — биллинговая ветка.
    gross = float(results["gross_revenue"])
    total_client = float(
        results.get("total_client", results["gross_revenue"] + results["disbursements"])
    )
    nne_val = float(results["nne"])
    effective_rate = float(results["tax"]) / total_client * 100 if total_client > 0 else 0.0
    stage_rows: list[dict[str, Any]] = []
    sum_hours = sum_revenue = sum_cost = 0.0
    for row in results.get("team_with_hours", []):
        hours = float(row.get("hours", 0.0))
        billing = float(row.get("billing_rate", 0.0))
        cost = float(row.get("cost_rate", 0.0))
        revenue = billing * hours
        labor_cost = cost * hours
        margin_pct = ((revenue - labor_cost) / revenue * 100) if revenue > 0 else 0.0
        stage_rows.append(
            {
                "stage_name": row.get("stage_name", "—"),
                "executor": f"{row.get('name', '—')} ({row.get('role', '—')})",
                "hours": hours,
                "billing_rate": billing,
                "revenue": revenue,
                "margin_pct": margin_pct,
            }
        )
        sum_hours += hours
        sum_revenue += revenue
        sum_cost += labor_cost
    total_margin_pct = ((sum_revenue - sum_cost) / sum_revenue * 100) if sum_revenue > 0 else 0.0
    return {
        "nne_margin_pct": (nne_val / gross * 100) if gross else None,
        "effective_tax_rate_pct": effective_rate,
        "stage_rows": stage_rows,
        "stage_totals": {
            "hours": sum_hours,
            "revenue": sum_revenue,
            "margin_pct": total_margin_pct,
        },
        "profitability": _profitability(nne_val),
    }
