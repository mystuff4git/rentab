"""Реестр ставок: единственное место, где ядро берёт налоговые ставки.

Ставки, пороги, расчётные показатели (МРП, МЗП, МРОТ) и шкалы лежат в
YAML-файлах этого пакета (`ru_2026.yaml`, `kz_2026.yaml`). У каждой записи —
значение, дата начала действия, источник, флаг «нужна проверка» и признак
«нужна проверка юристом». См. `rentab_core.rates.loader`.
"""

from rentab_core.rates.loader import (
    REGISTRY_YEAR,
    Amount,
    AmountUnit,
    Index,
    Rate,
    RateTable,
    Scale,
    amount,
    amount_entry,
    index_value,
    load_rates,
    rate,
    rate_entry,
    scale,
)

__all__ = [
    "REGISTRY_YEAR",
    "Amount",
    "AmountUnit",
    "Index",
    "Rate",
    "RateTable",
    "Scale",
    "amount",
    "amount_entry",
    "index_value",
    "load_rates",
    "rate",
    "rate_entry",
    "scale",
]
