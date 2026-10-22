"""Главная и проекты сессии: дисклеймер, демо-фирма, импорт и экспорт JSON, переключение."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from rentab_core.demo import TM_APPLICATION, TM_EXAMINATION, demo_firm, demo_workspace
from rentab_core.duties import Quantity
from rentab_core.io import Workspace, dumps, loads
from rentab_core.models import Country, JurisdictionMode, UsnVat
from tests.app.helpers import click, exported, metrics, run_page, slot_ids, switch, upload
from tests.app.test_smoke import firm_session
from tests.core.factories import d
from tests.core.test_io import full_featured_workspace
from ui import state

V03_FIXTURE = Path(__file__).parents[1] / "fixtures" / "legacy" / "v03_firm_profile.json"
PROJECT_PAGE = "pages/02_Проект.py"


def demo_app(page: str = "Home.py") -> AppTest:
    """Приложение с загруженной демо-фирмой."""
    app = run_page("Home.py")
    click(app, "🎓 Загрузить демо-фирму")
    if page != "Home.py":
        app.switch_page(page).run()
    return app


# ---------------------------------------------------------------------------
# Главная и демо-фирма
# ---------------------------------------------------------------------------
def test_home_shows_privacy_disclaimer() -> None:
    app = run_page("Home.py")
    disclaimer = app.info[0].value
    assert disclaimer.startswith("**Данные не покидают вашу сессию.**")
    assert "ничего не записывает на диск сервера" in disclaimer
    assert not app.session_state[state.TEAM]


def test_demo_firm_fills_setup_and_project_pages() -> None:
    app = demo_app("pages/01_Настройка.py")
    assert app.session_state[state.JURISDICTION] == demo_firm().jurisdiction
    assert app.radio(key=state.JURISDICTION_CHOICE).value == JurisdictionMode.RF
    assert app.selectbox(key=state.RF_USN_VAT).value == UsnVat.VAT_5
    assert app.number_input(key=state.FACT_INCOME_RF).value == 24_000_000
    assert app.success[0].value == "Выбранный режим применим по введённым данным."
    assert metrics(app)["Ставка накладных, ₽/ч"] == "100.0"
    assert app.session_state[state.OVERHEADS] == list(demo_firm().overheads)

    app.switch_page(PROJECT_PAGE).run()
    assert app.text_input(key=state.PROJECT_NAME).value == "Слайд 3: план"
    assert app.radio(key=state.PRICING_MODEL).value == state.PRICING_AGREED
    click(app, "💰 Рассчитать смету")
    estimate = app.session_state[state.ESTIMATE]
    assert estimate.nne == d(225_500)
    assert estimate.total_client == d(315_000)


def test_demo_replaces_data_only_after_confirmation() -> None:
    app = run_page("Home.py", firm_session())
    click(app, "🎓 Загрузить демо-фирму")
    assert "В сессии уже есть данные" in app.warning[0].value
    assert len(app.session_state[state.TEAM]) == 2
    click(app, "Отмена")
    assert not app.warning
    click(app, "🎓 Загрузить демо-фирму")
    click(app, "Заменить данные")
    assert [member.name for member in app.session_state[state.TEAM]] == [
        "Партнёр",
        "Юрист",
        "Стажёр",
    ]
    assert app.success[0].value.startswith("Загружена демо-фирма: фирма «Демо-фирма «Слайд 3»»")


def test_projects_table_shows_last_estimate() -> None:
    app = demo_app(PROJECT_PAGE)
    click(app, "💰 Рассчитать смету")
    app.switch_page("Home.py").run()
    table = app.dataframe[0].value
    assert list(table["Проект"]) == [record.project.name for record in demo_workspace().projects]
    assert list(table[""]) == ["▶", "", "", "", ""]
    assert table["NNE"][0] == "225 500 ₽"
    assert table["Маржа"][0] == "75.2%"
    assert table["NNE"][1] == "—"


# ---------------------------------------------------------------------------
# Несколько проектов
# ---------------------------------------------------------------------------
def test_switching_projects_keeps_each_projects_values() -> None:
    app = demo_app(PROJECT_PAGE)
    ids = slot_ids(app)
    app.text_input(key=state.PROJECT_NAME).input("План, изменён").run()
    switch(app, ids[2])
    assert app.text_input(key=state.PROJECT_NAME).value == "Регистрация товарного знака"
    assert app.radio(key=state.PRICING_MODEL).value == state.PRICING_HOURLY
    classes = state.duty_quantity_key(Country.RF, Quantity.CLASSES)
    assert app.number_input(key=classes).value == 3
    assert app.multiselect(key=f"{state.DUTY_MULTISELECT_PREFIX}RF").value == [
        TM_APPLICATION,
        TM_EXAMINATION,
    ]
    assert [s.name for s in app.session_state[state.PROJECT_STAGES]] == [
        "Анализ охраноспособности",
        "Подготовка и подача заявки",
    ]
    app.number_input(key=classes).set_value(5).run()

    switch(app, ids[0])
    assert app.text_input(key=state.PROJECT_NAME).value == "План, изменён"
    assert app.radio(key=state.PRICING_MODEL).value == state.PRICING_AGREED
    assert app.number_input(key=state.AGREED_PRICE).value == 300_000
    assert app.session_state[state.SELECTED_DUTIES] == {"RF": [], "KZ": []}

    switch(app, ids[2])
    assert app.number_input(key=classes).value == 5
    click(app, "💰 Рассчитать смету")
    # 4 000 + 4 × 1 000 и 13 000 + 4 × 2 500 за пять классов.
    assert app.session_state[state.ESTIMATE].disbursements_billed == d(31_000)


def test_estimate_belongs_to_project() -> None:
    app = demo_app(PROJECT_PAGE)
    ids = slot_ids(app)
    click(app, "💰 Рассчитать смету")
    switch(app, ids[1])
    assert state.ESTIMATE not in app.session_state
    click(app, "💰 Рассчитать смету")
    assert app.session_state[state.ESTIMATE].nne == d(-2_200)

    switch(app, ids[0])
    assert app.session_state[state.ESTIMATE].nne == d(225_500)
    app.switch_page("pages/03_Дашборд.py").run()
    assert app.subheader[0].value == "Слайд 3: план"
    switch(app, ids[3])
    assert "Сначала сформируйте смету проекта «Патентование изобретения»" in app.info[0].value


def test_new_copy_and_delete_projects() -> None:
    app = demo_app(PROJECT_PAGE)
    original_stages = app.session_state[state.PROJECT_STAGES]
    click(app, "💰 Рассчитать смету")

    click(app, "⧉ Копия")
    ids = slot_ids(app)
    assert len(ids) == 6
    assert app.session_state[state.ACTIVE_PROJECT] == ids[1]
    assert app.text_input(key=state.PROJECT_NAME).value == "Слайд 3: план (копия)"
    assert app.session_state[state.PROJECT_STAGES] == original_stages
    assert state.ESTIMATE not in app.session_state

    click(app, "➕ Новый")
    ids = slot_ids(app)
    assert app.session_state[state.ACTIVE_PROJECT] == ids[2]
    assert app.text_input(key=state.PROJECT_NAME).value == ""
    assert app.session_state[state.PROJECT_STAGES] == []
    assert app.radio(key=state.PRICING_MODEL).value == state.PRICING_HOURLY

    click(app, "🗑 Удалить проект")
    assert app.sidebar.warning[0].value == "Удалить проект «Без названия · 3»?"
    click(app, "Отмена")
    assert len(slot_ids(app)) == 7
    click(app, "🗑 Удалить проект")
    click(app, "Да, удалить")
    assert len(slot_ids(app)) == 6
    assert app.text_input(key=state.PROJECT_NAME).value == "Слайд 3: факт — партнёр тянет проект"


def test_deleting_last_project_leaves_empty_one() -> None:
    app = run_page("Home.py")
    (first,) = slot_ids(app)
    click(app, "🗑 Удалить проект")
    click(app, "Да, удалить")
    (second,) = slot_ids(app)
    assert second != first
    assert app.session_state[state.ACTIVE_PROJECT] == second


# ---------------------------------------------------------------------------
# Экспорт и импорт
# ---------------------------------------------------------------------------
def test_export_import_round_trip(downloads: dict[str, bytes]) -> None:
    app = demo_app()
    data = exported(downloads)
    assert loads(data).workspace == demo_workspace()
    raw = json.loads(data)
    assert raw["format"] == "rentab"
    assert raw["exported_at"] is not None

    fresh = run_page("Home.py")
    upload(fresh, data)
    summary = "В файле (профиль Rentab): фирма «Демо-фирма «Слайд 3»», сотрудников: 3, проектов: 5."
    assert summary in [caption.value for caption in fresh.caption]
    click(fresh, "📂 Загрузить данные из файла")
    assert fresh.success[0].value.startswith("Загружены данные из файла")
    assert loads(exported(downloads)).workspace == demo_workspace()
    # Загрузчик очищен: кнопка загрузки исчезла.
    assert not [b for b in fresh.button if b.label == "📂 Загрузить данные из файла"]
    assert app.session_state[state.TEAM] == fresh.session_state[state.TEAM]


def test_export_without_projects(downloads: dict[str, bytes]) -> None:
    app = demo_app()
    exported(downloads)
    app.checkbox(key="export_include_projects").uncheck().run()
    assert json.loads(exported(downloads))["projects"] == []


def test_export_keeps_draft_without_cap_as_hourly(downloads: dict[str, bytes]) -> None:
    session = {state.PRICING_MODEL: state.PRICING_CAPPED, state.PROJECT_NAME: "Черновик"}
    app = run_page("Home.py", session)
    assert any("«Черновик»: потолок или цена не заданы" in c.value for c in app.caption)
    (record,) = loads(exported(downloads)).workspace.projects
    assert record.project.pricing.kind == "hourly"


def test_import_v03_profile_shows_notes() -> None:
    app = run_page("Home.py")
    upload(app, V03_FIXTURE.read_bytes(), "firm_profile.json")
    assert any("профиль v0.3 — будет переведён" in caption.value for caption in app.caption)
    click(app, "📂 Загрузить данные из файла")
    assert len(app.session_state[state.TEAM]) == 4
    assert app.session_state[state.PRICING_MODEL] == state.PRICING_FIXED
    assert app.session_state[state.TARGET_MARGIN_PCT] == 30
    notes = app.expander[0]
    assert notes.label == "Что изменилось при загрузке (7)"
    assert "ставка взносов 30% — плоская ставка v0.3 (L-08)" in notes.markdown[0].value


def test_import_rejects_bad_file_and_keeps_session() -> None:
    app = run_page("Home.py", firm_session())
    upload(app, b"{not json")
    assert app.error[0].value.startswith("Файл не является JSON")
    assert not [b for b in app.button if b.label == "📂 Загрузить данные из файла"]
    assert len(app.session_state[state.TEAM]) == 2


def test_import_asks_before_replacing_and_rounds_margin() -> None:
    record = demo_workspace().projects[0]
    project = record.project.model_copy(update={"target_margin": Decimal("0.325")})
    data = dumps(Workspace(projects=(record.model_copy(update={"project": project}),)))
    app = run_page("Home.py", firm_session())
    upload(app, data)
    click(app, "📂 Загрузить данные из файла")
    assert "их заменит данные из файла" in app.warning[0].value
    click(app, "Заменить данные")
    assert app.session_state[state.TEAM] == []
    assert app.session_state[state.TARGET_MARGIN_PCT] == 33
    assert (
        "Проект «Слайд 3: план»: целевая маржа 32,5% округлена до 33% — в интерфейсе "
        "целые проценты." in app.expander[0].markdown[0].value
    )


def test_full_featured_file_survives_pages(downloads: dict[str, bytes]) -> None:
    # Режим «Оба», тариф МСП, ИП в РК, оклады и «из прибыли», накладные в долларах,
    # потолок, цены с НДС, страна расчёта, пошлины Казпатента для МСП.
    workspace = full_featured_workspace()
    app = run_page("Home.py")
    upload(app, dumps(workspace))
    click(app, "📂 Загрузить данные из файла")
    app.switch_page("pages/01_Настройка.py").run()
    assert app.session_state[state.JURISDICTION] == workspace.firm.jurisdiction
    assert app.radio(key=state.KZ_FORM).value == "ip"
    assert app.number_input(key=state.RUB_PER_USD).value == 81.2345
    assert app.number_input(key=state.FACT_HEADCOUNT).value == 7
    app.switch_page(PROJECT_PAGE).run()
    assert app.radio(key=state.CALC_COUNTRY).value == "KZ"
    assert app.number_input(key=state.CAP_AMOUNT).value == 1_000_000
    click(app, "💰 Рассчитать смету")
    assert app.session_state[state.ESTIMATE].currency == "KZT"
    app.switch_page("Home.py").run()
    assert loads(exported(downloads)).workspace == workspace


def test_switching_projects_in_both_mode_resets_calc_country() -> None:
    app = run_page("Home.py")
    upload(app, dumps(full_featured_workspace()))
    click(app, "📂 Загрузить данные из файла")
    app.switch_page(PROJECT_PAGE).run()
    assert app.radio(key=state.CALC_COUNTRY).value == "KZ"
    switch(app, slot_ids(app)[1])
    # Страна расчёта не задана: радиокнопка пустая, смета — в рублях.
    assert app.radio(key=state.CALC_COUNTRY).value is None
    app.text_input(key=state.PROJECT_NAME).input("Второй").run()
    app.text_input(key="assignment_new_stage").input("Этап")
    app.number_input(key="assignment_hours").set_value(2.0)
    click(app, "➕ Добавить")
    click(app, "💰 Рассчитать смету")
    assert app.session_state[state.ESTIMATE].currency == "RUB"
