"""Публичный API пакета и упаковка справочников."""

from __future__ import annotations

from importlib import resources

import rentab_core


def test_public_api() -> None:
    assert callable(rentab_core.calculate_estimate)
    assert set(rentab_core.__all__) >= {
        "__version__",
        "calculate_estimate",
        "dashboard",
        "estimate_xlsx",
    }


def test_reference_data_ships_with_package() -> None:
    rates = resources.files("rentab_core.rates")
    duties = resources.files("rentab_core.duties.data")
    for name in ("ru_2026.yaml", "kz_2026.yaml"):
        assert rates.joinpath(name).is_file()
    for name in ("rospatent_2026.yaml", "kazpatent_2026.yaml"):
        assert duties.joinpath(name).is_file()
