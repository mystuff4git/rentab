"""Формат файла профиля Rentab: фирма и проекты в JSON (docs/profile_format.md).

Файл — конверт с идентификатором формата и версией схемы вокруг рабочего
пространства (`Workspace`): фирма, последние загруженные курсы и проекты.
Фирма и проект — модели ядра как есть, поэтому файл хранит всё, что знает
расчёт. Проект дополнен выбором пошлин: пошлины хранятся как выбор (действия,
количества, категория заявителя), а суммы пересчитываются по актуальному
каталогу при загрузке. Суммы на дату экспорта пишутся справочно — по ним
импорт сообщает, что каталог изменился.

JSON Schema формата — `docs/profile.schema.json`, генерируется отсюда:

    uv run python -m rentab_core.io docs/profile.schema.json
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import Field

from rentab_core.documents import Letterhead, OfferTerms
from rentab_core.duties import ApplicantCategory, Quantity, duty_disbursements
from rentab_core.fx import RatesQuote
from rentab_core.models import (
    CoreModel,
    Country,
    Disbursement,
    Firm,
    NonNegativeDecimal,
    Project,
    Scenario,
)

# Идентификатор формата: отличает файл Rentab от любого другого JSON.
FORMAT_ID: Final = "rentab"
# Версия схемы файла. Несовместимое изменение моделей — новая версия и шаг
# миграции в `rentab_core.io.migrations`.
SCHEMA_VERSION: Final = 1

Count = Annotated[int, Field(ge=0)]


class DutySelection(CoreModel):
    """Пошлины страны, выбранные на проекте.

    Attributes:
        actions: Названия действий из каталога в порядке выбора.
        quantities: Классы МКТУ, пункты формулы и т. п. Не заданное
            количество — по умолчанию (`duties.QUANTITY_DEFAULTS`).
        category: Категория заявителя (цены Казпатента).
        amounts_at_export: Суммы пошлин на дату экспорта — справочно: при
            загрузке суммы пересчитываются по актуальному каталогу.
    """

    actions: tuple[str, ...] = ()
    quantities: dict[Quantity, Count] = Field(default_factory=dict)
    category: ApplicantCategory = ApplicantCategory.LEGAL_ENTITY
    amounts_at_export: dict[str, NonNegativeDecimal] = Field(default_factory=dict)

    def disbursements(self, country: Country) -> list[Disbursement]:
        """Возмещаемые расходы по актуальному каталогу страны."""
        return duty_disbursements(country, self.actions, self.quantities, self.category)


class ProjectRecord(CoreModel):
    """Проект в файле и в сессии: проект ядра, выбранные пошлины и условия КП.

    Attributes:
        project: Проект; `disbursements` — только расходы, введённые
            вручную. Пошлины из каталога добавляются при расчёте.
        duties: Выбранные пошлины по странам.
        offer: Условия КП (фаза 7); None — не заданы, КП с условиями по
            умолчанию.
    """

    project: Project = Field(default_factory=Project)
    duties: dict[Country, DutySelection] = Field(default_factory=dict)
    offer: OfferTerms | None = None

    def to_project(self, countries: Iterable[Country] = tuple(Country)) -> Project:
        """Проект для расчёта: пошлины стран `countries`, затем расходы, введённые вручную."""
        wanted = set(countries)
        duties = [
            item
            for country in Country
            if country in wanted and country in self.duties
            for item in self.duties[country].disbursements(country)
        ]
        return self.project.model_copy(
            update={"disbursements": (*duties, *self.project.disbursements)}
        )

    def scenario(self, firm: Firm) -> Scenario:
        """Вход расчёта сметы: пошлины — по странам юрисдикции фирмы."""
        return Scenario(firm=firm, project=self.to_project(firm.jurisdiction.countries))


class Workspace(CoreModel):
    """Данные пользователя: фирма, загруженные курсы, бланк для КП и проекты.

    Attributes:
        firm: Фирма; курсы в `firm.exchange_rates` — те, что идут в расчёт.
        rates_quote: Последние загруженные курсы с источником и датой; если
            их потом изменили вручную, `firm.exchange_rates` от них отличается.
        letterhead: Реквизиты и логотип фирмы для КП (фаза 7); None — не заданы.
        projects: Проекты в порядке, в каком их видит пользователь.
    """

    firm: Firm = Field(default_factory=Firm)
    rates_quote: RatesQuote | None = None
    letterhead: Letterhead | None = None
    projects: tuple[ProjectRecord, ...] = ()


class ProfileFile(CoreModel):
    """Файл профиля: конверт с форматом и версией вокруг `Workspace`.

    Attributes:
        format: Всегда "rentab".
        schema_version: Версия схемы.
        exported_at: Когда файл выгружен (справочно).
        app_version: Версия Rentab, которая его выгрузила (справочно).
    """

    format: Literal["rentab"] = FORMAT_ID
    schema_version: Literal[1] = SCHEMA_VERSION
    exported_at: dt.datetime | None = None
    app_version: str = ""
    firm: Firm = Field(default_factory=Firm)
    rates_quote: RatesQuote | None = None
    letterhead: Letterhead | None = None
    projects: tuple[ProjectRecord, ...] = ()

    def workspace(self) -> Workspace:
        """Содержимое файла без конверта."""
        return Workspace(
            firm=self.firm,
            rates_quote=self.rates_quote,
            letterhead=self.letterhead,
            projects=self.projects,
        )


def json_schema() -> dict[str, Any]:
    """JSON Schema файла профиля."""
    schema = ProfileFile.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Rentab profile, schema version {SCHEMA_VERSION}"
    return schema


def json_schema_text() -> str:
    """Текст `docs/profile.schema.json`."""
    return json.dumps(json_schema(), ensure_ascii=False, indent=2) + "\n"


def main(argv: Sequence[str]) -> None:
    """Пишет JSON Schema в путь из аргументов или выводит её в stdout."""
    text = json_schema_text()
    if argv:
        Path(argv[0]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
