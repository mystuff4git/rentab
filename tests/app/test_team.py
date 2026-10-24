"""Команда на шаге «Фирма»: правка сотрудника и новое имя во всех проектах сессии."""

from __future__ import annotations

from decimal import Decimal

from rentab_core.models import MonthlyCost, Role
from tests.app.helpers import click, run_page, slot_ids
from tests.app.test_projects import demo_app
from tests.app.test_smoke import firm_session
from ui import nav, state


def employees_in_projects(app: object, name: str) -> list[str]:
    """Где встречается исполнитель: назначения и факт каждого проекта сессии."""
    found = []
    for slot in app.session_state[state.PROJECTS]:  # type: ignore[attr-defined]
        values = (
            app.session_state.to_dict()  # type: ignore[attr-defined]
            if slot.id == app.session_state[state.ACTIVE_PROJECT]  # type: ignore[attr-defined]
            else slot.values
        )
        for stage in values.get(state.PROJECT_STAGES) or []:
            found += [f"{slot.id}:plan" for item in stage.assignments if item.employee == name]
        for row in values.get(state.ACTUAL_HOURS) or []:
            if row.employee == name:
                found.append(f"{slot.id}:fact")
    return found


def test_edit_fills_card_and_saves_changes() -> None:
    app = demo_app(nav.FIRM)
    app.button(key="edit_member_0").click().run()
    # Карточка заполнена данными партнёра: сумма в месяц, часы и утилизация.
    assert app.text_input(key=state.MEMBER_NAME).value == "Партнёр"
    assert app.radio(key=state.MEMBER_COMPENSATION).value == state.COMPENSATION_MONTHLY
    assert app.number_input(key=state.MEMBER_MONTHLY).value == 400_000
    assert app.number_input(key=state.MEMBER_UTILIZATION).value == 62.5
    assert "Изменить сотрудника «Партнёр»" in " ".join(item.value for item in app.markdown)
    app.number_input(key=state.MEMBER_UTILIZATION).set_value(50.0).run()
    click(app, "💾 Сохранить изменения")
    partner = app.session_state[state.TEAM][0]
    assert partner.name == "Партнёр"
    assert partner.target_utilization == Decimal("0.5")
    assert partner.compensation == MonthlyCost(amount=Decimal(400_000))
    assert "Сохранено: Партнёр." in [item.value for item in app.success]
    # Карточка снова добавляет нового сотрудника.
    assert app.text_input(key=state.MEMBER_NAME).value == ""
    assert state.EDIT_MEMBER not in app.session_state


def test_rename_reaches_every_project_of_session() -> None:
    app = demo_app(nav.FIRM)
    before = employees_in_projects(app, "Юрист")
    assert any(item.endswith(":fact") for item in before)
    app.button(key="edit_member_1").click().run()
    app.text_input(key=state.MEMBER_NAME).input("Юрист Петрова").run()
    click(app, "💾 Сохранить изменения")
    assert [member.name for member in app.session_state[state.TEAM]] == [
        "Партнёр",
        "Юрист Петрова",
        "Стажёр",
    ]
    assert employees_in_projects(app, "Юрист") == []
    assert employees_in_projects(app, "Юрист Петрова") == before
    note = "Новое имя — в этапах и факте всех проектов сессии."
    assert any(note in item.value for item in app.success)
    # Смета активного и неактивного проекта по-прежнему находит исполнителя.
    app.switch_page(nav.RESULT).run()
    assert not app.warning
    app.selectbox(key=state.PROJECT_SWITCH).set_value(slot_ids(app)[1]).run()
    assert not app.warning


def test_rename_to_name_in_use_is_refused() -> None:
    app = demo_app(nav.FIRM)
    app.button(key="edit_member_2").click().run()
    app.text_input(key=state.MEMBER_NAME).input("Партнёр").run()
    click(app, "💾 Сохранить изменения")
    assert "уже есть в команде или в проектах сессии" in app.error[0].value
    assert [member.name for member in app.session_state[state.TEAM]][2] == "Стажёр"
    # Карточка осталась в режиме правки.
    assert app.session_state[state.EDIT_MEMBER] == "Стажёр"


def test_cancel_and_delete() -> None:
    app = run_page(nav.FIRM, firm_session())
    app.button(key="edit_member_1").click().run()
    app.selectbox(key=state.MEMBER_ROLE).set_value(Role.INTERN).run()
    click(app, "Отмена")
    assert app.session_state[state.TEAM][1].role == Role.ASSOCIATE
    assert app.text_input(key=state.MEMBER_NAME).value == ""
    # Удаление сотрудника, которого правит карточка, закрывает правку.
    app.button(key="edit_member_0").click().run()
    app.button(key="del_member_0").click().run()
    assert [member.name for member in app.session_state[state.TEAM]] == ["Петрова"]
    assert state.EDIT_MEMBER not in app.session_state
    assert "Удалён(а): Иванов" in [item.value for item in app.success]
