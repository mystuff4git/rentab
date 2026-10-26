"""Деплой: файл зависимостей Streamlit Community Cloud не отстаёт от `uv.lock` (ADR 0010).

Cloud ищет файл зависимостей сначала рядом с точкой входа (`app/`), потом в
корне, поэтому `app/requirements.txt` важнее корневого `uv.lock`. Файл
генерирует `uv export` (команда — в его шапке и в CLAUDE.md).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from tests.app.conftest import APP_DIR

ROOT = APP_DIR.parent
REQUIREMENTS = APP_DIR / "requirements.txt"
EXPORT_COMMAND = [
    "export",
    "--frozen",
    "--no-default-groups",
    "--group",
    "app",
    "--no-hashes",
    "--no-editable",
]
HEADER_PREFIX = "#"
# В CI uv есть всегда; локально без него тест пропускается.
REQUIRE_UV = "CI"


def body(text: str) -> list[str]:
    """Строки файла без шапки-комментария uv."""
    lines = text.splitlines()
    while lines and lines[0].startswith(HEADER_PREFIX):
        lines.pop(0)
    return lines


def test_requirements_header_names_the_export_command() -> None:
    header = REQUIREMENTS.read_text(encoding="utf-8").splitlines()[1]
    assert header.split() == ["#", "uv", *EXPORT_COMMAND, "-o", "app/requirements.txt"]


def test_requirements_install_project_and_pin_everything() -> None:
    lines = [line for line in body(REQUIREMENTS.read_text(encoding="utf-8")) if line.strip()]
    requirements = [line for line in lines if not line.lstrip().startswith(HEADER_PREFIX)]
    # Сам проект (rentab_core) ставится из корня репозитория: рабочая папка Cloud — корень.
    assert requirements[0] == "."
    assert all("==" in line for line in requirements[1:]), requirements
    names = {line.split("==")[0] for line in requirements[1:]}
    assert {"streamlit", "plotly", "pandas", "reportlab", "python-docx", "pydantic"} <= names
    # Инструменты разработки на сервер не ставятся.
    assert not names & {"pytest", "mypy", "ruff", "hypothesis"}


def test_requirements_match_lock() -> None:
    uv = shutil.which("uv")
    if uv is None:
        if os.environ.get(REQUIRE_UV):
            pytest.fail("uv не найден, а в CI сверка app/requirements.txt обязательна")
        pytest.skip("uv не найден")
    exported = subprocess.run(
        [uv, *EXPORT_COMMAND, "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert body(REQUIREMENTS.read_text(encoding="utf-8")) == body(exported), (
        "app/requirements.txt отстал от uv.lock: uv "
        + " ".join(EXPORT_COMMAND)
        + " -o app/requirements.txt"
    )
