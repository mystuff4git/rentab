# Rentab

[![CI](https://github.com/mystuff4git/rentab/actions/workflows/ci.yml/badge.svg)](https://github.com/mystuff4git/rentab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Калькулятор стоимости и рентабельности проектов в IP-юридическом консалтинге
(Россия и Казахстан). В основе — методология Д. Майстера («Управление фирмой,
оказывающей профессиональные услуги»): себестоимость биллируемого часа, утилизация,
рычаг (leverage), реализация и чистая прибыль проекта (NNE). Маржа считается от
выручки за услуги. Патентные пошлины — транзитные расходы, а не доход фирмы.

> **Статус: в разработке.** Идёт переработка v0.3 (магистерская ВКР, НИУ ВШЭ,
> «ЛигалТех») в open-source продукт v1.0. Прогресс по фазам —
> в [docs/ROADMAP.md](docs/ROADMAP.md). Демо прежней версии v0.3:
> https://rentab.streamlit.app/

## Что будет в v1.0

- Расчётное ядро `rentab_core` — чистый Python-пакет без UI, покрытый тестами.
- Интерфейс на Streamlit: меню-визард «Фирма → Проект → Результат → Сравнение →
  План–факт», светлая и тёмная тема, подсказки с формулами у показателей.
- Почасовая модель, фикс-прайс и фикс с потолком; налоговые режимы РФ и РК на 2026 год
  со ставками из первоисточников.
- План–факт, анализ чувствительности, сравнение моделей ценообразования и налоговых
  режимов на одном экране.
- Коммерческое предложение в DOCX и PDF с логотипом и реквизитами, смета XLSX с живыми
  формулами Excel — [примеры](docs/examples/).
- Данные фирмы не хранятся на сервере: только импорт и экспорт файлов пользователем.

## Для разработчиков

Нужен [uv](https://docs.astral.sh/uv/).

```bash
uv sync                           # окружение по uv.lock
uv run streamlit run app/Home.py  # приложение на http://localhost:8501
uv run pytest                     # тесты
uv run ruff check && uv run mypy
```

Ядро можно вызывать и без интерфейса:

```python
from rentab_core import Scenario, calculate_estimate, dashboard

estimate = calculate_estimate(Scenario(firm=firm, project=project))
print(estimate.nne, dashboard(estimate).profitability)
```

Структура:

```
src/rentab_core/   расчётное ядро (без зависимостей от UI): модели, налоги,
                   ценообразование, аналитика, документы (КП в PDF и DOCX,
                   смета XLSX с формулами), реестр ставок
app/               интерфейс на Streamlit; только вызывает ядро и рисует:
                   Home.py — меню, views/ — шаги, ui/ — состояние и компоненты
tests/             core/ — тесты ядра и golden-сценарии, characterization/ —
                   поведение v0.3 и мост «было/стало», app/ — smoke-тесты интерфейса
legacy/rentab_v03/ замороженная копия v0.3 — эталон для сравнения
docs/              методология, сверка ставок с источниками, ROADMAP, аудит v0.3,
                   ADR, скриншоты, примеры документов
```

Данные фирмы приложение хранит только в памяти сессии и ничего не пишет на
диск сервера. Сохранить работу можно в файл JSON (фирма и проекты) и загрузить
его обратно на главной странице; там же — демо-фирма со слайда 3 и
`firm_profile.json` v0.3. Формат файла — [docs/profile_format.md](docs/profile_format.md).

Формулы расчёта и разобранные примеры — [docs/methodology.md](docs/methodology.md).
Почему цифры отличаются от v0.3 — [docs/legacy_audit.md](docs/legacy_audit.md) и
[docs/phase2_before_after.md](docs/phase2_before_after.md).

## English summary

Rentab is an open-source cost and profitability calculator for IP legal
consulting projects in Russia and Kazakhstan. It is based on David Maister's
professional services firm methodology: billable-hour cost, utilization,
leverage, realization and net-net earnings (NNE). It is being rebuilt from a
master's thesis prototype (v0.3) into a tested v1.0 with a pure-Python core
(`rentab_core`) and a Streamlit UI. See [docs/ROADMAP.md](docs/ROADMAP.md).
Licensed under MIT.

## Лицензия

[MIT](LICENSE) © 2026 Yaroslav Statsurin
