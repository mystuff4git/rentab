"""
Rentab v0.2 — модуль этапов проекта.

Отвечает за структурирование IP-проекта:
- этапы с оценкой трудозатрат и назначенными исполнителями;
- выбор патентных пошлин из каталогов Роспатента / Казпатента;
- формирование итогового словаря для страниц Dashboard и Project.

Модель ProjectStage расширена полем assigned_members для хранения
назначенных часов по каждому сотруднику на конкретном этапе.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectStage:
    """Этап юридического / IP-проекта.

    Attributes:
        name: Название этапа (например, «Анализ документов»).
        assigned_members: Список {name, role, billing_rate, cost_rate, hours}.
        complexity_factor: Коэффициент сложности (1.0 = норма, >1.0 = сложнее).
    """

    name: str
    assigned_members: list[dict] = field(default_factory=list)
    complexity_factor: float = 1.0

    def __post_init__(self) -> None:
        """Валидирует параметры этапа сразу после инициализации dataclass.

        Контракт: complexity_factor обязан быть строго положительным —
        нулевое или отрицательное значение сломает расчёты total_hours
        и stage_revenue, поэтому ловим как можно раньше.
        """
        if self.complexity_factor <= 0:
            raise ValueError(
                f"complexity_factor должен быть > 0, получено: {self.complexity_factor}"
            )

    @property
    def total_hours(self) -> float:
        """Суммарные часы по этапу с учётом коэффициента сложности."""
        raw = sum(m["hours"] for m in self.assigned_members)
        return raw * self.complexity_factor

    @property
    def stage_revenue(self) -> float:
        """Выручка по этапу = Σ(billing_rate × hours) × complexity_factor."""
        raw = sum(m["billing_rate"] * m["hours"] for m in self.assigned_members)
        return raw * self.complexity_factor

    @property
    def stage_labor_cost(self) -> float:
        """Прямые трудозатраты = Σ(cost_rate × hours) × complexity_factor."""
        raw = sum(m["cost_rate"] * m["hours"] for m in self.assigned_members)
        return raw * self.complexity_factor


def load_duties_catalog(path: str | Path) -> list[dict]:
    """Загружает каталог патентных пошлин из JSON-файла.

    Ожидаемая структура файла — массив объектов со схемой Expense:
        [
            {"action": "...", "amount": 0, "currency": "RUB",
             "category": "disbursement_billable", "note": "# TODO: ..."},
            ...
        ]

    Args:
        path: Путь к rospatent_duties.json или qazpatent_duties.json.

    Returns:
        Список словарей с ключами action, amount, currency, category.

    Raises:
        FileNotFoundError: Если файл не найден.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Каталог пошлин не найден: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def duties_display_options(duties: list[dict]) -> dict[str, float]:
    """Формирует словарь {отображаемое_название: сумма} для multiselect.

    Args:
        duties: Список словарей из load_duties_catalog().

    Returns:
        Словарь {action: amount} для st.multiselect.

    Example:
        >>> duties = [{"action": "Подача заявки", "amount": 3300, "currency": "RUB"}]
        >>> duties_display_options(duties)
        {'Подача заявки': 3300.0}
    """
    return {str(d["action"]): float(d.get("amount", 0.0)) for d in duties}


def collect_project_data(
    stages: list[ProjectStage],
    selected_duties: list[float],
) -> dict:
    """Агрегирует данные всех этапов и пошлин в единый словарь для расчётов.

    Args:
        stages: Список этапов проекта.
        selected_duties: Суммы выбранных пошлин (из duties_display_options).

    Returns:
        Словарь со следующими полями:
        - team_with_hours      : list[dict]  — сотрудники со всех этапов + часы
        - gross_revenue        : float
        - direct_labor         : float
        - total_hours          : float
        - disbursements_billed : float  — сумма выбранных пошлин (агентский транзит)
    """
    all_members: list[dict] = []
    for stage in stages:
        for member in stage.assigned_members:
            adjusted = dict(member)
            adjusted["hours"] = member["hours"] * stage.complexity_factor
            all_members.append(adjusted)

    gross = sum(m["billing_rate"] * m["hours"] for m in all_members)
    labor = sum(m["cost_rate"] * m["hours"] for m in all_members)
    hours = sum(m["hours"] for m in all_members)

    return {
        "team_with_hours": all_members,
        "gross_revenue": gross,
        "direct_labor": labor,
        "total_hours": hours,
        "disbursements_billed": float(sum(selected_duties)),
    }
