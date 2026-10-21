"""Smoke-тесты интерфейса: страницы запускаются на ядре и считают как ядро.

Страницы гоняются через `streamlit.testing.v1.AppTest` без браузера:
каждая отрабатывает без исключений, кнопки вызывают ядро, результат
совпадает с прямым вызовом `rentab_core.calculate_estimate`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from rentab_core import calculate_estimate
from rentab_core.models import (
    Country,
    Disbursement,
    DisbursementKind,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    OsnoProfile,
    UsnProfile,
)
from rentab_core.money import Currency
from rentab_core.results import FixedPriceEstimate, HourlyEstimate
from tests.app.conftest import APP_DIR
from tests.core.factories import ASSOCIATE, PARTNER, RENT, basic_scenario, fixed, stage
from ui import state
from ui.labels import PRICING_LABELS

TIMEOUT_SECONDS = 60


def run_page(path: str, session: dict[str, Any] | None = None) -> AppTest:
    """Открывает страницу из точки входа с заданным состоянием сессии.

    Приложение запускается с Home.py и переходит на страницу так же, как
    пользователь из меню: иначе не работают ссылки между страницами.
    """
    app = AppTest.from_file(str(APP_DIR / "Home.py"), default_timeout=TIMEOUT_SECONDS)
    for key, value in (session or {}).items():
        app.session_state[key] = value
    app.run()
    if path != "Home.py":
        app.switch_page(path).run()
    assert not app.exception, app.exception
    return app


def firm_session(jurisdiction: Jurisdiction | None = None) -> dict[str, Any]:
    """Состояние после «Настройки»: команда и накладные контрольного сценария v0.3."""
    return {
        state.JURISDICTION: jurisdiction or Jurisdiction(),
        state.TEAM: [PARTNER, ASSOCIATE],
        state.OVERHEADS: [RENT],
        state.BILLABLE_HOURS: 160.0,
    }


def project_session(**extra: Any) -> dict[str, Any]:
    """Состояние «Проекта»: этапы контрольного сценария и название."""
    return {
        **firm_session(extra.pop("jurisdiction", None)),
        state.PROJECT_NAME: "Контрольный проект",
        state.PROJECT_STAGES: [
            stage("Анализ", "Иванов", 5),
            stage("Подготовка документов", "Петрова", 15),
        ],
        **extra,
    }


def click(app: AppTest, label: str) -> None:
    """Нажимает кнопку по подписи и перезапускает страницу."""
    (button,) = [b for b in app.button if b.label == label]
    button.click().run()
    assert not app.exception, app.exception


def test_home_page() -> None:
    app = run_page("Home.py")
    assert app.title[0].value == "⚖️ Rentab"


def test_setup_page_builds_jurisdiction_and_adds_employee() -> None:
    app = run_page("pages/01_Настройка.py")
    assert app.session_state[state.JURISDICTION] == Jurisdiction(rf=UsnProfile())

    app.radio(key="jurisdiction_choice").set_value(JurisdictionMode.BOTH).run()
    jurisdiction = app.session_state[state.JURISDICTION]
    assert jurisdiction.mode == JurisdictionMode.BOTH
    assert jurisdiction.kz == KzOurProfile()

    app.text_input(key="add_member_name").input("Иванов")
    app.number_input(key="add_member_billing").set_value(15_000.0)
    app.number_input(key="add_member_gross_salary").set_value(200_000.0)
    app.number_input(key="add_member_employer_rate_pct").set_value(30.0)
    app.run()
    click(app, "🧮 Рассчитать стоимость часа")
    assert app.session_state["fot_calc_result"].cost_rate_per_hour == Decimal(260_000) / 168
    click(app, "✅ Использовать как cost_rate")
    click(app, "➕ Добавить сотрудника")
    (employee,) = app.session_state[state.TEAM]
    assert employee.name == "Иванов"
    assert employee.billing_rate == Decimal(15_000)
    # Значение проходит через числовой виджет Streamlit (float), как в v0.3.
    assert abs(employee.cost_rate - Decimal(260_000) / 168) < Decimal("1e-9")


def test_setup_page_shows_overhead_rate() -> None:
    app = run_page("pages/01_Настройка.py", firm_session())
    rates = [m.value for m in app.metric if m.label.startswith("Ставка накладных")]
    assert rates == ["500.0"]


def test_project_page_requires_setup() -> None:
    app = run_page("pages/02_Проект.py")
    assert "Сначала заполните состав команды" in app.warning[0].value


@pytest.mark.parametrize(
    ("pricing_label", "expected_type"),
    [(PRICING_LABELS["hourly"], HourlyEstimate), (PRICING_LABELS["fixed"], FixedPriceEstimate)],
)
def test_project_page_calculates_like_core(pricing_label: str, expected_type: type[object]) -> None:
    app = run_page("pages/02_Проект.py", project_session(**{state.PRICING_MODEL: pricing_label}))
    click(app, "💰 Рассчитать смету")
    estimate = app.session_state[state.ESTIMATE]
    assert isinstance(estimate, expected_type)
    pricing = fixed() if expected_type is FixedPriceEstimate else None
    assert estimate == calculate_estimate(basic_scenario(pricing))


def test_project_page_reports_unreachable_fixed_price() -> None:
    session = project_session(
        jurisdiction=Jurisdiction(rf=OsnoProfile()),
        **{state.PRICING_MODEL: PRICING_LABELS["fixed"], state.TARGET_MARGIN_PCT: 80},
    )
    app = run_page("pages/02_Проект.py", session)
    click(app, "💰 Рассчитать смету")
    assert "Не удалось рассчитать фиксированную цену" in app.error[0].value
    assert state.ESTIMATE not in app.session_state


def test_project_page_in_both_mode_uses_selected_country() -> None:
    jurisdiction = Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile())
    app = run_page("pages/02_Проект.py", project_session(jurisdiction=jurisdiction))
    app.radio(key=state.CALC_COUNTRY).set_value(Country.KZ.value).run()
    click(app, "💰 Рассчитать смету")
    assert app.session_state[state.ESTIMATE].currency == Currency.KZT


def test_dashboard_requires_estimate() -> None:
    app = run_page("pages/03_Дашборд.py")
    assert "Сначала сформируйте смету" in app.info[0].value


def test_dashboard_hourly() -> None:
    scenario = basic_scenario(
        disbursements=(
            Disbursement(name="Пошлина", kind=DisbursementKind.BILLABLE, amount=Decimal(38_500)),
        )
    )
    app = run_page("pages/03_Дашборд.py", {state.ESTIMATE: calculate_estimate(scenario)})
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Выручка (без перевыст.), ₽"] == "165,000"
    assert metrics["Итоговый счёт клиенту, ₽"] == "203,500"
    assert metrics["Чистая прибыль (NNE), ₽"] == "52,600"
    assert app.success[0].value.endswith("Проект рентабелен")


def test_dashboard_fixed() -> None:
    estimate = calculate_estimate(basic_scenario(fixed()))
    assert isinstance(estimate, FixedPriceEstimate)
    app = run_page("pages/03_Дашборд.py", {state.ESTIMATE: estimate})
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Целевая маржа"] == "30%"
    assert metrics["Фиксированная цена, ₽"] == f"{estimate.fixed_price:,.0f}"


def test_dashboard_converts_currency_for_display_in_both_mode() -> None:
    jurisdiction = Jurisdiction(mode=JurisdictionMode.BOTH)
    estimate = calculate_estimate(basic_scenario(jurisdiction=jurisdiction))
    session = {
        state.ESTIMATE: estimate,
        state.EXCHANGE_RATE: 0.2,
        "display_currency_code": Currency.KZT.value,
    }
    app = run_page("pages/03_Дашборд.py", session)
    metrics = {m.label: m.value for m in app.metric}
    # 165 000 ₽ / 0,2 ₽ за тенге = 825 000 ₸.
    assert metrics["Выручка, ₸"] == "825,000"


def test_setup_page_clears_form_after_adding_employee() -> None:
    app = run_page("pages/01_Настройка.py")
    app.text_input(key="add_member_name").input("Петрова")
    app.number_input(key="add_member_billing").set_value(6_000.0)
    app.number_input(key="add_member_cost_rate").set_value(3_500.0)
    app.run()
    click(app, "➕ Добавить сотрудника")
    assert app.success[0].value == "Добавлен(а): Петрова"
    assert app.text_input(key="add_member_name").value == ""
    assert app.number_input(key="add_member_billing").value == 0.0
    assert app.number_input(key="add_member_cost_rate").value == 0.0
    click(app, "➕ Добавить сотрудника")
    assert app.error[0].value == "Имя не может быть пустым."
    assert len(app.session_state[state.TEAM]) == 1
