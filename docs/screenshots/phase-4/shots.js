// Скриншоты фазы 4 и браузерная проверка переключения проектов.
//
// AppTest не моделирует состояние виджетов на стороне браузера, поэтому
// сохранение выбора пошлин при переключении проектов проверяется здесь
// (ADR 0006). Запуск из корня репозитория:
//
//   uv run streamlit run app/Home.py --server.port 8599 &
//   NODE_PATH=$(npm root -g) node docs/screenshots/phase-4/shots.js
//
// Нужен Playwright для Node (глобальный пакет) и Chromium (PLAYWRIGHT_BROWSERS_PATH).
const path = require('path');
const { chromium } = require('playwright');
const OUT = __dirname + '/';
const URL = process.env.RENTAB_URL || 'http://localhost:8599/';
const FIXTURE = path.join(__dirname, '..', '..', '..', 'tests', 'fixtures', 'legacy', 'v03_firm_profile.json');

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
async function shot(page, file, full = true) {
  await page.screenshot({ path: OUT + file, fullPage: full });
  console.log('saved', file);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } });
  await page.goto(URL);
  await settle(page);
  await page.waitForSelector('text=Данные не покидают вашу сессию');
  await shot(page, '01_home_empty.png');

  await button(page, '🎓 Загрузить демо-фирму');
  await page.waitForSelector('text=Загружена демо-фирма');
  await shot(page, '02_home_demo_loaded.png');

  await nav(page, 'Проект');
  await button(page, '💰 Рассчитать смету');
  await shot(page, '03_project_slide3_plan.png');

  // Переключатель проектов в боковой панели.
  await page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first().click();
  await page.waitForTimeout(500);
  await shot(page, '04_sidebar_project_list.png', false);
  await page.getByRole('option', { name: 'Регистрация товарного знака' }).click();
  await settle(page);
  await page.getByText('4. Расходы проекта').click();
  await settle(page);
  await button(page, '💰 Рассчитать смету');
  await page.waitForSelector('text=Возмещаемые расходы: 24,000 ₽', { timeout: 10000 });
  // Назад на слайд 3 и снова на товарный знак: выбор пошлин не теряется.
  await page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first().click();
  await page.getByRole('option', { name: 'Слайд 3: план' }).click();
  await settle(page);
  await page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first().click();
  await page.getByRole('option', { name: 'Регистрация товарного знака' }).click();
  await settle(page);
  await button(page, '💰 Рассчитать смету');
  await page.waitForSelector('text=Возмещаемые расходы: 24,000 ₽', { timeout: 10000 });
  console.log('duties kept after switching: OK');
  await page.getByText('4. Расходы проекта').click();
  await settle(page);
  await page.getByText('Пошлины из каталога').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await shot(page, '05_project_trademark_duties.png', false);

  await nav(page, 'Дашборд');
  await shot(page, '06_dashboard_trademark.png');

  await nav(page, 'Home');
  await settle(page);
  await page.setInputFiles('input[type=file]', FIXTURE);
  await settle(page);
  await button(page, '📂 Загрузить данные из файла');
  await page.waitForSelector('text=В сессии уже есть данные');
  await shot(page, '07_home_replace_confirm.png');
  await button(page, 'Заменить данные');
  await page.waitForSelector('text=Что изменилось при загрузке');
  await shot(page, '08_home_import_v03_notes.png');

  await nav(page, 'Настройка');
  await page.getByRole('tab', { name: /Команда/ }).click();
  await settle(page);
  await page.getByText('Текущая команда').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await shot(page, '09_setup_team_from_v03.png', false);

  await nav(page, 'Home');
  await page.setViewportSize({ width: 420, height: 900 });
  await settle(page);
  await page.locator('[data-testid="stSidebarCollapseButton"] button, [data-testid="stSidebarCollapseButton"]').first().click().catch(() => {});
  await settle(page);
  await shot(page, '10_home_narrow.png');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
