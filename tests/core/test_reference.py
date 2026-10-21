"""Справочники ядра: реестр ставок и каталоги пошлин."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.duties import duty_disbursements, load_catalog
from rentab_core.errors import ReferenceDataError
from rentab_core.models import Country, DisbursementKind
from rentab_core.money import Currency
from rentab_core.rates import RateTable, load_rates, rate
from rentab_core.reference import check_country, load_reference, parse_reference

# Ставки v0.3 (legacy/rentab_v03/modules/jurisdiction.py:29–102), которые
# использует ядро. Фаза 3 сверит их с первоисточниками.
V03_RATES = {
    Country.RF: {
        "usn.income": "0.06",
        "usn.income_minus_expenses": "0.15",
        "usn.vat_5": "0.05",
        "usn.vat_7": "0.07",
        "osno.profit": "0.25",
        "osno.vat": "0.22",
        "npd.individual": "0.04",
        "npd.legal_entity": "0.06",
        "ausn.income": "0.08",
        "ausn.social_contributions": "0",
        "social_contributions.standard": "0.30",
        "social_contributions.zero": "0",
    },
    Country.KZ: {
        "our.cit_too": "0.20",
        "our.iit_ip": "0.10",
        "vat_16": "0.16",
        "payroll.employer.oosms": "0.03",
        "payroll.employer.so": "0.05",
        "payroll.employer.opvr": "0.035",
    },
}


@pytest.mark.parametrize("country", list(Country))
def test_rate_registry_holds_v03_values(country: Country) -> None:
    table = load_rates(country)
    assert table.country == country
    assert {key: entry.value for key, entry in table.rates.items()} == {
        key: Decimal(value) for key, value in V03_RATES[country].items()
    }


@pytest.mark.parametrize("country", list(Country))
def test_every_v03_rate_is_flagged_for_review(country: Country) -> None:
    # Ставки не выдумываем: пока нет первоисточника — needs_review (CLAUDE.md, фаза 3).
    for key, entry in load_rates(country).rates.items():
        assert entry.needs_review, key
        assert entry.source_url is None, key
        assert "v0.3" in entry.note, key


def test_rate_lookup() -> None:
    assert rate(Country.RF, "usn.income") == Decimal("0.06")
    with pytest.raises(ReferenceDataError, match=r"нет ключа 'usn\.psn'"):
        rate(Country.RF, "usn.psn")


def test_reference_errors() -> None:
    with pytest.raises(ReferenceDataError, match="повреждён"):
        parse_reference("country: [", "bad.yaml", RateTable)
    with pytest.raises(ReferenceDataError, match="повреждён"):
        parse_reference("country: RF\nrates:\n  x: {value: 2}\n", "bad.yaml", RateTable)
    with pytest.raises(ReferenceDataError, match="не найден"):
        load_reference("rentab_core.rates", "missing.yaml", RateTable)
    with pytest.raises(ReferenceDataError, match="ожидалась KZ"):
        check_country(Country.RF, Country.KZ, "ru_2026.yaml")


@pytest.mark.parametrize(
    ("country", "currency"), [(Country.RF, Currency.RUB), (Country.KZ, Currency.KZT)]
)
def test_duty_catalogs_are_empty_like_v03(country: Country, currency: Currency) -> None:
    # KNOWN-BUG L-09 → фаза 3: все суммы в каталогах v0.3 нулевые.
    catalog = load_catalog(country)
    assert catalog.currency == currency
    assert len(catalog.duties) == 12
    assert {duty.amount for duty in catalog.duties} == {Decimal(0)}
    assert all(duty.needs_review for duty in catalog.duties)


def test_zero_and_unknown_duties_are_skipped() -> None:
    action = load_catalog(Country.RF).duties[0].action
    assert duty_disbursements(Country.RF, [action, "Нет такой пошлины"]) == []


def test_nonzero_duty_becomes_billable_disbursement(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = load_catalog(Country.KZ)
    duty = catalog.duties[0].model_copy(update={"amount": Decimal(15_000)})
    patched = catalog.model_copy(update={"duties": (duty, *catalog.duties[1:])})
    monkeypatch.setattr("rentab_core.duties.catalog.load_catalog", lambda _: patched)

    (item,) = duty_disbursements(Country.KZ, [duty.action])
    assert item.kind == DisbursementKind.BILLABLE
    assert item.amount == Decimal(15_000)
    assert item.currency == Currency.KZT
    assert item.name == duty.action
