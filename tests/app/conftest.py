"""Фикстуры smoke-тестов интерфейса (app/)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from streamlit.runtime.media_file_manager import MediaFileManager
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
    сбрасывается; поэтому содержимое перехватывается при добавлении. Файл,
    который собирается по нажатию (`data` — функция: КП и смета), фикстура
    собирает сразу — так, как это сделал бы сервер, когда пользователь нажал
    кнопку.
    """
    captured: dict[str, bytes] = {}
    original = MemoryMediaFileStorage.load_and_get_id
    original_deferred = MediaFileManager.add_deferred

    def build_now(
        self: MediaFileManager,
        data_callable: Callable[[], Any],
        mimetype: str | None,
        coordinates: str,
        file_name: str | None = None,
        *,
        element_id: str = "",
    ) -> str:
        if file_name is not None:
            data = data_callable()
            assert isinstance(data, bytes), file_name
            captured[file_name] = data
        return original_deferred(
            self, data_callable, mimetype, coordinates, file_name, element_id=element_id
        )

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
    monkeypatch.setattr(MediaFileManager, "add_deferred", build_now)
    return captured
