# Файл профиля Rentab (JSON)

Файл, который пользователь скачивает на главной («💾 Скачать JSON») и
загружает обратно («📂 Загрузить из файла»). Приложение его не хранит: файл
остаётся у пользователя (L-10). Код — `src/rentab_core/io/`, решения —
[ADR 0006](adr/0006-phase4-privacy-and-data.md), JSON Schema —
[`profile.schema.json`](profile.schema.json) (генерируется из моделей:
`uv run python -m rentab_core.io docs/profile.schema.json`).

## Пример

Сокращённый файл демо-фирмы:

```json
{
  "format": "rentab",
  "schema_version": 1,
  "exported_at": "2026-10-22T15:00:00Z",
  "app_version": "1.0.0",
  "firm": {
    "name": "Демо-фирма «Слайд 3»",
    "jurisdiction": {
      "mode": "RF",
      "rf": {"regime": "usn", "tax_object": "income", "vat": "5%", "social_contributions": "standard"},
      "kz": null
    },
    "team": [
      {
        "name": "Партнёр", "role": "partner",
        "compensation": {"kind": "monthly", "amount": "400000"},
        "currency": "RUB", "billing_rate": "10000",
        "contract_hours_per_month": "160", "target_utilization": "0.625",
        "paid_from_profit": false
      }
    ],
    "overheads": [{"name": "Аренда офиса", "amount": "18000", "currency": "RUB", "period": "monthly"}],
    "exchange_rates": {"rub_per_kzt": null, "rub_per_usd": null},
    "facts": {
      "rf_legal_form": "organization", "annual_income_rf": "24000000",
      "annual_income_kz": null, "headcount": null, "agency_contracts": false
    }
  },
  "rates_quote": null,
  "projects": [
    {
      "project": {
        "name": "Регистрация товарного знака", "client": "ООО «Ромашка»", "description": "…",
        "pricing": {"kind": "hourly"}, "target_margin": "0.30", "vat_mode": "on_top",
        "stages": [
          {"name": "Анализ охраноспособности",
           "assignments": [{"employee": "Партнёр", "hours": "2"}, {"employee": "Юрист", "hours": "8"}]}
        ],
        "disbursements": [
          {"name": "Нотариальный перевод доверенности", "kind": "own", "amount": "1800",
           "currency": "RUB", "stage": "Подготовка и подача заявки"}
        ],
        "calc_country": null
      },
      "duties": {
        "RF": {
          "actions": ["Регистрация заявки на товарный знак и формальная экспертиза"],
          "quantities": {"classes": 3},
          "category": "legal_entity",
          "amounts_at_export": {"Регистрация заявки на товарный знак и формальная экспертиза": "6000"}
        }
      }
    }
  ]
}
```

## Поля

| Поле | Что это |
|---|---|
| `format`, `schema_version` | Всегда `"rentab"`; версия схемы (сейчас 1). Файл более новой версии не загружается. |
| `exported_at`, `app_version` | Справочно: когда и какой версией выгружен файл. |
| `firm` | Модель ядра `Firm`: юрисдикция и режимы, команда, накладные, курсы, данные для проверки режимов (`FirmFacts`). |
| `firm.team[].compensation` | `{"kind": "salary", "gross_salary", "employer_contribution_rate"}` — оклад и ставка взносов (доля) или `{"kind": "monthly", "amount"}` — сумма в месяц (нормативное вознаграждение партнёра). |
| `firm.team[].target_utilization`, `paid_from_profit` | Утилизация (доля) и «вознаграждение выплачивается из прибыли». |
| `firm.jurisdiction.rf.social_contributions` | Тариф взносов: `standard`, `sme` (МСП), `zero`. |
| `firm.exchange_rates` | Курсы, которые идут в расчёт: ₽ за 1 ₸ и ₽ за 1 $; `null` — не задан. |
| `rates_quote` | Последние загруженные курсы: `rates`, `source` (`cbr`, `nbrk`), `rate_date`, `requested`. Если курсы потом правили вручную, они отличаются от `firm.exchange_rates`. |
| `projects[].project` | Модель ядра `Project`: модель цены, целевая маржа (доля), режим НДС, этапы с назначениями, страна расчёта. `disbursements` — только расходы, введённые вручную. |
| `projects[].project.actuals` | Факт проекта (план–факт, `methodology.md`, раздел 18): `hours` — фактические часы по парам «этап × сотрудник» (`stage`, `employee`, `hours`), `billed` — фактически выставлено клиенту в режиме НДС проекта (`null` — по модели цены). Нет поля или `null` — факт не введён. |
| `projects[].project.stages[].description` | Что получает клиент на этапе — строка под названием этапа в КП (фаза 7); в расчёт не входит. Пустое описание не пишется. |
| `projects[].offer` | Условия КП (фаза 7): `number` — номер, `validity_days` — срок действия в днях (по умолчанию 30), `payment_terms`, `execution_term`, `extra_terms` — порядок оплаты, срок выполнения, дополнительные условия. Нет поля — условия по умолчанию. |
| `letterhead` | Бланк фирмы для КП (фаза 7): `requisites` (ИНН/КПП/ОГРН или БИН одной строкой), `address`, `phone`, `email`, `website`, `signatory_name`, `signatory_position`, `bank_details`, `logo`. Название фирмы — `firm.name`. Нет поля — бланк не заполнен. |
| `letterhead.logo` | Логотип: `media_type` (`image/png`, `image/jpeg`) и `data` — файл в base64, до 500 КБ (в base64 ≈ 670 КБ) и до 4000 px по стороне. Повреждённый логотип при загрузке пропускается с предупреждением. |
| `projects[].duties` | Выбранные пошлины по стране (`RF`, `KZ`): действия каталога, количества (`classes`, `items_over_limit`, `claims`, `independent_claims`, `designs`; не указанное — по умолчанию), категория заявителя (`legal_entity`, `sme`, `individual`). |
| `projects[].duties.*.amounts_at_export` | Суммы пошлин на дату выгрузки — справочно. |

Суммы, ставки, доли и часы пишутся строками, чтобы не терять точность
Decimal; числа JSON при загрузке тоже читаются без потерь. Смета в файл не
входит: интерфейс считает её по данным проекта.

## Что происходит при загрузке

- Пошлины пересчитываются по текущему каталогу. Если сумма отличается от
  `amounts_at_export`, интерфейс показывает: «было … ₽, по текущему каталогу
  … ₽». Прежние названия позиций (v0.3) заменяются текущими, неизвестные
  пошлины пропускаются с предупреждением.
- Назначение на сотрудника, которого нет в команде, остаётся в проекте, но
  в расчёт не входит — с предупреждением.
- Дробная целевая маржа (32,5%) округляется до целых процентов
  интерфейса — с предупреждением.
- Логотип открывается как картинка; если он повреждён или больше 4000 px
  по стороне, он не загружается — с предупреждением, остальной бланк остаётся.
- Ошибки называют поле: `projects[0].project.stages[0].assignments[0].hours:
  должно быть не меньше 0`.

## Профиль v0.3 (`data/firm_profile.json`)

Загружается той же кнопкой и переводится в текущий формат (L-16):

| v0.3 | Rentab 1 |
|---|---|
| `jurisdiction_params` | `firm.jurisdiction` |
| `team[].role` — русская подпись | `role`: `partner`, `senior`, `associate`, `intern` |
| `cost_rate`, `gross_salary`, `employer_contribution_rate`, `contract_hours_per_month` | Оклад и взносы, если `cost_rate` = оклад × (1 + взносы) / часы; иначе сумма в месяц `cost_rate × часы`. Утилизация — 65% (L-01). |
| `employer_contribution_rate` = умолчание v0.3 (30% и т. п.) | Пересчитывается по реестру 2026 года с предельной базой (L-08); ставка, заданная вручную, остаётся. |
| `firm_expenses` (`category: overhead`) | `firm.overheads`; расходы других категорий пропускаются. |
| `billable_hours_per_month`, `contract_hours_total` | Не переносятся: ёмкость считается по команде. |
| `exchange_rate_rub_per_kzt` | `firm.exchange_rates.rub_per_kzt` |
| `pricing_model` — подпись радиокнопки, `target_margin` — целые проценты | Модель цены и маржа (доля) пустого проекта. |

Каждое изменение перечислено в замечаниях импорта.

## Новые версии схемы

Если модели ядра меняются так, что старые файлы перестают читаться:

1. увеличить `SCHEMA_VERSION` и `Literal` в `ProfileFile`;
2. добавить в `rentab_core/io/migrations.py` шаг «словарь версии N →
   словарь N + 1» (без моделей ядра) и тест;
3. перегенерировать `docs/profile.schema.json` и дописать раздел сюда.

Обратно совместимое изменение (новое поле со значением по умолчанию) версии
не требует: достаточно перегенерировать схему.

## Изменения схемы 1

- **Фаза 5** — поле `projects[].project.actuals` (факт проекта). Файлы фазы 4
  читаются без изменений (`tests/fixtures/profile/phase4_demo.json`). Проект
  без факта выгружается без этого поля, поэтому такой файл читает и
  приложение фазы 4; файл с фактом оно отклонит как «неизвестное поле».
- **Фаза 7** — поля `letterhead` (реквизиты, подписант, логотип),
  `projects[].offer` (условия КП) и `projects[].project.stages[].description`
  (описание этапа). Пустые не пишутся: файл без КП по-прежнему читает
  приложение фаз 4–6, файл с КП оно отклонит как «неизвестное поле».
