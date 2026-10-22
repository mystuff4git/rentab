"""Каталоги патентных пошлин Роспатента и Казпатента.

Пошлина из каталога превращается в перевыставляемый расход проекта
(`Disbursement` вида BILLABLE). Каталоги лежат в `duties/data/*.yaml`.

Размер пошлины — базовая сумма плюс надбавки за единицы сверх включённых:

    amount = base + Σ_extra max(0, n_extra − included) × per_unit

Например, регистрация заявки на товарный знак — 4 000 ₽ за один класс МКТУ и
1 000 ₽ за каждый класс сверх первого. Количества (классов, пунктов формулы,
образцов) задаются на проект. У Казпатента цена зависит от категории
заявителя (юрлицо, субъект МСП, физлицо).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from enum import StrEnum
from functools import cache
from typing import Annotated

from pydantic import Field, HttpUrl, model_validator

from rentab_core.models import (
    CoreModel,
    Country,
    Disbursement,
    DisbursementKind,
    NonNegativeDecimal,
)
from rentab_core.money import ZERO, Currency, decimal_sum
from rentab_core.reference import check_country, load_reference

DUTIES_DATA_PACKAGE = "rentab_core.duties.data"
CATALOG_FILES: Mapping[Country, str] = {
    Country.RF: "rospatent_2026.yaml",
    Country.KZ: "kazpatent_2026.yaml",
}


class Quantity(StrEnum):
    """Единица, от числа которых зависит пошлина."""

    CLASSES = "classes"  # классы МКТУ
    ITEMS_OVER_LIMIT = "items_over_limit"  # позиции перечня сверх 10 в классе, всего
    CLAIMS = "claims"  # пункты формулы
    INDEPENDENT_CLAIMS = "independent_claims"  # независимые пункты формулы
    DESIGNS = "designs"  # промышленные образцы в заявке


QUANTITY_LABELS: Mapping[Quantity, str] = {
    Quantity.CLASSES: "Классов МКТУ",
    Quantity.ITEMS_OVER_LIMIT: "Позиций сверх 10 в классе (всего)",
    Quantity.CLAIMS: "Пунктов формулы",
    Quantity.INDEPENDENT_CLAIMS: "Независимых пунктов формулы",
    Quantity.DESIGNS: "Промышленных образцов",
}
# Количество по умолчанию: одна единица (ничего сверх базы), позиций сверх лимита — 0.
DEFAULT_QUANTITY = 1
QUANTITY_DEFAULTS: Mapping[Quantity, int] = {Quantity.ITEMS_OVER_LIMIT: 0}


class ApplicantCategory(StrEnum):
    """Категория заявителя: от неё зависят цены Казпатента."""

    LEGAL_ENTITY = "legal_entity"
    SME = "sme"
    INDIVIDUAL = "individual"


APPLICANT_CATEGORY_LABELS: Mapping[ApplicantCategory, str] = {
    ApplicantCategory.LEGAL_ENTITY: "Юридическое лицо",
    ApplicantCategory.SME: "Субъект МСП",
    ApplicantCategory.INDIVIDUAL: "Физическое лицо",
}


class DutyExtra(CoreModel):
    """Надбавка к пошлине за каждую единицу сверх включённых в базовую сумму."""

    quantity: Quantity
    included: Annotated[int, Field(ge=0)]
    per_unit: NonNegativeDecimal

    def amount(self, count: int) -> NonNegativeDecimal:
        """Надбавка за count единиц."""
        return max(count - self.included, 0) * self.per_unit


class Duty(CoreModel):
    """Позиция каталога: юридически значимое действие и размер пошлины.

    Attributes:
        code: Пункт приложения к положению о пошлинах или прейскуранта.
        action: Название действия.
        amount: Базовая сумма (для Казпатента — для юридического лица).
        amount_by_category: Базовая сумма для других категорий заявителя.
        extras: Надбавки за единицы сверх включённых.
        aliases: Прежние названия (v0.3), по которым позиция тоже находится.
        source_url, needs_review, legal_review, note: Как в реестре ставок.
    """

    code: str = ""
    action: str
    amount: NonNegativeDecimal
    amount_by_category: dict[ApplicantCategory, NonNegativeDecimal] = Field(default_factory=dict)
    extras: tuple[DutyExtra, ...] = ()
    aliases: tuple[str, ...] = ()
    source_url: HttpUrl | None
    needs_review: bool
    legal_review: str | None = None
    note: str = ""

    @model_validator(mode="after")
    def _confirmed_needs_source(self) -> Duty:
        """Сверенная сумма (needs_review: false) ссылается на источник."""
        if not self.needs_review and self.source_url is None:
            raise ValueError(f"Пошлина «{self.action}»: needs_review: false без source_url")
        return self

    @property
    def quantities(self) -> tuple[Quantity, ...]:
        """Единицы, от которых зависит размер пошлины."""
        return tuple(extra.quantity for extra in self.extras)

    def total(
        self,
        quantities: Mapping[Quantity, int] | None = None,
        category: ApplicantCategory | None = None,
    ) -> NonNegativeDecimal:
        """Размер пошлины при заданных количествах и категории заявителя.

        Не заданное количество берётся по умолчанию (`QUANTITY_DEFAULTS`,
        иначе одна единица). Нет суммы для категории — базовая сумма.
        """
        counts = quantities or {}
        base = self.amount_by_category.get(category, self.amount) if category else self.amount
        return base + decimal_sum(
            extra.amount(
                counts.get(extra.quantity, QUANTITY_DEFAULTS.get(extra.quantity, DEFAULT_QUANTITY))
            )
            for extra in self.extras
        )


class DutyCatalog(CoreModel):
    """Каталог пошлин патентного ведомства страны.

    Attributes:
        source: Документ, которым установлены суммы.
        source_url: Где его прочитать.
        effective_from: С какой даты действуют суммы каталога.
        vat_included: Суммы указаны с НДС (цены Казпатента).
        categories: Каталог различает категории заявителя.
    """

    country: Country
    currency: Currency
    source: str = ""
    source_url: HttpUrl | None = None
    effective_from: dt.date | None = None
    vat_included: bool = False
    categories: bool = False
    duties: tuple[Duty, ...]

    def find(self, action: str) -> Duty | None:
        """Позиция по названию или прежнему названию; при повторе — последняя (как в v0.3)."""
        found: Duty | None = None
        for duty in self.duties:
            if action == duty.action or action in duty.aliases:
                found = duty
        return found

    def amounts(self) -> dict[str, NonNegativeDecimal]:
        """Базовая сумма по названию действия; при повторе побеждает последнее (как в v0.3)."""
        return {duty.action: duty.amount for duty in self.duties}


@cache
def load_catalog(country: Country) -> DutyCatalog:
    """Читает и проверяет каталог пошлин страны.

    Raises:
        ReferenceDataError: Файл не найден, не разбирается, не проходит
            проверку схемы или относится к другой стране.
    """
    filename = CATALOG_FILES[country]
    catalog = load_reference(DUTIES_DATA_PACKAGE, filename, DutyCatalog)
    check_country(catalog.country, country, filename)
    return catalog


def duty_disbursements(
    country: Country,
    actions: Iterable[str],
    quantities: Mapping[Quantity, int] | None = None,
    category: ApplicantCategory | None = None,
) -> list[Disbursement]:
    """Перевыставляемые расходы проекта по выбранным пошлинам (v0.3, 02_Проект.py:570–589).

    Пошлины с нулевой суммой (размер не установлен по источнику) и названия,
    которых нет в каталоге, пропускаются.

    Args:
        country: Страна каталога.
        actions: Названия действий в порядке выбора (текущие или прежние).
        quantities: Число классов, пунктов формулы и т. п. на проекте.
        category: Категория заявителя (для Казпатента).

    Returns:
        Расходы в национальной валюте страны.
    """
    catalog = load_catalog(country)
    result: list[Disbursement] = []
    for action in actions:
        duty = catalog.find(action)
        if duty is None:
            continue
        amount = duty.total(quantities, category)
        if amount <= ZERO:
            continue
        result.append(
            Disbursement(
                name=duty.action,
                kind=DisbursementKind.BILLABLE,
                amount=amount,
                currency=country.currency,
            )
        )
    return result
