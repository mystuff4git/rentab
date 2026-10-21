# Rentab v0.3 — контекст проекта для Claude Code

## О проекте
Rentab — веб-приложение на Python + Streamlit для расчёта стоимости проектов
и смет в IP-юридическом консалтинге. ВКР для НИУ ВШЭ.

Репозиторий: https://github.com/mystuff4git/HSE_Statsurin_Yaroslav
Рабочая папка: Rentab_VKR_v0.3 (скопирована из Rentab_VKR_v0.2, все изменения пушить сюда)

## Стек
Python 3.10+, Streamlit, Pandas, Plotly, openpyxl. Хранение — JSON локально, без БД.

## Структура v0.2 (база, не трогать без необходимости)
- app.py                      — точка входа, навигация
- modules/__init__.py
- modules/calculator.py       — Blended Rate, Leverage, NNE
- modules/jurisdiction.py     — налоги РФ (УСН 6%/15%, ОСНО, НПД, АУСН 8%) и РК (ОУР: ИП/ТОО)
- modules/expenses.py         — Overheads + Direct Disbursements (billable/non-billable)
- modules/team.py             — состав команды: роли, billing_rate, cost_rate
- modules/project.py          — этапы проекта, сборка данных для расчёта
- modules/profile.py          — сохранение и загрузка профиля фирмы (firm_profile.json)
- data/firm_expenses.json     — шаблон накладных фирмы
- data/firm_profile.json      — сохранённый профиль фирмы (автозагрузка при старте)
- data/rospatent_duties.json  — каталог пошлин Роспатента
- data/qazpatent_duties.json  — каталог пошлин Казпатента
- pages/01_Setup.py           — юрисдикция, команда, накладные фирмы
- pages/02_Project.py         — формирование сметы (billing-модель)
- pages/03_Dashboard.py       — дашборд, пирог стоимости, экспорт в Excel
- test_scenario.py            — сквозные тесты расчётов
- requirements.txt
- README.md

## Что строим в v0.3 — новые модули
- modules/fixed_price.py      — расчёт фиксированной цены от издержек + целевая маржа,
                                красные флаги по этапам

## Ключевые изменения v0.3 относительно v0.2
1. Выбор модели ценообразования в проекте: "Биллинговая" | "Фиксированная"
2. Фикс-прайс модель:
   Fixed Price = Total Costs / (1 - target_margin - effective_tax_rate)
   Total Costs = Direct Labor + Overheads_alloc + Disbursements_own
   Флаги по этапам: green (маржа >= target) / yellow (0..target) / red (<=0)
3. Смета как документ (не просто таблица данных):
   - Лист 1 "Смета" — для клиента, оформленный документ
   - Лист 2 "Внутренний расчёт" — полный breakdown для фирмы
   Содержимое зависит от модели (billing vs fixed)
4. ФОТ по сотрудникам: полная стоимость сотрудника с учётом
   налогов работодателя и доли накладных

## Расчётные формулы
Billing-модель (v0.2, без изменений):
  Blended Rate = Σ(billing_rate × hours) / Σ(hours)
  Leverage = часы младших / часы партнёров
  NNE = Gross Revenue − Direct Labor − Overheads_alloc − Disbursements_own − Tax
  Taxable Base = Gross Revenue − Disbursements_billed

Fixed-модель (новая в v0.3):
  Total Costs = Direct Labor + Overheads_alloc + Disbursements_own
  Fixed Price = Total Costs / (1 - target_margin - effective_tax_rate)
  NNE = Fixed Price − Total Costs − Tax

## Требования к коду
- Docstring на каждой функции и классе
- Все налоговые ставки только в TAX_RATES_RF_2026 и TAX_RATES_KZ_2026 в jurisdiction.py
- Никаких хардкодных числовых значений вне констант
- Интерфейс полностью на русском языке
- Streamlit API актуальный: использовать width='stretch' вместо
  устаревшего use_container_width=True (deprecated после 2025-12-31)
- Код читаемый для комиссии ВКР

## Текущая задача
Итерация 1: создать папку v0.3, скопировать базу из v0.2, добавить пустой
fixed_price.py, заменить все use_container_width на width= по всему коду,
убедиться что приложение запускается без warnings.