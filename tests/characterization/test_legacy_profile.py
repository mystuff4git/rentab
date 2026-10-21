"""Формат профиля фирмы v0.3 (data/firm_profile.json).

Фикстура tests/fixtures/legacy/v03_firm_profile.json — пример профиля в том
виде, в каком его пишет v0.3. В фазе 4 по ней проверяется миграция в формат
v1.0 (импорт JSON пользователем).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.characterization.legacy import profile

pytestmark = pytest.mark.characterization

FIXTURE = Path(__file__).parents[1] / "fixtures" / "legacy" / "v03_firm_profile.json"


def ui_session_state() -> dict[str, Any]:
    """Состояние сессии после заполнения «Настройки» и «Проекта» в v0.3."""
    state: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Ключи, которые есть в сессии, но в профиль не попадают.
    state.update(
        project_stages=[{"name": "Подача заявки", "executor": "Петрова М.", "hours": 4.0}],
        overhead_rate=1_031.25,
        _profile_autoloaded=True,
    )
    return state


def test_profile_keys() -> None:
    assert profile.PROFILE_KEYS == (
        "jurisdiction_params",
        "team",
        "firm_expenses",
        "billable_hours_per_month",
        "contract_hours_total",
        "exchange_rate_rub_per_kzt",
        "pricing_model",
        "target_margin",
    )


def test_save_profile_writes_only_profile_keys_to_server_disk(tmp_path: Path) -> None:
    # KNOWN-BUG L-10 → фаза 4: профиль с окладами пишется на диск сервера.
    path = profile.save_profile(tmp_path, ui_session_state())
    assert path == tmp_path / "firm_profile.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == json.loads(FIXTURE.read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    assert "Иванов А." in text  # v0.3 пишет кириллицу без \u-экранирования


def test_profile_round_trip_and_reset(tmp_path: Path) -> None:
    profile.save_profile(tmp_path, ui_session_state())
    loaded = profile.load_profile(tmp_path)
    assert loaded is not None

    fresh_session: dict[str, Any] = {}
    applied = profile.apply_profile(fresh_session, {**loaded, "garbage": 1})
    assert applied == list(profile.PROFILE_KEYS)
    assert "garbage" not in fresh_session

    assert profile.reset_profile(tmp_path, fresh_session) is True
    assert fresh_session == {}
    assert profile.load_profile(tmp_path) is None
    assert profile.reset_profile(tmp_path) is False


def test_fixture_documents_v03_value_formats() -> None:
    # KNOWN-BUG L-16 → фаза 4: модель хранится русской подписью радиокнопки,
    # маржа — целым числом процентов (в results — долей).
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["pricing_model"] == "Фиксированная (издержки + маржа)"
    assert data["target_margin"] == 30
    assert set(data["team"][0]) == {
        "name",
        "role",
        "billing_rate",
        "cost_rate",
        "gross_salary",
        "contract_hours_per_month",
        "employer_contribution_rate",
    }
