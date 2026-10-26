"""Данные документов: логотип, бланк фирмы, условия КП (`rentab_core.documents`)."""

from __future__ import annotations

import struct
import zlib

import pytest
from pydantic import ValidationError

from rentab_core.documents import (
    DEFAULT_OFFER_VALIDITY_DAYS,
    MAX_LOGO_BYTES,
    ImageFormat,
    Letterhead,
    Logo,
    LogoError,
    OfferTerms,
    logo_size,
    read_logo,
)
from tests.core.factories import image_bytes


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def png_header(width: int, height: int) -> bytes:
    """PNG, чей заголовок обещает заданный размер, а данных — несколько байт."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(b"\x00" * 16))
        + _chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    ("fmt", "media_type"), [("PNG", ImageFormat.PNG), ("JPEG", ImageFormat.JPEG)]
)
def test_png_and_jpeg_are_accepted(fmt: str, media_type: ImageFormat) -> None:
    logo = read_logo(image_bytes(120, 40, fmt))
    assert logo.media_type == media_type
    assert logo_size(logo) == (120, 40)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"GIF89a" + b"\x00" * 20, "PNG или JPEG"),
        (image_bytes(fmt="GIF"), "PNG или JPEG"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "повреждён"),
        (b"\x00" * (MAX_LOGO_BYTES + 1), "больше 500 КБ"),
        (image_bytes(4001, 10), "не больше 4000 px"),
        # Заголовок обещает 10 000 и 30 000 px по стороне: Pillow предупреждает
        # о «бомбе распаковки» или отказывается открыть — логотип не распаковывается.
        (png_header(10_000, 10_000), "не больше 4000 px"),
        (png_header(30_000, 30_000), "не больше 4000 px"),
    ],
)
def test_bad_logo_is_rejected(data: bytes, message: str) -> None:
    with pytest.raises(LogoError, match=message):
        read_logo(data)


def test_logo_model_checks_signature_and_size() -> None:
    with pytest.raises(ValidationError, match="не похож на файл image/jpeg"):
        Logo(media_type=ImageFormat.JPEG, data=image_bytes())
    with pytest.raises(ValidationError, match="512000"):
        Logo(media_type=ImageFormat.PNG, data=b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_LOGO_BYTES)


def test_logo_is_base64_in_json() -> None:
    logo = read_logo(image_bytes())
    raw = logo.model_dump(mode="json")
    assert raw["media_type"] == "image/png"
    assert isinstance(raw["data"], str)
    assert Logo.model_validate(raw) == logo
    for bad in ("@@@", "не base64"):
        with pytest.raises(ValidationError, match="строкой base64"):
            Logo.model_validate({"media_type": "image/png", "data": bad})


def test_letterhead_contacts_and_offer_defaults() -> None:
    letterhead = Letterhead(phone="+7 000 000-00-00", website="example.com")
    assert letterhead.contacts == "+7 000 000-00-00 · example.com"
    assert Letterhead().contacts == ""
    assert OfferTerms().validity_days == DEFAULT_OFFER_VALIDITY_DAYS == 30
    with pytest.raises(ValidationError):
        OfferTerms(validity_days=0)
