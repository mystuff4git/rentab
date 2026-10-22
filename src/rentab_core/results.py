"""Выходные модели ядра: налог, строки сметы, этапы, итог расчёта.

Значения не округлены: округление — дело вывода (`money.round_to`). Все
суммы — в валюте проекта. Обозначения — docs/methodology.md.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from rentab_core.models import (
    CappedPricing,
    CoreModel,
    Country,
    DisbursementKind,
    JurisdictionMode,
    PricingModel,
    Role,
    VatMode,
)
from rentab_core.money import ONE, ZERO, Currency


class TaxBase(StrEnum):
    """База налога на доход или прибыль."""

    INCOME = "income"  # «доходы»: Tax = max(t × R − S, (1 − κ) × t × R)
    PROFIT = "profit"  # «доходы − расходы»: Tax = max(t × max(R − C + N, 0), t_min × R)


class TaxTerms(CoreModel):
    """Параметры налогового режима для расчёта проекта.

    Attributes:
        regime_label: Подпись режима для интерфейса и сметы.
        base: База налога.
        rate: Ставка налога t.
        min_rate: Ставка минимального налога t_min (доля выручки); 0 — нет.
        vat_rate: Ставка НДС v; 0 — режим без НДС.
        contributions_deduction_cap: Предел уменьшения налога на взносы κ
            (доля налога, база «доходы»); 0 — уменьшения нет.
    """

    regime_label: str
    base: TaxBase
    rate: Decimal
    min_rate: Decimal = ZERO
    vat_rate: Decimal = ZERO
    contributions_deduction_cap: Decimal = ZERO


class TaxResult(CoreModel):
    """Налог на доход или прибыль по проекту и НДС.

    Attributes:
        terms: Параметры режима.
        revenue: Выручка за услуги без НДС R.
        costs: Издержки проекта C (для базы «доходы − расходы»).
        taxable_base: R для базы «доходы», max(R − C + N, 0) — для «доходы − расходы».
        regular_tax: t × taxable_base за вычетом уменьшения на взносы.
        min_tax: t_min × R.
        tax: Налог проекта: max(regular_tax, min_tax). Расход фирмы.
        vat: НДС, предъявляемый клиенту сверху: v × R. Не доход и не расход фирмы.
        contributions: Взносы на часах проекта S, на которые можно уменьшить налог.
        deduction: На сколько налог уменьшен на взносы: min(S, κ × t × R).
        nondeductible: Вознаграждение из прибыли N, которое не уменьшает базу.
    """

    terms: TaxTerms
    revenue: Decimal
    costs: Decimal
    taxable_base: Decimal
    regular_tax: Decimal
    min_tax: Decimal
    tax: Decimal
    vat: Decimal
    contributions: Decimal = ZERO
    deduction: Decimal = ZERO
    nondeductible: Decimal = ZERO

    @property
    def regime_label(self) -> str:
        """Подпись режима."""
        return self.terms.regime_label

    @property
    def min_tax_applied(self) -> bool:
        """Налог проекта определяет минимальный налог."""
        return self.min_tax > self.regular_tax

    @property
    def profit_tax_applied(self) -> bool:
        """Налог проекта — налог на прибыль t × (R − C + N) > 0 (база «доходы − расходы»)."""
        return self.terms.base == TaxBase.PROFIT and not self.min_tax_applied and self.tax > 0

    @property
    def deduction_capped(self) -> bool:
        """Уменьшение на взносы упёрлось в предел κ: часть взносов не учтена."""
        return self.contributions > self.deduction


class EstimateLine(CoreModel):
    """Строка сметы: назначение сотрудника на этап, ставки в валюте проекта.

    Attributes:
        cost_rate: Себестоимость биллируемого часа c = W / (H × u).
        contract_cost_rate: Себестоимость контрактного часа ĉ = W / H.
        contributions_rate: Взносы работодателя на биллируемый час s = взносы / K,
            если сотрудник трудоустроен в стране расчёта налога; иначе 0.
        tax_deductible: Трудозатраты — расход для налога «доходы − расходы»
            (False — вознаграждение выплачивается из прибыли).
    """

    stage_name: str
    employee_name: str
    role: Role
    billing_rate: Decimal
    cost_rate: Decimal
    contract_cost_rate: Decimal
    hours: Decimal
    contributions_rate: Decimal = ZERO
    tax_deductible: bool = True

    @property
    def shadow_value(self) -> Decimal:
        """Теневая стоимость строки: ставка для клиента × часы."""
        return self.billing_rate * self.hours

    @property
    def labor_cost(self) -> Decimal:
        """Трудозатраты строки: себестоимость биллируемого часа × часы."""
        return self.cost_rate * self.hours

    @property
    def underutilization_cost(self) -> Decimal:
        """Стоимость недозагрузки строки: (c − ĉ) × часы."""
        return (self.cost_rate - self.contract_cost_rate) * self.hours

    @property
    def contributions(self) -> Decimal:
        """Взносы на часах строки: s × часы."""
        return self.contributions_rate * self.hours

    @property
    def nondeductible_cost(self) -> Decimal:
        """Трудозатраты, которые не уменьшают базу налога (вознаграждение из прибыли)."""
        return ZERO if self.tax_deductible else self.labor_cost


class DisbursementLine(CoreModel):
    """Прямой расход проекта, пересчитанный в валюту проекта.

    Attributes:
        original_amount: Сумма во входной валюте.
        original_currency: Входная валюта.
        amount: Сумма в валюте проекта.
        stage: Этап собственного расхода; None — распределяется по этапам.
    """

    name: str
    kind: DisbursementKind
    original_amount: Decimal
    original_currency: Currency
    amount: Decimal
    stage: str | None = None

    @property
    def billable(self) -> bool:
        """Перевыставляется ли расход клиенту."""
        return self.kind == DisbursementKind.BILLABLE


class UnresolvedAssignment(CoreModel):
    """Назначение, чей исполнитель отсутствует в команде фирмы.

    Такие назначения пропускаются, как в v0.3, но ядро сообщает о них, чтобы
    интерфейс мог предупредить пользователя.
    """

    stage_name: str
    employee: str


class StageFlag(StrEnum):
    """Цвет светофора этапа."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class StageResult(CoreModel):
    """Экономика этапа: доля выручки, издержки, налог, маржа и цвет.

    Attributes:
        shadow_value: V_s = Σ ставка × часы.
        revenue: R_s — доля выручки проекта по ключу распределения.
        labor: Трудозатраты этапа.
        overheads: Накладные этапа ρ × H_s.
        own: Собственные расходы этапа (привязанные и распределённые).
        costs: C_s = labor + overheads + own.
        tax: Tax_s — доля налога проекта.
        nne: R_s − C_s − Tax_s.
        margin: nne / R_s (0, если R_s = 0).
    """

    stage_name: str
    hours: Decimal
    shadow_value: Decimal
    revenue: Decimal
    labor: Decimal
    overheads: Decimal
    own: Decimal
    costs: Decimal
    tax: Decimal
    nne: Decimal
    margin: Decimal
    flag: StageFlag


class Estimate(CoreModel):
    """Смета проекта по методологии фазы 2.

    Attributes:
        currency: Валюта проекта (страны расчёта налога).
        pricing: Модель ценообразования проекта.
        vat_mode: Как указаны цены проекта.
        target_margin: Целевая маржа NNE m*.
        lines: Строки «этап × сотрудник».
        disbursements: Прямые расходы в валюте проекта.
        blended_rate: Σ ставка × часы / Σ часы.
        leverage: Часы младших / часы старших.
        overhead_rate: Ставка накладных ρ на биллируемый час.
        shadow_value: Σ ставка × часы по проекту.
        revenue: Выручка за услуги без НДС R.
        disbursements_billed: Возмещаемые расходы D.
        disbursements_own: Собственные расходы Own.
        direct_labor: Трудозатраты Σ c × h.
        overheads_alloc: Накладные проекта ρ × Σ h.
        total_costs: C = Labor + OH + Own.
        underutilization_cost: Σ (c − ĉ) × h — сколько добавила утилизация.
        tax: Налог и НДС.
        nne: R − C − Tax.
        margin: NNE / R (0, если R = 0).
        stages: Экономика этапов.
    """

    project_name: str
    client: str
    description: str
    calc_country: Country
    jurisdiction_mode: JurisdictionMode
    currency: Currency
    pricing: PricingModel
    vat_mode: VatMode
    target_margin: Decimal
    lines: tuple[EstimateLine, ...]
    disbursements: tuple[DisbursementLine, ...]
    unresolved_assignments: tuple[UnresolvedAssignment, ...]
    total_hours: Decimal
    blended_rate: Decimal
    leverage: Decimal
    overhead_rate: Decimal
    shadow_value: Decimal
    revenue: Decimal
    disbursements_billed: Decimal
    disbursements_own: Decimal
    direct_labor: Decimal
    overheads_alloc: Decimal
    total_costs: Decimal
    underutilization_cost: Decimal
    tax: TaxResult
    nne: Decimal
    margin: Decimal
    stages: tuple[StageResult, ...]

    @property
    def regime_label(self) -> str:
        """Подпись налогового режима."""
        return self.tax.regime_label

    @property
    def vat(self) -> Decimal:
        """НДС, предъявляемый клиенту."""
        return self.tax.vat

    @property
    def price_with_vat(self) -> Decimal:
        """Цена услуг для клиента с НДС: R + VAT."""
        return self.revenue + self.tax.vat

    @property
    def total_client(self) -> Decimal:
        """К оплате клиентом: R + VAT + D."""
        return self.revenue + self.tax.vat + self.disbursements_billed

    @property
    def write_off(self) -> Decimal:
        """Списание по потолку (capped fee): теневая стоимость сверх потолка, без НДС."""
        if isinstance(self.pricing, CappedPricing):
            return self.shadow_value_net - self.revenue
        return ZERO

    @property
    def shadow_value_net(self) -> Decimal:
        """Теневая стоимость без НДС (в режиме «включая НДС» — за вычетом НДС)."""
        if self.vat_mode == VatMode.INCLUDED:
            return self.shadow_value / (ONE + self.tax.terms.vat_rate)
        return self.shadow_value
