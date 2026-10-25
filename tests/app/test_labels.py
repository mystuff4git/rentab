"""Подписи интерфейса: формат чисел ядра, дельты `st.metric` с ASCII-минусом, бейджи."""

from __future__ import annotations

from decimal import Decimal

from rentab_core import formatting
from rentab_core import labels as core_labels
from rentab_core.errors import FixedPriceError
from rentab_core.models import OsnoProfile, Role
from rentab_core.results import StageFlag
from ui import labels
from ui.labels import (
    MINUS,
    NBSP,
    employee_title,
    fixed_price_error,
    flag_badge,
    metric_delta,
    metric_points,
    money,
)


def test_interface_uses_core_formatting() -> None:
    # Интерфейс, КП и смета пишут числа и подписи режимов одними функциями ядра.
    assert labels.money is formatting.money
    assert labels.pct_label is formatting.pct_label
    assert labels.hours_label is formatting.hours_label
    assert labels.regime_label is core_labels.regime_label
    assert money(Decimal("1234567.49"), "₽").replace(NBSP, " ") == "1 234 567 ₽"


def test_metric_deltas_keep_ascii_minus() -> None:
    # Streamlit выбирает стрелку и цвет дельты по ведущему ASCII-минусу.
    assert metric_delta(Decimal("-69056"), "₽") == f"-69{NBSP}056{NBSP}₽"
    assert metric_delta(Decimal("0.4"), "₽") is None
    assert metric_points(Decimal("-0.759")) == f"-75,90{NBSP}п.{NBSP}п."
    assert MINUS not in (metric_delta(Decimal(-1), "₽") or "")


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
