"""Оформление документов в стиле темы «Бордо и пергамент» (ADR 0008, 0009).

Цвета — те же, что в теме интерфейса (`.streamlit/config.toml`), без «#».
Шрифты ParaType (OFL) лежат в пакете (`export/fonts`): PDF встраивает их
подмножество, DOCX — файлы целиком, поэтому документ выглядит одинаково на
любом компьютере и не зависит от системных шрифтов.
"""

from __future__ import annotations

import functools
from enum import StrEnum
from importlib import resources
from typing import Final

BORDO: Final = "7A1F2E"  # основной цвет темы
INK: Final = "221F1A"  # текст
MUTED: Final = "6B6357"  # реквизиты, подписи полей, примечания
PARCHMENT: Final = "F4EEE3"  # шапка таблицы и строка «Итого к оплате»
RULE: Final = "D9CFC0"  # тонкие линии таблиц

SANS: Final = "PT Sans"
SERIF: Final = "PT Serif"


class FontFace(StrEnum):
    """Начертания, которые используют документы."""

    SANS = "PT Sans"
    SANS_BOLD = "PT Sans Bold"
    SERIF_BOLD = "PT Serif Bold"


FONT_FILES: Final[dict[FontFace, str]] = {
    FontFace.SANS: "PT_Sans-Web-Regular.ttf",
    FontFace.SANS_BOLD: "PT_Sans-Web-Bold.ttf",
    FontFace.SERIF_BOLD: "PT_Serif-Web-Bold.ttf",
}


@functools.cache
def font_bytes(face: FontFace) -> bytes:
    """Файл шрифта TTF из пакета (читается один раз за процесс)."""
    return (resources.files("rentab_core.export") / "fonts" / FONT_FILES[face]).read_bytes()


def hex_rgb(color: str) -> tuple[int, int, int]:
    """«7A1F2E» → (122, 31, 46)."""
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
