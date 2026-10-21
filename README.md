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
- Интерфейс на Streamlit: онбординг «Фирма → Проект → Результат».
- Почасовая модель, фикс-прайс и фикс с потолком; налоговые режимы РФ и РК на 2026 год
  со ставками из первоисточников.
- План–факт, анализ чувствительности, сравнение моделей ценообразования и налоговых
  режимов на одном экране.
- Коммерческое предложение в DOCX и PDF, внутренний расчёт в XLSX.
- Данные фирмы не хранятся на сервере: только импорт и экспорт файлов пользователем.

## Для разработчиков

Нужен [uv](https://docs.astral.sh/uv/).

```bash
uv sync           # окружение по uv.lock
uv run pytest     # тесты
uv run ruff check && uv run mypy
```

Структура:

```
src/rentab_core/   расчётное ядро (без зависимостей от UI)
tests/             тесты; characterization/ фиксирует поведение v0.3
legacy/rentab_v03/ замороженная копия v0.3 — эталон для сравнения
docs/              ROADMAP, аудит v0.3, ADR; позже — методология и проверка ставок
```

Почему цифры v1.0 будут отличаться от v0.3, объясняет
[docs/legacy_audit.md](docs/legacy_audit.md).

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
