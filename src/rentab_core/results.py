"""Выходные модели ядра: налог, строки сметы, этапы, итог расчёта.

Значения не округлены: округление — дело вывода (`money.round_to`).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from rentab_core.models import (
    CoreModel,
    Country,
    Disbursement,
    JurisdictionMode,
    Role,
)
from rentab_core.money import Currency


class TaxResult(CoreModel):
    """Налоги проекта по выбранному режиму.

    Attributes:
        regime_label: Подпись режима для интерфейса и сметы.
        revenue: Сумма, полученная от клиента (база до вычета пошлин).
        taxable_base: revenue − перевыставленные пошлины, не меньше нуля.
        income_tax_base: База налога на доход или прибыль.
        income_tax_rate: Ставка налога на доход или прибыль.
        income_tax: Налог на доход или прибыль.
        vat_rate: Ставка НДС.
        vat: НДС с taxable_base.
        total_tax: income_tax + vat. НДС здесь — расход фирмы
            (KNOWN-BUG L-02 → фаза 2).
    """

    regime_label: str
    revenue: Decimal
    taxable_base: Decimal
    income_tax_base: Decimal
    income_tax_rate: Decimal
    income_tax: Decimal
    vat_rate: Decimal
    vat: Decimal
    total_tax: Decimal


class EstimateLine(CoreModel):
    """Строка сметы: сотрудник на этапе с часами (`team_with_hours` v0.3)."""

    stage_name: str
    employee_name: str
    role: Role
    billing_rate: Decimal
    cost_rate: Decimal
    hours: Decimal

    @property
    def revenue(self) -> Decimal:
        """Теневая выручка строки: ставка для клиента × часы."""
        return self.billing_rate * self.hours

    @property
    def labor_cost(self) -> Decimal:
        """Трудозатраты строки: себестоимость часа × часы."""
        return self.cost_rate * self.hours


class UnresolvedAssignment(CoreModel):
    """Назначение, чей исполнитель отсутствует в команде фирмы.

    v0.3 молча пропускает такие этапы в расчёте; ядро пропускает их так же,
    но сообщает о них, чтобы интерфейс мог предупредить пользователя.
    """

    stage_name: str
    employee: str


class StageFlag(StrEnum):
    """Цвет светофора этапа."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class StageResult(CoreModel):
    """Маржинальность этапа фикс-прайса (светофор)."""

    stage_name: str
    costs: Decimal
    revenue_share: Decimal
    margin: Decimal
    flag: StageFlag


class EstimateBase(CoreModel):
    """Поля сметы, общие для обеих моделей ценообразования."""

    project_name: str
    client: str
    description: str
    calc_country: Country
    jurisdiction_mode: JurisdictionMode
    currency: Currency
    regime_label: str
    blended_rate: Decimal
    leverage: Decimal
    total_hours: Decimal
    overhead_rate: Decimal
    lines: tuple[EstimateLine, ...]
    disbursements: tuple[Disbursement, ...]
    unresolved_assignments: tuple[UnresolvedAssignment, ...]
    direct_labor: Decimal
    overheads_alloc: Decimal
    disbursements_billed: Decimal
    disbursements_own: Decimal
    tax: TaxResult
    nne: Decimal


class HourlyEstimate(EstimateBase):
    """Смета почасовой модели."""

    pricing: Literal["hourly"] = "hourly"
    gross_revenue: Decimal

    @property
    def total_client(self) -> Decimal:
        """К оплате клиентом: услуги + перевыставляемые расходы (без НДС, L-02)."""
        return self.gross_revenue + self.disbursements_billed


class FixedPriceEstimate(EstimateBase):
    """Смета фиксированной цены."""

    pricing: Literal["fixed"] = "fixed"
    fixed_price: Decimal
    total_costs: Decimal
    actual_margin: Decimal
    target_margin: Decimal
    stages: tuple[StageResult, ...]

    @property
    def total_client(self) -> Decimal:
        """К оплате клиентом: фикс-цена + перевыставляемые расходы (без НДС, L-02)."""
        return self.fixed_price + self.disbursements_billed


Estimate = Annotated[HourlyEstimate | FixedPriceEstimate, Field(discriminator="pricing")]
