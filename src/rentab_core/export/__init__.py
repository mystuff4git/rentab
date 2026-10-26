"""Экспорт: КП для клиента в PDF и DOCX, смета XLSX с живыми формулами.

Все функции возвращают байты файла — на диск ядро ничего не пишет (L-10).
КП собирается в два шага: `offer_document` — содержимое и суммы до копейки,
`offer_pdf` и `offer_docx` — оформление.
"""

from rentab_core.export.docx import offer_docx
from rentab_core.export.offer import OfferDocument, offer_document
from rentab_core.export.pdf import offer_pdf
from rentab_core.export.xlsx import estimate_xlsx

__all__ = ["OfferDocument", "estimate_xlsx", "offer_document", "offer_docx", "offer_pdf"]
