# legacy/: Rentab v0.3 (эталон, только для чтения)

Здесь лежит код Rentab v0.3 — версии, защищённой как магистерская ВКР
(НИУ ВШЭ, «ЛигалТех»). Он нужен как **эталон поведения**: characterization-тесты
в `tests/characterization/` фиксируют его выходы. Поэтому каждое изменение
цифр в новом ядре `rentab_core` должно быть осознанным и объяснённым.

## Происхождение

| Поле | Значение |
|---|---|
| Репозиторий | https://github.com/mystuff4git/HSE_Statsurin_Yaroslav |
| Папка | `Rentab_VKR_v0.3/` |
| Коммит | `b769b5af7b61e0bac0ca71b442a948d82b897d14` |
| Живое демо | https://rentab.streamlit.app/ |

Копия побайтовая, с двумя отличиями:

1. Не перенесён `modules/__pycache__/` (байткод случайно попал в старый репозиторий).
2. `CLAUDE.md` переименован в `CLAUDE_v03.md`, содержимое не менялось.
   Иначе Claude Code подхватывал бы устаревшие инструкции v0.3 как действующие
   при работе с файлами в этой папке.

## Правила

- **Не редактировать.** Здесь не исправляют ошибки и не форматируют код.
  Известные проблемы v0.3 описаны в [`docs/legacy_audit.md`](../docs/legacy_audit.md)
  и исправляются в `rentab_core`.
- ruff и mypy папку не проверяют (см. `pyproject.toml`).
- Тесты импортируют модули v0.3 (`modules.*`) через
  `tests/characterization/legacy.py`. Страницы Streamlit (`pages/`) тесты не
  импортируют: их расчётная логика повторена в `tests/characterization/legacy_runner.py`.

## Как запустить старое приложение

```bash
cd legacy/rentab_v03
uv run --no-project --with-requirements requirements.txt streamlit run app.py
```

Внимание: v0.3 сохраняет профиль фирмы в `data/firm_profile.json` на диске
(проблема L-10 в аудите). Для локального запуска это безопасно, для публичного
деплоя — нет.
