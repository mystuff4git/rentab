"""Страницы аналитики: «Чувствительность», «Сравнение», «План–факт» (фаза 5).

Правку ячеек `st.data_editor` AppTest не моделирует: редактор факта
проверяется в браузере (`docs/screenshots/phase-5/shots.js`), разбор его строк —
в `test_state.py`. Здесь — показ, ползунки, форма «вне плана», «вручную» и
переключение проектов.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from rentab_core.demo import demo_workspace
from rentab_core.models import ActualHours
from tests.app.helpers import click, metrics, run_page, slot_ids, switch
from tests.app.test_projects import PROJECT_PAGE, demo_app
from tests.core.factories import d
from ui import state

SENSITIVITY_PAGE = "pages/04_Чувствительность.py"
COMPARE_PAGE = "pages/05_Сравнение.py"
PLAN_FACT_PAGE = "pages/06_План–факт.py"


def column(app: AppTest, index: int, name: str) -> list[str]:
    return list(app.dataframe[index].value[name])


# ---------------------------------------------------------------------------
# Чувствительность
# ---------------------------------------------------------------------------
def test_sensitivity_shows_slide3_headroom_and_tornado() -> None:
    app = demo_app(SENSITIVITY_PAGE)
    assert column(app, 0, "Чем добираются часы") == [
        "Микс проекта",
        "Партнёр",
        "Юрист (Младший юрист)",
        "Стажёр",
    ]
    assert column(app, 0, "Запас") == ["+159,6 ч", "+55 ч", "+140,9 ч", "+375,8 ч"]
    assert column(app, 1, "Фактор") == ["Утилизация", "Часы", "Накладные", "Ставки для клиента"]
    assert column(app, 1, "NNE при +10%")[1] == "219 850 ₽ (−5 650 ₽)"
    app.slider(key=state.TORNADO_PCT).set_value(20).run()
    assert "NNE при +20%" in app.dataframe[1].value.columns
    assert metrics(app)["NNE, ₽"] == "225 500 ₽"


def test_what_if_recalculates_without_changing_project() -> None:
    app = demo_app(SENSITIVITY_PAGE)
    stages = app.session_state[state.PROJECT_STAGES]
    app.slider(key=state.WHATIF_HOURS).set_value(100).run()
    app.slider(key=state.WHATIF_UTILIZATION).set_value(-10).run()
    # Методология, раздел 17.4.
    assert metrics(app)["NNE, ₽"] == "156 444 ₽"
    # Streamlit красит дельту по ASCII-минусу: убыток — вниз и красным.
    nne = next(item for item in app.metric if item.label == "NNE, ₽")
    assert nne.delta == "-69 056 ₽"
    revenue = next(item for item in app.metric if item.label == "Выручка без НДС, ₽")
    assert not revenue.delta
    assert app.session_state[state.PROJECT_STAGES] == stages
    assert app.session_state[state.AGREED_PRICE] == 300_000
    app.slider(key=state.WHATIF_PRICE).set_value(-10).run()
    assert metrics(app)["Выручка без НДС, ₽"] == "270 000 ₽"
    click(app, "↺ Сбросить «что если»")
    assert metrics(app)["NNE, ₽"] == "225 500 ₽"


def test_what_if_belongs_to_project() -> None:
    app = demo_app(SENSITIVITY_PAGE)
    ids = slot_ids(app)
    app.slider(key=state.WHATIF_HOURS).set_value(100).run()
    switch(app, ids[1])
    assert app.slider(key=state.WHATIF_HOURS).value == 0
    # Почасовая модель: две таблицы запаса, ползунка цены нет.
    assert column(app, 0, "Запас")[1] == "+14,3 ч"
    assert column(app, 1, "Запас") == ["не наступает"] * 4
    assert not [s for s in app.slider if s.key == state.WHATIF_PRICE]
    switch(app, ids[0])
    assert app.slider(key=state.WHATIF_HOURS).value == 100
    app.switch_page(PROJECT_PAGE).run()
    app.switch_page(SENSITIVITY_PAGE).run()
    assert app.slider(key=state.WHATIF_HOURS).value == 100


def test_cost_plus_shows_repriced_price() -> None:
    app = demo_app(SENSITIVITY_PAGE)
    switch(app, slot_ids(app)[2])
    assert "держится" in app.info[0].value
    app.slider(key=state.WHATIF_HOURS).set_value(10).run()
    assert any("Пересчитанная цена «издержки + маржа»" in c.value for c in app.caption)


def test_analytics_pages_need_team_and_hours() -> None:
    for page in (SENSITIVITY_PAGE, COMPARE_PAGE, PLAN_FACT_PAGE):
        app = run_page(page)
        assert "Сначала заполните состав команды" in app.warning[0].value
    app = demo_app(SENSITIVITY_PAGE)
    click(app, "➕ Новый")
    assert "нет этапов с часами" in app.info[0].value


# ---------------------------------------------------------------------------
# Сравнение
# ---------------------------------------------------------------------------
def test_comparison_matrix_marks_unavailable_regimes() -> None:
    app = demo_app(COMPARE_PAGE)
    switch(app, slot_ids(app)[1])
    assert column(app, 0, "Режим") == [
        "▶ УСН «доходы» 6% + НДС 5%",
        "УСН «доходы − расходы» 15% + НДС 5%",
        "АУСН 8%",
        "НПД 6% (юрлица)",
        "ОСНО 25% + НДС 22%",
    ]
    assert column(app, 0, "Доступность")[3] == "⛔ недоступен"
    assert column(app, 0, "▶ Почасовая")[0] == "58 800 ₽"
    assert column(app, 0, "▶ Почасовая")[3] == "(58 800 ₽)"
    assert column(app, 0, "С потолком")[0] == "задайте потолок"
    app.number_input(key=state.COMPARE_CAP).set_value(90_000.0).run()
    assert column(app, 0, "С потолком")[:2] == ["49 400 ₽", "46 580 ₽"]
    app.radio(key=state.COMPARE_METRIC).set_value("total_client").run()
    # Раздел 19.5: 86 573,33 ₽ при пошлине 15 000; у демо пошлины по каталогу — 24 000 ₽.
    assert column(app, 0, "Издержки + маржа")[4] == "95 573 ₽"
    app.checkbox(key=state.COMPARE_ALL_VARIANTS).check().run()
    assert len(column(app, 0, "Режим")) == 10
    reasons = " ".join(item.value for item in app.expander[0].markdown)
    assert "НПД применяют только физлица и ИП" in reasons


# ---------------------------------------------------------------------------
# План–факт
# ---------------------------------------------------------------------------
def test_plan_fact_slide3() -> None:
    app = demo_app(PLAN_FACT_PAGE)
    values = metrics(app)
    assert values["NNE по факту, ₽"] == "-2 200 ₽"
    assert values["Маржа по факту"] == "-0,73%"
    summary = dict(zip(column(app, 1, "Показатель"), column(app, 1, "Факт"), strict=True))
    assert summary["Реализация (выставлено / по ставкам)"] == "41,96%"
    assert summary["Списание"] == "415 000 ₽"
    assert column(app, 3, "Δ издержек")[0] == "+233 700 ₽"


def test_plan_fact_unplanned_work_and_manual_billing() -> None:
    app = demo_app(PLAN_FACT_PAGE)
    switch(app, slot_ids(app)[1])
    assert column(app, 0, state.ACTUALS_NOTE)[-1] == state.UNPLANNED_NOTE
    assert metrics(app)["NNE по факту, ₽"] == "78 160 ₽"
    app.checkbox(key=state.ACTUAL_BILLED_MANUAL).check().run()
    app.number_input(key=state.ACTUAL_BILLED).set_value(120_000.0).run()
    # Методология, раздел 18.6: уступка клиенту.
    assert metrics(app)["NNE по факту, ₽"] == "65 000 ₽"
    app.selectbox(key="unplanned_stage").set_value("Анализ охраноспособности")
    app.selectbox(key="unplanned_employee").set_value("Стажёр")
    app.number_input(key="unplanned_hours").set_value(3.0)
    click(app, "➕ Добавить")
    rows = app.session_state[state.ACTUAL_HOURS]
    assert ActualHours(stage="Анализ охраноспособности", employee="Стажёр", hours=d(3)) in rows
    assert column(app, 0, state.ACTUALS_EMPLOYEE)[2] == "Стажёр"


def test_plan_fact_start_switch_and_delete() -> None:
    app = demo_app(PLAN_FACT_PAGE)
    ids = slot_ids(app)
    switch(app, ids[2])
    assert "не введён" in app.info[0].value
    click(app, "📋 Заполнить факт по плану")
    assert metrics(app)["NNE по факту, ₽"] == "49 125 ₽"
    assert app.session_state[state.ACTUAL_HOURS]
    # Факт каждого проекта — свой: переключение его не теряет и не смешивает.
    switch(app, ids[1])
    assert len(app.session_state[state.ACTUAL_HOURS]) == 5
    switch(app, ids[2])
    assert len(app.session_state[state.ACTUAL_HOURS]) == 6
    click(app, "🗑 Удалить факт")
    click(app, "Да, удалить факт")
    assert app.session_state[state.ACTUAL_HOURS] is None
    assert "не введён" in app.info[0].value
    switch(app, ids[0])
    assert metrics(app)["NNE по факту, ₽"] == "-2 200 ₽"


def test_deleting_stage_drops_its_fact() -> None:
    app = demo_app(PROJECT_PAGE)
    switch(app, slot_ids(app)[1])
    # Этап 2: юрист, стажёр — удалить оба назначения, этап исчезнет.
    for _ in range(2):
        app.button(key="del_assignment_1_0").click().run()
    stages = {row.stage for row in app.session_state[state.ACTUAL_HOURS]}
    assert stages == {"Анализ охраноспособности"}
    app.switch_page(PLAN_FACT_PAGE).run()
    assert not app.exception


# ---------------------------------------------------------------------------
# Дашборд и главная
# ---------------------------------------------------------------------------
def test_dashboard_teaser_and_home_fact_column() -> None:
    app = demo_app(PROJECT_PAGE)
    click(app, "💰 Рассчитать смету")
    app.switch_page("pages/03_Дашборд.py").run()
    texts = [item.value for item in app.markdown]
    assert any("Запас часов до безубыточности (микс проекта): +159,6 ч" in t for t in texts)
    assert any("NNE по факту: -2 200 ₽" in t for t in texts)
    app.switch_page("Home.py").run()
    assert list(app.dataframe[0].value["Факт"]) == [
        "введён" if record.project.actuals else "—" for record in demo_workspace().projects
    ]
