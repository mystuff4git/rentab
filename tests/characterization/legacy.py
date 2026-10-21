"""Доступ к модулям замороженного Rentab v0.3 из legacy/rentab_v03.

v0.3 импортирует свои модули как пакет верхнего уровня `modules`
(`from modules.team import ...`), поэтому корень legacy-приложения добавляется
в sys.path. После импорта проверяем, что `modules` действительно загружен из
legacy/, а не подменён одноимённым пакетом из окружения.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_APP_DIR = REPO_ROOT / "legacy" / "rentab_v03"
LEGACY_DATA_DIR = LEGACY_APP_DIR / "data"


def load(name: str) -> ModuleType:
    """Импортирует модуль v0.3 по короткому имени.

    Args:
        name: Имя модуля внутри `modules/`, например `"jurisdiction"`.

    Returns:
        Загруженный модуль `modules.<name>` из legacy/rentab_v03.

    Raises:
        ImportError: Если модуль с таким именем загружен не из legacy/.
    """
    legacy_root = str(LEGACY_APP_DIR)
    if legacy_root not in sys.path:
        sys.path.insert(0, legacy_root)
    module = importlib.import_module(f"modules.{name}")
    module_path = Path(module.__file__ or "").resolve()
    if LEGACY_APP_DIR not in module_path.parents:
        raise ImportError(f"modules.{name} загружен не из legacy/: {module_path}")
    return module


calculator = load("calculator")
estimate_export = load("estimate_export")
expenses = load("expenses")
fixed_price = load("fixed_price")
jurisdiction = load("jurisdiction")
profile = load("profile")
project = load("project")
team = load("team")
