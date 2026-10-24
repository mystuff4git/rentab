"""Формат чисел интерфейса: «1 234 567 ₽», минус «−», дельты `st.metric` с ASCII-минусом."""

from __future__ import annotations

from decimal import Decimal

import pytest

from rentab_core.errors import FixedPriceError
from rentab_core.models import OsnoProfile, Role, UsnObject, UsnProfile, UsnVat
from rentab_core.results import StageFlag
from ui.labels import (
    MINUS,
    NBSP,
    employee_title,
    fixed_price_error,
    flag_badge,
    hours_label,
    metric_delta,
    metric_points,
    money,
    money_rate,
    number_label,
    pct_label,
    points_label,
    regime_label,
    signed_hours,
    signed_money,
    signed_pct,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (money(Decimal("1234567.49"), "₽"), "1 234 567 ₽"),
        (money(Decimal("-2200"), "₽"), "−2 200 ₽"),
        (money(Decimal("-0.4"), "₽"), "0 ₽"),
        (money_rate(Decimal("4125"), "₽"), "4 125 ₽/ч"),
        (number_label(Decimal(7), Decimal("0.01")), "7,00"),
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
    ],
)
def test_numbers_use_space_groups_and_typographic_minus(text: str, expected: str) -> None:
    assert text.replace(NBSP, " ") == expected
    # Разряды и единицы — неразрывным пробелом: число не переносится на узком экране.
    assert " " not in text
    assert "-" not in text


def test_metric_deltas_keep_ascii_minus() -> None:
    # Streamlit выбирает стрелку и цвет дельты по ведущему ASCII-минусу.
    assert metric_delta(Decimal("-69056"), "₽") == f"-69{NBSP}056{NBSP}₽"
    assert metric_delta(Decimal("0.4"), "₽") is None
    assert metric_points(Decimal("-0.759")) == f"-75,90{NBSP}п.{NBSP}п."
    assert MINUS not in (metric_delta(Decimal(-1), "₽") or "")


def test_regime_labels_are_human_readable() -> None:
    # L-18: в подписи нет внутренних ключей («УСН income (6%)»).
    assert regime_label(UsnProfile()) == "УСН «доходы» 6% без НДС"
    profile = UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES, vat=UsnVat.VAT_5)
    assert regime_label(profile) == "УСН «доходы − расходы» 15% + НДС 5%"


def test_fixed_price_error_uses_interface_regime_label() -> None:
    exc = FixedPriceError("ядро", target_margin=Decimal("0.8"), tax_rate=Decimal("0.25"))
    message = fixed_price_error(exc, OsnoProfile()).replace(NBSP, " ")
    assert message == (
        "целевая маржа 80,0% недостижима при режиме «ОСНО 25% + НДС 22%»: "
        "маржа + ставка налога = 105,0% ≥ 100%."
    )
    assert fixed_price_error(FixedPriceError("цель вне [0; 1)"), OsnoProfile()) == (
        "цель вне [0; 1)"
    )


def test_badges_and_employee_titles() -> None:
    assert flag_badge(StageFlag.YELLOW) == ":orange-badge[▲ Низкая маржа]"
    assert employee_title("Партнёр", Role.PARTNER) == "Партнёр"
    assert employee_title("Юрист", Role.ASSOCIATE) == "Юрист (Младший юрист)"
