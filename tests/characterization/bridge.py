"""Мост «было/стало»: как цифры v0.3 превращаются в цифры фазы 2.

Для каждого characterization-сценария (6 именованных и 26 ячеек матрицы)
ядро считается несколько раз с разными правилами перевода входов v0.3
(`core_adapter.BRIDGE_STEPS`), и каждое исправление методологии получает
свою долю расхождения:

    v0.3 → L-02 (НДС сверху) → L-12 («Прочее») → L-06 (курсы)
         → L-05 (слияние этапов) → L-01 (утилизация) → L-03 (мин. налог) = стало

Минимальный налог — последний шаг: все предыдущие считаются без него (для
фикс-цены — `price_without_min_tax`, для почасовой модели — налог с прибыли
без минимального), поэтому столбец L-03 показывает его действие на итоговых
издержках. L-04, L-05, L-15 и L-19 меняют только этапы и показатели
дашборда, итоги проекта от них не зависят.

Документ `docs/phase2_before_after.md` генерируется этим модулем:

    uv run python -m tests.characterization.bridge

`test_phase2_bridge.py` проверяет, что документ актуален и мост сходится.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from rentab_core.costing import billable_capacity
from rentab_core.estimate import calculate_estimate
from rentab_core.models import FixedPricing, Scenario
from rentab_core.money import CURRENCY_SYMBOLS, ONE, PERCENT, ZERO, decimal_sum, round_to
from rentab_core.pricing.fixed import price_without_min_tax
from rentab_core.results import Estimate
from rentab_core.tax.labels import percent
from tests.characterization.core_adapter import BRIDGE_STEPS, LEGACY_EXTRA, scenario_to_core
from tests.characterization.legacy_runner import load_scenario
from tests.characterization.snapshot import SNAPSHOT_DIR
from tests.characterization.test_legacy_scenarios import NAMED_SCENARIOS
from tests.characterization.test_legacy_tax_matrix import PRICING, TAX_CONFIGS, build_scenario

DOC_PATH = Path(__file__).parents[2] / "docs" / "phase2_before_after.md"

NEUTRAL_STEP, EXTRA_STEP, FX_STEP, MERGE_STEP, FINAL_STEP = (label for label, _ in BRIDGE_STEPS)
# Столбцы моста в документе по порядку шагов; L-05 итогов не меняет.
DELTA_COLUMNS = ("L-02", "L-12", "L-06", "L-01", "L-03")
# Последний шаг моста — смета фазы 2.
RESULT_KEY = DELTA_COLUMNS[-1]

MINUS = "−"
MONEY_QUANTUM = Decimal(1)
PCT_QUANTUM = Decimal("0.1")


# ---------------------------------------------------------------------------
# Расчёт моста
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScenarioBridge:
    """Сценарий v0.3 и его расчёт ядром на каждом шаге моста."""

    scenario_id: str
    legacy: Mapping[str, Any]  # сценарий v0.3
    v03: Mapping[str, Any]  # results из снимка v0.3
    v03_dashboard: Mapping[str, Any]
    scenarios: Mapping[str, Scenario]  # входы ядра по шагам
    steps: Mapping[str, Estimate]  # сметы ядра по шагам

    @property
    def fixed(self) -> bool:
        """Фиксированная цена (иначе почасовая модель)."""
        return bool(self.v03["pricing_model"] == "fixed")

    @property
    def neutral(self) -> Estimate:
        """Смета при нейтральном переводе входов."""
        return self.steps[NEUTRAL_STEP]

    @property
    def final(self) -> Estimate:
        """Смета фазы 2."""
        return self.steps[FINAL_STEP]

    @property
    def symbol(self) -> str:
        """Символ валюты проекта."""
        return CURRENCY_SYMBOLS[self.final.currency]


def scenarios() -> Iterator[tuple[str, dict[str, Any], str]]:
    """Все сценарии моста: (id, сценарий v0.3, имя снимка v0.3)."""
    for name in NAMED_SCENARIOS:
        yield name, load_scenario(name), f"scenarios/{name}"
    for config_id in TAX_CONFIGS:
        for pricing_id in PRICING:
            cell = f"{config_id}__{pricing_id}"
            yield cell, build_scenario(config_id, pricing_id), f"matrix/{cell}"


def build_bridge(scenario_id: str, legacy: dict[str, Any], snapshot: str) -> ScenarioBridge:
    """Считает сценарий ядром на каждом шаге моста."""
    v03_snapshot = json.loads((SNAPSHOT_DIR / f"{snapshot}.json").read_text(encoding="utf-8"))
    inputs: dict[str, Scenario] = {}
    steps: dict[str, Estimate] = {}
    for label, mapping in BRIDGE_STEPS:
        inputs[label], _ = scenario_to_core(legacy, mapping)
        steps[label] = calculate_estimate(inputs[label])
    return ScenarioBridge(
        scenario_id=scenario_id,
        legacy=legacy,
        v03=v03_snapshot["results"],
        v03_dashboard=v03_snapshot["dashboard"],
        scenarios=inputs,
        steps=steps,
    )


def all_bridges() -> list[ScenarioBridge]:
    """Мосты всех сценариев."""
    return [build_bridge(*item) for item in scenarios()]


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class Metric:
    """Показатель моста: значение v0.3, значение сметы ядра и то же без минимального налога."""

    label: str
    is_money: bool
    v03: Callable[[ScenarioBridge], Decimal]
    core: Callable[[Estimate], Decimal]
    without_min_tax: Callable[[Estimate], Decimal]


def _v03_revenue(b: ScenarioBridge) -> Decimal:
    return _dec(b.v03["fixed_price"] if b.fixed else b.v03["gross_revenue"])


def _v03_total(b: ScenarioBridge) -> Decimal:
    if b.fixed:
        return _dec(b.v03["fixed_price"]) + _dec(b.v03["disbursements"])
    return _dec(b.v03["total_client"])


def _v03_margin(b: ScenarioBridge) -> Decimal:
    if b.fixed:
        return _dec(b.v03["actual_margin"])
    revenue = _v03_revenue(b)
    return _dec(b.v03["nne"]) / revenue if revenue else ZERO


def _is_fixed(estimate: Estimate) -> bool:
    return isinstance(estimate.pricing, FixedPricing)


def price_without_min(estimate: Estimate) -> Decimal:
    """Фикс-цена «издержки + маржа» без минимального налога."""
    return price_without_min_tax(estimate.tax.terms, estimate.total_costs, estimate.target_margin)


def _revenue_without_min(e: Estimate) -> Decimal:
    return price_without_min(e) if _is_fixed(e) else e.revenue


def _nne_without_min(e: Estimate) -> Decimal:
    if _is_fixed(e):
        return e.target_margin * price_without_min(e)
    return e.revenue - e.total_costs - e.tax.regular_tax


def _total_without_min(e: Estimate) -> Decimal:
    return _revenue_without_min(e) * (ONE + e.tax.terms.vat_rate) + e.disbursements_billed


def _margin_without_min(e: Estimate) -> Decimal:
    revenue = _revenue_without_min(e)
    return _nne_without_min(e) / revenue if revenue else ZERO


METRICS: tuple[Metric, ...] = (
    Metric("Выручка без НДС R", True, _v03_revenue, lambda e: e.revenue, _revenue_without_min),
    Metric("К оплате клиентом", True, _v03_total, lambda e: e.total_client, _total_without_min),
    Metric("NNE", True, lambda b: _dec(b.v03["nne"]), lambda e: e.nne, _nne_without_min),
    Metric("Маржа NNE", False, _v03_margin, lambda e: e.margin, _margin_without_min),
)


def chain(b: ScenarioBridge, metric: Metric) -> dict[str, Decimal]:
    """Значения показателя по порядку шагов: v0.3, L-02, L-12, L-06, L-05, L-01, L-03.

    Все шаги до L-03 — без минимального налога; L-03 — смета фазы 2.
    """
    return {
        "v0.3": metric.v03(b),
        "L-02": metric.without_min_tax(b.neutral),
        "L-12": metric.without_min_tax(b.steps[EXTRA_STEP]),
        "L-06": metric.without_min_tax(b.steps[FX_STEP]),
        "L-05": metric.without_min_tax(b.steps[MERGE_STEP]),
        "L-01": metric.without_min_tax(b.final),
        "L-03": metric.core(b.final),
    }


def deltas(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Изменение показателя на каждом шаге."""
    labels = list(values)
    return {label: values[label] - values[previous] for previous, label in pairwise(labels)}


# ---------------------------------------------------------------------------
# Объяснения
# ---------------------------------------------------------------------------
def _money(value: Decimal, *, signed: bool = False) -> str:
    rounded = round_to(value, MONEY_QUANTUM)
    text = f"{abs(rounded):,.0f}".replace(",", " ")
    if rounded < 0:
        return f"{MINUS}{text}"
    return f"+{text}" if signed and rounded > 0 else text


def _pct(value: Decimal) -> str:
    rounded = round_to(value * PERCENT, PCT_QUANTUM)
    text = f"{abs(rounded):.1f}".replace(".", ",")
    return f"{MINUS}{text}%" if rounded < 0 else f"{text}%"


def _pp(value: Decimal) -> str:
    rounded = round_to(value * PERCENT, PCT_QUANTUM)
    if rounded == 0:
        return "—"
    text = f"{abs(rounded):.1f}".replace(".", ",")
    return f"{MINUS if rounded < 0 else '+'}{text} п. п."


def _delta(value: Decimal, is_money: bool) -> str:
    if not is_money:
        return _pp(value)
    return "—" if round_to(value, MONEY_QUANTUM) == 0 else _money(value, signed=True)


def _value(value: Decimal, is_money: bool) -> str:
    return _money(value) if is_money else _pct(value)


def _number(value: Decimal, digits: int = 2) -> str:
    return f"{value:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


def _extra_amount(b: ScenarioBridge) -> Decimal:
    return decimal_sum(
        _dec(item["amount"])
        for item in b.legacy.get("custom_expenses", [])
        if item.get("category") == LEGACY_EXTRA
    )


FLAG_NAMES: Mapping[str, str] = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def _stage_explanations(b: ScenarioBridge) -> list[str]:
    """L-04 и L-19: как меняются этапы."""
    lines: list[str] = []
    if b.fixed:
        before = "".join(FLAG_NAMES[row["flag"]] for row in b.v03["stage_flags"])
        after = "".join(FLAG_NAMES[stage.flag.value] for stage in b.neutral.stages)
        key = "теневой стоимости (ставка × часы)"
        if b.neutral.shadow_value == 0:
            key = "трудозатратам (ставок для клиента в сценарии нет)"
        lines.append(
            f"L-04: выручка этапов делится по {key}, а не по часам; маржа этапа — после "
            f"налога, с собственными расходами: светофор {before} → {after}"
        )
    else:
        lines.append(
            "L-19, L-04: маржа этапа на дашборде — после накладных, собственных расходов "
            "и налога, а не (ставка − себестоимость) / ставка"
        )
    return lines


def explain(b: ScenarioBridge) -> list[str]:
    """Объяснение каждого ненулевого шага моста конкретными числами."""
    sym = b.symbol
    n = b.neutral
    terms = n.tax.terms
    lines: list[str] = []

    v03_vat = _dec(b.v03.get("vat", 0)) if not b.fixed else ZERO
    if terms.vat_rate > 0:
        if b.fixed:
            m, t, v = n.target_margin, terms.rate, terms.vat_rate
            lines.append(
                f"L-02: НДС {percent(v)}% не закладывается в цену как налог фирмы "
                f"(делитель {_number(ONE - m - t - v)} → {_number(ONE - m - t)}): "
                f"цена {_money(_v03_revenue(b))} → {_money(price_without_min(n))} {sym}, "
                f"НДС {_money(price_without_min(n) * v)} {sym} начисляется сверху"
            )
        else:
            lines.append(
                f"L-02: НДС {percent(terms.vat_rate)}% ({_money(v03_vat)} {sym}) не вычитается "
                f"из NNE, а начисляется клиенту сверху"
            )
    extra = _extra_amount(b)
    if extra > 0:
        lines.append(
            f"L-12: «Прочее» ({_money(extra)} {sym}) — собственный расход проекта: "
            f"издержки {_money(n.total_costs)} → {_money(b.steps[EXTRA_STEP].total_costs)} {sym}"
        )
    before_fx, after_fx = b.steps[EXTRA_STEP], b.steps[FX_STEP]
    if (before_fx.overhead_rate, before_fx.disbursements_billed, before_fx.lines) != (
        after_fx.overhead_rate,
        after_fx.disbursements_billed,
        after_fx.lines,
    ):
        rate = b.scenarios[FX_STEP].firm.exchange_rates.rub_per_kzt
        lines.append(
            f"L-06: суммы в другой валюте пересчитаны по курсу {_number(rate or ZERO)} ₽ за 1 ₸: "
            f"ставка накладных {_money(before_fx.overhead_rate)} → "
            f"{_money(after_fx.overhead_rate)} {sym}/ч, возмещаемые расходы "
            f"{_money(before_fx.disbursements_billed)} → "
            f"{_money(after_fx.disbursements_billed)} {sym}"
        )
    merged, final = b.steps[MERGE_STEP], b.final
    if len(merged.stages) != len(b.steps[FX_STEP].stages):
        lines.append(
            "L-05: одноимённые этапы v0.3 — один этап с несколькими исполнителями; "
            "итоги проекта не меняются"
        )
    capacity_before = billable_capacity(b.scenarios[MERGE_STEP].firm.team)
    capacity_after = billable_capacity(b.scenarios[FINAL_STEP].firm.team)
    lines.append(
        f"L-01: себестоимость часа v0.3 — это себестоимость контрактного часа, биллируемый "
        f"час дороже в 1/0,65 раза: трудозатраты "
        f"{_money(merged.direct_labor)} → {_money(final.direct_labor)} {sym}; ставка накладных "
        f"{_money(merged.overhead_rate)} → {_money(final.overhead_rate)} {sym}/ч (ёмкость "
        f"{_number(capacity_before, 1)} → {_number(capacity_after, 1)} ч/мес)"
    )
    if final.tax.min_tax_applied:
        lines.append(
            f"L-03: прибыль ниже {_pct(final.tax.terms.min_rate / final.tax.terms.rate)} "
            f"выручки — работает минимальный налог {percent(terms.min_rate)}%: "
            f"{_money(final.tax.min_tax)} {sym} вместо {_money(final.tax.regular_tax)} {sym} "
            f"налога с прибыли"
        )
    lines.extend(_stage_explanations(b))
    if not b.fixed:
        v03_rate = _dec(b.v03_dashboard["effective_tax_rate_pct"]) / PERCENT
        revenue = _v03_revenue(b)
        v03_by_new_base = _dec(b.v03["income_tax"]) / revenue if revenue else ZERO
        new_rate = final.tax.tax / final.revenue if final.revenue else ZERO
        if round_to(v03_rate, Decimal("0.001")) != round_to(v03_by_new_base, Decimal("0.001")):
            lines.append(
                f"L-15: эффективная ставка налога — налог / R, без НДС в числителе и пошлин "
                f"в знаменателе: в v0.3 {_pct(v03_rate)}, по новой формуле на цифрах v0.3 "
                f"{_pct(v03_by_new_base)}, стало {_pct(new_rate)}"
            )
    return lines


# ---------------------------------------------------------------------------
# Документ
# ---------------------------------------------------------------------------
def _title(b: ScenarioBridge) -> str:
    return f"`{b.scenario_id}`"


def _main_metric(b: ScenarioBridge) -> Metric:
    return METRICS[0] if b.fixed else METRICS[2]


def summary_table(bridges: Sequence[ScenarioBridge]) -> list[str]:
    """Сводка: главный показатель каждого сценария по шагам моста."""
    header = (
        "| Сценарий | Режим | Показатель | v0.3 | "
        + " | ".join(DELTA_COLUMNS)
        + " | Стало | К оплате: v0.3 → стало |"
    )
    rows = [header, "|" + "---|" * (len(DELTA_COLUMNS) + 6)]
    for b in bridges:
        metric = _main_metric(b)
        values = chain(b, metric)
        changes = deltas(values)
        totals = chain(b, METRICS[1])
        label = "Цена R" if b.fixed else "NNE"
        cells = [
            _title(b),
            b.final.regime_label,
            f"{label}, {b.symbol}",
            _money(values["v0.3"]),
            *(_delta(changes[column], True) for column in DELTA_COLUMNS),
            f"**{_money(values[RESULT_KEY])}**",
            f"{_money(totals['v0.3'])} → {_money(totals[RESULT_KEY])}",
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def metrics_table(b: ScenarioBridge) -> list[str]:
    """Все итоговые показатели сценария по шагам моста."""
    rows = [
        "| Показатель | v0.3 | " + " | ".join(DELTA_COLUMNS) + " | Стало |",
        "|" + "---|" * (len(DELTA_COLUMNS) + 3),
    ]
    for metric in METRICS:
        values = chain(b, metric)
        changes = deltas(values)
        suffix = f", {b.symbol}" if metric.is_money else ""
        cells = [
            f"{metric.label}{suffix}",
            _value(values["v0.3"], metric.is_money),
            *(_delta(changes[column], metric.is_money) for column in DELTA_COLUMNS),
            f"**{_value(values[RESULT_KEY], metric.is_money)}**",
        ]
        rows.append("| " + " | ".join(cells) + " |")
    v03_rate = _dec(b.v03_dashboard.get("effective_tax_rate_pct", 0)) / PERCENT
    if not b.fixed:
        final_rate = b.final.tax.tax / b.final.revenue if b.final.revenue else ZERO
        rows.append(
            f"| Эффективная ставка налога (L-15) | {_pct(v03_rate)} | "
            + " | ".join("" for _ in DELTA_COLUMNS)
            + f" | **{_pct(final_rate)}** |"
        )
    return rows


def _base_name(name: str) -> str:
    return name.split(" #", 1)[0]


def stages_table(b: ScenarioBridge) -> list[str]:
    """Этапы: v0.3 → по теневой стоимости после налога (L-04) → фаза 2."""
    sym = b.symbol
    neutral = {stage.stage_name: stage for stage in b.neutral.stages}
    final = {stage.stage_name: stage for stage in b.final.stages}
    flags = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    if b.fixed:
        rows = [
            f"| Этап | v0.3: выручка по часам, {sym} | v0.3: маржа до налога | "
            f"L-04: выручка по теневой стоимости, {sym} | L-04: маржа после налога | "
            f"Стало: выручка, {sym} | Стало: маржа |",
            "|---|---|---|---|---|---|---|",
        ]
        v03_rows = [
            (
                row["stage_name"],
                _dec(row["stage_revenue_share"]),
                _dec(row["stage_margin"]),
                row["flag"],
            )
            for row in b.v03["stage_flags"]
        ]
    else:
        rows = [
            f"| Этап | Исполнитель | v0.3: выручка строки, {sym} | v0.3: маржа строки (L-19) | "
            f"L-04: выручка этапа, {sym} | L-04: маржа после налога | "
            f"Стало: выручка, {sym} | Стало: маржа |",
            "|---|---|---|---|---|---|---|---|",
        ]
        v03_rows = [
            (
                row["stage_name"],
                _dec(row["revenue"]),
                _dec(row["margin_pct"]) / PERCENT,
                row["executor"],
            )
            for row in b.v03_dashboard["stage_rows"]
        ]
    seen_neutral: dict[str, int] = {}
    shown_final: set[str] = set()
    for name, revenue, margin, extra in v03_rows:
        count = seen_neutral.get(name, 0) + 1
        seen_neutral[name] = count
        neutral_name = name if count == 1 else f"{name} #{count}"
        n_stage = neutral.get(neutral_name)
        f_stage = final.get(_base_name(name))
        n_cells = (
            [_money(n_stage.revenue), f"{_pct(n_stage.margin)} {flags[n_stage.flag.value]}"]
            if n_stage is not None and (b.fixed or neutral_name not in shown_final)
            else ["↑", "↑"]
        )
        if f_stage is None:
            f_cells = ["—", "—"]
        elif f_stage.stage_name in shown_final:
            f_cells = ["↑ один этап", "↑"]
        else:
            f_cells = [
                _money(f_stage.revenue),
                f"{_pct(f_stage.margin)} {flags[f_stage.flag.value]}",
            ]
            shown_final.add(f_stage.stage_name)
        if b.fixed:
            v03_cells = [_money(revenue), f"{_pct(margin)} {flags[extra]}"]
            rows.append("| " + " | ".join([name, *v03_cells, *n_cells, *f_cells]) + " |")
        else:
            v03_cells = [_money(revenue), _pct(margin)]
            rows.append(
                "| " + " | ".join([name, str(extra), *v03_cells, *n_cells, *f_cells]) + " |"
            )
    return rows


NAMED_DESCRIPTIONS: Mapping[str, str] = {
    "legacy_billing_basic": "контрольный сценарий 1 v0.3, почасовая модель, УСН 6%",
    "legacy_fixed_basic": "контрольный сценарий 2 v0.3, фикс 30%, УСН 6%, ставок для клиента нет",
    "stage_flags_traffic_light": "фикс 30% с четырьмя этапами всех цветов светофора",
    "both_mode_mixed_currency": "режим «Оба», рубли и тенге, налог по РФ",
    "catalog_duties_are_zero": "пошлины из каталога (суммы нулевые, L-09 — фаза 3)",
    "duplicate_stage_names": "пирамида, заданная в v0.3 одноимёнными этапами (L-05)",
}

HEADER = """# Было/стало: characterization-сценарии v0.3 в методологии фазы 2

> Документ сгенерирован: `uv run python -m tests.characterization.bridge`.
> Не редактировать вручную: `tests/characterization/test_phase2_bridge.py`
> проверяет, что он совпадает с расчётом ядра. Формулы — [`methodology.md`](methodology.md),
> пункты аудита — [`legacy_audit.md`](legacy_audit.md).

## Как читать

- **v0.3** — выход живого v0.3 (снимки `tests/characterization/snapshots/`).
- **Стало** — выход ядра фазы 2 (снимки `tests/characterization/snapshots/core/`).
- Столбцы **L-NN** — насколько изменился показатель, когда включили это
  исправление. Исправления включаются по очереди, слева направо, и в сумме
  дают разницу «стало − v0.3». Числа округлены до рубля (тенге).
- Порядок шагов:
  1. **L-02** — НДС начисляется сверху: не вычитается из NNE и не
     закладывается в фикс-цену; клиент платит `R + НДС + пошлины`. Здесь же
     закрытые формулы фикса (L-03): сами по себе они цифр не меняют —
     итерации v0.3 совпадали с ними до копейки.
  2. **L-12** — расход «Прочее» стал собственным расходом проекта.
  3. **L-06** — суммы в тенге пересчитываются по курсу (0,18 ₽ за 1 ₸ из
     сценария), а не складываются с рублями.
  4. **L-05** — одноимённые этапы v0.3 слиты в один этап с несколькими
     исполнителями. Итоги проекта не меняются, поэтому столбца нет.
  5. **L-01** — себестоимость часа из v0.3 считается себестоимостью
     контрактного часа (так её считал калькулятор ФОТ v0.3): `W = cost_rate × 168`,
     утилизация 0,65, себестоимость биллируемого часа `= cost_rate / 0,65`;
     ставка накладных — на ёмкость команды `Σ 168 × 0,65`, а не на
     биллируемые часы фирмы из v0.3.
  6. **L-03** — минимальный налог УСН «доходы − расходы» (1% выручки). Он
     последний: до этого шага все расчёты идут без него, поэтому столбец
     показывает его действие на итоговых издержках. Минимальный налог
     работает, когда прибыль до налога ниже 1/15 ≈ 6,7% выручки.
- **L-04, L-19** меняют только этапы (выручка по теневой стоимости, маржа после
  налога с собственными расходами), **L-15** — эффективную ставку налога на
  дашборде. Они показаны в таблицах этапов ниже.
- Шаг L-02 считается при «нейтральном» переводе входов: ядро видит те же
  себестоимость часа, ёмкость, расходы и курсы 1 : 1, что и v0.3. На этом
  шаге издержки ядра совпадают с издержками v0.3 до копейки — это проверяет
  тест, — поэтому вся разница в нём объясняется только НДС.
"""


def render(bridges: Sequence[ScenarioBridge]) -> str:
    """Текст документа `docs/phase2_before_after.md`."""
    out = [HEADER, "## Сводка", ""]
    out.append("Главный показатель: для почасовой модели — NNE, для фикс-цены — цена без НДС.")
    out.append("")
    out.extend(summary_table(bridges))
    out.append("")
    out.append("## Именованные сценарии")
    by_id = {b.scenario_id: b for b in bridges}
    for name in NAMED_SCENARIOS:
        b = by_id[name]
        out += ["", f"### `{name}` — {NAMED_DESCRIPTIONS[name]}", ""]
        out.extend(metrics_table(b))
        out += ["", "Почему:", ""]
        out.extend(f"- {line}" for line in explain(b))
        out += ["", "Этапы:", ""]
        out.extend(stages_table(b))
    out += ["", "## Матрица «режим × модель»: объяснения", ""]
    out.append("Проект регистрации ТЗ (4 этапа, пошлины, курьер, «Прочее» — нотариальный перевод).")
    for b in bridges:
        if b.scenario_id in NAMED_DESCRIPTIONS:
            continue
        out += ["", f"**`{b.scenario_id}`** ({b.final.regime_label}):", ""]
        out.extend(f"- {line}" for line in explain(b))
    return "\n".join(out) + "\n"


def main() -> None:
    """Перегенерирует `docs/phase2_before_after.md`."""
    DOC_PATH.write_text(render(all_bridges()), encoding="utf-8")


if __name__ == "__main__":
    main()
