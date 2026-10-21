"""
Rentab v0.2 — модуль финансовых расчётов.

Все функции являются чистыми (pure functions): принимают параметры,
возвращают результат, не изменяют глобальное состояние.
Каждая функция соответствует одной формуле из методологии PSF (Mayster).
"""

from __future__ import annotations

from modules.team import JUNIOR_ROLES, SENIOR_ROLES


# ---------------------------------------------------------------------------
# Тип данных для описания члена команды (используется внутри модуля)
# ---------------------------------------------------------------------------
# team_member: dict со следующими полями:
#   "name"         : str   — имя/ФИО сотрудника
#   "role"         : str   — значение EmployeeRole (см. modules.team)
#   "billing_rate" : float — внешняя ставка для клиента (руб./час или тенге/час)
#   "cost_rate"    : float — себестоимость часа сотрудника (ФОТ + соц. отчисления)
#   "hours"        : float — часы, запланированные на проект


def blended_rate(team: list[dict]) -> float:
    """Взвешенная средняя ставка команды по проекту (Blended Rate).

    Формула: Σ(billing_rate_i × hours_i) / Σ(hours_i)

    Blended Rate показывает «реальную» среднюю стоимость часа проекта с учётом
    структуры команды. Используется при выставлении единой ставки клиенту.

    Args:
        team: Список словарей, каждый содержит "billing_rate" (float) и "hours" (float).

    Returns:
        Blended rate в единицах валюты/час. Возвращает 0.0, если Σhours = 0.

    Example:
        >>> team = [
        ...     {"billing_rate": 15000, "hours": 10},
        ...     {"billing_rate": 6000,  "hours": 40},
        ... ]
        >>> blended_rate(team)
        8400.0
    """
    total_revenue = sum(m["billing_rate"] * m["hours"] for m in team)
    total_hours = sum(m["hours"] for m in team)
    if total_hours == 0:
        return 0.0
    return total_revenue / total_hours


def leverage(team: list[dict]) -> float:
    """Коэффициент рычага команды (Leverage).

    Формула: Σ часов (младшие) / Σ часов (старшие)

    Leverage — индикатор эффективности структуры PSF-команды. Высокий Leverage
    означает, что бо́льшую часть работы выполняют менее дорогие сотрудники под
    надзором партнёров, что улучшает маржинальность.

    Классификация ролей на старших / младших берётся из модуля team
    (SENIOR_ROLES и JUNIOR_ROLES), чтобы оба модуля использовали один источник
    истины и при добавлении роли не требовалось править эту функцию.

    Args:
        team: Список словарей, каждый содержит "role" (str) и "hours" (float).
              Значения role должны попадать либо в team.SENIOR_ROLES,
              либо в team.JUNIOR_ROLES.

    Returns:
        Значение Leverage. Возвращает 0.0, если нет старших сотрудников.

    Example:
        >>> team = [
        ...     {"role": "Партнёр",       "hours": 10},
        ...     {"role": "Младший юрист", "hours": 40},
        ... ]
        >>> leverage(team)
        4.0
    """
    senior_hours = sum(m["hours"] for m in team if m["role"] in SENIOR_ROLES)
    junior_hours = sum(m["hours"] for m in team if m["role"] in JUNIOR_ROLES)

    if senior_hours == 0:
        return 0.0
    return junior_hours / senior_hours


def gross_revenue(team: list[dict]) -> float:
    """Валовая выручка проекта за юридические услуги.

    Формула: Σ(billing_rate_i × hours_i)

    Важно: gross_revenue не включает сквозные расходы (пошлины). Пошлины
    добавляются отдельно при формировании счёта клиенту.

    Args:
        team: Список словарей, каждый содержит "billing_rate" (float) и "hours" (float).

    Returns:
        Суммарная выручка в единицах валюты.

    Example:
        >>> team = [{"billing_rate": 10000, "hours": 20}]
        >>> gross_revenue(team)
        200000.0
    """
    return sum(m["billing_rate"] * m["hours"] for m in team)


def overhead_rate(total_overheads: float, billable_hours_month: float) -> float:
    """Ставка накладных расходов на один оплачиваемый час.

    Формула: total_overheads / billable_hours_month

    Используется для расчёта Overhead Allocation — доли накладных, относимой
    на конкретный проект пропорционально потраченным часам.

    Args:
        total_overheads: Суммарные накладные расходы фирмы за месяц (аренда,
                         ПО, административный персонал и т.д.).
        billable_hours_month: Плановый объём оплачиваемых часов команды в месяц.

    Returns:
        Ставка накладных (валюта/час). Возвращает 0.0 при нулевых часах.

    Example:
        >>> overhead_rate(160000, 160)
        1000.0
    """
    if billable_hours_month == 0:
        return 0.0
    return total_overheads / billable_hours_month


def tax_base(gross: float, disbursements_billed: float) -> float:
    """Налогооблагаемая база при агентском оформлении пошлин.

    Формула: gross − disbursements_billed

    При агентской схеме патентные пошлины перевыставляются клиенту «транзитом»
    и не являются доходом фирмы, поэтому исключаются из налоговой базы.

    Args:
        gross: Общая сумма, полученная от клиента (услуги + пошлины).
        disbursements_billed: Пошлины, перевыставленные клиенту по агентской схеме.

    Returns:
        Налогооблагаемая база (только выручка за услуги).

    Example:
        >>> tax_base(250000, 50000)
        200000.0
    """
    return gross - disbursements_billed


def calculate_tax(gross: float, costs: float, regime: dict) -> float:
    """Расчёт налога по заданному налоговому режиму.

    Поддерживаемые базы (поле "base" в словаре режима):
    - "revenue" : налог = gross × rate  (УСН 6%, НПД, СНР 3%)
    - "profit"  : налог = (gross − costs) × rate  (УСН 15%, ОСНО, ОУР)

    Args:
        gross: Налогооблагаемая выручка (уже за вычетом агентских пошлин,
               если применимо).
        costs: Подтверждённые расходы (используются только при base="profit").
        regime: Словарь из TAX_REGIMES в modules/jurisdiction.py.
                Пример: {"country": "RF", "rate": 0.06, "base": "revenue"}.

    Returns:
        Сумма налога в единицах валюты. Не может быть отрицательной.

    Example:
        >>> regime = {"country": "RF", "rate": 0.06, "base": "revenue"}
        >>> calculate_tax(200000, 80000, regime)
        12000.0
    """
    rate = regime["rate"]
    base_type = regime["base"]

    if base_type == "revenue":
        tax = gross * rate
    elif base_type == "profit":
        taxable = max(gross - costs, 0.0)
        tax = taxable * rate
    else:
        raise ValueError(f"Неизвестный тип налоговой базы: {base_type!r}")

    return max(tax, 0.0)


def nne(
    gross: float,
    direct_labor: float,
    overheads_alloc: float,
    disbursements_own: float,
    tax: float,
) -> float:
    """Net Net Effective — чистая прибыль фирмы от проекта.

    Формула: Gross − Direct Labor − Overheads_alloc − Disbursements_own − Tax

    NNE — ключевой показатель рентабельности PSF-проекта по методологии Mayster.
    Отражает реальную прибыль после покрытия всех прямых и косвенных затрат.

    Args:
        gross: Валовая выручка за услуги (без агентских пошлин).
        direct_labor: Прямые трудозатраты = Σ(cost_rate × hours) команды проекта.
        overheads_alloc: Аллоцированные накладные = overhead_rate × total_hours.
        disbursements_own: Прямые небиллируемые расходы фирмы (командировки,
                           курьеры, нотариус — не перевыставляемые клиенту).
        tax: Налоговая нагрузка, рассчитанная через calculate_tax().

    Returns:
        NNE в единицах валюты. Может быть отрицательным (убыток по проекту).

    Example:
        >>> nne(200000, 70000, 20000, 5000, 12000)
        93000.0
    """
    return gross - direct_labor - overheads_alloc - disbursements_own - tax
