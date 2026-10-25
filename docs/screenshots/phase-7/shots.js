// Скриншоты фазы 7 и браузерная проверка экспорта:
// - реквизиты, логотип и условия КП переживают смену шага и переключение проектов
//   (у каждого проекта свои условия и описания этапов);
// - логотип загружается, удаляется и загружается снова;
// - кнопки «КП (PDF)», «КП (DOCX)», «Смета (XLSX)» отдают файлы (собираются по нажатию);
// - на экране 420 px нет горизонтальной прокрутки.
//
// AppTest не моделирует состояние виджетов на стороне браузера, загрузку файла и
// скачивание, поэтому это проверяется здесь (CLAUDE.md, ADR 0006–0009).
// Запуск из корня репозитория:
//
//   uv run streamlit run app/Home.py --server.port 8599 &
//   NODE_PATH=$(npm root -g) node docs/screenshots/phase-7/shots.js
//
// Кадры — в docs/screenshots/phase-7/. Нужен Playwright для Node и Chromium.
const fs = require('fs');
const path = require('path');
const os = require('os');
const { chromium } = require('playwright');
const OUT = __dirname + '/';
const URL = process.env.RENTAB_URL || 'http://localhost:8599/';
const LOGO = path.join(__dirname, '..', '..', '..', 'src', 'rentab_core', 'demo_logo.png');
const NBSP = / /g;

async function settle(page) {
  await page.waitForTimeout(400);
  await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(700);
}
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
async function fill(page, label, value) {
  const field = page.getByLabel(label, { exact: true });
  await field.fill(value);
  await field.press(label.startsWith('Дополнительные') ? 'Control+Enter' : 'Enter');
  await settle(page);
}
async function value(page, label) {
  return page.getByLabel(label, { exact: true }).inputValue();
}
async function expander(page, title) {
  const summary = page.locator('summary', { hasText: title }).first();
  const open = await summary.evaluate((el) => el.parentElement.open);
  if (!open) {
    await summary.click();
    await settle(page);
  }
}
function expect(condition, message) {
  if (!condition) throw new Error('CHECK FAILED: ' + message);
  console.log('ok:', message);
}
async function noHorizontalScroll(page, name) {
  const width = await page.evaluate(() => {
    const main = document.querySelector('[data-testid="stMain"]');
    return main.scrollWidth - main.clientWidth;
  });
  expect(width <= 1, `узкий экран «${name}»: без горизонтальной прокрутки (${width} px)`);
}
async function download(page, name, signature) {
  const [file] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    page.getByRole('button', { name, exact: true }).click(),
  ]);
  const target = path.join(os.tmpdir(), `rentab-${Date.now()}`);
  await file.saveAs(target);
  const data = fs.readFileSync(target);
  fs.unlinkSync(target);
  // Headless Chromium называет файл «download»; имя файла сервер отдаёт в заголовке.
  const response = await page.request.get(file.url());
  const disposition = decodeURIComponent(response.headers()['content-disposition'].split("''")[1]);
  expect(data.subarray(0, signature.length).equals(Buffer.from(signature)), `«${name}» — ${disposition}, ${data.length} байт`);
  return data;
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, acceptDownloads: true });
  await page.goto(URL);
  await settle(page);
  await button(page, '🎓 Загрузить демо-фирму');
  await page.waitForSelector('text=Загружена демо-фирма');

  // Фирма → «Реквизиты и логотип».
  await nav(page, 'Фирма');
  await tab(page, 'Реквизиты и логотип');
  expect((await value(page, 'Реквизиты одной строкой')).startsWith('ИНН 0000000000'), 'реквизиты демо-фирмы на месте');
  expect(await page.locator('[data-testid="stImage"] img').count() === 1, 'логотип демо-фирмы показан');
  await shot(page, '01_firm_letterhead.png');
  await button(page, 'Удалить логотип');
  expect(await page.getByText('Логотипа нет').count() === 1, 'логотип удалён');
  await page.locator('[data-testid="stFileUploader"] input[type="file"]').first().setInputFiles(LOGO);
  await settle(page);
  expect(await page.locator('[data-testid="stImage"] img').count() === 1, 'логотип загружен снова');
  await fill(page, 'Должность подписанта', 'партнёр');

  // Проект: условия КП и описания этапов у каждого проекта свои.
  await nav(page, 'Проект');
  await project(page, 'Патентование изобретения');
  await expander(page, 'Условия КП');
  expect(await value(page, 'Номер КП') === 'КП-3', 'у изобретения номер КП-3');
  await fill(page, 'Номер КП', 'КП-30');
  await fill(page, 'Дополнительные условия (каждое — с новой строки)', 'Перевод документов — по отдельному счёту.');
  const description = page.locator('[data-testid="stTextInput"] input[placeholder^="Что получит клиент"]').first();
  await description.fill('Отчёт о поиске и оценка патентоспособности');
  await description.press('Enter');
  await settle(page);
  await shot(page, '02_project_offer_terms.png');
  await project(page, 'Регистрация товарного знака');
  await expander(page, 'Условия КП');
  expect(await value(page, 'Номер КП') === 'КП-2', 'у товарного знака свой номер КП-2');
  await project(page, 'Патентование изобретения');
  await expander(page, 'Условия КП');
  expect(await value(page, 'Номер КП') === 'КП-30', 'номер КП-30 сохранился после переключения проектов');
  expect(await description.inputValue() === 'Отчёт о поиске и оценка патентоспособности', 'описание этапа сохранилось после переключения проектов');

  // Результат: три документа; реквизиты пережили смену шагов.
  await nav(page, 'Результат');
  await page.getByText('Документы', { exact: true }).first().scrollIntoViewIfNeeded();
  await shot(page, '03_result_documents.png');
  await download(page, 'КП (PDF)', '%PDF');
  await download(page, 'КП (DOCX)', 'PK');
  await download(page, 'Смета (XLSX)', 'PK');
  await nav(page, 'Фирма');
  await tab(page, 'Реквизиты и логотип');
  expect(await value(page, 'Должность подписанта') === 'партнёр', 'должность подписанта сохранилась после смены шагов');
  expect(await page.locator('[data-testid="stImage"] img').count() === 1, 'логотип сохранился после смены шагов');

  // Узкий экран.
  await page.setViewportSize({ width: 420, height: 900 });
  await settle(page);
  await page.locator('[data-testid="stSidebarCollapseButton"] button').first().click().catch(() => {});
  await settle(page);
  await noHorizontalScroll(page, 'Фирма → Реквизиты и логотип');
  await shot(page, '04_firm_letterhead_narrow.png');
  await nav(page, 'Результат');
  await page.locator('[data-testid="stSidebarCollapseButton"] button').first().click().catch(() => {});
  await settle(page);
  await noHorizontalScroll(page, 'Результат');
  await page.getByText('Документы', { exact: true }).first().scrollIntoViewIfNeeded();
  await shot(page, '05_result_documents_narrow.png', false);
  await browser.close();

  // Тёмная тема.
  const dark = await chromium.launch();
  const context = await dark.newContext({ viewport: { width: 1400, height: 1000 }, colorScheme: 'dark' });
  const night = await context.newPage();
  await night.goto(URL);
  await settle(night);
  await button(night, '🎓 Загрузить демо-фирму');
  await nav(night, 'Фирма');
  await tab(night, 'Реквизиты и логотип');
  await shot(night, '06_firm_letterhead_dark.png');
  await dark.close();
})().catch((e) => { console.error(e); process.exit(1); });
