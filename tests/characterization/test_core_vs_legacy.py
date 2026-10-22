"""Дифференциальный тест: ядро и живой v0.3 на случайных сценариях (фаза 2).

hypothesis генерирует сценарии в формате `scenarios/README.md` — команды,
этапы (в том числе с удалённым исполнителем и одноимённые), накладные в
разных валютах и периодах, пошлины и прочие расходы, все налоговые
конфигурации матрицы и режим «Оба», обе модели ценообразования. Каждый
сценарий считает `legacy_runner` (повтор страниц v0.3) и ядро при
нейтральном переводе входов (`core_adapter.NEUTRAL`).

При нейтральном переводе ядро видит те же себестоимость часа, ёмкость,
расходы и курсы 1 : 1, что и v0.3, поэтому вправе отличаться только
методологией налога (docs/methodology.md):

- издержки, ставки, часы, рычаг и пошлины совпадают с v0.3;
- почасовая модель: выручка и налог на доход совпадают, НДС не вычитается
  из NNE (L-02), а минимальный налог УСН может его уменьшить (L-03):
  NNE = NNE v0.3 + НДС v0.3 − (мин. налог − налог с прибыли);
- фикс-цена: НДС уходит из знаменателя закрытой формулы (L-02), минимальный
  налог берётся по формуле max(P_A, P_B) (L-03); цена существует ровно
  тогда, когда m* + t < 1 (и m* + t_min < 1).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import event, example, given, settings
from hypothesis import strategies as st

from rentab_core.errors import FixedPriceError
from rentab_core.estimate import calculate_estimate
from rentab_core.money import ONE
from rentab_core.pricing.fixed import cost_plus_price
from rentab_core.results import Estimate
from tests.characterization.core_adapter import NEUTRAL, scenario_to_core
from tests.characterization.legacy_runner import calculate_estimate as legacy_calculate
from tests.characterization.legacy_runner import load_scenario
from tests.characterization.test_legacy_tax_matrix import build_scenario

pytestmark = pytest.mark.characterization

MAX_EXAMPLES = 300
# Деньги сравниваются с точностью до копейки (v0.3 считает во float).
KOPECK = Decimal("0.01")
RATIO_TOLERANCE = Decimal("1e-6")

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


def _d(value: object) -> Decimal:
    return Decimal(str(value))


def _close(expected: object, actual: Decimal, tolerance: Decimal = KOPECK) -> bool:
    return abs(_d(expected) - actual) <= tolerance


def check_common(legacy: dict[str, Any], estimate: Estimate) -> None:
    """Входы, которые нейтральный перевод сохраняет: строки, ставки, издержки, пошлины."""
    lines = legacy["team_with_hours"]
    assert len(lines) == len(estimate.lines)
    for expected, actual in zip(lines, estimate.lines, strict=True):
        assert expected["name"] == actual.employee_name
        assert _close(expected["hours"], actual.hours)
        assert _close(expected["billing_rate"], actual.billing_rate)
        assert _close(expected["cost_rate"], actual.cost_rate)
    assert _close(legacy["total_hours"], estimate.total_hours)
    assert _close(legacy["blended_rate"], estimate.blended_rate)
    assert _close(legacy["leverage"], estimate.leverage, RATIO_TOLERANCE)
    assert _close(legacy["overhead_rate"], estimate.overhead_rate)
    assert _close(legacy["direct_labor"], estimate.direct_labor)
    assert _close(legacy["overheads_alloc"], estimate.overheads_alloc)
    assert _close(legacy["disbursements_own"], estimate.disbursements_own)
    assert _close(legacy["disbursements"], estimate.disbursements_billed)
    # Подпись УСН с НДС в фазе 2 дополнена ставкой НДС, остальные — как в v0.3.
    assert estimate.regime_label.startswith(legacy["regime_label"])


def check_hourly(legacy: dict[str, Any], estimate: Estimate) -> None:
    """Почасовая модель: разница с v0.3 — только НДС (L-02) и минимальный налог (L-03)."""
    tax = estimate.tax
    assert _close(legacy["gross_revenue"], estimate.revenue)
    assert _close(legacy["income_tax"], tax.regular_tax)
    assert _close(legacy["vat"], tax.vat)
    min_tax_top_up = tax.tax - tax.regular_tax
    assert _close(_d(legacy["nne"]) + _d(legacy["vat"]) - min_tax_top_up, estimate.nne)
    assert _close(_d(legacy["total_client"]) + _d(legacy["vat"]), estimate.total_client)
    if min_tax_top_up > 0:
        event("L-03: работает минимальный налог")


def check_fixed(legacy: dict[str, Any], estimate: Estimate) -> None:
    """Фикс-цена: НДС не в знаменателе (L-02), минимальный налог — max(P_A, P_B) (L-03)."""
    terms, m = estimate.tax.terms, estimate.target_margin
    assert _close(legacy["total_costs"], estimate.total_costs)
    assert estimate.revenue == cost_plus_price(terms, estimate.total_costs, m)
    assert abs(estimate.margin - m) <= RATIO_TOLERANCE or estimate.revenue == 0
    if terms.vat_rate > 0:
        assert estimate.revenue <= _d(legacy["fixed_price"]) + KOPECK
        event("L-02: фикс-цена без НДС в знаменателе")


@settings(max_examples=MAX_EXAMPLES)
# Маржа + налог + НДС ≥ 1: в v0.3 цены нет, а без НДС в знаменателе она есть.
@example(scenario=matrix_example("RF_OSNO", 60))
# Маржа + налог ≥ 1: цены нет ни в v0.3, ни в ядре.
@example(scenario=matrix_example("RF_OSNO", 80))
# Точная граница v0.3: 0,70 + 0,16 + 0,20 × 0,70 = 1.
@example(scenario=matrix_example("KZ_OUR_too_vat16", 70))
# Низкая маржа на УСН 15%: цену определяет минимальный налог.
@example(scenario=matrix_example("RF_USN_income_expenses", 1))
@given(scenario=scenarios())
def test_core_differs_from_legacy_only_by_tax_methodology(scenario: dict[str, Any]) -> None:
    core_scenario, core_warnings = scenario_to_core(scenario, NEUTRAL)
    try:
        estimate = calculate_estimate(core_scenario)
    except FixedPriceError:
        terms_reachable = False
    else:
        terms_reachable = True
    try:
        legacy, legacy_warnings = legacy_calculate(scenario)
    except ValueError:
        # v0.3 не нашёл фикс-цену (маржа + налог + НДС ≥ 1).
        event("v0.3: фикс-цена недостижима")
        if terms_reachable:
            event("L-02: цена существует, когда НДС не в знаменателе")
            # Деление в Decimal оставляет погрешность порядка 1e-28 — сравнение с допуском.
            margin_gap = abs(estimate.margin - estimate.target_margin)
            assert margin_gap <= RATIO_TOLERANCE or estimate.revenue == 0
        return
    assert core_warnings == legacy_warnings
    if not terms_reachable:
        # Ядро отказывает только если m* + t ≥ 1 — тогда и v0.3 обязан был отказать.
        pytest.fail("Ядро отказало там, где v0.3 нашёл цену")
    check_common(legacy, estimate)
    event(f"модель: {legacy['pricing_model']}")
    if legacy["pricing_model"] == "fixed":
        check_fixed(legacy, estimate)
    else:
        check_hourly(legacy, estimate)


def test_vat_regime_needs_no_vat_in_fixed_price_denominator() -> None:
    # ОСНО, маржа 60%: в v0.3 0,60 + 0,25 + 0,22 > 1 — цены нет; без НДС — есть.
    scenario = matrix_example("RF_OSNO", 60)
    with pytest.raises(ValueError, match="не может быть прибыльным"):
        legacy_calculate(scenario)
    core_scenario, _ = scenario_to_core(scenario, NEUTRAL)
    estimate = calculate_estimate(core_scenario)
    assert estimate.margin == Decimal("0.6")
    expected = (
        estimate.total_costs * (ONE - Decimal("0.25")) / (ONE - Decimal("0.25") - Decimal("0.6"))
    )
    assert estimate.revenue == expected
