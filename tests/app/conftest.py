"""Фикстуры smoke-тестов интерфейса (app/)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.runtime.media_file_storage import MediaFileKind
from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage

APP_DIR = Path(__file__).resolve().parents[2] / "app"

# Страницы импортируют общие модули как `ui.*`: Streamlit кладёт app/ в sys.path,
# AppTest — нет, поэтому добавляем сами.
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Файлы кнопок `st.download_button` по имени файла.

    AppTest хранит их в памяти подменённого Runtime, который после прогона
    сбрасывается; поэтому содержимое перехватывается при добавлении.
    """
    captured: dict[str, bytes] = {}
    original = MemoryMediaFileStorage.load_and_get_id

    def spy(
        self: MemoryMediaFileStorage,
        path_or_data: str | bytes,
        mimetype: str,
        kind: MediaFileKind,
        filename: str | None = None,
    ) -> str:
        if isinstance(path_or_data, bytes) and filename is not None:
            captured[filename] = path_or_data
        return original(self, path_or_data, mimetype, kind, filename)

    monkeypatch.setattr(MemoryMediaFileStorage, "load_and_get_id", spy)
    return captured
