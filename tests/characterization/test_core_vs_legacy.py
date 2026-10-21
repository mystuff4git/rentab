"""Дифференциальный тест: ядро и живой v0.3 на случайных сценариях.

Снимки фиксируют 35 точек. Здесь hypothesis генерирует сценарии в формате
`scenarios/README.md` — команды, этапы (в том числе с удалённым
исполнителем и одноимённые), накладные в разных валютах и периодах,
пошлины и прочие расходы, все налоговые конфигурации матрицы и режим
«Оба», обе модели ценообразования. Каждый сценарий считает
`legacy_runner` (повтор страниц v0.3) и ядро через адаптер; results,
dashboard и XLSX должны совпасть с допуском `equivalence.py`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest import mock

import pytest
from hypothesis import event, example, given, settings
from hypothesis import strategies as st

from rentab_core.analysis.dashboard import dashboard
from rentab_core.errors import FixedPriceError
from rentab_core.estimate import calculate_estimate
from rentab_core.export import estimate_xlsx
from tests.characterization.core_adapter import (
    dashboard_to_legacy,
    estimate_to_results,
    scenario_to_core,
)
from tests.characterization.equivalence import diff, diff_workbook, fail_on_diffs
from tests.characterization.legacy import estimate_export
from tests.characterization.legacy_runner import (
    calculate_estimate as legacy_calculate,
)
from tests.characterization.legacy_runner import (
    dashboard_view,
    load_scenario,
)
from tests.characterization.test_legacy_export import FrozenDate, dump_workbook
from tests.characterization.test_legacy_tax_matrix import build_scenario

pytestmark = pytest.mark.characterization

MAX_EXAMPLES = 300
UNREACHABLE_MESSAGE = "проект не может быть прибыльным"
ISSUE_DATE = dt.date(2026, 3, 16)

MATRIX = load_scenario("matrix")
JURISDICTIONS: list[dict[str, Any]] = [c["jurisdiction_params"] for c in MATRIX["tax_configs"]]
# Режим «Оба»: оба набора параметров, налог считается по одной из стран.
BOTH_JURISDICTIONS: list[dict[str, Any]] = [
    {
        "jurisdiction": "Both",
        "rf": rf["rf"],
        "kz": kz["kz"],
        "exchange_rate_rub_per_kzt": 0.18,
    }
    for rf in JURISDICTIONS
    if rf["jurisdiction"] == "RF"
    for kz in JURISDICTIONS
    if kz["jurisdiction"] == "KZ"
]
ROLES = ["Партнёр", "Старший юрист", "Младший юрист", "Стажёр"]
NAMES = ["Иванов", "Петрова", "Сидоров", "Кузнецова"]
STAGE_NAMES = ["Поиск", "Подача заявки", "Ответ на уведомление", "Сопровождение"]

kopecks = st.integers(min_value=0, max_value=5_000_000).map(lambda k: k / 100)
hours = st.integers(min_value=1, max_value=1_200).map(lambda h: h / 10)

members = st.fixed_dictionaries(
    {
        "name": st.sampled_from(NAMES),
        "role": st.sampled_from(ROLES),
        "billing_rate": kopecks,
        "cost_rate": kopecks,
    }
)
# Исполнитель выбирается из всех имён: если его нет в команде, этап пропускается.
stages = st.fixed_dictionaries(
    {
        "name": st.sampled_from(STAGE_NAMES),
        "executor": st.sampled_from(NAMES),
        "hours": hours,
    }
)
overheads = st.fixed_dictionaries(
    {
        "name": st.just("Статья"),
        "category": st.just("overhead"),
        "amount": kopecks,
        "currency": st.sampled_from(["RUB", "KZT", "USD"]),
        "period": st.sampled_from(["monthly", "annual", "one-time"]),
        "billable": st.just(False),
    }
)


@st.composite
def custom_expenses(draw: st.DrawFn) -> dict[str, Any]:
    """Произвольный расход проекта, как его создаёт форма v0.3."""
    category = draw(st.sampled_from(["disbursement_billable", "disbursement_own", "project_extra"]))
    return {
        "name": draw(st.sampled_from(["Пошлина", "Курьер", "Перевод"])),
        "category": category,
        "amount": draw(kopecks.filter(lambda a: a > 0)),
        "currency": draw(st.sampled_from(["RUB", "KZT", "USD"])),
        "period": "one-time",
        "billable": category == "disbursement_billable",
    }


@st.composite
def scenarios(draw: st.DrawFn) -> dict[str, Any]:
    """Случайный сценарий страницы «Проект» v0.3."""
    both = draw(st.booleans())
    jurisdiction = draw(st.sampled_from(BOTH_JURISDICTIONS if both else JURISDICTIONS))
    scenario: dict[str, Any] = {
        "jurisdiction_params": jurisdiction,
        "billable_hours_per_month": float(draw(st.integers(min_value=1, max_value=2_000))),
        "team": draw(st.lists(members, min_size=1, max_size=4)),
        "stages": draw(st.lists(stages, min_size=1, max_size=5)),
        "firm_expenses": draw(st.lists(overheads, max_size=4)),
        "custom_expenses": draw(st.lists(custom_expenses(), max_size=4)),
        "selected_duties": {},
        "project": {
            "name": draw(st.sampled_from(["", "Регистрация ТЗ"])),
            "client": draw(st.sampled_from(["", "ООО «Клиент»"])),
            "description": "",
        },
    }
    if both:
        scenario["calc_country"] = draw(st.sampled_from(["RF", "KZ"]))
    if draw(st.booleans()):
        scenario["pricing_model"] = "fixed"
        scenario["target_margin_pct"] = draw(st.integers(min_value=1, max_value=80))
    else:
        scenario["pricing_model"] = "billing"
    return scenario


def matrix_example(config_id: str, target_margin_pct: int) -> dict[str, Any]:
    """Ячейка матрицы с фикс-прайсом и заданной маржой — явный пример для hypothesis."""
    return {**build_scenario(config_id, "fixed"), "target_margin_pct": target_margin_pct}


def legacy_xlsx(results: dict[str, Any], firm_name: str) -> bytes:
    """Смета v0.3 с зафиксированной датой."""
    with mock.patch.object(estimate_export, "date", FrozenDate):
        content: bytes = estimate_export.generate_estimate_xlsx(results, firm_name=firm_name)
    return content


@settings(max_examples=MAX_EXAMPLES)
# Маржа + налог ≥ 1: фикс-цена недостижима (случайно такие входы почти не выпадают).
@example(scenario=matrix_example("RF_OSNO", 80), firm_name="")
# Точная граница: 0,70 + 0,16 + 0,20 × 0,70 = 1 (знаменатель первого шага равен нулю).
@example(scenario=matrix_example("KZ_OUR_too_vat16", 70), firm_name="")
# Граница: при 60% на ОСНО цена ещё не существует, при 50% — существует.
@example(scenario=matrix_example("RF_OSNO", 50), firm_name="ООО «ИП-Консалт»")
@given(scenario=scenarios(), firm_name=st.sampled_from(["", "ООО «ИП-Консалт»"]))
def test_core_matches_legacy_on_random_scenarios(scenario: dict[str, Any], firm_name: str) -> None:
    core_scenario, core_warnings = scenario_to_core(scenario)
    try:
        legacy_results, legacy_warnings = legacy_calculate(scenario)
    except ValueError:
        # v0.3 не нашёл фикс-цену (маржа + налог ≥ 1) — ядро должно отказать так же.
        # Сумму в тексте ошибки не сравниваем: на точной границе m + t = 1 v0.3
        # делает лишний шаг из-за погрешности float и сообщает другую сумму (L-20).
        event("фикс-цена недостижима")
        with pytest.raises(FixedPriceError, match=UNREACHABLE_MESSAGE):
            calculate_estimate(core_scenario)
        return

    estimate = calculate_estimate(core_scenario)
    event(f"модель: {legacy_results['pricing_model']}")
    assert core_warnings == legacy_warnings
    expected = {"results": legacy_results, "dashboard": dashboard_view(legacy_results)}
    actual = {
        "results": estimate_to_results(estimate),
        "dashboard": dashboard_to_legacy(dashboard(estimate)),
    }
    fail_on_diffs("Ядро разошлось с v0.3", list(diff(expected, actual)))

    workbook_diffs = diff_workbook(
        dump_workbook(legacy_xlsx(legacy_results, firm_name)),
        dump_workbook(estimate_xlsx(estimate, firm_name=firm_name, issue_date=ISSUE_DATE)),
        allow_rounding_ties=True,
    )
    fail_on_diffs("XLSX ядра разошёлся с v0.3", list(workbook_diffs))
