"""Патентные пошлины: каталоги и перевод выбранных пошлин в расходы проекта."""

from rentab_core.duties.catalog import Duty, DutyCatalog, duty_disbursements, load_catalog

__all__ = ["Duty", "DutyCatalog", "duty_disbursements", "load_catalog"]
