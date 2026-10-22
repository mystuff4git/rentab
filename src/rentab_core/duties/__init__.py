"""Патентные пошлины: каталоги и перевод выбранных пошлин в расходы проекта."""

from rentab_core.duties.catalog import (
    APPLICANT_CATEGORY_LABELS,
    QUANTITY_LABELS,
    ApplicantCategory,
    Duty,
    DutyCatalog,
    DutyExtra,
    Quantity,
    duty_disbursements,
    load_catalog,
)

__all__ = [
    "APPLICANT_CATEGORY_LABELS",
    "QUANTITY_LABELS",
    "ApplicantCategory",
    "Duty",
    "DutyCatalog",
    "DutyExtra",
    "Quantity",
    "duty_disbursements",
    "load_catalog",
]
