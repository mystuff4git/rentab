// Скриншоты фазы 5 и браузерная проверка: переключение проектов не теряет
// факт (редактор st.data_editor) и параметры «что если» (ползунки).
//
// AppTest не моделирует состояние виджетов на стороне браузера и правку ячеек
// редактора, поэтому это проверяется здесь (CLAUDE.md, ADR 0006, ADR 0007).
// Запуск из корня репозитория:
//
//   uv run streamlit run app/Home.py --server.port 8599 &
//   NODE_PATH=$(npm root -g) node docs/screenshots/phase-5/shots.js
//
// Нужен Playwright для Node (глобальный пакет) и Chromium (PLAYWRIGHT_BROWSERS_PATH).
const { chromium } = require('playwright');
const OUT = __dirname + '/';
const URL = process.env.RENTAB_URL || 'http://localhost:8599/';

async function settle(page) {
  await page.waitForTimeout(400);
  await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(600);
}
async function nav(page, name) {
  await page.locator('[data-testid="stSidebarNav"] a', { hasText: name }).first().click();
  await settle(page);
}
async function button(page, name) {
  await page.getByRole('button', { name }).first().click();
  await settle(page);
}
async function scrollTo(page, text) {
  await page.getByText(text).first().evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await page.waitForTimeout(400);
}
async function shot(page, file, full = true) {
  await page.screenshot({ path: OUT + file, fullPage: full });
  console.log('saved', file);
}
async function project(page, name) {
  await page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first().click();
  await page.getByRole('option', { name }).click();
  await settle(page);
}
// Ползунок Streamlit 1.66 — react-aria: скрытый input type="range".
function slider(page, label) {
  return page.locator('[data-testid="stSlider"]', { hasText: label }).locator('input[type="range"]');
}
async function sliderValue(page, label) {
  return Number(await slider(page, label).inputValue());
}
async function moveSlider(page, label, steps) {
  const thumb = slider(page, label);
  await thumb.focus();
  for (let i = 0; i < Math.abs(steps); i++) {
    await page.keyboard.press(steps > 0 ? 'ArrowRight' : 'ArrowLeft');
    await page.waitForTimeout(50);
  }
  await settle(page);
}
async function metric(page, label) {
  const box = page.locator('[data-testid="stMetric"]', { hasText: label }).first();
  return (await box.locator('[data-testid="stMetricValue"]').innerText()).trim();
}
function expect(condition, message) {
  if (!condition) throw new Error('CHECK FAILED: ' + message);
  console.log('ok:', message);
}
// Ячейка редактора — холст glide-data-grid. Фокус на первую ячейку строки
// через скрытую таблицу для экранных читалок, стрелками — к нужному столбцу;
// Enter открывает редактор ячейки, Ctrl+A и ввод заменяют значение, Enter
// сохраняет. Символы вводятся с паузой: иначе редактор теряет первый.
async function editCell(page, row, col, value) {
  const grid = page.locator('[data-testid="stDataFrame"]').first();
  await grid.scrollIntoViewIfNeeded();
  await grid.locator(`[data-testid="glide-cell-0-${row}"]`).focus();
  for (let i = 0; i < col; i++) {
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(100);
  }
  await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
  await page.keyboard.press('Control+A');
  await page.keyboard.type(String(value), { delay: 80 });
  await page.keyboard.press('Enter');
  await settle(page);
}
// Текст ячейки — из скрытой таблицы glide для экранных читалок.
async function cellNumber(page, row, col) {
  const grid = page.locator('[data-testid="stDataFrame"]').first();
  const text = await grid.locator(`[data-testid="glide-cell-${col}-${row}"]`).textContent();
  return parseFloat(text.replace(',', '.'));
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } });
  await page.goto(URL);
  await settle(page);
  await button(page, '🎓 Загрузить демо-фирму');
  await page.waitForSelector('text=Загружена демо-фирма');

  // Дашборд слайда 3: запас часов и NNE по факту.
  await nav(page, 'Проект');
  await button(page, '💰 Рассчитать смету');
  await nav(page, 'Дашборд');
  await page.getByText('Запас часов до безубыточности').scrollIntoViewIfNeeded();
  await shot(page, '01_dashboard_teaser.png', false);

  // Чувствительность слайда 3.
  await nav(page, 'Чувствительность');
  // Таблицы — холст; текст ячеек есть в скрытой таблице для экранных читалок.
  await page.waitForSelector('text=+55 ч', { state: 'attached' });
  await shot(page, '02_sensitivity_slide3.png');
  // Основной контейнер Streamlit прокручивается сам: нижние блоки — отдельными кадрами.
  await scrollTo(page, '2. Торнадо');
  await shot(page, '02b_tornado_slide3.png', false);

  // «Что если» на слайде 3: часы +100% (шаг 5%).
  await moveSlider(page, 'Часы, %', 20);
  expect(await sliderValue(page, 'Часы, %') === 100, 'ползунок «Часы» слайда 3 = 100%');
  await moveSlider(page, 'Утилизация, %', -2);
  const whatIfNne = await metric(page, 'NNE, ₽');
  expect(whatIfNne === '156 444 ₽', `NNE «что если» слайда 3 = ${whatIfNne}`);
  await scrollTo(page, '3. Что если');
  await shot(page, '03_what_if_slide3.png', false);

  // Переключение на товарный знак и обратно: у каждого проекта свои ползунки.
  await project(page, 'Регистрация товарного знака');
  expect(await sliderValue(page, 'Часы, %') === 0, 'у товарного знака «Часы» = 0%');
  await shot(page, '04_sensitivity_trademark_hourly.png');
  await project(page, 'Слайд 3: план и факт');
  expect(await sliderValue(page, 'Часы, %') === 100, 'слайд 3 после переключения: «Часы» = 100%');
  expect(await sliderValue(page, 'Утилизация, %') === -10, 'слайд 3 после переключения: «Утилизация» = −10%');
  expect(await metric(page, 'NNE, ₽') === whatIfNne, 'NNE «что если» слайда 3 сохранилось');
  console.log('what-if kept after switching: OK');

  // План–факт слайда 3.
  await nav(page, 'План–факт');
  expect(await metric(page, 'NNE по факту') === '-2 200 ₽', 'NNE по факту слайда 3 = −2 200 ₽');
  await shot(page, '05_plan_fact_slide3.png');
  await scrollTo(page, '2. План → факт');
  await shot(page, '05b_plan_fact_slide3_summary.png', false);
  await scrollTo(page, '4. Перерасход по ролям');
  await shot(page, '05c_plan_fact_slide3_roles.png', false);

  // План–факт товарного знака: правка ячейки факта (Партнёр, этап 1: 3 → 7 ч).
  await project(page, 'Регистрация товарного знака');
  expect(await metric(page, 'NNE по факту') === '78 160 ₽', 'NNE по факту товарного знака = 78 160 ₽');
  await editCell(page, 0, 3, 7);
  const edited = await metric(page, 'NNE по факту');
  // +4 ч партнёра: выручка +40 000, налог +2 400, издержки +16 400 → +21 200.
  expect(edited === '99 360 ₽', `после правки ячейки NNE по факту = ${edited}`);
  await shot(page, '06_plan_fact_trademark_edited.png');
  expect(await cellNumber(page, 0, 3) === 7, 'в ячейке факта — 7');
  await project(page, 'Слайд 3: план и факт');
  expect(await metric(page, 'NNE по факту') === '-2 200 ₽', 'факт слайда 3 не тронут правкой другого проекта');
  expect(await cellNumber(page, 0, 3) === 62, 'в редакторе слайда 3 — его факт партнёра, 62 ч');
  await project(page, 'Регистрация товарного знака');
  expect(await metric(page, 'NNE по факту') === edited, 'правка факта товарного знака сохранилась после переключения');
  expect(await cellNumber(page, 0, 3) === 7, 'в ячейке факта после переключения — 7');
  console.log('actuals kept after switching: OK');
  // Переход на другую страницу и обратно тоже не теряет правку.
  await nav(page, 'Проект');
  await nav(page, 'План–факт');
  expect(await metric(page, 'NNE по факту') === edited, 'правка факта сохранилась после смены страницы');

  // Сравнение для товарного знака с потолком 90 000 ₽.
  await nav(page, 'Сравнение');
  const cap = page.getByLabel('Потолок для сравнения, ₽', { exact: true });
  await cap.fill('90000');
  await cap.press('Enter');
  await settle(page);
  await page.waitForSelector('text=49 400 ₽', { state: 'attached' });
  await shot(page, '07_compare_trademark.png');

  // Узкий экран.
  await nav(page, 'План–факт');
  await page.setViewportSize({ width: 420, height: 900 });
  await settle(page);
  await page.locator('[data-testid="stSidebarCollapseButton"] button, [data-testid="stSidebarCollapseButton"]').first().click().catch(() => {});
  await settle(page);
  await shot(page, '08_plan_fact_narrow.png');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
