"""Выгрузка и загрузка файла профиля: `dumps` и `loads`.

Файл создаёт и загружает сам пользователь: ядро работает с байтами и ничего
не пишет на диск (L-10). Суммы пишутся строками, чтобы Decimal не терял
точность; при чтении числа JSON тоже переводятся в Decimal, минуя float.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final, NoReturn

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from rentab_core import __version__
from rentab_core.duties import load_catalog
from rentab_core.errors import ProfileImportError
from rentab_core.io.migrations import ImportNote, RawProfile, upgrade, warning
from rentab_core.io.schema import (
    FORMAT_ID,
    SCHEMA_VERSION,
    DutySelection,
    ProfileFile,
    ProjectRecord,
    Workspace,
)
from rentab_core.io.v03 import LEGACY_V03_VERSION, import_v03, is_v03_profile
from rentab_core.models import CoreModel, Country, Firm
from rentab_core.money import CURRENCY_SYMBOLS, format_number

# Файл профиля — десятки килобайт; больше — не тот файл.
MAX_PROFILE_BYTES: Final = 2 * 1024 * 1024
# Сколько ошибок проверки показывать пользователю.
MAX_ERRORS_SHOWN: Final = 5
FILE_ENCODING: Final = "utf-8"

# Ошибки проверки pydantic по-русски (ключ — тип ошибки, поля — из её контекста).
VALIDATION_MESSAGES: Mapping[str, str] = {
    "missing": "обязательное поле не заполнено",
    "extra_forbidden": "неизвестное поле",
    "greater_than": "должно быть больше {gt}",
    "greater_than_equal": "должно быть не меньше {ge}",
    "less_than": "должно быть меньше {lt}",
    "less_than_equal": "должно быть не больше {le}",
    "decimal_parsing": "должно быть числом",
    "decimal_type": "должно быть числом",
    "finite_number": "должно быть конечным числом",
    "int_parsing": "должно быть целым числом",
    "int_type": "должно быть целым числом",
    "int_from_float": "должно быть целым числом",
    "string_type": "должно быть строкой",
    "bool_type": "должно быть true или false",
    "bool_parsing": "должно быть true или false",
    "enum": "допустимые значения: {expected}",
    "literal_error": "допустимые значения: {expected}",
    "union_tag_invalid": "неизвестный вид «{tag}», допустимые: {expected_tags}",
    "union_tag_not_found": "не указано поле {discriminator}",
    "date_parsing": "неверная дата",
    "date_from_datetime_parsing": "неверная дата",
    "datetime_parsing": "неверные дата и время",
    "datetime_from_date_parsing": "неверные дата и время",
    "list_type": "должно быть списком",
    "tuple_type": "должно быть списком",
    "dict_type": "должно быть объектом",
    "model_type": "должно быть объектом",
}
VALUE_ERROR_PREFIX = "Value error, "


class ImportResult(CoreModel):
    """Результат загрузки файла.

    Attributes:
        workspace: Данные в текущей схеме; суммы пошлин на дату экспорта
            из них убраны — по ним уже составлены замечания.
        notes: Что изменилось при загрузке: перевод из v0.3, пересчёт
            пошлин, исполнители вне команды.
        source_version: Версия схемы файла; 0 — профиль v0.3.
    """

    workspace: Workspace
    notes: tuple[ImportNote, ...] = ()
    source_version: int

    @property
    def from_v03(self) -> bool:
        """Файл — профиль фирмы v0.3."""
        return self.source_version == LEGACY_V03_VERSION


# ---------------------------------------------------------------------------
# Выгрузка
# ---------------------------------------------------------------------------
def _with_amounts(country: Country, selection: DutySelection) -> DutySelection:
    """Выбор пошлин с суммами по текущему каталогу (справочно)."""
    catalog = load_catalog(country)
    amounts = {}
    for action in selection.actions:
        duty = catalog.find(action)
        if duty is not None:
            amounts[action] = duty.total(selection.quantities, selection.category)
    return selection.model_copy(update={"amounts_at_export": amounts})


def _record_for_export(record: ProjectRecord) -> ProjectRecord:
    duties = {
        country: _with_amounts(country, selection) for country, selection in record.duties.items()
    }
    return record.model_copy(update={"duties": duties})


def dumps(
    workspace: Workspace,
    *,
    exported_at: dt.datetime | None = None,
    include_projects: bool = True,
) -> bytes:
    """Файл профиля в UTF-8: JSON с отступами, кириллица без экранирования.

    Args:
        workspace: Фирма, курсы и проекты.
        exported_at: Время выгрузки для конверта (справочно).
        include_projects: Выгружать проекты; иначе — только фирму и курсы.
    """
    projects = workspace.projects if include_projects else ()
    profile = ProfileFile(
        exported_at=exported_at,
        app_version=__version__,
        firm=workspace.firm,
        rates_quote=workspace.rates_quote,
        projects=tuple(_record_for_export(record) for record in projects),
    )
    text = json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return (text + "\n").encode(FILE_ENCODING)


# ---------------------------------------------------------------------------
# Загрузка
# ---------------------------------------------------------------------------
def _reject_constant(name: str) -> NoReturn:
    raise ProfileImportError(f"В файле есть значение {name}: суммы должны быть конечными числами")


def _parse_json(data: bytes) -> RawProfile:
    """Байты файла → объект JSON верхнего уровня."""
    if len(data) > MAX_PROFILE_BYTES:
        raise ProfileImportError(
            f"Файл слишком большой ({len(data):,} байт): профиль Rentab меньше "
            f"{MAX_PROFILE_BYTES // 1024 // 1024} МБ".replace(",", " ")
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProfileImportError("Файл не в кодировке UTF-8: это не профиль Rentab") from exc
    try:
        raw: Any = json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ProfileImportError(
            f"Файл не является JSON: {exc.msg} (строка {exc.lineno}, столбец {exc.colno})"
        ) from exc
    except RecursionError as exc:
        raise ProfileImportError(
            "Файл не является профилем Rentab: слишком глубокая вложенность"
        ) from exc
    if not isinstance(raw, dict):
        raise ProfileImportError("Файл не является профилем Rentab: ожидался объект JSON")
    return raw


def _source_version(raw: RawProfile) -> int:
    """Версия схемы файла; 0 — профиль v0.3."""
    if "format" not in raw:
        if is_v03_profile(raw):
            return LEGACY_V03_VERSION
        raise ProfileImportError("Файл не является профилем Rentab: нет поля format")
    if raw["format"] != FORMAT_ID:
        raise ProfileImportError(f"Файл не является профилем Rentab: format = {raw['format']!r}")
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ProfileImportError(f"Неверная версия схемы файла: {version!r}")
    if version > SCHEMA_VERSION:
        raise ProfileImportError(
            f"Файл создан более новой версией Rentab (схема {version}, поддерживается "
            f"до {SCHEMA_VERSION}). Обновите приложение."
        )
    return version


def _location(loc: tuple[int | str, ...]) -> str:
    """Путь к полю: projects[0].project.stages[1].name."""
    path = ""
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
    return path or "файл"


def _error_message(error: ErrorDetails) -> str:
    template = VALIDATION_MESSAGES.get(error["type"])
    if template is not None:
        try:
            return template.format(**error.get("ctx", {}))
        except KeyError:
            pass
    return error["msg"].removeprefix(VALUE_ERROR_PREFIX)


def validation_error_text(exc: ValidationError) -> str:
    """Ошибки проверки по-русски: путь к полю и что с ним не так."""
    errors = exc.errors()
    lines = [f"{_location(error['loc'])}: {_error_message(error)}" for error in errors]
    shown = lines[:MAX_ERRORS_SHOWN]
    if len(lines) > MAX_ERRORS_SHOWN:
        shown.append(f"и ещё {len(lines) - MAX_ERRORS_SHOWN}")
    return "Неверные данные в файле: " + "; ".join(shown)


def project_title(record: ProjectRecord, index: int) -> str:
    """Проект в тексте замечания: «Название» или «№ 2» без названия."""
    name = record.project.name.strip()
    return f"«{name}»" if name else f"№ {index + 1}"


def _check_duties(
    record: ProjectRecord, title: str, notes: list[ImportNote]
) -> dict[Country, DutySelection]:
    """Пошлины по актуальному каталогу: прежние названия — к текущим, неизвестные — прочь."""
    result = {}
    for country, selection in record.duties.items():
        catalog = load_catalog(country)
        symbol = CURRENCY_SYMBOLS[catalog.currency]
        actions: list[str] = []
        for action in selection.actions:
            duty = catalog.find(action)
            if duty is None:
                notes.append(
                    warning(f"Проект {title}: пошлины «{action}» нет в каталоге — она пропущена.")
                )
                continue
            if duty.action in actions:
                continue
            actions.append(duty.action)
            exported = selection.amounts_at_export.get(action)
            current = duty.total(selection.quantities, selection.category)
            if exported is not None and exported != current:
                notes.append(
                    warning(
                        f"Проект {title}: пошлина «{duty.action}» при выгрузке была "
                        f"{format_number(exported)} {symbol}, по текущему каталогу — "
                        f"{format_number(current)} {symbol}. В смете будет новая сумма."
                    )
                )
        result[country] = selection.model_copy(
            update={"actions": tuple(actions), "amounts_at_export": {}}
        )
    return result


def _check_assignments(
    record: ProjectRecord, title: str, firm: Firm, notes: list[ImportNote]
) -> None:
    """Предупреждения о назначениях на сотрудников, которых нет в команде."""
    team = firm.team_by_name()
    for stage in record.project.stages:
        for assignment in stage.assignments:
            if assignment.employee not in team:
                notes.append(
                    warning(
                        f"Проект {title}, этап «{stage.name}»: исполнителя "
                        f"«{assignment.employee}» нет в команде — назначение не войдёт в расчёт."
                    )
                )


def _review(workspace: Workspace, notes: list[ImportNote]) -> Workspace:
    """Сверка загруженных проектов с каталогами пошлин и командой."""
    projects = []
    for index, record in enumerate(workspace.projects):
        title = project_title(record, index)
        _check_assignments(record, title, workspace.firm, notes)
        projects.append(record.model_copy(update={"duties": _check_duties(record, title, notes)}))
    return workspace.model_copy(update={"projects": tuple(projects)})


def loads(data: bytes) -> ImportResult:
    """Загружает файл профиля Rentab или `firm_profile.json` v0.3.

    Raises:
        ProfileImportError: Файл не JSON, слишком большой, чужого формата,
            более новой версии схемы или с неверными значениями.
    """
    raw = _parse_json(data)
    version = _source_version(raw)
    notes: list[ImportNote] = []
    if version == LEGACY_V03_VERSION:
        workspace, legacy_notes = import_v03(raw)
        notes += legacy_notes
    else:
        raw, step_notes = upgrade(raw, version)
        notes += step_notes
        try:
            workspace = ProfileFile.model_validate(raw).workspace()
        except ValidationError as exc:
            raise ProfileImportError(validation_error_text(exc)) from exc
    workspace = _review(workspace, notes)
    return ImportResult(workspace=workspace, notes=tuple(notes), source_version=version)
