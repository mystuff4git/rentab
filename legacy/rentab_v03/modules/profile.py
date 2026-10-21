"""
Rentab v0.2 — модуль сохранения профиля фирмы.

Профиль фирмы — это «снимок» параметров, которые пользователь настраивает
на странице Setup один раз и хочет переиспользовать между сессиями:

- юрисдикция и налоговый режим (jurisdiction_params);
- состав команды (team);
- накладные расходы фирмы (firm_expenses);
- плановые оплачиваемые часы в месяц (billable_hours_per_month).

Профиль хранится в одном JSON-файле (data/firm_profile.json). Формат —
плоский словарь «ключ session_state → значение». Отдельный модуль нужен,
чтобы страница 01_Setup не разрасталась работой с файловой системой
и чтобы тот же путь автозагрузки можно было вызвать из app.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Ключи session_state, которые входят в профиль. Изменение этого списка —
# единственное место, где следует учитывать добавление новых параметров
# фирмы (ни файл 01_Setup, ни app.py не содержат собственных списков ключей).
PROFILE_KEYS: tuple[str, ...] = (
    "jurisdiction_params",
    "team",
    "firm_expenses",
    "billable_hours_per_month",
    "contract_hours_total",
    "exchange_rate_rub_per_kzt",
    "pricing_model",
    "target_margin",
)


def profile_path(data_dir: Path | str) -> Path:
    """Возвращает стандартный путь к файлу профиля внутри переданной папки data.

    Args:
        data_dir: Путь к каталогу data/ (абсолютный или относительный).

    Returns:
        Абсолютный путь к firm_profile.json в этом каталоге.
    """
    return Path(data_dir) / "firm_profile.json"


def save_profile(data_dir: Path | str, state: dict[str, Any]) -> Path:
    """Сохраняет переданный срез session_state в data/firm_profile.json.

    Из state берутся только ключи PROFILE_KEYS; отсутствующие ключи
    пропускаются (чтобы функция работала и при частично заполненном Setup).
    Файл пишется в UTF-8 без экранирования кириллицы.

    Args:
        data_dir: Каталог data/ проекта.
        state: Мэппинг вида st.session_state (любой объект с __getitem__
               и __contains__). Ожидается, что значения — JSON-сериализуемые.

    Returns:
        Путь к записанному JSON-файлу.

    Raises:
        TypeError: Если одно из значений не сериализуется в JSON.
    """
    path = profile_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    snapshot: dict[str, Any] = {}
    for key in PROFILE_KEYS:
        if key in state:
            snapshot[key] = state[key]

    with path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return path


def load_profile(data_dir: Path | str) -> dict[str, Any] | None:
    """Загружает профиль фирмы, если файл существует.

    Args:
        data_dir: Каталог data/ проекта.

    Returns:
        Словарь {ключ: значение} из JSON, либо None, если файла нет.

    Raises:
        json.JSONDecodeError: Если файл существует, но битый.
    """
    path = profile_path(data_dir)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def apply_profile(state: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    """Проставляет значения из profile в state (in-place).

    Выставляются только ключи из PROFILE_KEYS — если в профиле есть
    дополнительные поля, они игнорируются (защита от мусорных файлов).

    Args:
        state: st.session_state или совместимый dict-like объект.
        profile: Ранее сохранённый профиль (load_profile()).

    Returns:
        Список имён ключей, которые были применены.
    """
    applied: list[str] = []
    for key in PROFILE_KEYS:
        if key in profile:
            state[key] = profile[key]
            applied.append(key)
    return applied


def reset_profile(data_dir: Path | str, state: dict[str, Any] | None = None) -> bool:
    """Удаляет firm_profile.json и (опционально) чистит ключи в state.

    Args:
        data_dir: Каталог data/ проекта.
        state: Если передан — удаляем из него ключи PROFILE_KEYS.

    Returns:
        True, если файл был удалён; False, если файла не было.
    """
    path = profile_path(data_dir)
    existed = path.exists()
    if existed:
        path.unlink()
    if state is not None:
        for key in PROFILE_KEYS:
            if key in state:
                del state[key]
    return existed
