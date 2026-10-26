# Примеры документов демо-фирмы

Файлы, которые отдают кнопки «КП (PDF)», «КП (DOCX)» и «Смета (XLSX)» на
шаге «Результат» для трёх типовых проектов демо-фирмы («🎓 Загрузить
демо-фирму» на главной), дата документа — 25.10.2026. Реквизиты демо-фирмы
вымышленные. Решения по оформлению и формулам — [ADR 0009](../adr/0009-phase7-export.md).

| Проект | Модель цены | КП (PDF) | Страница PDF | КП (DOCX) | Смета (XLSX) |
|---|---|---|---|---|---|
| Регистрация товарного знака | почасовая, пошлины Роспатента | [PDF](demo_trademark_hourly.pdf) | [PNG](demo_trademark_hourly-1.png) | [DOCX](demo_trademark_hourly.docx) | [XLSX](demo_trademark_hourly.xlsx) |
| Патентование изобретения | фикс «издержки + маржа», цены этапов по теневой стоимости (L-17) | [PDF](demo_invention_fixed.pdf) | [PNG](demo_invention_fixed-1.png) | [DOCX](demo_invention_fixed.docx) | [XLSX](demo_invention_fixed.xlsx) |
| Ответ на уведомление экспертизы | почасовая с потолком, скидка до предельной стоимости | [PDF](demo_office_action_capped.pdf) | [PNG](demo_office_action_capped-1.png) | [DOCX](demo_office_action_capped.docx) | [XLSX](demo_office_action_capped.xlsx) |

- **PDF** — шрифты PT Serif и PT Sans встроены подмножеством; суммы — до
  копейки; «Итого к оплате» = стоимость услуг без НДС + НДС + возмещаемые
  расходы.
- **DOCX** — то же содержимое для правки в Word или LibreOffice; шрифты
  встроены целиком (поэтому файл ≈ 0,7 МБ).
- **XLSX** — «Смета» для клиента, «Внутренний расчёт» и «Исходные данные» с
  живыми формулами: поменяйте часы или ставку на листе «Исходные данные» —
  пересчитаются издержки, налог, NNE, этапы и светофор. Значения формул
  появляются при открытии в Excel или LibreOffice.

Перегенерировать (PNG — через `pdftoppm` из poppler-utils):

```bash
uv run python -m tests.export_examples docs/examples --png
```

Актуальность примеров проверяет `tests/core/test_examples.py`.
