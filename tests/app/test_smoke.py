"""Smoke-тесты интерфейса: страницы запускаются на ядре и считают как ядро.

Страницы гоняются через `streamlit.testing.v1.AppTest` без браузера:
каждая отрабатывает без исключений, кнопки вызывают ядро, результат
совпадает с прямым вызовом `rentab_core.calculate_estimate`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from rentab_core import calculate_estimate
from rentab_core.duties import ApplicantCategory, Quantity
from rentab_core.errors import ExchangeRateFetchError
from rentab_core.fx import RateSource, RatesQuote
from rentab_core.models import (
    CappedPricing,
    Country,
    Disbursement,
    DisbursementKind,
    ExchangeRates,
    Jurisdiction,
    JurisdictionMode,
    KzOurProfile,
    MonthlyCost,
    NpdProfile,
    OsnoProfile,
    PricingModel,
    Salary,
    SocialContributions,
    UsnProfile,
    UsnVat,
)
from rentab_core.money import Currency
from tests.app.conftest import APP_DIR
from tests.core.factories import ASSOCIATE, PARTNER, RENT, basic_scenario, d, fixed, stage
from ui import state

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
    }


def project_session(**extra: Any) -> dict[str, Any]:
    """Состояние «Проекта»: этапы контрольного сценария и название."""
    return {
        **firm_session(extra.pop("jurisdiction", None)),
        state.PROJECT_NAME: "Контрольный проект",
        state.PROJECT_STAGES: [
            stage("Анализ", ("Иванов", 5)),
            stage("Подготовка документов", ("Петрова", 15)),
        ],
        **extra,
    }


def click(app: AppTest, label: str) -> None:
    """Нажимает кнопку по подписи и перезапускает страницу."""
    (button,) = [b for b in app.button if b.label == label]
    button.click().run()
    assert not app.exception, app.exception


def metrics(app: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in app.metric}


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

    app.text_input(key="add_member_name").input("Петрова")
    app.number_input(key="add_member_billing").set_value(6_000.0)
    app.number_input(key="add_member_gross_salary").set_value(200_000.0)
    app.number_input(key="add_member_employer_rate_pct").set_value(30.0)
    app.run()
    # Себестоимость биллируемого часа: 260 000 / (168 × 0,65), контрактного — / 168.
    preview = metrics(app)
    assert preview["Контрактный час, ₽"] == "1,548"
    assert preview["Биллируемый час, ₽"] == "2,381"
    click(app, "➕ Добавить сотрудника")
    (employee,) = app.session_state[state.TEAM]
    assert employee.name == "Петрова"
    assert employee.billing_rate == Decimal(6_000)
    assert employee.compensation == Salary(
        gross_salary=Decimal(200_000), employer_contribution_rate=Decimal("0.3")
    )
    assert employee.contract_hours_per_month == Decimal(168)
    assert employee.target_utilization == Decimal("0.65")
    assert employee.currency == Currency.RUB


def test_setup_page_adds_partner_with_normative_compensation() -> None:
    app = run_page("pages/01_Настройка.py")
    app.text_input(key="add_member_name").input("Иванов")
    app.radio(key="add_member_compensation").set_value("monthly").run()
    app.number_input(key="add_member_monthly_cost").set_value(400_000.0)
    app.number_input(key="add_member_contract_hours").set_value(160.0)
    app.number_input(key="add_member_utilization_pct").set_value(62.5)
    app.run()
    assert metrics(app)["Биллируемый час, ₽"] == "4,000"
    click(app, "➕ Добавить сотрудника")
    (employee,) = app.session_state[state.TEAM]
    assert employee.compensation == MonthlyCost(amount=Decimal(400_000))
    assert employee.target_utilization == Decimal("0.625")


def test_setup_page_contribution_rate_follows_employee_country() -> None:
    session = {state.JURISDICTION: Jurisdiction(mode=JurisdictionMode.BOTH)}
    app = run_page("pages/01_Настройка.py", session)
    app.radio(key="jurisdiction_choice").set_value(JurisdictionMode.BOTH).run()
    # Без оклада — номинальная ставка РФ: 30% + 0,2% на травматизм.
    assert app.number_input(key="add_member_employer_rate_pct").value == pytest.approx(30.2)
    app.selectbox(key="add_member_currency").set_value(Currency.KZT.value).run()
    # РК, ТОО: 3% + 5% × 0,9 + 3,5% + 6% × 0,88.
    assert app.number_input(key="add_member_employer_rate_pct").value == pytest.approx(16.28)


def test_setup_page_contribution_rate_follows_salary() -> None:
    app = run_page("pages/01_Настройка.py")
    app.number_input(key="add_member_gross_salary").set_value(300_000.0).run()
    # 3,6 млн ₽ за год: 30% до базы 2 979 000, 15,1% сверх, 0,2% на травматизм.
    rate = app.number_input(key="add_member_employer_rate_pct").value
    assert rate == pytest.approx(27.63)
    assert any("Взносы за год (общий тариф): 994 671 ₽" in c.value for c in app.caption)
    # Ручная ставка не пересчитывается, пока её не вернули к расчёту.
    app.number_input(key="add_member_employer_rate_pct").set_value(25.0).run()
    app.number_input(key="add_member_gross_salary").set_value(100_000.0).run()
    assert app.number_input(key="add_member_employer_rate_pct").value == 25.0
    click(app, "↺ Ставка по расчёту")
    assert app.number_input(key="add_member_employer_rate_pct").value == pytest.approx(30.2)


def test_setup_page_adds_profit_paid_partner() -> None:
    app = run_page("pages/01_Настройка.py")
    app.text_input(key="add_member_name").input("Иванов")
    app.radio(key="add_member_compensation").set_value("monthly").run()
    app.number_input(key="add_member_monthly_cost").set_value(400_000.0)
    app.checkbox(key="add_member_paid_from_profit").check()
    app.run()
    click(app, "➕ Добавить сотрудника")
    (employee,) = app.session_state[state.TEAM]
    assert employee.paid_from_profit
    assert not app.checkbox(key="add_member_paid_from_profit").value


def test_setup_page_checks_regime_eligibility() -> None:
    app = run_page("pages/01_Настройка.py")
    app.radio(key="rf_regime_choice").set_value("npd").run()
    app.number_input(key=state.FACT_INCOME_RF).set_value(3_000_000.0).run()
    errors = [e.value for e in app.error]
    assert any("НПД применяют только физлица и ИП" in e for e in errors)
    assert any("2 400 000 ₽" in e for e in errors)
    assert isinstance(app.session_state[state.JURISDICTION].rf, NpdProfile)

    app.radio(key="rf_regime_choice").set_value("osno").run()
    assert app.success[0].value == "Выбранный режим применим по введённым данным."
    app.selectbox(key="rf_osno_sc").set_value(SocialContributions.SME).run()
    jurisdiction = app.session_state[state.JURISDICTION]
    assert jurisdiction.rf == OsnoProfile(social_contributions=SocialContributions.SME)


def test_setup_page_loads_exchange_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    quote = RatesQuote(
        rates=ExchangeRates(rub_per_kzt=d("0.1543"), rub_per_usd=d("81.2345")),
        source=RateSource.CBR,
        rate_date=dt.date(2026, 10, 22),
        requested=dt.date(2026, 10, 22),
    )
    monkeypatch.setattr("rentab_core.fx.fetch_rates", lambda on: quote)
    app = run_page("pages/01_Настройка.py")
    click(app, "⬇️ Загрузить курсы ЦБ РФ")
    assert app.number_input(key=state.RUB_PER_KZT).value == pytest.approx(0.1543)
    assert app.number_input(key=state.RUB_PER_USD).value == pytest.approx(81.2345)
    assert any("Загружены курсы ЦБ РФ на 22.10.2026" in c.value for c in app.caption)


def test_setup_page_keeps_manual_rates_when_loading_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(on: dt.date) -> RatesQuote:
        raise ExchangeRateFetchError("Курсы не загружены, введите их вручную.")

    monkeypatch.setattr("rentab_core.fx.fetch_rates", offline)
    app = run_page("pages/01_Настройка.py")
    click(app, "⬇️ Загрузить курсы ЦБ РФ")
    assert "введите их вручную" in app.warning[0].value
    assert app.number_input(key=state.RUB_PER_KZT).value == pytest.approx(0.18)


def test_setup_page_shows_capacity_and_overhead_rate() -> None:
    app = run_page("pages/01_Настройка.py", firm_session())
    values = metrics(app)
    assert values["Биллируемая ёмкость"] == "160.0 ч/мес"
    assert values["Ставка накладных, ₽/ч"] == "500.0"
    assert values["Утилизация фирмы"] == "50.0%"


def test_setup_page_clears_form_after_adding_employee() -> None:
    app = run_page("pages/01_Настройка.py")
    app.text_input(key="add_member_name").input("Петрова")
    app.number_input(key="add_member_billing").set_value(6_000.0)
    app.number_input(key="add_member_gross_salary").set_value(100_000.0)
    app.run()
    click(app, "➕ Добавить сотрудника")
    assert app.success[0].value == "Добавлен(а): Петрова"
    assert app.text_input(key="add_member_name").value == ""
    assert app.number_input(key="add_member_billing").value == 0.0
    assert app.number_input(key="add_member_gross_salary").value == 0.0
    click(app, "➕ Добавить сотрудника")
    assert app.error[0].value == "Имя не может быть пустым."
    assert len(app.session_state[state.TEAM]) == 1


def test_project_page_requires_setup() -> None:
    app = run_page("pages/02_Проект.py")
    assert "Сначала заполните состав команды" in app.warning[0].value


@pytest.mark.parametrize(
    ("kind", "extra", "pricing"),
    [
        (state.PRICING_HOURLY, {}, None),
        (state.PRICING_CAPPED, {state.CAP_AMOUNT: 150_000.0}, CappedPricing(cap=d(150_000))),
        (state.PRICING_FIXED, {}, fixed()),
        (state.PRICING_AGREED, {state.AGREED_PRICE: 300_000.0}, fixed(300_000)),
    ],
)
def test_project_page_calculates_like_core(
    kind: str, extra: dict[str, Any], pricing: PricingModel | None
) -> None:
    app = run_page("pages/02_Проект.py", project_session(**{state.PRICING_MODEL: kind, **extra}))
    click(app, "💰 Рассчитать смету")
    estimate = app.session_state[state.ESTIMATE]
    expected = calculate_estimate(basic_scenario(pricing))
    assert estimate == expected.model_copy(update={"project_name": "Контрольный проект"})


def test_project_page_adds_second_assignee_to_stage() -> None:
    # L-05: «партнёр проверяет 2 ч» — второй исполнитель на том же этапе.
    app = run_page("pages/02_Проект.py", project_session())
    app.selectbox(key="assignment_stage").set_value("Подготовка документов")
    app.selectbox(key="assignment_executor").set_value("Иванов")
    app.number_input(key="assignment_hours").set_value(2.0)
    click(app, "➕ Добавить")
    stages = app.session_state[state.PROJECT_STAGES]
    assert [s.name for s in stages] == ["Анализ", "Подготовка документов"]
    assert [(a.employee, a.hours) for a in stages[1].assignments] == [
        ("Петрова", Decimal(15)),
        ("Иванов", Decimal(2)),
    ]


def test_project_page_new_stage_is_selectable_at_once() -> None:
    app = run_page("pages/02_Проект.py", project_session())
    app.text_input(key="assignment_new_stage").input("Ответ на уведомление")
    app.number_input(key="assignment_hours").set_value(3.0)
    click(app, "➕ Добавить")
    assert app.success[0].value == "Добавлен этап: Ответ на уведомление"
    assert "Ответ на уведомление" in app.selectbox(key="assignment_stage").options
    click(app, "➕ Добавить")
    assert app.error[0].value == "Часы должны быть больше нуля."


def test_project_page_vat_on_top() -> None:
    session = project_session(jurisdiction=Jurisdiction(rf=UsnProfile(vat=UsnVat.VAT_5)))
    app = run_page("pages/02_Проект.py", session)
    click(app, "💰 Рассчитать смету")
    values = metrics(app)
    assert values["Выручка без НДС, ₽"] == "165,000"
    assert values["НДС, ₽"] == "8,250"
    assert values["К оплате клиентом, ₽"] == "173,250"


def test_project_page_reports_unreachable_fixed_price() -> None:
    session = project_session(
        jurisdiction=Jurisdiction(rf=OsnoProfile()),
        **{state.PRICING_MODEL: state.PRICING_FIXED, state.TARGET_MARGIN_PCT: 80},
    )
    app = run_page("pages/02_Проект.py", session)
    click(app, "💰 Рассчитать смету")
    assert "Не удалось рассчитать фиксированную цену" in app.error[0].value
    assert state.ESTIMATE not in app.session_state


def test_project_page_in_both_mode_converts_to_project_currency() -> None:
    jurisdiction = Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile())
    app = run_page("pages/02_Проект.py", project_session(jurisdiction=jurisdiction))
    app.radio(key=state.CALC_COUNTRY).set_value(Country.KZ.value).run()
    click(app, "💰 Рассчитать смету")
    estimate = app.session_state[state.ESTIMATE]
    assert estimate.currency == Currency.KZT
    # Рублёвые ставки команды пересчитаны по 0,18 ₽ за 1 ₸.
    assert abs(estimate.revenue - d(165_000) / d("0.18")) < d("1e-18")


def test_project_page_reports_missing_exchange_rate() -> None:
    jurisdiction = Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile())
    session = project_session(jurisdiction=jurisdiction, **{state.RUB_PER_KZT: 0.0})
    app = run_page("pages/02_Проект.py", session)
    app.radio(key=state.CALC_COUNTRY).set_value(Country.KZ.value).run()
    click(app, "💰 Рассчитать смету")
    assert "Укажите курс ₽ за 1 ₸" in app.error[0].value


def test_project_page_duties_depend_on_classes() -> None:
    session = project_session(
        **{
            state.SELECTED_DUTIES: {
                "RF": [
                    "Регистрация заявки на товарный знак и формальная экспертиза",
                    "Экспертиза заявленного обозначения (товарный знак)",
                ],
                "KZ": [],
            },
            state.duty_quantity_key(Country.RF, Quantity.CLASSES): 3,
        }
    )
    app = run_page("pages/02_Проект.py", session)
    assert app.number_input(key=state.duty_quantity_key(Country.RF, Quantity.CLASSES)).value == 3
    click(app, "💰 Рассчитать смету")
    estimate = app.session_state[state.ESTIMATE]
    # 4 000 + 1 000 × 2 класса; 13 000 + 2 500 × 2 класса.
    assert estimate.disbursements_billed == d(24_000)


def test_project_page_kazpatent_price_depends_on_applicant() -> None:
    jurisdiction = Jurisdiction(mode=JurisdictionMode.KZ)
    action = "Подача заявки на товарный знак и экспертиза (до 3 классов МКТУ)"
    session = project_session(
        jurisdiction=jurisdiction,
        **{
            state.SELECTED_DUTIES: {"RF": [], "KZ": [action]},
            state.duty_category_key(Country.KZ): ApplicantCategory.SME,
        },
    )
    app = run_page("pages/02_Проект.py", session)
    click(app, "💰 Рассчитать смету")
    assert app.session_state[state.ESTIMATE].disbursements_billed == d("88359.38")


def test_project_page_shows_contribution_deduction() -> None:
    lawyer = ASSOCIATE.model_copy(
        update={
            "compensation": Salary(gross_salary=d(200_000), employer_contribution_rate=d("0.3"))
        }
    )
    session = project_session(**{state.TEAM: [PARTNER, lawyer]})
    app = run_page("pages/02_Проект.py", session)
    click(app, "💰 Рассчитать смету")
    estimate = app.session_state[state.ESTIMATE]
    assert estimate.tax.deduction > 0
    assert any("уменьшен на страховые взносы" in c.value for c in app.caption)


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
    values = metrics(app)
    assert values["Выручка без НДС, ₽"] == "165,000"
    assert values["К оплате клиентом, ₽"] == "203,500"
    assert values["Чистая прибыль (NNE), ₽"] == "52,600"
    assert values["Эффективная ставка налога"] == "6.0%"
    assert app.success[0].value.endswith("Проект рентабелен")


def test_dashboard_fixed() -> None:
    estimate = calculate_estimate(basic_scenario(fixed()))
    app = run_page("pages/03_Дашборд.py", {state.ESTIMATE: estimate})
    values = metrics(app)
    assert values["Выручка без НДС, ₽"] == f"{estimate.revenue:,.0f}"
    assert len(app.dataframe) >= 1
