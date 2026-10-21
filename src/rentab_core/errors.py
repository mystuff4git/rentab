"""Исключения ядра Rentab.

Все исключения наследуют `RentabError`, поэтому интерфейс может перехватить
их одним `except`. Ошибки входных данных — это `pydantic.ValidationError`
(подкласс `ValueError`), их бросают сами модели.
"""

from __future__ import annotations


class RentabError(Exception):
    """Базовое исключение ядра."""


class FixedPriceError(RentabError, ValueError):
    """Фиксированную цену нельзя рассчитать при заданной марже и режиме.

    Наследует ValueError: в v0.3 эта ситуация была ValueError, и вызывающий
    код мог ловить именно его.
    """


class ReferenceDataError(RentabError):
    """Справочник ядра (ставки, каталог пошлин) отсутствует или повреждён."""
