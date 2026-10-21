"""Реестр ставок: единственное место, где ядро берёт налоговые ставки.

Ставки лежат в YAML-файлах этого пакета (`ru_2026.yaml`, `kz_2026.yaml`).
У каждой ставки — значение, дата начала действия, ссылка на первоисточник и
флаг «нужна проверка». См. `rentab_core.rates.loader`.
"""

from rentab_core.rates.loader import Rate, RateTable, load_rates, rate

__all__ = ["Rate", "RateTable", "load_rates", "rate"]
