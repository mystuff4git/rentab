"""Данные документов для клиента: бланк фирмы (реквизиты, логотип) и условия КП.

Это не входы расчёта: смета от них не зависит. Бланк — один на фирму
(`Workspace.letterhead`), условия — у каждого проекта (`ProjectRecord.offer`);
в файл профиля они попадают, только если заданы (docs/profile_format.md).

Логотип хранится байтами файла (PNG или JPEG), в JSON — строкой base64.
Модель проверяет сигнатуру и размер; `read_logo` дополнительно открывает
картинку и проверяет её размеры в пикселях — так её проверяют загрузка в
интерфейсе и импорт файла профиля.
"""

from __future__ import annotations

import base64
import io
import warnings
from enum import StrEnum
from typing import Annotated, Any, Final

from PIL import Image
from pydantic import (
    BeforeValidator,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)

from rentab_core.errors import RentabError
from rentab_core.models import CoreModel

# Логотип: до 500 КБ (в файле профиля base64 — около 670 КБ при пределе 2 МБ)
# и до 4000 px по стороне.
MAX_LOGO_BYTES: Final = 500 * 1024
MAX_LOGO_SIDE_PX: Final = 4000
# Срок действия КП по умолчанию, дней (так было и в смете XLSX v0.3).
DEFAULT_OFFER_VALIDITY_DAYS: Final = 30
MAX_OFFER_VALIDITY_DAYS: Final = 365


class LogoError(RentabError, ValueError):
    """Файл логотипа не подходит: не PNG или JPEG, слишком большой или повреждён.

    Текст ошибки показывается пользователю как есть.
    """


class ImageFormat(StrEnum):
    """Формат логотипа (MIME-тип)."""

    PNG = "image/png"
    JPEG = "image/jpeg"


# Сигнатуры файлов: PNG — 8 байт, JPEG — маркер SOI и начало следующего маркера.
IMAGE_SIGNATURES: Final[dict[ImageFormat, bytes]] = {
    ImageFormat.PNG: b"\x89PNG\r\n\x1a\n",
    ImageFormat.JPEG: b"\xff\xd8\xff",
}


def _decode_base64(value: Any) -> Any:
    """Строка из файла профиля — base64; байты из интерфейса проходят как есть."""
    if isinstance(value, str):
        try:
            return base64.b64decode(value, validate=True)
        except ValueError as exc:  # binascii.Error и не-ASCII символы
            raise ValueError("логотип должен быть строкой base64") from exc
    return value


ImageBytes = Annotated[
    bytes,
    BeforeValidator(_decode_base64),
    PlainSerializer(lambda data: base64.b64encode(data).decode("ascii"), when_used="json"),
    WithJsonSchema({"type": "string", "contentEncoding": "base64"}),
]


def image_format(data: bytes) -> ImageFormat | None:
    """Формат по сигнатуре файла; None — не PNG и не JPEG."""
    for fmt, signature in IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            return fmt
    return None


class Logo(CoreModel):
    """Логотип фирмы: байты файла PNG или JPEG.

    Attributes:
        media_type: Формат файла.
        data: Содержимое файла (в JSON — base64).
    """

    media_type: ImageFormat
    data: ImageBytes = Field(max_length=MAX_LOGO_BYTES)

    @model_validator(mode="after")
    def _check_signature(self) -> Logo:
        """Сигнатура файла совпадает с заявленным форматом."""
        if image_format(self.data) != self.media_type:
            raise ValueError(f"логотип не похож на файл {self.media_type.value}")
        return self


def read_logo(data: bytes) -> Logo:
    """Проверяет загруженный файл логотипа и возвращает модель.

    Raises:
        LogoError: Не PNG и не JPEG, больше `MAX_LOGO_BYTES`, больше
            `MAX_LOGO_SIDE_PX` по стороне или повреждён.
    """
    if len(data) > MAX_LOGO_BYTES:
        raise LogoError(
            f"Логотип больше {MAX_LOGO_BYTES // 1024} КБ: уменьшите картинку или сохраните "
            "её в PNG с меньшим разрешением."
        )
    fmt = image_format(data)
    if fmt is None:
        raise LogoError("Логотип должен быть картинкой PNG или JPEG.")
    too_large = LogoError(f"Логотип допускается не больше {MAX_LOGO_SIDE_PX} px по стороне.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if max(image.size) > MAX_LOGO_SIDE_PX:
                    raise too_large
                image.verify()
    except LogoError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        # Заголовок обещает больше пикселей, чем Pillow согласен распаковать.
        raise too_large from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise LogoError("Файл логотипа повреждён или это не картинка.") from exc
    return Logo(media_type=fmt, data=data)


def logo_size(logo: Logo) -> tuple[int, int]:
    """Размер логотипа в пикселях (ширина, высота)."""
    with Image.open(io.BytesIO(logo.data)) as image:
        width, height = image.size
    return width, height


class Letterhead(CoreModel):
    """Бланк фирмы для КП: реквизиты, контакты, подписант, логотип.

    Название фирмы — `Firm.name`. Реквизиты — одной строкой, как их пишет
    фирма: «ИНН … · КПП … · ОГРН …» в РФ, «БИН …» в РК.

    Attributes:
        requisites: Регистрационные реквизиты одной строкой.
        address: Адрес.
        phone: Телефон.
        email: Электронная почта.
        website: Сайт.
        signatory_name: ФИО подписанта КП.
        signatory_position: Должность подписанта.
        bank_details: Банковские реквизиты (несколько строк), необязательно.
        logo: Логотип; None — без логотипа.
    """

    requisites: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    signatory_name: str = ""
    signatory_position: str = ""
    bank_details: str = ""
    logo: Logo | None = None

    @property
    def contacts(self) -> str:
        """Телефон, почта и сайт одной строкой через «·»."""
        return " · ".join(part for part in (self.phone, self.email, self.website) if part)


class OfferTerms(CoreModel):
    """Условия КП по проекту.

    Attributes:
        number: Номер КП; пусто — без номера.
        validity_days: Срок действия предложения, дней от даты документа.
        payment_terms: Порядок оплаты; пусто — строки нет.
        execution_term: Срок выполнения работ; пусто — строки нет.
        extra_terms: Дополнительные условия (несколько строк).
    """

    number: str = ""
    validity_days: Annotated[int, Field(ge=1, le=MAX_OFFER_VALIDITY_DAYS)] = (
        DEFAULT_OFFER_VALIDITY_DAYS
    )
    payment_terms: str = ""
    execution_term: str = ""
    extra_terms: str = ""
