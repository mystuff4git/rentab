"""Каталоги патентных пошлин Роспатента и Казпатента.

Пошлина из каталога превращается в перевыставляемый расход проекта
(`Disbursement` вида BILLABLE). Каталоги лежат в `duties/data/*.yaml`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import cache

from pydantic import HttpUrl

from rentab_core.models import (
    CoreModel,
    Country,
    Disbursement,
    DisbursementKind,
    NonNegativeDecimal,
)
from rentab_core.money import Currency
from rentab_core.reference import check_country, load_reference

DUTIES_DATA_PACKAGE = "rentab_core.duties.data"
CATALOG_FILES: Mapping[Country, str] = {
    Country.RF: "rospatent_2026.yaml",
    Country.KZ: "kazpatent_2026.yaml",
}


class Duty(CoreModel):
    """Позиция каталога: юридически значимое действие и размер пошлины."""

    action: str
    amount: NonNegativeDecimal
    source_url: HttpUrl | None
    needs_review: bool
    note: str = ""


class DutyCatalog(CoreModel):
    """Каталог пошлин патентного ведомства страны."""

    country: Country
    currency: Currency
    duties: tuple[Duty, ...]

    def amounts(self) -> dict[str, NonNegativeDecimal]:
        """Размер пошлины по названию действия; при повторе побеждает последнее (как в v0.3)."""
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


def duty_disbursements(country: Country, actions: Iterable[str]) -> list[Disbursement]:
    """Перевыставляемые расходы проекта по выбранным пошлинам (v0.3, 02_Проект.py:570–589).

    Пошлины с нулевой суммой и названия, которых нет в каталоге, пропускаются.
    В v0.3 все суммы каталогов нулевые, поэтому выбор пошлины ни на что не
    влияет (KNOWN-BUG L-09 → фаза 3).

    Args:
        country: Страна каталога.
        actions: Названия действий в порядке выбора.

    Returns:
        Расходы в национальной валюте страны.
    """
    amounts = load_catalog(country).amounts()
    result: list[Disbursement] = []
    for action in actions:
        amount = amounts.get(action)
        if amount is None or amount <= 0:
            continue
        result.append(
            Disbursement(
                name=action,
                kind=DisbursementKind.BILLABLE,
                amount=amount,
                currency=country.currency,
            )
        )
    return result
