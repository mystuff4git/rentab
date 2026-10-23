"""Чувствительность сметы (docs/methodology.md, разделы 16–17).

- `break_even` — запас часов до NNE = 0 при зафиксированной цене: миксом
  проекта и каждым сотрудником команды (раздел 16);
- `tornado` — влияние ±X% по часам, ставкам, утилизации, накладным и курсу на
  NNE (раздел 17.1);
- `what_if` — смета при одновременных отклонениях входов (раздел 17.4).

Проект не меняется: функции строят изменённую копию сценария и пересчитывают
смету ядром. Цена «издержки + маржа» держится на цене плана — она уже
предложена клиенту (раздел 16.6).

Запас часов считается закрытой формулой. NNE при `x` дополнительных часах —
минимум линий `α_j − β_j·x` (ветви налога × части выручки с потолком,
раздел 16.4), поэтому

    x* = min{ α_j / β_j : β_j > 0 }.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field

from rentab_core.costing import monthly_amount
from rentab_core.errors import CurrencyConversionError
from rentab_core.estimate import calculate_estimate, resolve_stages
from rentab_core.models import (
    Assignment,
    CappedPricing,
    CoreModel,
    ExchangeRates,
    FixedPricing,
    HourlyPricing,
    PricingModel,
    Project,
    Role,
    Scenario,
    Stage,
)
from rentab_core.money import ONE, ZERO, Currency, decimal_sum, round_to
from rentab_core.pricing import net_of_vat
from rentab_core.results import Estimate, EstimateLine, TaxBase, TaxTerms

# Отклонение входа — доля: 0,10 — +10%, −0,10 — −10%; −100% и меньше не бывает.
Deviation = Annotated[Decimal, Field(gt=-1, allow_inf_nan=False)]
# Отклонение для торнадо по умолчанию (раздел 17.1).
DEFAULT_TORNADO_DEVIATION = Decimal("0.10")
# Утилизация выше 100% не бывает (models.Utilization).
MAX_UTILIZATION = ONE
# Линии, значения которых в точке плана отличаются меньше чем на эту величину,
# считаются проходящими через точку: деление в Decimal оставляет погрешность
# порядка 1e-25, а наклон NNE берётся по линии, которая идёт ниже правее точки.
LINE_TIE_TOLERANCE = Decimal("1e-12")
# Название этапа-пробы, на котором ядро находит ставки сотрудника вне плана.
PROBE_STAGE = "probe"


class PriceRule(StrEnum):
    """Как выручка зависит от часов (раздел 16.3)."""

    FIXED = "fixed"  # согласованная цена или цена плана «издержки + маржа»
    CAPPED = "capped"  # часы оплачиваются, пока выручка не упрётся в потолок
    BILLED = "billed"  # почасовая: каждый час оплачивается


class TaxBranch(StrEnum):
    """Ветвь налога, на которой лежит линия NNE (раздел 16.4)."""

    INCOME = "income"  # «доходы»: t × R
    CONTRIBUTIONS = "contributions"  # «доходы»: t × R − S
    DEDUCTION_CAP = "deduction_cap"  # «доходы»: уменьшение на взносы упёрлось в предел κ
    PROFIT = "profit"  # «доходы − расходы»: налог на прибыль
    MIN_TAX = "min_tax"  # «доходы − расходы»: минимальный налог (или 0 без него)


class BreakEvenStatus(StrEnum):
    """Итог расчёта запаса часов (раздел 16.5)."""

    HEADROOM = "headroom"  # NNE ≥ 0, точка есть
    NOT_REACHED = "not_reached"  # NNE ≥ 0 и не убывает с часами
    LOSS = "loss"  # NNE < 0, ноль достижим изменением часов
    LOSS_ANY_HOURS = "loss_any_hours"  # NNE < 0 при любых часах этого направления
    NO_HOURS = "no_hours"  # в проекте нет часов: микса нет


class NneLine(CoreModel):
    """Линия NNE(x) = α − β·x: одна ветвь налога и одна часть выручки.

    Attributes:
        alpha: NNE этой линии в точке плана.
        beta: Сколько NNE теряется на каждом дополнительном часе.
        branch: Ветвь налога.
        capped: Выручка этой линии упирается в потолок.
    """

    alpha: Decimal
    beta: Decimal
    branch: TaxBranch
    capped: bool = False

    def value(self, extra_hours: Decimal) -> Decimal:
        """NNE этой линии при x дополнительных часах."""
        return self.alpha - self.beta * extra_hours


class BreakEven(CoreModel):
    """Запас часов одного направления (раздел 16.5).

    Attributes:
        employee: Сотрудник, чьи часы растут; None — микс проекта.
        role: Роль сотрудника; у микса — None.
        plan_hours: h_d — часы направления в плане.
        cost_per_hour: k_d — себестоимость часа с накладными.
        billing_per_hour: b_d — ставка для клиента без НДС.
        nne: NNE плана.
        nne_per_hour: Прибыль дополнительного часа в точке плана (наклон NNE).
        status: Итог.
        extra_hours: HEADROOM — запас x*; LOSS — ближайший ноль (отрицательный:
            часов нужно меньше; положительный: оплачиваемых часов нужно больше);
            иначе None.
        branch: Ветвь налога в точке; None, если точки нет.
        capped: В точке выручка упирается в потолок.
        lines: Линии NNE(x): по ним считается точка.
    """

    employee: str | None
    role: Role | None
    plan_hours: Decimal
    cost_per_hour: Decimal
    billing_per_hour: Decimal
    nne: Decimal
    nne_per_hour: Decimal
    status: BreakEvenStatus
    extra_hours: Decimal | None = None
    branch: TaxBranch | None = None
    capped: bool = False
    lines: tuple[NneLine, ...] = ()

    @property
    def total_hours(self) -> Decimal | None:
        """Часы направления в точке безубыточности."""
        return None if self.extra_hours is None else self.plan_hours + self.extra_hours

    @property
    def share_of_plan(self) -> Decimal | None:
        """Запас в долях плана (1 — +100%); None, если плана нет или точки нет."""
        if self.extra_hours is None or self.plan_hours <= 0:
            return None
        return self.extra_hours / self.plan_hours

    def nne_at(self, extra_hours: Decimal) -> Decimal:
        """NNE при x дополнительных часах — минимум линий."""
        return min(line.value(extra_hours) for line in self.lines)


class BreakEvenReport(CoreModel):
    """Запас часов проекта.

    Attributes:
        rule: Как выручка зависит от часов.
        revenue: Выручка плана без НДС.
        rows: Микс проекта, затем сотрудники команды (в порядке команды).
        written_off: Почасовая модель: те же направления, если часы сверх
            плана не выставляются (выручка остаётся выручкой плана); у
            остальных моделей — пусто.
    """

    rule: PriceRule
    revenue: Decimal
    rows: tuple[BreakEven, ...]
    written_off: tuple[BreakEven, ...] = ()


def price_rule(pricing: PricingModel) -> PriceRule:
    """Как выручка модели цены зависит от часов."""
    if isinstance(pricing, HourlyPricing):
        return PriceRule.BILLED
    if isinstance(pricing, CappedPricing):
        return PriceRule.CAPPED
    return PriceRule.FIXED


def is_cost_plus(pricing: PricingModel) -> bool:
    """Цена «издержки + маржа»: при отклонениях она держится на цене плана."""
    return isinstance(pricing, FixedPricing) and pricing.price is None


def held_revenue(scenario: Scenario, base: Estimate) -> Decimal | None:
    """Выручка, которая держится при отклонениях: цена плана «издержки + маржа»."""
    return base.revenue if is_cost_plus(scenario.project.pricing) else None


# ---------------------------------------------------------------------------
# Точка безубыточности по часам
# ---------------------------------------------------------------------------
class _Direction(CoreModel):
    """Параметры дополнительного часа направления (раздел 16.2)."""

    employee: str | None
    role: Role | None
    plan_hours: Decimal
    cost: Decimal  # k_d
    billing: Decimal  # b_d без НДС
    contributions: Decimal  # s_d
    nondeductible: Decimal  # n_d


class _Segment(CoreModel):
    """Часть выручки R(x) = start + slope·x."""

    start: Decimal
    slope: Decimal
    capped: bool = False


def _net(value: Decimal, base: Estimate) -> Decimal:
    return net_of_vat(value, base.vat_mode, base.tax.terms.vat_rate)


def _segments(base: Estimate, rule: PriceRule, billing: Decimal) -> list[_Segment]:
    """Части выручки по правилу цены (раздел 16.3)."""
    if rule == PriceRule.FIXED:
        return [_Segment(start=base.revenue, slope=ZERO)]
    uncapped = _Segment(start=base.shadow_value_net, slope=billing)
    if rule == PriceRule.BILLED:
        return [uncapped]
    assert isinstance(base.pricing, CappedPricing)
    return [uncapped, _Segment(start=_net(base.pricing.cap, base), slope=ZERO, capped=True)]


def _lines(
    terms: TaxTerms,
    segments: Iterable[_Segment],
    *,
    costs: Decimal,
    contributions: Decimal,
    nondeductible: Decimal,
    direction: _Direction,
) -> list[NneLine]:
    """Линии NNE(x) = α − β·x (раздел 16.4)."""
    t = terms.rate
    k, s, n = direction.cost, direction.contributions, direction.nondeductible
    lines: list[NneLine] = []
    for segment in segments:
        r0, slope, capped = segment.start, segment.slope, segment.capped
        if terms.base == TaxBase.INCOME:
            if terms.contributions_deduction_cap > 0:
                branch = TaxBranch.CONTRIBUTIONS if contributions > 0 or s > 0 else TaxBranch.INCOME
                lines.append(
                    NneLine(
                        alpha=(ONE - t) * r0 - costs + contributions,
                        beta=k - s - (ONE - t) * slope,
                        branch=branch,
                        capped=capped,
                    )
                )
                kept = ONE - (ONE - terms.contributions_deduction_cap) * t
                lines.append(
                    NneLine(
                        alpha=kept * r0 - costs,
                        beta=k - kept * slope,
                        branch=TaxBranch.DEDUCTION_CAP,
                        capped=capped,
                    )
                )
            else:
                lines.append(
                    NneLine(
                        alpha=(ONE - t) * r0 - costs,
                        beta=k - (ONE - t) * slope,
                        branch=TaxBranch.INCOME,
                        capped=capped,
                    )
                )
        else:
            lines.append(
                NneLine(
                    alpha=(ONE - t) * (r0 - costs) - t * nondeductible,
                    beta=(ONE - t) * (k - slope) + t * n,
                    branch=TaxBranch.PROFIT,
                    capped=capped,
                )
            )
            kept = ONE - terms.min_rate
            lines.append(
                NneLine(
                    alpha=kept * r0 - costs,
                    beta=k - kept * slope,
                    branch=TaxBranch.MIN_TAX,
                    capped=capped,
                )
            )
    return lines


def _slope_at_plan(lines: Sequence[NneLine]) -> Decimal:
    """Прибыль дополнительного часа в точке плана: −β линии, идущей ниже правее точки."""
    lowest = min(line.alpha for line in lines)
    active = [line for line in lines if line.alpha - lowest <= LINE_TIE_TOLERANCE]
    return -max(line.beta for line in active)


def _solve(direction: _Direction, lines: list[NneLine], nne: Decimal) -> BreakEven:
    """Запас часов по линиям NNE(x) (раздел 16.5)."""
    falling = [(line.alpha / line.beta, line) for line in lines if line.beta > 0]
    rising = [(line.alpha / line.beta, line) for line in lines if line.beta < 0]
    flat_loss = any(line.beta == 0 and line.alpha < 0 for line in lines)
    result = BreakEven(
        employee=direction.employee,
        role=direction.role,
        plan_hours=direction.plan_hours,
        cost_per_hour=direction.cost,
        billing_per_hour=direction.billing,
        nne=nne,
        nne_per_hour=_slope_at_plan(lines),
        status=BreakEvenStatus.NOT_REACHED,
        lines=tuple(lines),
    )

    def point(status: BreakEvenStatus, hours: Decimal, line: NneLine) -> BreakEven:
        return result.model_copy(
            update={
                "status": status,
                "extra_hours": hours,
                "branch": line.branch,
                "capped": line.capped,
            }
        )

    # При равных корнях ветвь в точке — та, что падает круче.
    upper = min(falling, key=lambda item: (item[0], -item[1].beta), default=None)
    lower = max(rising, key=lambda item: item[0], default=None)
    if round_to(nne) >= 0:
        return result if upper is None else point(BreakEvenStatus.HEADROOM, *upper)
    impossible = flat_loss or (
        upper is not None
        and (upper[0] < -direction.plan_hours or (lower is not None and lower[0] > upper[0]))
    )
    if impossible:
        return result.model_copy(update={"status": BreakEvenStatus.LOSS_ANY_HOURS})
    if upper is not None and upper[0] < 0:
        return point(BreakEvenStatus.LOSS, *upper)
    # NNE растёт с часами: ноль — на нижней границе.
    assert lower is not None
    return point(BreakEvenStatus.LOSS, *lower)


def _mix(base: Estimate) -> _Direction:
    hours = base.total_hours
    return _Direction(
        employee=None,
        role=None,
        plan_hours=hours,
        cost=base.direct_labor / hours + base.overhead_rate,
        billing=base.shadow_value_net / hours,
        contributions=decimal_sum(line.contributions for line in base.lines) / hours,
        nondeductible=decimal_sum(line.nondeductible_cost for line in base.lines) / hours,
    )


def _employee_line(scenario: Scenario, base: Estimate, name: str) -> EstimateLine:
    """Строка сметы сотрудника с одним часом: ставки в валюте проекта."""
    probe = Project(
        stages=(Stage(name=PROBE_STAGE, assignments=(Assignment(employee=name, hours=ONE),)),)
    )
    stages, _ = resolve_stages(scenario.firm, probe, base.currency, base.calc_country)
    return stages[0].lines[0]


def _employee(scenario: Scenario, base: Estimate, name: str) -> _Direction:
    line = _employee_line(scenario, base, name)
    return _Direction(
        employee=name,
        role=line.role,
        plan_hours=decimal_sum(item.hours for item in base.lines if item.employee_name == name),
        cost=line.cost_rate + base.overhead_rate,
        billing=_net(line.billing_rate, base),
        contributions=line.contributions_rate,
        nondeductible=line.nondeductible_cost,
    )


def _team_names(scenario: Scenario) -> list[str]:
    """Имена сотрудников команды без повторов, в порядке команды."""
    return list(dict.fromkeys(employee.name for employee in scenario.firm.team))


def _rows(scenario: Scenario, base: Estimate, rule: PriceRule) -> tuple[BreakEven, ...]:
    costs = base.total_costs
    contributions = decimal_sum(line.contributions for line in base.lines)
    nondeductible = decimal_sum(line.nondeductible_cost for line in base.lines)
    directions: list[_Direction | None] = [_mix(base) if base.total_hours > 0 else None]
    for name in _team_names(scenario):
        try:
            directions.append(_employee(scenario, base, name))
        except CurrencyConversionError:
            # Сотрудник вне проекта со ставками в валюте без курса: строки нет.
            continue
    rows: list[BreakEven] = []
    for direction in directions:
        if direction is None:
            rows.append(
                BreakEven(
                    employee=None,
                    role=None,
                    plan_hours=ZERO,
                    cost_per_hour=ZERO,
                    billing_per_hour=ZERO,
                    nne=base.nne,
                    nne_per_hour=ZERO,
                    status=BreakEvenStatus.NO_HOURS,
                )
            )
            continue
        lines = _lines(
            base.tax.terms,
            _segments(base, rule, direction.billing),
            costs=costs,
            contributions=contributions,
            nondeductible=nondeductible,
            direction=direction,
        )
        rows.append(_solve(direction, lines, base.nne))
    return tuple(rows)


def break_even(scenario: Scenario) -> BreakEvenReport:
    """Запас часов до NNE = 0 при зафиксированной цене (раздел 16).

    Строки — микс проекта и сотрудники команды; сотрудник вне проекта, чьи
    ставки нельзя пересчитать в валюту проекта (нет курса), пропускается.

    Raises:
        FixedPriceError: Цена «издержки + маржа» недостижима при режиме.
        CurrencyConversionError: Для пересчёта суммы в валюту проекта нет курса.
    """
    base = calculate_estimate(scenario)
    rule = price_rule(scenario.project.pricing)
    return BreakEvenReport(
        rule=rule,
        revenue=base.revenue,
        rows=_rows(scenario, base, rule),
        written_off=_rows(scenario, base, PriceRule.FIXED) if rule == PriceRule.BILLED else (),
    )


# ---------------------------------------------------------------------------
# «Что если» и торнадо
# ---------------------------------------------------------------------------
class Factor(StrEnum):
    """Фактор чувствительности (раздел 17.1)."""

    HOURS = "hours"
    BILLING_RATES = "billing_rates"
    UTILIZATION = "utilization"
    OVERHEADS = "overheads"
    FX = "fx"
    PRICE = "price"  # только «что если»: согласованная цена, потолок, цена плана


class WhatIf(CoreModel):
    """Отклонения входов «что если», доли: 0,10 — +10%.

    Пустой `WhatIf()` — смета без изменений. Курс — цена валюты в валюте
    проекта (раздел 17.1).
    """

    hours: Deviation = ZERO
    billing_rates: Deviation = ZERO
    utilization: Deviation = ZERO
    overheads: Deviation = ZERO
    price: Deviation = ZERO
    fx: dict[Currency, Deviation] = Field(default_factory=dict)


def _scaled_rates(
    rates: ExchangeRates, foreign: Currency, project: Currency, factor: Decimal
) -> ExchangeRates:
    """Курсы, при которых цена валюты foreign в валюте project умножена на factor."""
    if foreign == project:
        return rates
    if foreign == Currency.RUB:
        # Рубль дорожает к тенге: дешевеют все остальные валюты в рублях.
        return ExchangeRates(
            rub_per_kzt=None if rates.rub_per_kzt is None else rates.rub_per_kzt / factor,
            rub_per_usd=None if rates.rub_per_usd is None else rates.rub_per_usd / factor,
        )
    field = "rub_per_kzt" if foreign == Currency.KZT else "rub_per_usd"
    value = getattr(rates, field)
    return rates if value is None else rates.model_copy(update={field: value * factor})


def _scaled_pricing(pricing: PricingModel, factor: Decimal) -> PricingModel:
    if isinstance(pricing, CappedPricing):
        return CappedPricing(cap=pricing.cap * factor)
    if isinstance(pricing, FixedPricing) and pricing.price is not None:
        return FixedPricing(price=pricing.price * factor)
    return pricing


def apply_what_if(scenario: Scenario, what_if: WhatIf, currency: Currency) -> Scenario:
    """Копия сценария с отклонениями входов; сам сценарий не меняется.

    Args:
        scenario: Фирма и проект.
        what_if: Отклонения.
        currency: Валюта проекта (для курса).
    """
    firm, project = scenario.firm, scenario.project
    team = tuple(
        employee.model_copy(
            update={
                "billing_rate": employee.billing_rate * (ONE + what_if.billing_rates),
                "target_utilization": min(
                    employee.target_utilization * (ONE + what_if.utilization), MAX_UTILIZATION
                ),
            }
        )
        for employee in firm.team
    )
    overheads = tuple(
        item.model_copy(update={"amount": item.amount * (ONE + what_if.overheads)})
        for item in firm.overheads
    )
    rates = firm.exchange_rates
    for foreign, deviation in what_if.fx.items():
        rates = _scaled_rates(rates, foreign, currency, ONE + deviation)
    stages = tuple(
        stage.model_copy(
            update={
                "assignments": tuple(
                    assignment.model_copy(
                        update={"hours": assignment.hours * (ONE + what_if.hours)}
                    )
                    for assignment in stage.assignments
                )
            }
        )
        for stage in project.stages
    )
    return Scenario(
        firm=firm.model_copy(
            update={"team": team, "overheads": overheads, "exchange_rates": rates}
        ),
        project=project.model_copy(
            update={
                "stages": stages,
                "pricing": _scaled_pricing(project.pricing, ONE + what_if.price),
            }
        ),
    )


def utilization_capped(scenario: Scenario, deviation: Decimal) -> tuple[str, ...]:
    """Сотрудники, чья утилизация при отклонении упирается в 100%."""
    return tuple(
        employee.name
        for employee in scenario.firm.team
        if employee.target_utilization * (ONE + deviation) > MAX_UTILIZATION
    )


class WhatIfResult(CoreModel):
    """Смета «что если» рядом со сметой плана.

    Attributes:
        base: Смета проекта.
        changed: Смета при отклонениях; цена «издержки + маржа» держится на
            цене плана (с отклонением цены).
        repriced: Цена «издержки + маржа», пересчитанная по изменённым
            издержкам; у других моделей — None.
        utilization_capped: Сотрудники, чья утилизация упёрлась в 100%.
    """

    base: Estimate
    changed: Estimate
    repriced: Decimal | None = None
    utilization_capped: tuple[str, ...] = ()


def _estimate(scenario: Scenario, what_if: WhatIf, base: Estimate) -> Estimate:
    """Смета при отклонениях: цена плана «издержки + маржа» держится."""
    held = held_revenue(scenario, base)
    changed = apply_what_if(scenario, what_if, base.currency)
    return calculate_estimate(
        changed, revenue=None if held is None else held * (ONE + what_if.price)
    )


def what_if(scenario: Scenario, what_if: WhatIf) -> WhatIfResult:
    """Смета при одновременных отклонениях входов (раздел 17.4).

    Raises:
        FixedPriceError: Цена «издержки + маржа» недостижима при режиме.
        CurrencyConversionError: Для пересчёта суммы в валюту проекта нет курса.
    """
    base = calculate_estimate(scenario)
    repriced = None
    if is_cost_plus(scenario.project.pricing):
        repriced = calculate_estimate(apply_what_if(scenario, what_if, base.currency)).revenue
    return WhatIfResult(
        base=base,
        changed=_estimate(scenario, what_if, base),
        repriced=repriced,
        utilization_capped=utilization_capped(scenario, what_if.utilization),
    )


class TornadoNote(StrEnum):
    """Почему у фактора нет влияния или оно ограничено."""

    FIXED_PRICE = "fixed_price"  # в фиксе ставки влияют только на распределение по этапам
    CAP_REACHED = "cap_reached"  # выручка упирается в потолок
    UTILIZATION_CAPPED = "utilization_capped"  # утилизация упёрлась в 100%
    TRANSIT_ONLY = "transit_only"  # в этой валюте только возмещаемые расходы


class TornadoBar(CoreModel):
    """Влияние фактора на NNE при ±X% (раздел 17.1).

    Attributes:
        factor: Фактор.
        currency: Валюта для фактора «курс».
        low: NNE при −X%.
        high: NNE при +X%.
        base: NNE плана.
        note: Пояснение, если влияния нет или оно ограничено.
    """

    factor: Factor
    currency: Currency | None = None
    low: Decimal
    high: Decimal
    base: Decimal
    note: TornadoNote | None = None

    @property
    def low_delta(self) -> Decimal:
        """ΔNNE при −X%."""
        return self.low - self.base

    @property
    def high_delta(self) -> Decimal:
        """ΔNNE при +X%."""
        return self.high - self.base

    @property
    def spread(self) -> Decimal:
        """Размах влияния: max(|ΔNNE(−X)|, |ΔNNE(+X)|)."""
        return max(abs(self.low_delta), abs(self.high_delta))


class Tornado(CoreModel):
    """Торнадо: факторы по убыванию влияния на NNE."""

    deviation: Decimal
    base: Estimate
    bars: tuple[TornadoBar, ...]


def foreign_currencies(scenario: Scenario, base: Estimate) -> dict[Currency, bool]:
    """Валюты сумм проекта, кроме валюты проекта.

    Returns:
        Валюта → влияет ли она на NNE (False — в ней только возмещаемые расходы).
    """
    team = scenario.firm.team_by_name()
    affecting: set[Currency] = {
        team[line.employee_name].currency for line in base.lines if line.employee_name in team
    }
    affecting |= {item.currency for item in scenario.firm.overheads if monthly_amount(item) > 0}
    affecting |= {item.original_currency for item in base.disbursements if not item.billable}
    transit = {item.original_currency for item in base.disbursements if item.billable}
    return {
        currency: currency in affecting
        for currency in Currency
        if currency != base.currency and (currency in affecting or currency in transit)
    }


def _what_if_for(factor: Factor, deviation: Decimal, currency: Currency | None) -> WhatIf:
    if factor == Factor.FX:
        assert currency is not None
        return WhatIf(fx={currency: deviation})
    return WhatIf.model_validate({factor.value: deviation})


def _note(
    factor: Factor,
    rule: PriceRule,
    scenario: Scenario,
    deviation: Decimal,
    zero: bool,
    affects_nne: bool,
) -> TornadoNote | None:
    if factor == Factor.BILLING_RATES and rule == PriceRule.FIXED:
        return TornadoNote.FIXED_PRICE
    if factor == Factor.BILLING_RATES and rule == PriceRule.CAPPED and zero:
        return TornadoNote.CAP_REACHED
    if factor == Factor.UTILIZATION and utilization_capped(scenario, deviation):
        return TornadoNote.UTILIZATION_CAPPED
    if factor == Factor.FX and not affects_nne:
        return TornadoNote.TRANSIT_ONLY
    return None


TORNADO_FACTORS: tuple[Factor, ...] = (
    Factor.HOURS,
    Factor.BILLING_RATES,
    Factor.UTILIZATION,
    Factor.OVERHEADS,
)


def tornado(scenario: Scenario, deviation: Decimal = DEFAULT_TORNADO_DEVIATION) -> Tornado:
    """Влияние ±X% по каждому фактору на NNE (раздел 17.1).

    Args:
        scenario: Фирма и проект.
        deviation: X — доля, больше 0 и меньше 1.

    Raises:
        ValueError: X вне (0; 1).
        FixedPriceError: Цена «издержки + маржа» недостижима при режиме.
        CurrencyConversionError: Для пересчёта суммы в валюту проекта нет курса.
    """
    if not ZERO < deviation < ONE:
        raise ValueError(f"Отклонение для торнадо должно быть в (0; 1), получено {deviation}")
    base = calculate_estimate(scenario)
    rule = price_rule(scenario.project.pricing)
    factors: list[tuple[Factor, Currency | None, bool]] = [
        (factor, None, True) for factor in TORNADO_FACTORS
    ]
    factors += [
        (Factor.FX, currency, affects)
        for currency, affects in foreign_currencies(scenario, base).items()
    ]
    bars = []
    for factor, currency, affects in factors:
        low = _estimate(scenario, _what_if_for(factor, -deviation, currency), base).nne
        high = _estimate(scenario, _what_if_for(factor, deviation, currency), base).nne
        zero = low == base.nne and high == base.nne
        bars.append(
            TornadoBar(
                factor=factor,
                currency=currency,
                low=low,
                high=high,
                base=base.nne,
                note=_note(factor, rule, scenario, deviation, zero, affects),
            )
        )
    bars.sort(key=lambda bar: bar.spread, reverse=True)
    return Tornado(deviation=deviation, base=base, bars=tuple(bars))
