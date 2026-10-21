"""
Rentab v0.2 — модуль управления командой проекта.

Содержит модель сотрудника (Fee Earner) и вспомогательные функции
для преобразования данных и расчёта трудозатрат.

Модель перенесена из v0.1 (models.py → Employee, EmployeeRole)
и расширена helper-функциями для работы со Streamlit data_editor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class EmployeeRole(str, Enum):
    """Роли сотрудников в юридической фирме (иерархия PSF).

    Порядок ролей отражает типичную иерархию профессиональной сервисной фирмы:
    Партнёр > Старший юрист > Младший юрист > Стажёр.

    Значения — русские, потому что эти строки напрямую показываются в UI
    и сохраняются в session_state/JSON. Любой новой роли достаточно
    добавить сюда и (при необходимости) классифицировать в SENIOR_ROLES
    или JUNIOR_ROLES — остальные модули подхватят автоматически.
    """

    PARTNER = "Партнёр"
    SENIOR = "Старший юрист"
    ASSOCIATE = "Младший юрист"
    JUNIOR = "Стажёр"


# Список строковых значений для использования в st.selectbox / st.data_editor
ROLE_OPTIONS: list[str] = [role.value for role in EmployeeRole]

# Разделение ролей для расчёта Leverage (используется calculator.leverage()).
SENIOR_ROLES: set[str] = {EmployeeRole.PARTNER.value, EmployeeRole.SENIOR.value}
JUNIOR_ROLES: set[str] = {EmployeeRole.ASSOCIATE.value, EmployeeRole.JUNIOR.value}


@dataclass
class Employee:
    """Fee Earner — сотрудник, генерирующий выручку.

    Attributes:
        name: ФИО или псевдоним сотрудника.
        role: Роль в фирме (используется для расчёта Leverage).
        billing_rate: Внешняя ставка, предъявляемая клиенту (валюта/час).
        cost_rate: Внутренняя себестоимость часа: ФОТ + соцотчисления
                   (без накладных — они аллоцируются отдельно через overhead_rate).
    """

    name: str
    role: EmployeeRole
    billing_rate: float
    cost_rate: float

    def __post_init__(self) -> None:
        """Проверяет корректность параметров при создании объекта."""
        if self.billing_rate < 0:
            raise ValueError(f"billing_rate не может быть отрицательным: {self.billing_rate}")
        if self.cost_rate < 0:
            raise ValueError(f"cost_rate не может быть отрицательным: {self.cost_rate}")

    def to_dict(self) -> dict:
        """Сериализует объект в словарь для хранения в session_state.

        Returns:
            Словарь с полями name, role (str), billing_rate, cost_rate.
        """
        return {
            "name": self.name,
            "role": self.role.value,
            "billing_rate": self.billing_rate,
            "cost_rate": self.cost_rate,
        }


def default_team_df() -> pd.DataFrame:
    """Создаёт DataFrame с демонстрационным составом команды.

    Используется для инициализации st.data_editor на странице Setup.

    Returns:
        DataFrame с тремя примерными строками (Partner, Senior, Associate).
    """
    return pd.DataFrame(
        {
            "Имя": ["Иванов А.", "Петрова М.", "Сидоров К."],
            "Роль": [
                EmployeeRole.PARTNER.value,
                EmployeeRole.SENIOR.value,
                EmployeeRole.ASSOCIATE.value,
            ],
            "Ставка": [15000.0, 10000.0, 6000.0],
            "Себестоимость": [8000.0, 5500.0, 3500.0],
        }
    )


def team_from_editor(df: pd.DataFrame) -> list[dict]:
    """Преобразует DataFrame из st.data_editor в список словарей.

    Формат выходных словарей совместим с функциями модуля calculator.py
    (поля: name, role, billing_rate, cost_rate).

    Args:
        df: DataFrame из st.data_editor со столбцами:
            "Имя", "Роль", "Ставка", "Себестоимость".

    Returns:
        Список словарей, по одному на каждого сотрудника.

    Example:
        >>> import pandas as pd
        >>> df = pd.DataFrame({
        ...     "Имя": ["Иванов"],
        ...     "Роль": ["Partner"],
        ...     "Ставка": [15000.0],
        ...     "Себестоимость": [8000.0],
        ... })
        >>> team_from_editor(df)[0]["billing_rate"]
        15000.0
    """
    result = []
    for _, row in df.iterrows():
        result.append(
            {
                "name": row["Имя"],
                "role": row["Роль"],
                "billing_rate": float(row["Ставка"]),
                "cost_rate": float(row["Себестоимость"]),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Калькулятор ФОТ — расчёт стоимости сотрудника и его часа.
# ---------------------------------------------------------------------------
# Стандартная норма часов в месяц по договору. Дефолт 168 ч ≈ 8 ч × 21
# рабочих дня; используется как value по умолчанию в UI карточки сотрудника.
DEFAULT_CONTRACT_HOURS_PER_MONTH: float = 168.0


def calculate_employee_full_cost(
    gross_salary: float,
    contract_hours_per_month: float,
    employer_contribution_rate: float,
) -> dict:
    """Рассчитывает полную стоимость сотрудника и стоимость его часа.

    Используется на странице 01_Setup → «Команда» в карточке добавления
    сотрудника, чтобы пользователь не подбирал cost_rate «на глаз»: задаёт
    оклад, контрактные часы и ставку взносов работодателя — получает
    рекомендуемую стоимость часа (полную нагрузку на ФОТ).

    Формула:
        employer_taxes     = gross_salary * employer_contribution_rate
        total_monthly_cost = gross_salary + employer_taxes
        cost_rate_per_hour = total_monthly_cost / contract_hours_per_month

    Args:
        gross_salary: Оклад до вычетов (валюта проекта, в месяц).
        contract_hours_per_month: Сколько часов сотрудник должен работать
            по договору в месяц. Должно быть > 0.
        employer_contribution_rate: Доля взносов работодателя (например,
            0.30 для стандартного тарифа РФ). Должна быть >= 0.

    Returns:
        Словарь с ключами:
          - gross_salary       (float) — оклад на входе;
          - employer_taxes     (float) — gross × rate;
          - total_monthly_cost (float) — gross + employer_taxes;
          - cost_rate_per_hour (float) — total_monthly_cost / часы по договору.

    Raises:
        ValueError: Если contract_hours_per_month <= 0 либо
            employer_contribution_rate < 0 либо gross_salary < 0.

    Example:
        >>> r = calculate_employee_full_cost(200_000, 168, 0.30)
        >>> round(r["cost_rate_per_hour"], 2)
        1547.62
    """
    if gross_salary < 0:
        raise ValueError(
            f"gross_salary не может быть отрицательным: {gross_salary}"
        )
    if contract_hours_per_month <= 0:
        raise ValueError(
            "contract_hours_per_month должно быть > 0, "
            f"получено {contract_hours_per_month}"
        )
    if employer_contribution_rate < 0:
        raise ValueError(
            "employer_contribution_rate не может быть отрицательным: "
            f"{employer_contribution_rate}"
        )

    employer_taxes = gross_salary * employer_contribution_rate
    total_monthly_cost = gross_salary + employer_taxes
    cost_rate_per_hour = total_monthly_cost / contract_hours_per_month

    return {
        "gross_salary": float(gross_salary),
        "employer_taxes": float(employer_taxes),
        "total_monthly_cost": float(total_monthly_cost),
        "cost_rate_per_hour": float(cost_rate_per_hour),
    }


def total_direct_labor(team_with_hours: list[dict]) -> float:
    """Считает прямые трудозатраты команды по проекту.

    Формула: Σ(cost_rate_i × hours_i)

    Прямые трудозатраты — часть NNE-формулы. Включают только ФОТ и соцотчисления;
    накладные учитываются отдельно через overhead_rate × hours.

    Args:
        team_with_hours: Список словарей с полями "cost_rate" (float) и "hours" (float).

    Returns:
        Сумма прямых трудозатрат в единицах валюты.

    Example:
        >>> members = [
        ...     {"cost_rate": 8000, "hours": 10},
        ...     {"cost_rate": 3500, "hours": 40},
        ... ]
        >>> total_direct_labor(members)
        220000.0
    """
    return sum(m["cost_rate"] * m["hours"] for m in team_with_hours)
