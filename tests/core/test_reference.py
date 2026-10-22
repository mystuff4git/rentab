"""Справочники ядра: реестр ставок 2026 года и каталоги пошлин (фаза 3, L-08, L-09)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from rentab_core.duties import (
    ApplicantCategory,
    Duty,
    DutyCatalog,
    Quantity,
    duty_disbursements,
    load_catalog,
)
from rentab_core.errors import ReferenceDataError
from rentab_core.models import Country, DisbursementKind
from rentab_core.money import Currency
from rentab_core.rates import (
    REGISTRY_YEAR,
    RateTable,
    amount,
    amount_entry,
    index_value,
    load_rates,
    rate,
    rate_entry,
    scale,
)
from rentab_core.rates.loader import Rate, Scale
from rentab_core.reference import check_country, load_reference, parse_reference

# Ставки 2026 года, которые использует ядро (rates/*.yaml, docs/rates_verification.md).
REGISTRY_RATES = {
    Country.RF: {
        "usn.income": "0.06",
        "usn.income_minus_expenses": "0.15",
        "usn.min_tax": "0.01",
        "usn.contributions_deduction_cap": "0.50",
        "usn.vat_5": "0.05",
        "usn.vat_7": "0.07",
        "osno.profit": "0.25",
        "osno.vat": "0.22",
        "npd.individual": "0.04",
        "npd.legal_entity": "0.06",
        "ausn.income": "0.08",
        "ausn.social_contributions": "0",
        "social_contributions.standard": "0.30",
        "social_contributions.above_base": "0.151",
        "social_contributions.sme": "0.15",
        "social_contributions.zero": "0",
        "social_contributions.injury": "0.002",
    },
    Country.KZ: {
        "our.cit_too": "0.20",
        "our.iit_ip": "0.10",
        "our.iit_ip_high": "0.15",
        "vat_16": "0.16",
        "payroll.employer.oosms": "0.03",
        "payroll.employer.so": "0.05",
        "payroll.employer.opvr": "0.035",
        "payroll.employee.opv": "0.10",
        "payroll.employee.vosms": "0.02",
        "social_tax": "0.06",
    },
}
# Значения, не сверенные с первоисточником полностью: список меняется осознанно.
NEEDS_REVIEW = {
    Country.RF: {
        "social_contributions.sme",
        "social_contributions.zero",
        "social_contributions.sme_threshold",
    },
    Country.KZ: {
        "our.cit_too",
        "payroll.oosms.base_max",
        "payroll.vosms.base_max",
        "social_tax.base_min",
    },
}


@pytest.mark.parametrize("country", list(Country))
def test_rate_registry_values(country: Country) -> None:
    table = load_rates(country)
    assert table.country == country
    assert {key: entry.value for key, entry in table.rates.items()} == {
        key: Decimal(value) for key, value in REGISTRY_RATES[country].items()
    }


@pytest.mark.parametrize("country", list(Country))
def test_review_flags_and_sources(country: Country) -> None:
    # Ставки не выдумываем: без сверки с источником — needs_review (CLAUDE.md).
    table = load_rates(country)
    entries = {
        **table.rates,
        **table.amounts,
        **table.scales,
        **{
            f"{key}.{year}": value
            for key, index in table.indices.items()
            for year, value in index.values.items()
        },
    }
    assert {key for key, entry in entries.items() if entry.needs_review} == NEEDS_REVIEW[country]
    for key, entry in entries.items():
        assert entry.effective_from.year in (REGISTRY_YEAR, REGISTRY_YEAR - 1), key
        if not entry.needs_review:
            assert entry.source_url is not None, key
            assert entry.source, key


def test_amounts_resolve_indices() -> None:
    assert amount(Country.RF, "usn.income_limit") == Decimal(490_500_000)
    assert amount(Country.RF, "usn.headcount_limit") == 130
    assert amount(Country.RF, "social_contributions.sme_threshold") == Decimal("40639.5")
    assert amount(Country.KZ, "vat.registration_threshold") == Decimal(43_250_000)
    assert amount(Country.KZ, "vat.registration_threshold", 2025) == Decimal(39_320_000)
    assert amount(Country.KZ, "payroll.so.base_max") == Decimal(595_000)
    assert index_value(Country.KZ, "mrp") == 4_325
    assert index_value(Country.RF, "mrot") == 27_093
    assert amount_entry(Country.KZ, "payroll.oosms.base_max").needs_review
    assert rate_entry(Country.RF, "usn.min_tax").source == "НК РФ, ст. 346.18, п. 6"


def test_valid_until_marks_experimental_regimes() -> None:
    assert rate_entry(Country.RF, "ausn.income").valid_until == dt.date(2027, 12, 31)
    assert rate_entry(Country.RF, "npd.individual").valid_until == dt.date(2028, 12, 31)
    assert rate_entry(Country.RF, "usn.income").valid_until is None


def test_ndfl_scale() -> None:
    brackets = scale(Country.RF, "ndfl.resident").brackets
    assert [(b.up_to, b.rate) for b in brackets] == [
        (Decimal(2_400_000), Decimal("0.13")),
        (Decimal(5_000_000), Decimal("0.15")),
        (Decimal(20_000_000), Decimal("0.18")),
        (Decimal(50_000_000), Decimal("0.20")),
        (None, Decimal("0.22")),
    ]


def test_lookup_errors() -> None:
    assert rate(Country.RF, "usn.income") == Decimal("0.06")
    with pytest.raises(ReferenceDataError, match=r"ставок RF нет ключа 'usn\.psn'"):
        rate(Country.RF, "usn.psn")
    with pytest.raises(ReferenceDataError, match="сумм KZ нет ключа"):
        amount(Country.KZ, "usn.income_limit")
    with pytest.raises(ReferenceDataError, match="показателей RF нет ключа 'mrp'"):
        index_value(Country.RF, "mrp")
    with pytest.raises(ReferenceDataError, match="нет значения МРП за 2030 год"):
        index_value(Country.KZ, "mrp", 2030)
    with pytest.raises(ReferenceDataError, match="шкал KZ нет ключа"):
        scale(Country.KZ, "ndfl.resident")


ENTRY = {"effective_from": "2026-01-01", "needs_review": True}


def test_confirmed_entry_needs_source() -> None:
    with pytest.raises(ValueError, match="needs_review: false"):
        Rate.model_validate({**ENTRY, "value": "0.1", "needs_review": False})
    with pytest.raises(ValueError, match="needs_review: false"):
        Rate.model_validate(
            {**ENTRY, "value": "0.1", "needs_review": False, "source_url": "https://example.org"}
        )
    confirmed = Rate.model_validate(
        {
            **ENTRY,
            "value": "0.1",
            "needs_review": False,
            "source": "Закон",
            "source_url": "https://example.org",
        }
    )
    assert not confirmed.needs_review


@pytest.mark.parametrize(
    ("brackets", "message"),
    [
        ([{"up_to": 100, "rate": "0.1"}], "без верхней границы"),
        ([], "без верхней границы"),
        ([{"up_to": None, "rate": "0.1"}, {"up_to": None, "rate": "0.2"}], "без верхней границы"),
        (
            [
                {"up_to": 200, "rate": "0.1"},
                {"up_to": 100, "rate": "0.2"},
                {"up_to": None, "rate": "0.3"},
            ],
            "строго возрастать",
        ),
    ],
)
def test_scale_validation(brackets: list[dict[str, object]], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Scale.model_validate({**ENTRY, "brackets": brackets})


def test_reference_errors() -> None:
    with pytest.raises(ReferenceDataError, match="повреждён"):
        parse_reference("country: [", "bad.yaml", RateTable)
    with pytest.raises(ReferenceDataError, match="повреждён"):
        parse_reference("country: RF\nrates:\n  x: {value: 2}\n", "bad.yaml", RateTable)
    with pytest.raises(ReferenceDataError, match="не найден"):
        load_reference("rentab_core.rates", "missing.yaml", RateTable)
    with pytest.raises(ReferenceDataError, match="ожидалась KZ"):
        check_country(Country.RF, Country.KZ, "ru_2026.yaml")


# ---------------------------------------------------------------------------
# Каталоги пошлин (L-09)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("country", "currency", "size", "vat_included"),
    [(Country.RF, Currency.RUB, 15, False), (Country.KZ, Currency.KZT, 13, True)],
)
def test_duty_catalogs(country: Country, currency: Currency, size: int, vat_included: bool) -> None:
    catalog = load_catalog(country)
    assert catalog.currency == currency
    assert len(catalog.duties) == size
    assert catalog.vat_included == vat_included
    # Сумма без источника — 0 и needs_review; сверенная — с источником.
    for duty in catalog.duties:
        if duty.amount == 0:
            assert duty.needs_review, duty.action
        if not duty.needs_review:
            assert duty.source_url is not None, duty.action


def test_rospatent_trademark_duties_depend_on_classes() -> None:
    catalog = load_catalog(Country.RF)
    filing = catalog.find("Регистрация заявки на товарный знак и формальная экспертиза")
    examination = catalog.find("Экспертиза заявленного обозначения (товарный знак)")
    registration = catalog.find(
        "Регистрация товарного знака и выдача свидетельства в электронной форме"
    )
    assert filing is not None
    assert examination is not None
    assert registration is not None
    assert filing.total() == 4_000
    assert filing.total({Quantity.CLASSES: 3}) == 6_000
    assert examination.total() == 13_000
    assert examination.total({Quantity.CLASSES: 3, Quantity.ITEMS_OVER_LIMIT: 4}) == 20_000
    assert registration.total({Quantity.CLASSES: 5}) == 18_000
    assert registration.total({Quantity.CLASSES: 7}) == 22_000
    assert examination.quantities == (Quantity.CLASSES, Quantity.ITEMS_OVER_LIMIT)


def test_v03_names_still_resolve() -> None:
    (filing, examination) = duty_disbursements(
        Country.RF,
        [
            "Подача заявки на товарный знак (1 класс МКТУ)",
            "Экспертиза заявки на товарный знак по существу (1 класс МКТУ)",
        ],
    )
    assert filing.name == "Регистрация заявки на товарный знак и формальная экспертиза"
    assert (filing.amount, examination.amount) == (Decimal(4_000), Decimal(13_000))


def test_kazpatent_price_depends_on_applicant_category() -> None:
    action = "Подача заявки на товарный знак и экспертиза (до 3 классов МКТУ)"
    amounts = {
        category: duty_disbursements(Country.KZ, [action], category=category)[0].amount
        for category in ApplicantCategory
    }
    assert amounts == {
        ApplicantCategory.LEGAL_ENTITY: Decimal("117812.50"),
        ApplicantCategory.SME: Decimal("88359.38"),
        ApplicantCategory.INDIVIDUAL: Decimal("82468.75"),
    }
    (item,) = duty_disbursements(Country.KZ, [action])
    assert item.kind == DisbursementKind.BILLABLE
    assert item.currency == Currency.KZT


def test_zero_and_unknown_duties_are_skipped() -> None:
    assert (
        duty_disbursements(
            Country.RF, ["Регистрация изобретения и выдача патента", "Нет такой пошлины"]
        )
        == []
    )


def test_duty_without_source_cannot_be_confirmed() -> None:
    with pytest.raises(ValueError, match="needs_review: false без source_url"):
        Duty.model_validate({"action": "x", "amount": 1, "source_url": None, "needs_review": False})


def test_catalog_lookup_prefers_last_duplicate() -> None:
    first = Duty(action="Пошлина", amount=Decimal(1), source_url=None, needs_review=True)
    second = first.model_copy(update={"amount": Decimal(2)})
    catalog = DutyCatalog(country=Country.RF, currency=Currency.RUB, duties=(first, second))
    assert catalog.find("Пошлина") == second
    assert catalog.amounts() == {"Пошлина": Decimal(2)}
