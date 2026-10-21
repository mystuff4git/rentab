"""
Rentab v0.2 — модуль налоговых режимов РФ и РК.

Содержит:
- Константные словари ставок (TAX_RATES_RF_2026, TAX_RATES_KZ_2026),
  в которых собраны все ставки налогов, НДС и соцотчислений по состоянию
  на 2026 год. Это единственное место для правки ставок при изменении
  законодательства — логика ниже параметризуется через эти словари.
- Пресеты режимов (REGIME_PRESETS) — готовые комбинации параметров
  для селектора на странице 01_Setup.
- Класс TaxCalculator — вычисляет налоговую нагрузку по переданному
  набору параметров (revenue + params), а также (для РК) зарплатные
  налоги и взносы через add_payroll_taxes().

Налоговая база считается по принципу агентского договора:
    taxable_base = gross_revenue − disbursements_billed
Так пошлины (перевыставляемые клиенту «транзитом») не попадают
в выручку фирмы и, соответственно, в базу налогообложения.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Константы ставок — РФ, на 2026 г.
# ---------------------------------------------------------------------------
TAX_RATES_RF_2026: dict[str, Any] = {
    # УСН: ставка зависит от объекта налогообложения
    "USN": {
        "income": 0.06,                  # «Доходы»
        "income_minus_expenses": 0.15,   # «Доходы минус расходы»
    },
    # НДС для плательщиков УСН (с 2026 г. при превышении порога выручки)
    "USN_VAT": {
        "none": 0.0,
        "5%": 0.05,
        "7%": 0.07,
    },
    # ОСНО
    "OSNO": {
        "profit": 0.25,   # налог на прибыль (ставка с 2025 г.)
        "vat": 0.22,      # стандартная ставка НДС (с 2026 г.)
    },
    # НПД (самозанятость)
    "NPD": {
        "individual": 0.04,     # с доходов от физлиц
        "legal_entity": 0.06,   # с доходов от юрлиц и ИП
    },
    # Страховые взносы работодателя с ФОТ
    "SOCIAL_CONTRIBUTIONS": {
        "standard": 0.30,   # стандартный тариф
        "zero": 0.0,        # для плательщиков АУСН
    },
    # НДФЛ (упрощённая двухступенчатая модель по запросу ВКР):
    # 13% до порога, 15% сверх. Порог трактуется как годовая сумма,
    # т.е. для применения прогрессии в gross_salary нужно передавать
    # годовой (а не месячный) доход работника.
    "NDFL": {
        "base_rate": 0.13,
        "high_rate": 0.15,
        "threshold": 5_000_000,   # руб./год
    },
    # АУСН — Автоматизированная УСН (с 2022 г., пилотный режим).
    # Фиксированная ставка 8% с дохода; НДС не применяется;
    # страховые взносы 0% (работодатель не платит, бюджет компенсирует).
    # Ограничения: доход до 60 млн ₽/год, штат до 5 чел.
    "AUSN": {
        "country": "RF",
        "income_tax_rate": 0.08,
        "vat": None,
        "insurance_contributions": 0.0,
        "label": "АУСН — Автоматизированная УСН (8%, без страховых взносов)",
    },
}


# ---------------------------------------------------------------------------
# Константы ставок — РК, на 2026 г.
# ---------------------------------------------------------------------------
TAX_RATES_KZ_2026: dict[str, Any] = {
    # Общеустановленный режим (ОУР) — единственный доступный для юруслуг
    "CIT": 0.20,      # КПН (корпоративный подоходный налог) для ТОО
    "IIT_IP": 0.10,   # ИПН для ИП с чистой прибыли
    "VAT": {
        "none": 0.0,
        "16%": 0.16,
    },
    # Налоги и отчисления с зарплаты — удерживаются у работника
    "PAYROLL_EMPLOYEE": {
        "ОПВ":   0.10,   # обязательные пенсионные взносы
        "ИПН":   0.10,   # индивидуальный подоходный налог
        "ВОСМС": 0.02,   # взносы на ОСМС
    },
    # Платит работодатель сверх оклада
    "PAYROLL_EMPLOYER": {
        "ООСМС": 0.03,   # отчисления на ОСМС
        "СО":    0.05,   # социальные отчисления
        "ОПВР":  0.035,  # обязательные пенсионные взносы работодателя
    },
}


# ---------------------------------------------------------------------------
# Общие справочники
# ---------------------------------------------------------------------------
COUNTRY_NAMES: dict[str, str] = {
    "RF": "Российская Федерация",
    "KZ": "Республика Казахстан",
}

# Символ валюты для UI-отображения (метрики, подписи колонок).
CURRENCY_SYMBOLS: dict[str, str] = {
    "RF": "₽",
    "KZ": "₸",
}

# ISO-код валюты (используется в JSON-справочниках и Expense).
CURRENCY_CODES: dict[str, str] = {
    "RF": "RUB",
    "KZ": "KZT",
}


# ---------------------------------------------------------------------------
# Извлечение ставки взносов работодателя из jurisdiction_params.
# Используется в карточке сотрудника на 01_Setup для дефолтного значения
# поля «Страховые взносы работодателя».
# ---------------------------------------------------------------------------
def get_employer_contribution_rate(jurisdiction_params: dict | None) -> float:
    """Возвращает суммарную ставку взносов работодателя из настроек юрисдикции.

    Логика:
      - РФ + УСН/ОСНО — берём по ключу social_contributions из
        TAX_RATES_RF_2026["SOCIAL_CONTRIBUTIONS"] (standard / zero).
      - РФ + АУСН — фиксированный 0% (по конструкции режима).
      - РФ + НПД — самозанятость, работодателя нет → 0%.
      - РК — сумма ставок PAYROLL_EMPLOYER (ООСМС + СО + ОПВР).
      - Режим «Оба» (jurisdiction == "Both") — берём РФ как основную.
      - Если ничего не определено — возвращаем стандартный РФ-тариф 0.30.

    Args:
        jurisdiction_params: Словарь, лежащий в session_state["jurisdiction_params"]
            (см. формат в pages/01_Setup.py). Может быть None — тогда
            используем стандартный тариф РФ.

    Returns:
        Доля от 0 до 1 — суммарная ставка взносов с ФОТ.
    """
    if not jurisdiction_params:
        return TAX_RATES_RF_2026["SOCIAL_CONTRIBUTIONS"]["standard"]

    jur = jurisdiction_params.get("jurisdiction", "RF")

    # Режим «Оба» сводим к РФ — у юриста-резидента работодатель один.
    if jur in ("RF", "Both"):
        rf = jurisdiction_params.get("rf") or {}
        regime = rf.get("regime", "USN")
        if regime == "AUSN":
            return float(TAX_RATES_RF_2026["AUSN"]["insurance_contributions"])
        if regime == "NPD":
            return 0.0
        sc_key = rf.get("social_contributions", "standard")
        return float(TAX_RATES_RF_2026["SOCIAL_CONTRIBUTIONS"].get(sc_key, 0.30))

    if jur == "KZ":
        return float(sum(TAX_RATES_KZ_2026["PAYROLL_EMPLOYER"].values()))

    return float(TAX_RATES_RF_2026["SOCIAL_CONTRIBUTIONS"]["standard"])


# ---------------------------------------------------------------------------
# Пресеты режимов — готовые конфигурации для селектора в UI
# ---------------------------------------------------------------------------
# Каждый пресет — словарь с ключами:
#   "key"    : уникальный идентификатор пресета
#   "label"  : человекочитаемое название для st.selectbox
#   "params" : словарь параметров, принимаемый TaxCalculator.calculate_tax()

REGIME_PRESETS: dict[str, list[dict]] = {
    "RF": [
        {
            "key": "RF_USN_income",
            "label": "УСН Доходы (6%)",
            "params": {
                "country": "RF",
                "regime": "USN",
                "object": "income",
                "vat": "none",
                "social_contributions": "standard",
            },
        },
        {
            "key": "RF_USN_income_expenses",
            "label": "УСН Доходы − Расходы (15%)",
            "params": {
                "country": "RF",
                "regime": "USN",
                "object": "income_minus_expenses",
                "vat": "none",
                "social_contributions": "standard",
            },
        },
        {
            "key": "RF_OSNO",
            "label": "ОСНО — прибыль 25% + НДС 22%",
            "params": {
                "country": "RF",
                "regime": "OSNO",
                "social_contributions": "standard",
            },
        },
        {
            "key": "RF_NPD_legal",
            "label": "НПД — самозанятость (6% с юрлиц)",
            "params": {
                "country": "RF",
                "regime": "NPD",
                "client_type": "legal_entity",
            },
        },
        {
            "key": "RF_NPD_individual",
            "label": "НПД — самозанятость (4% с физлиц)",
            "params": {
                "country": "RF",
                "regime": "NPD",
                "client_type": "individual",
            },
        },
    ],
    "KZ": [
        {
            "key": "KZ_OUR_too",
            "label": "ОУР — ТОО (КПН 20%)",
            "params": {
                "country": "KZ",
                "regime": "OUR",
                "form": "too",
                "vat": "none",
            },
        },
        {
            "key": "KZ_OUR_too_vat",
            "label": "ОУР — ТОО + НДС 16%",
            "params": {
                "country": "KZ",
                "regime": "OUR",
                "form": "too",
                "vat": "16%",
            },
        },
        {
            "key": "KZ_OUR_ip",
            "label": "ОУР — ИП (ИПН 10%)",
            "params": {
                "country": "KZ",
                "regime": "OUR",
                "form": "ip",
                "vat": "none",
            },
        },
    ],
}


def get_presets_by_country(country: str) -> list[dict]:
    """Возвращает список пресетов налоговых режимов для страны.

    Args:
        country: Код страны — "RF" или "KZ".

    Returns:
        Список словарей-пресетов с ключами key/label/params.

    Raises:
        ValueError: Если страна не поддерживается.
    """
    if country not in REGIME_PRESETS:
        raise ValueError(f"Неизвестная страна: {country!r}. Допустимые: {list(REGIME_PRESETS)}")
    return REGIME_PRESETS[country]


def get_preset(key: str) -> dict:
    """Возвращает пресет по его уникальному ключу.

    Args:
        key: Значение поля "key", например "RF_USN_income".

    Returns:
        Словарь пресета с ключами key/label/params.

    Raises:
        KeyError: Если пресет с таким ключом не найден.
    """
    for country_presets in REGIME_PRESETS.values():
        for preset in country_presets:
            if preset["key"] == key:
                return preset
    raise KeyError(f"Пресет с ключом {key!r} не найден")


def get_currency_symbol(country: str) -> str:
    """Возвращает символ валюты для UI (₽ / ₸)."""
    if country not in CURRENCY_SYMBOLS:
        raise ValueError(f"Неизвестная страна: {country!r}")
    return CURRENCY_SYMBOLS[country]


def get_currency_code(country: str) -> str:
    """Возвращает ISO-код валюты (RUB / KZT)."""
    if country not in CURRENCY_CODES:
        raise ValueError(f"Неизвестная страна: {country!r}")
    return CURRENCY_CODES[country]


# ---------------------------------------------------------------------------
# Налоговый калькулятор
# ---------------------------------------------------------------------------
class TaxCalculator:
    """Калькулятор налоговой нагрузки по выбранному режиму.

    Режим задаётся словарём params (структура см. REGIME_PRESETS).
    Все числовые ставки берутся из TAX_RATES_RF_2026 / TAX_RATES_KZ_2026 —
    никаких хардкодных значений в логике.

    Типовой сценарий:
        >>> preset = get_preset("RF_USN_income")
        >>> calc = TaxCalculator(preset["params"])
        >>> result = calc.calculate_tax(revenue=250_000, params={
        ...     **preset["params"],
        ...     "expenses": 80_000,
        ...     "disbursements_billed": 50_000,
        ... })
        >>> result["total_tax"]
        12000.0
    """

    def __init__(self, params: dict):
        """Сохраняет параметры режима.

        Args:
            params: Словарь вида {"country": "RF"|"KZ", "regime": "...",
                    ...}, как в REGIME_PRESETS[...]["params"].

        Raises:
            ValueError: Если страна не поддерживается.
        """
        if "country" not in params or "regime" not in params:
            raise ValueError("params должен содержать 'country' и 'regime'")
        if params["country"] not in COUNTRY_NAMES:
            raise ValueError(f"Неизвестная страна: {params['country']!r}")
        self.params = dict(params)

    # -- публичный API ------------------------------------------------------

    def calculate_tax(self, revenue: float, params: dict | None = None) -> dict:
        """Рассчитывает налоги по заданному режиму.

        Args:
            revenue: Общая сумма, полученная от клиента (услуги + пошлины).
                     При агентской схеме пошлины исключаются из базы через
                     params["disbursements_billed"].
            params: Словарь параметров режима. Если None — используется
                    self.params. Поддерживаемые ключи (помимо country/regime):
                    - "expenses"             : float, подтверждённые расходы
                                               (используются в базах "profit")
                    - "disbursements_billed" : float, перевыставленные клиенту
                                               пошлины (вычитаются из базы)
                    Специфичные ключи:
                    - RF/USN:   "object" ∈ {"income","income_minus_expenses"},
                                "vat" ∈ {"none","5%","7%"}
                    - RF/OSNO:  (НДС и прибыль фиксированные)
                    - RF/NPD:   "client_type" ∈ {"individual","legal_entity"}
                    - KZ/OUR:   "form" ∈ {"too","ip"},
                                "vat" ∈ {"none","16%"}

        Returns:
            Словарь со структурой:
            {
                "regime_label"        : str,     # подпись режима
                "revenue"             : float,   # исходная выручка
                "taxable_base"        : float,   # revenue − disbursements_billed
                "income_tax_base"     : float,   # база налога на доход
                "income_tax_rate"     : float,   # применённая ставка
                "income_tax"          : float,   # налог на доход
                "vat_rate"            : float,   # применённая ставка НДС
                "vat"                 : float,   # сумма НДС
                "total_tax"           : float,   # общий налог (доход + НДС)
                "details"             : dict,    # произвольные детали по режиму
            }
        """
        p = dict(self.params)
        if params is not None:
            p.update(params)

        expenses = float(p.get("expenses", 0.0))
        disb_billed = float(p.get("disbursements_billed", 0.0))
        taxable_base = max(revenue - disb_billed, 0.0)

        country = p["country"]
        if country == "RF":
            return self._calc_rf(p, taxable_base, expenses, revenue)
        if country == "KZ":
            return self._calc_kz(p, taxable_base, expenses, revenue)
        raise ValueError(f"Неизвестная страна: {country!r}")

    def add_payroll_taxes(self, gross_salary: float) -> dict:
        """Рассчитывает зарплатные налоги и взносы (РФ и РК).

        Метод раскладывает начисленную («грязную») зарплату на:
        - суммы, удерживаемые у работника (РФ: НДФЛ; РК: ОПВ/ИПН/ВОСМС);
        - суммы, уплачиваемые работодателем сверху
          (РФ: страховые взносы; РК: ООСМС/СО/ОПВР).

        Используется при расчёте cost_rate сотрудника в Setup: полная
        себестоимость часа = (зарплата + взносы работодателя) / часы.

        Args:
            gross_salary: Начисленная зарплата работника.
                          Для РФ — трактуется как годовая (для применения
                          порога НДФЛ 5 млн руб.); для РК — месячная.

        Returns:
            Словарь:
            {
                "country"               : str,             # "RF" | "KZ"
                "gross_salary"          : float,           # «грязная» зарплата
                "employee"              : dict[str,float], # что удержали у работника
                "employer"              : dict[str,float], # что платит работодатель
                "employee_total"        : float,           # сумма удержаний
                "employer_total"        : float,           # сумма взносов работодателя
                "net_to_employee"       : float,           # «на руки»
                "total_employer_cost"   : float,           # полная стоимость работника
            }

        Raises:
            ValueError: Если юрисдикция не поддерживается или gross_salary < 0.
        """
        if gross_salary < 0:
            raise ValueError(f"gross_salary не может быть отрицательной: {gross_salary}")

        country = self.params["country"]
        if country == "RF":
            return self._payroll_rf(gross_salary)
        if country == "KZ":
            return self._payroll_kz(gross_salary)
        raise ValueError(f"add_payroll_taxes: неизвестная юрисдикция {country!r}")

    def _payroll_rf(self, gross_salary: float) -> dict:
        """Зарплатные налоги по РФ (упрощённая модель НДФЛ 13%/15%).

        Работодатель платит сверху страховые взносы: 30% (standard) либо
        0% (zero) — для плательщиков АУСН. Тариф читается из self.params
        (ключ "social_contributions"); если ключа нет — берётся "standard".

        НДФЛ с работника — двухступенчатая прогрессия из TAX_RATES_RF_2026:
        base_rate до threshold, high_rate — со сверхпороговой части.
        """
        ndfl = TAX_RATES_RF_2026["NDFL"]
        sc_rates = TAX_RATES_RF_2026["SOCIAL_CONTRIBUTIONS"]

        sc_choice = self.params.get("social_contributions", "standard")
        if sc_choice not in sc_rates:
            raise ValueError(
                f"Неизвестный тариф страховых взносов: {sc_choice!r}. "
                f"Допустимые: {list(sc_rates)}"
            )
        sc_rate = sc_rates[sc_choice]

        threshold = ndfl["threshold"]
        if gross_salary <= threshold:
            ndfl_amount = gross_salary * ndfl["base_rate"]
        else:
            ndfl_amount = (
                threshold * ndfl["base_rate"]
                + (gross_salary - threshold) * ndfl["high_rate"]
            )

        insurance = gross_salary * sc_rate

        employee = {"НДФЛ": ndfl_amount}
        employer = {"Страховые взносы": insurance}
        emp_total = sum(employee.values())
        er_total = sum(employer.values())

        return {
            "country": "RF",
            "gross_salary": gross_salary,
            "employee": employee,
            "employer": employer,
            "employee_total": emp_total,
            "employer_total": er_total,
            "net_to_employee": gross_salary - emp_total,
            "total_employer_cost": gross_salary + er_total,
        }

    def _payroll_kz(self, gross_salary: float) -> dict:
        """Зарплатные налоги и взносы по РК (ОПВ/ИПН/ВОСМС + ООСМС/СО/ОПВР)."""
        emp_rates = TAX_RATES_KZ_2026["PAYROLL_EMPLOYEE"]
        er_rates = TAX_RATES_KZ_2026["PAYROLL_EMPLOYER"]

        employee = {name: gross_salary * rate for name, rate in emp_rates.items()}
        employer = {name: gross_salary * rate for name, rate in er_rates.items()}
        emp_total = sum(employee.values())
        er_total = sum(employer.values())

        return {
            "country": "KZ",
            "gross_salary": gross_salary,
            "employee": employee,
            "employer": employer,
            "employee_total": emp_total,
            "employer_total": er_total,
            "net_to_employee": gross_salary - emp_total,
            "total_employer_cost": gross_salary + er_total,
        }

    # -- внутренние расчёты -------------------------------------------------

    def _calc_rf(
        self,
        p: dict,
        taxable_base: float,
        expenses: float,
        revenue: float,
    ) -> dict:
        """Расчёт налогов для РФ. Не вызывать напрямую."""
        rates = TAX_RATES_RF_2026
        regime = p["regime"]

        if regime == "USN":
            obj = p.get("object", "income")
            if obj not in rates["USN"]:
                raise ValueError(f"Неизвестный объект УСН: {obj!r}")
            income_rate = rates["USN"][obj]

            if obj == "income":
                income_tax_base = taxable_base
            else:
                income_tax_base = max(taxable_base - expenses, 0.0)

            income_tax = income_tax_base * income_rate

            vat_choice = p.get("vat", "none")
            if vat_choice not in rates["USN_VAT"]:
                raise ValueError(f"Неизвестная ставка НДС УСН: {vat_choice!r}")
            vat_rate = rates["USN_VAT"][vat_choice]
            vat = taxable_base * vat_rate

            label = f"УСН {obj} ({income_rate * 100:.0f}%)"

        elif regime == "OSNO":
            income_rate = rates["OSNO"]["profit"]
            profit = max(taxable_base - expenses, 0.0)
            income_tax_base = profit
            income_tax = profit * income_rate

            vat_rate = rates["OSNO"]["vat"]
            vat = taxable_base * vat_rate

            label = f"ОСНО (прибыль {income_rate * 100:.0f}% + НДС {vat_rate * 100:.0f}%)"

        elif regime == "AUSN":
            # АУСН: плоские 8% от дохода, НДС и взносы не применяются.
            # Налоговая база — как у «УСН Доходы», просто ставка выше.
            ausn = rates["AUSN"]
            income_rate = float(ausn["income_tax_rate"])
            income_tax_base = taxable_base
            income_tax = taxable_base * income_rate
            vat_rate = 0.0
            vat = 0.0
            label = ausn["label"]

        elif regime == "NPD":
            client_type = p.get("client_type", "legal_entity")
            if client_type not in rates["NPD"]:
                raise ValueError(f"Неизвестный тип клиента для НПД: {client_type!r}")
            income_rate = rates["NPD"][client_type]

            income_tax_base = taxable_base
            income_tax = taxable_base * income_rate
            vat_rate = 0.0
            vat = 0.0

            label = f"НПД ({income_rate * 100:.0f}%)"

        else:
            raise ValueError(f"Неизвестный режим РФ: {regime!r}")

        return {
            "regime_label": label,
            "revenue": revenue,
            "taxable_base": taxable_base,
            "income_tax_base": income_tax_base,
            "income_tax_rate": income_rate,
            "income_tax": income_tax,
            "vat_rate": vat_rate,
            "vat": vat,
            "total_tax": income_tax + vat,
            "details": {
                "regime": regime,
                "object": p.get("object"),
                "client_type": p.get("client_type"),
                "vat_choice": p.get("vat"),
            },
        }

    def _calc_kz(
        self,
        p: dict,
        taxable_base: float,
        expenses: float,
        revenue: float,
    ) -> dict:
        """Расчёт налогов для РК. Не вызывать напрямую."""
        rates = TAX_RATES_KZ_2026
        regime = p["regime"]
        if regime != "OUR":
            raise ValueError(
                f"Для РК юридические услуги поддерживают только ОУР, получено: {regime!r}"
            )

        form = p.get("form", "too")
        profit = max(taxable_base - expenses, 0.0)

        if form == "too":
            income_rate = rates["CIT"]
            label_form = "ТОО (КПН)"
        elif form == "ip":
            income_rate = rates["IIT_IP"]
            label_form = "ИП (ИПН)"
        else:
            raise ValueError(f"Неизвестная форма организации в РК: {form!r}")

        income_tax = profit * income_rate

        vat_choice = p.get("vat", "none")
        if vat_choice not in rates["VAT"]:
            raise ValueError(f"Неизвестная ставка НДС РК: {vat_choice!r}")
        vat_rate = rates["VAT"][vat_choice]
        vat = taxable_base * vat_rate

        label = f"ОУР — {label_form} {income_rate * 100:.0f}%"
        if vat_rate > 0:
            label += f" + НДС {vat_rate * 100:.0f}%"

        return {
            "regime_label": label,
            "revenue": revenue,
            "taxable_base": taxable_base,
            "income_tax_base": profit,
            "income_tax_rate": income_rate,
            "income_tax": income_tax,
            "vat_rate": vat_rate,
            "vat": vat,
            "total_tax": income_tax + vat,
            "details": {
                "regime": regime,
                "form": form,
                "vat_choice": vat_choice,
            },
        }
