"""Справочники ядра в YAML: чтение из пакета и проверка схемы.

Общий код для реестра ставок (`rates/`) и каталогов пошлин (`duties/`).
"""

from __future__ import annotations

from importlib import resources
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from rentab_core.errors import ReferenceDataError
from rentab_core.models import Country

ModelT = TypeVar("ModelT", bound=BaseModel)


def parse_reference(text: str, filename: str, model: type[ModelT]) -> ModelT:
    """Разбирает YAML справочника и проверяет его по модели.

    Raises:
        ReferenceDataError: YAML не разбирается или не проходит проверку схемы.
    """
    try:
        return model.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ReferenceDataError(f"Справочник {filename} повреждён: {exc}") from exc


def load_reference(package: str, filename: str, model: type[ModelT]) -> ModelT:
    """Читает справочник из данных пакета и проверяет его по модели.

    Raises:
        ReferenceDataError: Файла нет или он повреждён.
    """
    try:
        text = resources.files(package).joinpath(filename).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReferenceDataError(f"Справочник {filename} не найден: {exc}") from exc
    return parse_reference(text, filename, model)


def check_country(actual: Country, expected: Country, filename: str) -> None:
    """Проверяет, что справочник относится к нужной стране.

    Raises:
        ReferenceDataError: Страна справочника не совпадает с ожидаемой.
    """
    if actual != expected:
        raise ReferenceDataError(
            f"Справочник {filename} относится к стране {actual}, ожидалась {expected}"
        )
