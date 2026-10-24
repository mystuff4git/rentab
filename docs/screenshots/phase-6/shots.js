// Скриншоты «после» фазы 6 и браузерная проверка: переключение проектов не
// теряет выбор пошлин, факт (правка ячейки редактора) и «что если» (ползунки);
// открытая вкладка «Результата» переживает смену шага; новое имя сотрудника
// доходит до всех проектов.
//
// AppTest не моделирует состояние виджетов на стороне браузера и правку ячеек
// редактора, поэтому это проверяется здесь (CLAUDE.md, ADR 0006–0008).
// Запуск из корня репозитория:
//
//   uv run streamlit run app/Home.py --server.port 8599 &
//   NODE_PATH=$(npm root -g) node docs/screenshots/phase-6/shots.js
//
// Кадры — в docs/screenshots/phase-6/after/ (кадры «до» — before.js).
// Нужен Playwright для Node (глобальный пакет) и Chromium.
const { chromium } = require('playwright');
const OUT = __dirname + '/after/';
const URL = process.env.RENTAB_URL || 'http://localhost:8599/';
const NBSP = / /g;

async function settle(page) {
  await page.waitForTimeout(400);
  await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(700);
}
// Основной контейнер Streamlit прокручивается сам: высота окна — по содержимому.
async function shot(page, file, full = true) {
  const size = page.viewportSize();
  if (full) {
    const height = await page.evaluate(() => {
      const main = document.querySelector('[data-testid="stMain"]') || document.body;
      return Math.max(main.scrollHeight, document.body.scrollHeight);
    });
    await page.setViewportSize({ width: size.width, height: Math.min(Math.max(height, size.height), 9000) });
    await page.waitForTimeout(500);
  }
  await page.screenshot({ path: OUT + file, fullPage: full });
  if (full) {
    await page.setViewportSize(size);
    await page.waitForTimeout(300);
  }
  console.log('saved', file);
}
// Меню сверху; на узком экране Streamlit переносит его в боковую панель.
async function nav(page, name) {
  const link = page.getByRole('link', { name, exact: true }).first();
  const box = await link.boundingBox().catch(() => null);
  const width = page.viewportSize().width;
  if (!box || box.x < 0 || box.x > width) {
    await page.locator('[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapsedControl"] button').first().click();
    await page.waitForTimeout(500);
  }
  await link.click();
  await settle(page);
}
async function button(page, name) {
  await page.getByRole('button', { name, exact: true }).first().click();
  await settle(page);
}
async function tab(page, name) {
  await page.getByRole('tab', { name }).click();
  await settle(page);
}
async function project(page, name) {
  await page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first().click();
  await page.getByRole('option', { name }).click();
  await settle(page);
}
async function scrollTo(page, text) {
  await page.getByText(text).first().evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await page.waitForTimeout(400);
}
// Значение KPI по точной подписи (у «NNE» и «NNE по факту» общее начало).
async function metric(page, label) {
  const value = await page.evaluate((wanted) => {
    for (const box of document.querySelectorAll('[data-testid="stMetric"]')) {
      const name = box.querySelector('[data-testid="stMetricLabel"]');
      if (name && name.innerText.trim() === wanted) {
        return box.querySelector('[data-testid="stMetricValue"]').innerText.trim();
      }
    }
    return null;
  }, label);
  return value === null ? null : value.replace(NBSP, ' ');
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
function expect(condition, message) {
  if (!condition) throw new Error('CHECK FAILED: ' + message);
  console.log('ok:', message);
}
// Ячейка редактора — холст glide-data-grid. Фокус на первую ячейку строки
// через скрытую таблицу для экранных читалок, стрелками — к нужному столбцу;
// Enter открывает редактор ячейки, Ctrl+A и ввод заменяют значение, Enter
// сохраняет. Символы вводятся с паузой: иначе редактор теряет первый.
// Столбцы на экране: «Этап · исполнитель», «План, ч», «Факт, ч» (факт — 2).
const FACT_COLUMN = 2;
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
async function cellNumber(page, row, col) {
  const grid = page.locator('[data-testid="stDataFrame"]').first();
  const text = await grid.locator(`[data-testid="glide-cell-${col}-${row}"]`).textContent();
  return parseFloat(text.replace(',', '.'));
}
// Пошлины проекта — по подписи «возмещаемые …» в сводке «Проекта».
async function billedText(page) {
  const caption = page.getByText(/НДС .* · возмещаемые/).first();
  return (await caption.innerText()).replace(NBSP, ' ');
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  await page.goto(URL);
  await settle(page);
  await page.waitForSelector('text=Данные не покидают вашу сессию');
  await shot(page, '01_home_empty.png');
  await nav(page, 'Проект');
  await shot(page, '01b_project_empty_state.png');
  await nav(page, 'Главная');
  await button(page, '🎓 Загрузить демо-фирму');
  await page.waitForSelector('text=Загружена демо-фирма');
  await shot(page, '02_home_demo.png');

  // Фирма: вкладки и правка сотрудника.
  await nav(page, 'Фирма');
  await shot(page, '03_firm_jurisdiction.png');
  await tab(page, 'Команда');
  await page.locator('.st-key-edit_member_1 button').first().click();
  await settle(page);
  expect(await page.getByLabel('Имя / ФИО').inputValue() === 'Юрист', 'карточка заполнена данными «Юриста»');
  await shot(page, '04_firm_team_edit.png');
  await page.getByLabel('Имя / ФИО').fill('Юрист Петрова');
  await page.getByLabel('Имя / ФИО').press('Enter');
  await settle(page);
  await button(page, '💾 Сохранить изменения');
  await page.waitForSelector('text=Новое имя — в этапах и факте всех проектов сессии');
  await shot(page, '04b_firm_team_renamed.png');
  await tab(page, 'Накладные');
  await shot(page, '05_firm_overheads.png');

  // Проект: пошлины товарного знака переживают переключение проектов.
  await nav(page, 'Проект');
  await project(page, 'Регистрация товарного знака');
  await page.getByText('Расходы проекта — пошлины').click();
  await settle(page);
  const billed = await billedText(page);
  expect(billed.includes('возмещаемые 24 000 ₽'), `пошлины товарного знака: «${billed}»`);
  await shot(page, '06_project_trademark.png');
  await project(page, 'Слайд 3: план и факт');
  await project(page, 'Регистрация товарного знака');
  expect((await billedText(page)).includes('возмещаемые 24 000 ₽'), 'пошлины сохранились после переключения проектов');
  console.log('duties kept after switching: OK');

  // Результат товарного знака: исполнитель с новым именем найден.
  await nav(page, 'Результат');
  expect(await page.locator('[data-testid="stAlertContentWarning"]').count() === 0, 'после переименования нет «исполнителя нет в команде»');
  expect(await metric(page, 'Чистая прибыль (NNE)') === '58 800 ₽', 'NNE товарного знака = 58 800 ₽');
  await shot(page, '07_result_trademark.png');
  await page.locator('[class*="st-key-kpi-nne"] [data-testid="stTooltipIcon"]').first().hover();
  await page.waitForTimeout(800);
  await shot(page, '07b_result_kpi_formula.png', false);

  // Чувствительность слайда 3 и «что если»: часы +100%, утилизация −10%.
  await project(page, 'Слайд 3: план и факт');
  await tab(page, 'Чувствительность');
  await shot(page, '08_sensitivity_slide3.png');
  await moveSlider(page, 'Часы, %', 20);
  expect(await sliderValue(page, 'Часы, %') === 100, 'ползунок «Часы» слайда 3 = 100%');
  await moveSlider(page, 'Утилизация, %', -2);
  const whatIfNne = await metric(page, 'NNE');
  expect(whatIfNne === '156 444 ₽', `NNE «что если» слайда 3 = ${whatIfNne}`);
  await scrollTo(page, 'Что если — проект не меняется');
  await shot(page, '08b_what_if_slide3.png', false);
  await project(page, 'Регистрация товарного знака');
  expect(await sliderValue(page, 'Часы, %') === 0, 'у товарного знака «Часы» = 0%');
  await project(page, 'Слайд 3: план и факт');
  expect(await sliderValue(page, 'Часы, %') === 100, 'слайд 3 после переключения: «Часы» = 100%');
  expect(await sliderValue(page, 'Утилизация, %') === -10, 'слайд 3 после переключения: «Утилизация» = −10%');
  expect(await metric(page, 'NNE') === whatIfNne, 'NNE «что если» слайда 3 сохранилось');
  await nav(page, 'Проект');
  await nav(page, 'Результат');
  expect(await page.getByRole('tab', { name: 'Чувствительность', selected: true }).count() === 1, 'вкладка «Чувствительность» осталась открытой после смены шага');
  expect(await sliderValue(page, 'Часы, %') === 100, '«что если» сохранилось после смены шага');
  console.log('what-if kept after switching: OK');

  // Сравнение товарного знака с потолком 90 000 ₽.
  await project(page, 'Регистрация товарного знака');
  await nav(page, 'Сравнение');
  const cap = page.getByLabel('Потолок для сравнения, ₽', { exact: true });
  await cap.fill('90000');
  await cap.press('Enter');
  await settle(page);
  await page.waitForSelector('text=49 400 ₽', { state: 'attached' });
  await shot(page, '09_compare_trademark.png');

  // План–факт: правка ячейки факта товарного знака (Партнёр, этап 1: 3 → 7 ч).
  await nav(page, 'План–факт');
  expect(await metric(page, 'NNE по факту') === '78 160 ₽', 'NNE по факту товарного знака = 78 160 ₽');
  await editCell(page, 0, FACT_COLUMN, 7);
  const edited = await metric(page, 'NNE по факту');
  // +4 ч партнёра: выручка +40 000, налог +2 400, издержки +16 400 → +21 200.
  expect(edited === '99 360 ₽', `после правки ячейки NNE по факту = ${edited}`);
  expect(await cellNumber(page, 0, FACT_COLUMN) === 7, 'в ячейке факта — 7');
  await project(page, 'Слайд 3: план и факт');
  expect(await metric(page, 'NNE по факту') === '−2 200 ₽', 'факт слайда 3 не тронут правкой другого проекта');
  expect(await cellNumber(page, 0, FACT_COLUMN) === 62, 'в редакторе слайда 3 — его факт партнёра, 62 ч');
  await shot(page, '10_plan_fact_slide3.png');
  await project(page, 'Регистрация товарного знака');
  expect(await metric(page, 'NNE по факту') === edited, 'правка факта товарного знака сохранилась после переключения');
  expect(await cellNumber(page, 0, FACT_COLUMN) === 7, 'в ячейке факта после переключения — 7');
  await nav(page, 'Проект');
  await nav(page, 'План–факт');
  expect(await metric(page, 'NNE по факту') === edited, 'правка факта сохранилась после смены шага');
  console.log('actuals kept after switching: OK');

  // Узкий экран: редактор факта и таблицы — без горизонтальной прокрутки.
  await page.setViewportSize({ width: 420, height: 900 });
  await settle(page);
  await page.locator('[data-testid="stSidebarCollapseButton"] button').first().click().catch(() => {});
  await settle(page);
  const overflow = await page.evaluate(() => {
    const main = document.querySelector('[data-testid="stMain"]');
    const grid = document.querySelector('[data-testid="stDataFrame"]');
    const scroller = grid && grid.querySelector('.dvn-scroller');
    return {
      page: main.scrollWidth - main.clientWidth,
      grid: scroller ? scroller.scrollWidth - scroller.clientWidth : 0,
    };
  });
  expect(overflow.page <= 1, `узкий экран: страница без горизонтальной прокрутки (${overflow.page} px)`);
  expect(overflow.grid <= 1, `узкий экран: редактор факта без горизонтальной прокрутки (${overflow.grid} px)`);
  await shot(page, '11_plan_fact_narrow.png');
  for (const [name, file] of [['Результат', '12_result_narrow.png'], ['Сравнение', '13_compare_narrow.png'], ['Главная', '14_home_narrow.png']]) {
    await nav(page, name);
    await page.locator('[data-testid="stSidebarCollapseButton"] button').first().click().catch(() => {});
    await settle(page);
    const width = await page.evaluate(() => {
      const main = document.querySelector('[data-testid="stMain"]');
      return main.scrollWidth - main.clientWidth;
    });
    expect(width <= 1, `узкий экран «${name}»: без горизонтальной прокрутки (${width} px)`);
    await shot(page, file);
  }
  await browser.close();

  // Тёмная тема: Streamlit берёт её у системы (prefers-color-scheme).
  const dark = await chromium.launch();
  const context = await dark.newContext({ viewport: { width: 1400, height: 1000 }, colorScheme: 'dark' });
  const night = await context.newPage();
  await night.goto(URL);
  await settle(night);
  await button(night, '🎓 Загрузить демо-фирму');
  await shot(night, '15_home_dark.png');
  await nav(night, 'Результат');
  await shot(night, '16_result_dark.png');
  await nav(night, 'План–факт');
  await shot(night, '17_plan_fact_dark.png');
  await dark.close();
})().catch((e) => { console.error(e); process.exit(1); });
