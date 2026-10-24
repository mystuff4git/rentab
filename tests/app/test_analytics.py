"""Шаги расчёта: «Результат» (чувствительность), «Сравнение», «План–факт».

Правку ячеек `st.data_editor` AppTest не моделирует: редактор факта
проверяется в браузере (`docs/screenshots/phase-6/shots.js`), разбор его строк —
в `test_state.py`. Здесь — показ, ползунки, форма «вне плана», «вручную» и
переключение проектов.
"""

from __future__ import annotations

from rentab_core.demo import demo_workspace
from rentab_core.models import ActualHours
from tests.app.helpers import (
    HOME,
    click,
    column,
    has_table,
    metrics,
    open_tab,
    plain,
    run_page,
    slot_ids,
    switch,
    table,
)
from tests.app.test_projects import PROJECT_PAGE, demo_app
from tests.core.factories import d
from ui import nav, state

SENSITIVITY = "Чувствительность"


# ---------------------------------------------------------------------------
# Чувствительность
# ---------------------------------------------------------------------------
def test_sensitivity_shows_slide3_headroom_and_tornado() -> None:
    app = demo_app(nav.RESULT)
    open_tab(app, SENSITIVITY)
    assert column(app, "break-even", "Чем добираются часы") == [
        "Микс проекта",
        "Партнёр",
        "Юрист (Младший юрист)",
        "Стажёр",
    ]
    assert column(app, "break-even", "Запас") == ["+159,6 ч", "+55 ч", "+140,9 ч", "+375,8 ч"]
    assert metrics(app)["Запас часов (микс проекта)"] == "+159,6 ч"
    tornado = table(app, "tornado")
    assert [row["Фактор"] for row in tornado.rows] == [
        "Утилизация",
        "Часы",
        "Накладные",
        "Ставки для клиента",
    ]
    assert tornado.rows[1]["NNE при +10%"] == "219 850 ₽"
    assert tornado.notes[1]["NNE при +10%"] == "−5 650 ₽"
    app.slider(key=state.TORNADO_PCT).set_value(20).run()
    assert "NNE при +20%" in table(app, "tornado").rows[0]
    assert metrics(app)["NNE"] == "225 500 ₽"


def test_what_if_recalculates_without_changing_project() -> None:
    app = demo_app(nav.RESULT)
    open_tab(app, SENSITIVITY)
    stages = app.session_state[state.PROJECT_STAGES]
    app.slider(key=state.WHATIF_HOURS).set_value(100).run()
    app.slider(key=state.WHATIF_UTILIZATION).set_value(-10).run()
    # Методология, раздел 17.4.
    assert metrics(app)["NNE"] == "156 444 ₽"
    # Streamlit красит дельту по ASCII-минусу: убыток — вниз и красным.
    nne = next(item for item in app.metric if item.label == "NNE")
    assert plain(nne.delta or "") == "-69 056 ₽"
    revenue = next(item for item in app.metric if item.label == "Выручка без НДС")
    assert not revenue.delta
    assert app.session_state[state.PROJECT_STAGES] == stages
    assert app.session_state[state.AGREED_PRICE] == 300_000
    app.slider(key=state.WHATIF_PRICE).set_value(-10).run()
    assert metrics(app)["Выручка без НДС"] == "270 000 ₽"
    click(app, "↺ Сбросить «что если»")
    assert metrics(app)["NNE"] == "225 500 ₽"


def test_what_if_belongs_to_project() -> None:
    app = demo_app(nav.RESULT)
    open_tab(app, SENSITIVITY)
    ids = slot_ids(app)
    app.slider(key=state.WHATIF_HOURS).set_value(100).run()
    switch(app, ids[1])
    assert app.slider(key=state.WHATIF_HOURS).value == 0
    # Почасовая модель: две таблицы запаса, ползунка цены нет.
    assert column(app, "break-even-written-off", "Запас")[1] == "+14,3 ч"
    assert column(app, "break-even", "Запас") == ["не наступает"] * 4
    assert not [s for s in app.slider if s.key == state.WHATIF_PRICE]
    switch(app, ids[0])
    assert app.slider(key=state.WHATIF_HOURS).value == 100
    # Вкладка и ползунки переживают переход на другой шаг и обратно.
    app.switch_page(PROJECT_PAGE).run()
    app.switch_page(nav.RESULT).run()
    assert app.session_state[state.RESULT_TAB] == SENSITIVITY
    assert app.slider(key=state.WHATIF_HOURS).value == 100


def test_cost_plus_shows_repriced_price() -> None:
    app = demo_app(nav.RESULT)
    open_tab(app, SENSITIVITY)
    switch(app, slot_ids(app)[2])
    assert "держится" in app.info[0].value
    app.slider(key=state.WHATIF_HOURS).set_value(10).run()
    assert any("Пересчитанная цена «издержки + маржа»" in c.value for c in app.caption)


def test_result_teaser_opens_sensitivity_tab() -> None:
    app = demo_app(nav.RESULT)
    assert not [s for s in app.slider if s.key == state.WHATIF_HOURS]
    click(app, "Чувствительность")
    assert app.session_state[state.RESULT_TAB] == SENSITIVITY
    assert app.slider(key=state.WHATIF_HOURS).value == 0


def test_analytics_pages_need_team_and_hours() -> None:
    for page in (nav.RESULT, nav.COMPARE, nav.PLAN_FACT):
        app = run_page(page)
        texts = " ".join(item.value for item in app.markdown)
        assert "Сначала заполните состав команды" in texts
    app = demo_app(nav.RESULT)
    click(app, "➕ Новый")
    assert "нет этапов с часами" in " ".join(item.value for item in app.markdown)


# ---------------------------------------------------------------------------
# Сравнение
# ---------------------------------------------------------------------------
def test_comparison_matrix_marks_unavailable_regimes() -> None:
    app = demo_app(nav.COMPARE)
    switch(app, slot_ids(app)[1])
    matrix = table(app, "compare-RF")
    assert [row["Режим"] for row in matrix.rows] == [
        "▶ УСН «доходы» 6% + НДС 5%",
        "УСН «доходы − расходы» 15% + НДС 5%",
        "АУСН 8%",
        "НПД 6% (юрлица)",
        "ОСНО 25% + НДС 22%",
    ]
    assert matrix.rows[3]["Доступность"] == "⛔ недоступен"
    assert "rt-muted" in matrix.classes[3]
    assert column(app, "compare-RF", "▶ Почасовая")[0] == "58 800 ₽"
    assert column(app, "compare-RF", "▶ Почасовая")[3] == "(58 800 ₽)"
    assert column(app, "compare-RF", "С потолком")[0] == "задайте потолок"
    app.number_input(key=state.COMPARE_CAP).set_value(90_000.0).run()
    assert column(app, "compare-RF", "С потолком")[:2] == ["49 400 ₽", "46 580 ₽"]
    app.radio(key=state.COMPARE_METRIC).set_value("total_client").run()
    # Раздел 19.5: 86 573,33 ₽ при пошлине 15 000; у демо пошлины по каталогу — 24 000 ₽.
    assert column(app, "compare-RF", "Издержки + маржа")[4] == "95 573 ₽"
    app.checkbox(key=state.COMPARE_ALL_VARIANTS).check().run()
    assert len(column(app, "compare-RF", "Режим")) == 10
    reasons = " ".join(item.value for item in app.expander[0].markdown)
    assert "НПД применяют только физлица и ИП" in reasons
    # Подпись режима ядра («УСН income») в интерфейс не попадает (L-18).
    assert not [item for item in app.html if "income" in item.value]


# ---------------------------------------------------------------------------
# План–факт
# ---------------------------------------------------------------------------
def test_plan_fact_slide3() -> None:
    app = demo_app(nav.PLAN_FACT)
    values = metrics(app)
    assert values["NNE по факту"] == "−2 200 ₽"
    assert values["Маржа по факту"] == "−0,73%"
    assert values["Реализация"] == "41,96%"
    summary = {row["Показатель"]: row["Факт"] for row in table(app, "plan-fact-summary").rows}
    assert summary["Реализация (выставлено / по ставкам)"] == "41,96%"
    assert summary["Списание"] == "415 000 ₽"
    assert column(app, "plan-fact-employees", "Δ издержек")[0] == "+233 700 ₽"
    assert table(app, "plan-fact-stages").rows[0]["Статус по факту"] == "Убыточный"


def test_plan_fact_editor_fits_narrow_screen() -> None:
    app = demo_app(nav.PLAN_FACT)
    switch(app, slot_ids(app)[1])
    editor = app.dataframe[0]
    # На экране три столбца; этап, исполнитель и отметка — скрытые, но в данных.
    assert list(editor.proto.column_order) == list(state.ACTUALS_VISIBLE)
    labels = list(editor.value[state.ACTUALS_LABEL])
    assert labels[-1] == "Партнёр · Подготовка и подача заявки · вне плана"


def test_plan_fact_unplanned_work_and_manual_billing() -> None:
    app = demo_app(nav.PLAN_FACT)
    switch(app, slot_ids(app)[1])
    assert list(app.dataframe[0].value[state.ACTUALS_NOTE])[-1] == state.UNPLANNED_NOTE
    assert metrics(app)["NNE по факту"] == "78 160 ₽"
    app.checkbox(key=state.ACTUAL_BILLED_MANUAL).check().run()
    app.number_input(key=state.ACTUAL_BILLED).set_value(120_000.0).run()
    # Методология, раздел 18.6: уступка клиенту.
    assert metrics(app)["NNE по факту"] == "65 000 ₽"
    app.selectbox(key="unplanned_stage").set_value("Анализ охраноспособности")
    app.selectbox(key="unplanned_employee").set_value("Стажёр")
    app.number_input(key="unplanned_hours").set_value(3.0)
    click(app, "➕ Добавить")
    rows = app.session_state[state.ACTUAL_HOURS]
    assert ActualHours(stage="Анализ охраноспособности", employee="Стажёр", hours=d(3)) in rows
    assert list(app.dataframe[0].value[state.ACTUALS_EMPLOYEE])[2] == "Стажёр"


def test_plan_fact_start_switch_and_delete() -> None:
    app = demo_app(nav.PLAN_FACT)
    ids = slot_ids(app)
    switch(app, ids[2])
    assert "не введён" in " ".join(item.value for item in app.markdown)
    click(app, "📋 Заполнить факт по плану")
    assert metrics(app)["NNE по факту"] == "49 125 ₽"
    assert app.session_state[state.ACTUAL_HOURS]
    # Факт каждого проекта — свой: переключение его не теряет и не смешивает.
    switch(app, ids[1])
    assert len(app.session_state[state.ACTUAL_HOURS]) == 5
    switch(app, ids[2])
    assert len(app.session_state[state.ACTUAL_HOURS]) == 6
    click(app, "🗑 Удалить факт")
    click(app, "Да, удалить факт")
    assert app.session_state[state.ACTUAL_HOURS] is None
    assert "не введён" in " ".join(item.value for item in app.markdown)
    switch(app, ids[0])
    assert metrics(app)["NNE по факту"] == "−2 200 ₽"


def test_deleting_stage_drops_its_fact() -> None:
    app = demo_app(PROJECT_PAGE)
    switch(app, slot_ids(app)[1])
    # Этап 2: юрист, стажёр — удалить оба назначения, этап исчезнет.
    for _ in range(2):
        app.button(key="del_assignment_1_0").click().run()
    stages = {row.stage for row in app.session_state[state.ACTUAL_HOURS]}
    assert stages == {"Анализ охраноспособности"}
    app.switch_page(nav.PLAN_FACT).run()
    assert not app.exception


# ---------------------------------------------------------------------------
# Результат и главная
# ---------------------------------------------------------------------------
def test_result_teaser_and_home_fact_column() -> None:
    app = demo_app(nav.RESULT)
    texts = [plain(item.value) for item in app.markdown]
    assert any("Запас часов до безубыточности (микс проекта): **+159,6 ч**" in t for t in texts)
    assert any("NNE по факту: **−2 200 ₽**" in t for t in texts)
    app.switch_page(HOME).run()
    assert column(app, "projects", "Факт") == [
        "введён" if record.project.actuals else "—" for record in demo_workspace().projects
    ]
    assert has_table(app, "projects")
