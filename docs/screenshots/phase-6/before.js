// Скриншоты «до» фазы 6: все экраны приложения фазы 5 (коммит 4c7fe07).
//
// Запуск из корня репозитория на версии до фазы 6:
//
//   git switch --detach 4c7fe07
//   uv run streamlit run app/Home.py --server.port 8599 &
//   NODE_PATH=$(npm root -g) node docs/screenshots/phase-6/before.js
//
// Кадры — в docs/screenshots/phase-6/before/. Нужен Playwright для Node.
const { chromium } = require('playwright');
const OUT = __dirname + '/before/';
const URL = process.env.RENTAB_URL || 'http://localhost:8599/';

async function settle(page) {
  await page.waitForTimeout(400);
  await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(700);
}
async function nav(page, name) {
  await page.locator('[data-testid="stSidebarNav"] a', { hasText: name }).first().click();
  await settle(page);
}
async function button(page, name) {
  await page.getByRole('button', { name }).first().click();
  await settle(page);
}
async function project(page, name) {
  await page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first().click();
  await page.getByRole('option', { name }).click();
  await settle(page);
}
// Основной контейнер Streamlit прокручивается сам: высота окна — по содержимому.
async function shot(page, file) {
  const size = page.viewportSize();
  const height = await page.evaluate(() => {
    const main = document.querySelector('[data-testid="stMain"]') || document.body;
    return Math.max(main.scrollHeight, document.body.scrollHeight);
  });
  await page.setViewportSize({ width: size.width, height: Math.min(Math.max(height, size.height), 9000) });
  await page.waitForTimeout(500);
  await page.screenshot({ path: OUT + file, fullPage: true });
  await page.setViewportSize(size);
  await page.waitForTimeout(300);
  console.log('saved', file);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  await page.goto(URL);
  await settle(page);
  await shot(page, '01_home_empty.png');
  await button(page, '🎓 Загрузить демо-фирму');
  await page.waitForSelector('text=Загружена демо-фирма');
  await shot(page, '02_home_demo.png');

  await nav(page, 'Настройка');
  await shot(page, '03_setup_jurisdiction.png');
  await page.getByRole('tab', { name: /Команда/ }).click();
  await settle(page);
  await shot(page, '04_setup_team.png');
  await page.getByRole('tab', { name: /Расходы/ }).click();
  await settle(page);
  await shot(page, '05_setup_overheads.png');

  await nav(page, 'Проект');
  await project(page, 'Регистрация товарного знака');
  await button(page, '💰 Рассчитать смету');
  await shot(page, '06_project_trademark.png');
  await nav(page, 'Дашборд');
  await shot(page, '07_dashboard_trademark.png');
  await nav(page, 'Чувствительность');
  await shot(page, '08_sensitivity_trademark.png');
  await nav(page, 'Сравнение');
  await shot(page, '09_compare_trademark.png');
  await project(page, 'Слайд 3: план и факт');
  await nav(page, 'План–факт');
  await shot(page, '10_plan_fact_slide3.png');

  await page.setViewportSize({ width: 420, height: 900 });
  await settle(page);
  await page.locator('[data-testid="stSidebarCollapseButton"] button').first().click().catch(() => {});
  await settle(page);
  await shot(page, '11_plan_fact_narrow.png');
  for (const [name, file] of [['Дашборд', '12_dashboard_narrow.png'], ['Сравнение', '13_compare_narrow.png'], ['Home', '14_home_narrow.png']]) {
    await page.locator('[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapsedControl"] button').first().click();
    await settle(page);
    if (name === 'Дашборд') await project(page, 'Регистрация товарного знака');
    await nav(page, name);
    await page.locator('[data-testid="stSidebarCollapseButton"] button').first().click().catch(() => {});
    await settle(page);
    await shot(page, file);
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
