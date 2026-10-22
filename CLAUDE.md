# Rentab — контекст для Claude Code

## Что это
Open-source калькулятор стоимости и рентабельности проектов IP-юридического консалтинга (РФ, РК).
Методология Майстера: себестоимость биллируемого часа, утилизация, рычаг, NNE.
Наследник ВКР НИУ ВШЭ (Rentab v0.3, папка legacy/).

## Стек
Python ≥3.11, uv, Pydantic v2, Decimal; Streamlit (UI), Plotly; openpyxl, python-docx, PDF-генерация
с встроенными кириллическими шрифтами; pytest + hypothesis, ruff, mypy; GitHub Actions.

## Архитектура
- `src/rentab_core/` — чистое ядро, НЕ импортирует streamlit. Вся математика здесь.
- `app/` — Streamlit; только вызывает ядро и рисует. Модели ядра собираются из
  `st.session_state` в `app/ui/state.py`, русские подписи — в `app/ui/labels.py`.
  Точка входа — `app/Home.py`; страницы импортируют `ui.*` (Streamlit кладёт `app/` в sys.path).
- `src/rentab_core/rates/*.yaml` — ЕДИНСТВЕННОЕ место ставок, порогов (в RUB/KZT/МРП/МЗП/МРОТ),
  показателей по годам и шкал; у каждой записи: value, effective_from, source, source_url,
  needs_review, legal_review. `needs_review: false` без источника схема не пропустит.
  `docs/rates_verification.md` генерируется из реестров и каталогов пошлин.
- `docs/methodology.md` — источник истины по формулам. Код и тесты соответствуют ему; при
  изменении формулы сначала правится документ.
- `docs/ROADMAP.md` — прогресс по фазам. Обновлять в конце каждой сессии.
- `docs/spec_v1.md` — исходное ТЗ автора: цели, целевая методология, архитектура, границы
  и промпты фаз. Читать в начале каждой фазы вместе с ROADMAP. При расхождении приоритет
  у решений, принятых позже (ROADMAP, docs/adr/, docs/methodology.md).

## Ключевые формулы (кратко; полностью — docs/methodology.md)
cost_rate = (оклад + взносы) / (контрактные часы × утилизация)
overhead_rate = Σ накладных / Σ биллируемой ёмкости фирмы
C = Labor + OH + Own;  R — выручка БЕЗ НДС;  НДС сверху;  пошлины — транзит, вне R
NNE = R − C − Tax;  m = NNE / R
Фикс: «доходы» P = C/(1−m−t);  «доходы−расходы» P = C(1−t)/(1−t−m) (+ мин. налог)
Этапы: выручка распределяется по теневой стоимости (ставка × часы), а не по часам.

## Правила
- Интерфейс на русском; идентификаторы на английском; докстринги на русском.
- Деньги — Decimal в ядре, округление только при выводе. Формат: 1 234 567 ₽.
- Никаких магических чисел: ставки в YAML, пороги и дефолты — именованные константы.
- НИКАКОЙ записи пользовательских данных на сервер. Только импорт/экспорт файлов пользователем.
- Ставки не выдумывать. Нет первоисточника → needs_review: true и сообщить.
- Streamlit: `width='stretch'` вместо `use_container_width`. Значения виджетов меняются в
  колбэках (`on_click`), а не после создания виджета; вход `st.data_editor` не
  пересобирается из его же результата (иначе правки теряются, L-21).
- Перед PR: ruff, mypy, pytest зелёные; для UI — скриншоты через Playwright.
- Одна фаза = одна ветка = один PR. Фазы описаны в docs/ROADMAP.md.

## Команды
```bash
uv sync                          # окружение по uv.lock (группы dev, legacy и app)
uv run ruff check                # линтер
uv run ruff format               # форматирование (в CI — ruff format --check)
uv run mypy                      # типы (strict)
uv run pytest                    # все тесты + покрытие ядра (порог 90%)
uv run pytest --no-cov -k slide3 # быстрый прогон части тестов
uv run pytest --update-snapshots # перезаписать снимки и мост «было/стало» — только осознанно
uv run python -m tests.characterization.bridge  # перегенерировать docs/phase2_before_after.md
uv run python -m rentab_core.rates.verification docs/rates_verification.md  # после правки YAML
uv run streamlit run app/Home.py # приложение (конфиг — .streamlit/config.toml в корне)
```

## legacy/ и characterization-тесты
- `legacy/rentab_v03/` — замороженная копия v0.3. НЕ редактировать, не форматировать.
- Проблемы v0.3 описаны в `docs/legacy_audit.md` под ID `L-NN`.
- `tests/characterization/` фиксирует выходы v0.3: сценарии в `scenarios/*.json`,
  снимки в `snapshots/`. Изменение снимка = изменение цифр: в PR его объясняют
  ссылкой на `L-NN`. Тесты заведомо неверного поведения помечены `KNOWN-BUG L-NN → фаза N`.
- Ядро против v0.3 (с фазы 2 цифры различаются осознанно):
  - `tests/characterization/core_adapter.py` переводит сценарии v0.3 в модели ядра:
    `FINAL` — правила фазы 2 (cost_rate v0.3 = контрактный час, u = 0,65), `NEUTRAL` —
    ядро видит те же числа, что v0.3. Ядро о формате v0.3 не знает.
  - `test_core_snapshots.py` — снимки ядра в `snapshots/core/` (снимки v0.3 не трогать).
  - `bridge.py` раскладывает каждое расхождение с v0.3 по шагам L-NN и генерирует
    `docs/phase2_before_after.md`; `test_phase2_bridge.py` проверяет мост и документ.
  - `test_core_vs_legacy.py` — 300 случайных сценариев против живого v0.3: при `NEUTRAL`
    разница только в налоговой методологии (НДС, минимальный налог).
  - Изменили формулу — обновить снимки ядра (`--update-snapshots`), перегенерировать
    мост и объяснить diff в PR.
- Тесты ядра — `tests/core/` (golden: примеры `methodology.md` и слайд 3), smoke-тесты
  интерфейса — `tests/app/` (`streamlit.testing`, запуск с `Home.py` и `switch_page`).
- Архитектурные решения — `docs/adr/`.
