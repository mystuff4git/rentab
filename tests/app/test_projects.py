"""Главная и проекты сессии: дисклеймер, демо-фирма, импорт и экспорт JSON, переключение."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit import config
from streamlit.testing.v1 import AppTest

from rentab_core import __version__
from rentab_core.demo import TM_APPLICATION, TM_EXAMINATION, demo_firm, demo_workspace
from rentab_core.duties import Quantity
from rentab_core.io import Workspace, dumps, loads
from rentab_core.models import Country, JurisdictionMode, UsnVat
from tests.app.helpers import (
    HOME,
    click,
    column,
    estimate_of,
    exported,
    metrics,
    run_page,
    slot_ids,
    switch,
    upload,
)
from tests.app.test_smoke import firm_session
from tests.core.factories import d
from tests.core.test_io import full_featured_workspace
from ui import nav, state

V03_FIXTURE = Path(__file__).parents[1] / "fixtures" / "legacy" / "v03_firm_profile.json"
PROJECT_PAGE = nav.PROJECT
USAGE_STATS = "browser.gatherUsageStats"


def demo_app(page: str = HOME) -> AppTest:
    """Приложение с загруженной демо-фирмой."""
    app = run_page(HOME)
    click(app, "🎓 Загрузить демо-фирму")
    if page != HOME:
        app.switch_page(page).run()
    return app


# ---------------------------------------------------------------------------
# Главная и демо-фирма
# ---------------------------------------------------------------------------
def test_home_shows_privacy_disclaimer() -> None:
    app = run_page(HOME)
    disclaimer = app.info[0].value
    assert disclaimer.startswith("**Данные не покидают вашу сессию.**")
    assert "ничего не записывает на диск сервера" in disclaimer
    assert "статистика" not in disclaimer
    assert not app.session_state[state.TEAM]
    assert any(f"Rentab {__version__} · " in caption.value for caption in app.caption)


@pytest.fixture
def usage_stats_on() -> Iterator[None]:
    """Сервер включил статистику Streamlit, как Streamlit Community Cloud (ADR 0010)."""
    before = config.get_option(USAGE_STATS)
    config.set_option(USAGE_STATS, True)
    yield
    config.set_option(USAGE_STATS, before)


@pytest.mark.usefixtures("usage_stats_on")
def test_home_disclaimer_mentions_usage_stats_enabled_by_server() -> None:
    disclaimer = run_page(HOME).info[0].value
    assert disclaimer.startswith("**Данные не покидают вашу сессию.**")
    assert "включена анонимная статистика использования Streamlit" in disclaimer
    assert "страница вокруг приложения подключает и свою веб-аналитику" in disclaimer
    assert "запустите Rentab у себя" in disclaimer


def test_demo_firm_fills_setup_and_project_pages() -> None:
    app = demo_app(nav.FIRM)
    assert app.session_state[state.JURISDICTION] == demo_firm().jurisdiction
    assert app.radio(key=state.JURISDICTION_CHOICE).value == JurisdictionMode.RF
    assert app.selectbox(key=state.RF_USN_VAT).value == UsnVat.VAT_5
    assert app.number_input(key=state.FACT_INCOME_RF).value == 24_000_000
    assert app.success[0].value == "Выбранный режим применим по введённым данным."
    assert metrics(app)["Ставка накладных"] == "100 ₽/ч"
    assert app.session_state[state.OVERHEADS] == list(demo_firm().overheads)

    app.switch_page(PROJECT_PAGE).run()
    assert app.text_input(key=state.PROJECT_NAME).value == "Слайд 3: план и факт"
    assert app.radio(key=state.PRICING_MODEL).value == state.PRICING_AGREED
    estimate = estimate_of(app)
    assert estimate.nne == d(225_500)
    assert estimate.total_client == d(315_000)
    assert metrics(app)["Чистая прибыль (NNE)"] == "225 500 ₽"


def test_demo_replaces_data_only_after_confirmation() -> None:
    app = run_page(HOME, firm_session())
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


def test_projects_table_shows_live_estimates() -> None:
    app = demo_app()
    names = [record.project.name for record in demo_workspace().projects]
    assert column(app, "projects", "Проект") == [f"▶ {names[0]}", *names[1:]]
    # Смета не хранится: таблица считает каждый проект по текущим данным.
    assert column(app, "projects", "NNE") == ["225 500 ₽", "58 800 ₽", "49 125 ₽", "32 200 ₽"]
    assert column(app, "projects", "Маржа")[0] == "75,17%"
    texts = " ".join(item.value for item in app.markdown)
    assert texts.count("✓ заполнено") == 3


# ---------------------------------------------------------------------------
# Несколько проектов
# ---------------------------------------------------------------------------
def test_switching_projects_keeps_each_projects_values() -> None:
    app = demo_app(PROJECT_PAGE)
    ids = slot_ids(app)
    app.text_input(key=state.PROJECT_NAME).input("План, изменён").run()
    switch(app, ids[1])
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

    switch(app, ids[1])
    assert app.number_input(key=classes).value == 5
    # 4 000 + 4 × 1 000 и 13 000 + 4 × 2 500 за пять классов.
    assert estimate_of(app).disbursements_billed == d(31_000)


def test_result_belongs_to_project() -> None:
    app = demo_app(nav.RESULT)
    ids = slot_ids(app)
    assert app.subheader[0].value == "Слайд 3: план и факт"
    assert metrics(app)["Чистая прибыль (NNE)"] == "225 500 ₽"
    switch(app, ids[1])
    assert metrics(app)["Чистая прибыль (NNE)"] == "58 800 ₽"
    switch(app, ids[0])
    assert metrics(app)["Чистая прибыль (NNE)"] == "225 500 ₽"
    switch(app, ids[2])
    assert app.subheader[0].value == "Патентование изобретения"


def test_new_copy_and_delete_projects() -> None:
    app = demo_app(PROJECT_PAGE)
    original_stages = app.session_state[state.PROJECT_STAGES]

    click(app, "⧉ Копия")
    ids = slot_ids(app)
    assert len(ids) == 5
    assert app.session_state[state.ACTIVE_PROJECT] == ids[1]
    assert app.text_input(key=state.PROJECT_NAME).value == "Слайд 3: план и факт (копия)"
    assert app.session_state[state.PROJECT_STAGES] == original_stages

    click(app, "➕ Новый")
    ids = slot_ids(app)
    assert app.session_state[state.ACTIVE_PROJECT] == ids[2]
    assert app.text_input(key=state.PROJECT_NAME).value == ""
    assert app.session_state[state.PROJECT_STAGES] == []
    assert app.radio(key=state.PRICING_MODEL).value == state.PRICING_HOURLY

    click(app, "🗑 Удалить проект")
    assert app.sidebar.warning[0].value == "Удалить проект «Без названия · 3»?"
    click(app, "Отмена")
    assert len(slot_ids(app)) == 6
    click(app, "🗑 Удалить проект")
    click(app, "Да, удалить")
    assert len(slot_ids(app)) == 5
    assert app.text_input(key=state.PROJECT_NAME).value == "Регистрация товарного знака"


def test_deleting_last_project_leaves_empty_one() -> None:
    app = run_page(HOME)
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

    fresh = run_page(HOME)
    upload(fresh, data)
    summary = "В файле (профиль Rentab): фирма «Демо-фирма «Слайд 3»», сотрудников: 3, проектов: 4."
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
    app = run_page(HOME, session)
    assert any("«Черновик»: потолок или цена не заданы" in c.value for c in app.caption)
    (record,) = loads(exported(downloads)).workspace.projects
    assert record.project.pricing.kind == "hourly"


def test_import_v03_profile_shows_notes() -> None:
    app = run_page(HOME)
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
    app = run_page(HOME, firm_session())
    upload(app, b"{not json")
    assert app.error[0].value.startswith("Файл не является JSON")
    assert not [b for b in app.button if b.label == "📂 Загрузить данные из файла"]
    assert len(app.session_state[state.TEAM]) == 2


def test_import_asks_before_replacing_and_rounds_margin() -> None:
    record = demo_workspace().projects[0]
    project = record.project.model_copy(update={"target_margin": Decimal("0.325")})
    data = dumps(Workspace(projects=(record.model_copy(update={"project": project}),)))
    app = run_page(HOME, firm_session())
    upload(app, data)
    click(app, "📂 Загрузить данные из файла")
    assert "их заменит данные из файла" in app.warning[0].value
    click(app, "Заменить данные")
    assert app.session_state[state.TEAM] == []
    assert app.session_state[state.TARGET_MARGIN_PCT] == 33
    assert (
        "Проект «Слайд 3: план и факт»: целевая маржа 32,5% округлена до 33% — в интерфейсе "
        "целые проценты." in app.expander[0].markdown[0].value
    )


def test_full_featured_file_survives_pages(downloads: dict[str, bytes]) -> None:
    # Режим «Оба», тариф МСП, ИП в РК, оклады и «из прибыли», накладные в долларах,
    # потолок, цены с НДС, страна расчёта, пошлины Казпатента для МСП.
    workspace = full_featured_workspace()
    app = run_page(HOME)
    upload(app, dumps(workspace))
    click(app, "📂 Загрузить данные из файла")
    app.switch_page(nav.FIRM).run()
    assert app.session_state[state.JURISDICTION] == workspace.firm.jurisdiction
    assert app.radio(key=state.KZ_FORM).value == "ip"
    assert app.number_input(key=state.RUB_PER_USD).value == 81.2345
    assert app.number_input(key=state.FACT_HEADCOUNT).value == 7
    app.switch_page(PROJECT_PAGE).run()
    assert app.radio(key=state.CALC_COUNTRY).value == "KZ"
    assert app.number_input(key=state.CAP_AMOUNT).value == 1_000_000
    assert estimate_of(app).currency == "KZT"
    app.switch_page(HOME).run()
    assert loads(exported(downloads)).workspace == workspace


def test_switching_projects_in_both_mode_resets_calc_country() -> None:
    app = run_page(HOME)
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
    assert estimate_of(app).currency == "RUB"
