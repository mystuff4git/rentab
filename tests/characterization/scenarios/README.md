# Сценарии characterization-тестов

Сценарий описывает входные данные так же, как их видит страница «Проект» v0.3
после заполнения «Настройки». Формат нейтральный (JSON), поэтому в фазе 1 те же
сценарии пойдут в `rentab_core` для проверки эквивалентности, а в фазе 2 по ним
будут видны осознанные изменения цифр.

## Поля сценария сметы

| Поле | Тип | Откуда в v0.3 |
|---|---|---|
| `id`, `description` | str | — |
| `known_bugs` | list[str] | ID проблем из `docs/legacy_audit.md`, которые влияют на выход |
| `pricing_model` | `"billing"` \| `"fixed"` | переключатель модели на «Проекте» |
| `target_margin_pct` | int | целевая маржа в процентах (только `fixed`; так хранит UI) |
| `jurisdiction_params` | dict | `session_state["jurisdiction_params"]`: `jurisdiction` (`RF`/`KZ`/`Both`), `rf`, `kz`, `exchange_rate_rub_per_kzt` |
| `calc_country` | `"RF"` \| `"KZ"` | выбор юрисдикции расчёта, только в режиме `Both` |
| `billable_hours_per_month` | float | биллируемые часы фирмы в месяц |
| `team` | list | `{name, role, billing_rate, cost_rate}` |
| `stages` | list | `{name, executor, hours}`; один исполнитель на этап |
| `firm_expenses` | list | накладные: `{name, category: "overhead", amount, currency, period, billable}` |
| `selected_duties` | dict | `{"RF": [название пошлины из каталога], "KZ": [...]}` |
| `custom_expenses` | list | произвольные расходы: `{name, category, amount, currency, period, billable}` |
| `project` | dict | `{name, client, description}` |

`matrix.json` задаёт две базы проекта (`base_projects.RF` и `.KZ`), список
налоговых конфигураций и моделей ценообразования; тесты строят их декартово
произведение.

`slide3.json` описан отдельно: выручка там задана числом (300 000 ₽), что в UI
v0.3 невозможно, поэтому он считается функциями модулей напрямую.

## Перевод в модели ядра (фаза 2)

Формат сценариев — это входы v0.3, его не меняем. Как они переводятся в модели
ядра фазы 2 (себестоимость часа v0.3 — контрактная, утилизация 0,65, «Прочее» —
собственный расход, курс тенге из сценария, одноимённые этапы — один этап),
описано в `core_adapter.py` и `docs/methodology.md`, раздел 13.
