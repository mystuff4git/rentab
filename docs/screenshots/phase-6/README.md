# Скриншоты фазы 6: до и после

- `before/` — версия фазы 5 (коммит `4c7fe07`), сценарий `before.js`.
- `after/` — фаза 6, сценарий `shots.js`. Он же проверяет в браузере, что:
  - выбор пошлин, параметры «что если» и правка ячейки редактора факта
    переживают переключение проектов и смену шага;
  - открытая вкладка «Результата» не сбрасывается при смене шага;
  - после переименования сотрудника смета находит исполнителя;
  - на экране 420 px нет горизонтальной прокрутки у страниц и редактора
    факта.

Запуск — в шапке каждого сценария.

| Экран | До | После |
|---|---|---|
| Главная, пустая сессия | [01](before/01_home_empty.png) | [01](after/01_home_empty.png), пустое состояние «Проекта» — [01b](after/01b_project_empty_state.png) |
| Главная с демо-фирмой | [02](before/02_home_demo.png) | [02](after/02_home_demo.png) |
| Фирма: юрисдикция | [03](before/03_setup_jurisdiction.png) | [03](after/03_firm_jurisdiction.png) |
| Фирма: команда | [04](before/04_setup_team.png) | правка — [04](after/04_firm_team_edit.png), после переименования — [04b](after/04b_firm_team_renamed.png) |
| Фирма: накладные | [05](before/05_setup_overheads.png) | [05](after/05_firm_overheads.png) |
| Проект (товарный знак, пошлины) | [06](before/06_project_trademark.png) | [06](after/06_project_trademark.png) |
| Дашборд → «Результат», вкладка «Смета» | [07](before/07_dashboard_trademark.png) | [07](after/07_result_trademark.png), подсказка с формулой — [07b](after/07b_result_kpi_formula.png) |
| Чувствительность → вкладка «Результата» | [08](before/08_sensitivity_trademark.png) | [08](after/08_sensitivity_slide3.png), «что если» — [08b](after/08b_what_if_slide3.png) |
| Сравнение | [09](before/09_compare_trademark.png) | [09](after/09_compare_trademark.png) |
| План–факт | [10](before/10_plan_fact_slide3.png) | [10](after/10_plan_fact_slide3.png) |
| Узкий экран: план–факт | [11](before/11_plan_fact_narrow.png) | [11](after/11_plan_fact_narrow.png) |
| Узкий экран: дашборд / результат | [12](before/12_dashboard_narrow.png) | [12](after/12_result_narrow.png) |
| Узкий экран: сравнение | [13](before/13_compare_narrow.png) | [13](after/13_compare_narrow.png) |
| Узкий экран: главная | [14](before/14_home_narrow.png) | [14](after/14_home_narrow.png) |
| Тёмная тема | — | главная — [15](after/15_home_dark.png), результат — [16](after/16_result_dark.png), план–факт — [17](after/17_plan_fact_dark.png) |
