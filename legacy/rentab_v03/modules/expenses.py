"""
Rentab v0.2 — модуль управления расходами фирмы и проекта.

Расходы в IP-юридической практике делятся на несколько категорий, которые
по-разному участвуют в финансовой модели:

- "overhead" (накладные фирмы): аренда, ПО, ассистент, бухгалтерия.
  Относятся к фирме целиком и аллоцируются на проекты пропорционально
  отработанным часам через overhead_rate = Σoverheads / billable_hours.

- "disbursement_billable" (сквозные расходы, перевыставляемые клиенту):
  пошлины Роспатента/Казпатента, пошлины WIPO, нотариус для клиента.
  Платятся фирмой и возмещаются клиентом по агентской схеме — не являются
  доходом, вычитаются из налоговой базы.

- "disbursement_own" (собственные расходы проекта): командировки, курьер,
  перевод документов — несёт фирма. Уменьшают NNE, но не попадают в счёт.

- "project_extra" (прочее по проекту): любые нестандартные статьи,
  которые пользователь решит учесть отдельно.

Классы Expense и ExpenseManager инкапсулируют эту логику, чтобы страницы
Streamlit оперировали объектами, а не сырыми словарями.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import ClassVar

import pandas as pd


# ---------------------------------------------------------------------------
# Единичная статья расхода
# ---------------------------------------------------------------------------
@dataclass
class Expense:
    """Одна статья расхода — накладной фирмы или прямого расхода проекта.

    Attributes:
        name: Название статьи (например «Аренда офиса», «Госпошлина ТЗ»).
        category: Одно из значений ALLOWED_CATEGORIES.
        amount: Сумма расхода в указанной валюте.
        currency: ISO-код валюты: "RUB" | "KZT" | "USD".
        period: Для overhead — "monthly" | "annual"; для прямых расходов
                проекта — обычно "one-time". Используется для приведения
                к месячной сумме в total_overheads_monthly().
        billable: Перевыставляется ли клиенту. Для overhead всегда False.
                  Для disbursement_billable — True.
    """

    ALLOWED_CATEGORIES: ClassVar[frozenset[str]] = frozenset(
        {"overhead", "disbursement_billable", "disbursement_own", "project_extra"}
    )
    ALLOWED_PERIODS: ClassVar[frozenset[str]] = frozenset({"monthly", "annual", "one-time"})
    ALLOWED_CURRENCIES: ClassVar[frozenset[str]] = frozenset({"RUB", "KZT", "USD"})

    name: str
    category: str
    amount: float = 0.0
    currency: str = "RUB"
    period: str = "one-time"
    billable: bool = False

    def __post_init__(self) -> None:
        """Проверяет корректность полей при создании объекта."""
        if self.category not in self.ALLOWED_CATEGORIES:
            raise ValueError(
                f"category={self.category!r}. Допустимые: {sorted(self.ALLOWED_CATEGORIES)}"
            )
        if self.period not in self.ALLOWED_PERIODS:
            raise ValueError(
                f"period={self.period!r}. Допустимые: {sorted(self.ALLOWED_PERIODS)}"
            )
        if self.currency not in self.ALLOWED_CURRENCIES:
            raise ValueError(
                f"currency={self.currency!r}. Допустимые: {sorted(self.ALLOWED_CURRENCIES)}"
            )
        if self.amount < 0:
            raise ValueError(f"amount не может быть отрицательной: {self.amount}")

    def to_dict(self) -> dict:
        """Сериализует Expense в словарь для JSON / session_state."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Expense":
        """Создаёт Expense из словаря (например, прочитанного из JSON).

        Недостающие поля заполняются значениями по умолчанию — это даёт
        устойчивость к неполным записям в JSON-шаблонах и в session_state.

        Args:
            data: Словарь с произвольным набором полей Expense. Единственное
                  поле без «разумного» дефолта — name, но и для него мы
                  подставляем пустую строку, чтобы не падать на битых записях
                  (проверку на непустое имя оставляем вызывающему коду).

        Returns:
            Новый Expense со всеми подстановками дефолтов.
        """
        return cls(
            name=str(data.get("name", "")),
            category=str(data.get("category", "overhead")),
            amount=float(data.get("amount", 0.0)),
            currency=str(data.get("currency", "RUB")),
            period=str(data.get("period", "one-time")),
            billable=bool(data.get("billable", False)),
        )

    def monthly_amount(self) -> float:
        """Приводит сумму к месячному объёму (для расчёта overhead_rate).

        - monthly  → amount
        - annual   → amount / 12
        - one-time → 0.0 (разовые расходы не входят в ежемесячные накладные)
        """
        if self.period == "monthly":
            return self.amount
        if self.period == "annual":
            return self.amount / 12.0
        return 0.0


# ---------------------------------------------------------------------------
# Менеджер расходов
# ---------------------------------------------------------------------------
class ExpenseManager:
    """Менеджер расходов фирмы и проекта.

    Хранит два независимых списка:
    1. firm_overheads — накладные фирмы (ежемесячные расходы, которые
       аллоцируются на проекты).
    2. project_expenses — прямые расходы по конкретному проекту: пошлины,
       командировки и прочее.

    Также хранит `billable_hours_per_month` — среднее число оплачиваемых
    часов всей фирмы за месяц. Используется как знаменатель в формуле
    overhead_rate = Σ(monthly overheads) / billable_hours_per_month,
    а через неё — в аллокации накладных на конкретный проект.

    По умолчанию оба списка пусты: расходы проекта добавляются, только
    если они реально есть в задаче.
    """

    def __init__(self, billable_hours_per_month: float = 160.0) -> None:
        """Создаёт пустой менеджер расходов.

        Args:
            billable_hours_per_month: Плановый объём оплачиваемых часов
                всей фирмы в месяц. 160 часов — средняя норма при полной
                загрузке одного юриста (8 ч × ~20 рабочих дней).
                Можно переопределить атрибутом позднее.
        """
        if billable_hours_per_month <= 0:
            raise ValueError(
                f"billable_hours_per_month должно быть > 0, получено: {billable_hours_per_month}"
            )
        self._overheads: list[Expense] = []
        self._project_expenses: list[Expense] = []
        self.billable_hours_per_month: float = float(billable_hours_per_month)

    # -- накладные фирмы ----------------------------------------------------

    def add_firm_overhead(self, expense: Expense) -> None:
        """Добавляет статью накладных расходов фирмы.

        Args:
            expense: Expense с category="overhead".

        Raises:
            ValueError: Если category отличается от "overhead".
        """
        if expense.category != "overhead":
            raise ValueError(
                f"Ожидалась category='overhead', получено {expense.category!r}. "
                "Для расходов проекта используйте add_project_expense()."
            )
        self._overheads.append(expense)

    def clear_firm_overheads(self) -> None:
        """Очищает список накладных (для повторной инициализации из UI)."""
        self._overheads.clear()

    @property
    def firm_overheads(self) -> list[Expense]:
        """Возвращает копию списка накладных фирмы."""
        return list(self._overheads)

    def total_overheads_monthly(self) -> float:
        """Суммарные накладные за месяц (с учётом period каждой статьи).

        Разовые расходы (period="one-time") в сумму не включаются —
        они не являются регулярными накладными.
        """
        return sum(e.monthly_amount() for e in self._overheads)

    def calculate_overhead_rate(self, billable_hours_per_month: float) -> float:
        """Ставка накладных на час оплачиваемой работы.

        Формула: total_overheads_monthly() / billable_hours_per_month.

        Args:
            billable_hours_per_month: Плановый объём оплачиваемых часов в месяц.

        Returns:
            Ставка (валюта/час). 0.0 при нулевых часах.
        """
        if billable_hours_per_month <= 0:
            return 0.0
        return self.total_overheads_monthly() / billable_hours_per_month

    def get_overheads_allocated(self, direct_costs: float) -> float:
        """Аллоцированные накладные на проект в абсолютном выражении.

        Формула (прямая аллокация):
            overhead_rate = total_overheads_monthly() / billable_hours_per_month
            allocated     = overhead_rate × direct_costs

        direct_costs здесь — любой показатель объёма работы по проекту,
        к которому мы хотим привязать накладные (обычно прямые трудозатраты
        или часы). Ставка берётся как доля месячных накладных на единицу
        стандартного оплачиваемого часа — см. self.billable_hours_per_month.

        Args:
            direct_costs: База для аллокации (трудозатраты либо часы по проекту).

        Returns:
            Сумма накладных, отнесённых на проект. 0.0 при нулевой базе
            или при пустом списке накладных.
        """
        if direct_costs <= 0:
            return 0.0
        # Переиспользуем calculate_overhead_rate — единый источник правды для ставки
        # (DRY + защита от ZeroDivisionError внутри самого метода).
        overhead_rate = self.calculate_overhead_rate(self.billable_hours_per_month)
        return overhead_rate * direct_costs

    # -- расходы проекта ----------------------------------------------------

    def add_project_expense(self, expense: Expense) -> None:
        """Добавляет прямой расход проекта (пошлина, командировка и т.п.).

        Args:
            expense: Expense с category из {"disbursement_billable",
                     "disbursement_own", "project_extra"}.

        Raises:
            ValueError: Если передали expense с category="overhead".
        """
        if expense.category == "overhead":
            raise ValueError(
                "Для накладных фирмы используйте add_firm_overhead()."
            )
        self._project_expenses.append(expense)

    def clear_project_expenses(self) -> None:
        """Очищает список расходов проекта."""
        self._project_expenses.clear()

    @property
    def project_expenses(self) -> list[Expense]:
        """Возвращает копию списка расходов проекта."""
        return list(self._project_expenses)

    def get_disbursements_billed(self) -> float:
        """Сумма перевыставляемых клиенту расходов (пошлины и т.п.).

        Используется в tax_base: taxable_base = gross − disbursements_billed.
        """
        return sum(
            e.amount for e in self._project_expenses
            if e.category == "disbursement_billable"
        )

    def get_disbursements_own(self) -> float:
        """Сумма собственных расходов фирмы по проекту.

        Используется в NNE: вычитается из Gross.
        """
        return sum(
            e.amount for e in self._project_expenses
            if e.category == "disbursement_own"
        )

    def get_project_extras(self) -> float:
        """Сумма прочих расходов проекта (category="project_extra")."""
        return sum(
            e.amount for e in self._project_expenses
            if e.category == "project_extra"
        )


# ---------------------------------------------------------------------------
# Загрузка JSON-шаблонов и конверсия в DataFrame для Streamlit
# ---------------------------------------------------------------------------
def load_firm_overheads(path: str | Path) -> list[Expense]:
    """Загружает шаблон накладных фирмы из data/firm_expenses.json.

    Ожидаемая структура файла:
        [
            {"name": "Аренда офиса", "category": "overhead", "amount": 0,
             "currency": "RUB", "period": "monthly", "billable": false},
            ...
        ]

    Args:
        path: Путь к firm_expenses.json.

    Returns:
        Список Expense с category="overhead".

    Raises:
        FileNotFoundError: Если файл не найден.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл накладных не найден: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return [Expense.from_dict(item) for item in data]


def load_duties_as_expenses(path: str | Path) -> list[Expense]:
    """Загружает каталог пошлин (Роспатент / Казпатент) как список Expense.

    Каждая пошлина становится Expense с category="disbursement_billable",
    period="one-time", billable=True.

    Ожидаемая структура JSON:
        [
            {"action": "...", "amount": 0, "currency": "RUB",
             "category": "disbursement_billable", "note": "# TODO: ..."},
            ...
        ]

    Args:
        path: Путь к rospatent_duties.json или qazpatent_duties.json.

    Returns:
        Список Expense.

    Raises:
        FileNotFoundError: Если файл не найден.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Каталог пошлин не найден: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    expenses: list[Expense] = []
    for item in data:
        expenses.append(
            Expense(
                name=str(item["action"]),
                category=str(item.get("category", "disbursement_billable")),
                amount=float(item.get("amount", 0.0)),
                currency=str(item.get("currency", "RUB")),
                period="one-time",
                billable=True,
            )
        )
    return expenses


def overheads_to_df(overheads: list[Expense]) -> pd.DataFrame:
    """Готовит DataFrame для st.data_editor из списка накладных."""
    rows = [
        {
            "Статья расходов": e.name,
            "Сумма": e.amount,
            "Валюта": e.currency,
            "Период": e.period,
        }
        for e in overheads
    ]
    return pd.DataFrame(rows)


def df_to_overheads(df: pd.DataFrame) -> list[Expense]:
    """Преобразует DataFrame из st.data_editor обратно в список Expense."""
    out: list[Expense] = []
    for _, row in df.iterrows():
        out.append(
            Expense(
                name=str(row["Статья расходов"]),
                category="overhead",
                amount=float(row["Сумма"]),
                currency=str(row.get("Валюта", "RUB")),
                period=str(row.get("Период", "monthly")),
                billable=False,
            )
        )
    return out
