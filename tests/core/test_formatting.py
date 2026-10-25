"""Формат чисел и подписи режимов ядра: «1 234 567 ₽», минус «−», L-18."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.formatting import (
    KOPECK,
    NBSP,
    hours_label,
    money,
    money_rate,
    number_label,
    number_text,
    pct_label,
    points_label,
    rate_text,
    signed_hours,
    signed_money,
    signed_pct,
)
from rentab_core.labels import regime_label
from rentab_core.models import (
    AusnProfile,
    KzForm,
    KzOurProfile,
    KzVat,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    TaxProfile,
    UsnObject,
    UsnProfile,
    UsnVat,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (money(Decimal("1234567.49"), "₽"), "1 234 567 ₽"),
        (money(Decimal("-2200"), "₽"), "−2 200 ₽"),
        (money(Decimal("-0.4"), "₽"), "0 ₽"),
        (money(Decimal("40669.934640"), "₽", KOPECK), "40 669,93 ₽"),
        (money(Decimal("55000"), "₸", KOPECK), "55 000,00 ₸"),
        (money(Decimal("0.005"), "₽", KOPECK), "0,01 ₽"),
        (money_rate(Decimal("4125"), "₽"), "4 125 ₽/ч"),
        (number_label(Decimal(7), Decimal("0.01")), "7,00"),
        (number_text(Decimal("-1234.5")), "−1 234,5"),
        (number_text(Decimal("-0")), "0"),
        (pct_label(Decimal("0.588")), "58,80%"),
        (pct_label(Decimal("-0.0073333")), "−0,73%"),
        (pct_label(None), "—"),
        (hours_label(Decimal("40")), "40 ч"),
        (hours_label(Decimal("159.646")), "159,6 ч"),
        (signed_hours(Decimal("-0.5")), "−0,5 ч"),
        (signed_money(Decimal("5650"), "₸"), "+5 650 ₸"),
        (signed_money(Decimal("-227700"), "₽"), "−227 700 ₽"),
        (signed_pct(Decimal("3.991")), "+399%"),
        (points_label(Decimal("-0.759")), "−75,90 п. п."),
        (rate_text(Decimal("0.151")), "15,1"),
        (rate_text(Decimal("0.05")), "5"),
    ],
)
def test_numbers_use_space_groups_and_typographic_minus(text: str, expected: str) -> None:
    assert text.replace(NBSP, " ") == expected
    # Разряды и единицы — неразрывным пробелом: число не переносится.
    assert " " not in text
    assert "-" not in text


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (UsnProfile(), "УСН «доходы» 6% без НДС"),
        (
            UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES, vat=UsnVat.VAT_5),
            "УСН «доходы − расходы» 15% + НДС 5%",
        ),
        (AusnProfile(), "АУСН 8%"),
        (NpdProfile(), "НПД 6% (юрлица)"),
        (NpdProfile(client_type=NpdClientType.INDIVIDUAL), "НПД 4% (физлица)"),
        (OsnoProfile(), "ОСНО 25% + НДС 22%"),
        (KzOurProfile(), "ОУР РК, ТОО (КПН 20%) без НДС"),
        (KzOurProfile(form=KzForm.IP, vat=KzVat.VAT_16), "ОУР РК, ИП (ИПН 10%) + НДС 16%"),
    ],
)
def test_regime_labels_are_human_readable(profile: TaxProfile, expected: str) -> None:
    # L-18: в подписи нет внутренних ключей («УСН income (6%)»).
    assert regime_label(profile) == expected
