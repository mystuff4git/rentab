// Скриншоты v1.0 для README и кадры GIF «демо-фирма → смета → КП» (ADR 0010).
//
// Сценарий проходит приложение как посетитель: загружает демо-фирму, открывает
// фирму, проект, смету с подсказкой-формулой, чувствительность, сравнение,
// план–факт и скачивает КП. По пути проверяет то, что видно только в браузере:
// дисклеймер, отсутствие горизонтальной прокрутки на узком экране, файлы КП и сметы.
//
// Запуск из корня репозитория (приложение — в контейнере только для чтения с
// аудитом записи на диск; итог аудита — в логе контейнера после остановки):
//
//   docker build -t rentab .
//   docker run -d --name rentab-audit --read-only --tmpfs /tmp:size=64m,mode=1777 \
//     -e PYTHONPATH=/app/tests/server_audit:/app -v "$PWD/tests:/app/tests:ro" \
//     -p 127.0.0.1:8599:8501 rentab
//   FRAMES=/tmp/rentab-frames NODE_PATH=$(npm root -g) node docs/screenshots/v1.0/shots.js
//   uv run python docs/screenshots/v1.0/make_gif.py /tmp/rentab-frames docs/screenshots/v1.0/rentab.gif
//   docker stop rentab-audit && docker logs rentab-audit | grep write-audit
//
// Скриншоты — в docs/screenshots/v1.0/, кадры GIF и manifest.json — в $FRAMES.
// Нужен Playwright для Node и Chromium.
const fs = require('fs');
const path = require('path');
const os = require('os');
const { chromium } = require('playwright');

const OUT = __dirname + '/';
const URL = process.env.RENTAB_URL || 'http://localhost:8599/';
const FRAMES = process.env.FRAMES || path.join(os.tmpdir(), 'rentab-frames');
const VIEWPORT = { width: 1280, height: 800 };
const NARROW = { width: 420, height: 900 };

const manifest = [];

async function settle(page) {
  await page.waitForTimeout(400);
  await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(700);
}
function expect(condition, message) {
  if (!condition) throw new Error('CHECK FAILED: ' + message);
  console.log('ok:', message);
}
// Скриншот для README: видимая часть окна.
async function shot(page, file) {
  await page.screenshot({ path: OUT + file });
  console.log('saved', file);
}
// Кадр GIF: видимая часть окна, длительность показа и точка клика (если будет).
async function frame(page, name, hold, target = null) {
  const file = `${String(manifest.length + 1).padStart(2, '0')}_${name}.png`;
  await page.screenshot({ path: path.join(FRAMES, file) });
  let click = null;
  if (target) {
    const box = await target.boundingBox();
    click = [Math.round(box.x + box.width / 2), Math.round(box.y + box.height / 2)];
  }
  manifest.push({ file, hold, click });
  console.log('frame', file);
}
// Прокрутка основной области так, чтобы элемент оказался у верхнего края окна.
async function scrollTo(page, locator, offset = 90) {
  await locator.first().scrollIntoViewIfNeeded();
  const box = await locator.first().boundingBox();
  await page.evaluate(([dy]) => {
    const main = document.querySelector('[data-testid="stMain"]');
    (main || document.scrollingElement).scrollBy(0, dy);
  }, [box.y - offset]);
  await page.waitForTimeout(400);
}
async function scrollTop(page) {
  await page.evaluate(() => {
    const main = document.querySelector('[data-testid="stMain"]');
    (main || document.scrollingElement).scrollTo(0, 0);
  });
  await page.waitForTimeout(300);
}
function link(page, name) {
  return page.getByRole('link', { name, exact: true }).first();
}
async function nav(page, name) {
  const target = link(page, name);
  const box = await target.boundingBox().catch(() => null);
  if (!box || box.x < 0 || box.x > page.viewportSize().width) {
    await page.locator('[data-testid="stExpandSidebarButton"], [data-testid="stSidebarCollapsedControl"] button').first().click();
    await page.waitForTimeout(500);
  }
  await target.click();
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
  expect(data.subarray(0, signature.length).equals(Buffer.from(signature)), `«${name}»: ${data.length} байт`);
}

(async () => {
  fs.mkdirSync(FRAMES, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: VIEWPORT, acceptDownloads: true, colorScheme: 'light' });
  const page = await context.newPage();
  await page.goto(URL);
  await settle(page);

  // Главная: дисклеймер и кнопка демо-фирмы.
  const disclaimer = await page.locator('[data-testid="stAlertContentInfo"]').first().innerText();
  expect(disclaimer.includes('Данные не покидают вашу сессию.'), 'дисклеймер о приватности на главной');
  await frame(page, 'home', 2600);
  const demo = page.getByRole('button', { name: '🎓 Загрузить демо-фирму' });
  await scrollTo(page, page.getByText('Данные', { exact: true }));
  await frame(page, 'home_data', 1400, demo);
  await demo.click();
  await settle(page);
  await page.waitForSelector('text=Загружена демо-фирма');
  await scrollTo(page, page.getByText('Проекты сессии', { exact: true }), 330);
  await frame(page, 'home_demo', 2200, link(page, 'Фирма'));
  await scrollTop(page);
  await shot(page, '01_home_demo.png');

  // Фирма: команда — себестоимость часа и стоимость недозагрузки.
  await nav(page, 'Фирма');
  await tab(page, 'Команда');
  await scrollTo(page, page.getByText('Состав команды', { exact: true }));
  await shot(page, '02_firm_team.png');
  await frame(page, 'firm_team', 2400, link(page, 'Проект'));

  // Проект: товарный знак — этапы, исполнители, модель цены.
  await nav(page, 'Проект');
  await project(page, 'Регистрация товарного знака');
  await scrollTo(page, page.getByText('Анализ охраноспособности').first(), 110);
  await shot(page, '03_project.png');
  await frame(page, 'project', 2400, link(page, 'Результат'));

  // Результат: KPI, подсказка с формулой, светофор этапов.
  await nav(page, 'Результат');
  await shot(page, '04_result_estimate.png');
  const nneHelp = page.locator('[data-testid="stMetric"]', { hasText: 'Чистая прибыль' }).locator('[data-testid="stTooltipIcon"]').first();
  await frame(page, 'result', 2200, nneHelp);
  await nneHelp.hover();
  await page.waitForSelector('[data-testid="stTooltipContent"]');
  await page.waitForTimeout(500);
  await frame(page, 'result_formula', 3600);
  await page.mouse.move(5, 5);
  await scrollTo(page, page.getByText('Структура выручки без НДС', { exact: true }), 80);
  await frame(page, 'result_stages', 2400);

  // Чувствительность: запас часов и торнадо.
  await scrollTop(page);
  const sensitivity = page.getByRole('tab', { name: 'Чувствительность' });
  await frame(page, 'result_top', 1200, sensitivity);
  await tab(page, 'Чувствительность');
  await scrollTo(page, page.getByText('Торнадо: что сильнее всего двигает NNE', { exact: true }), 120);
  await shot(page, '05_sensitivity.png');
  await frame(page, 'sensitivity', 2600);

  // Документы: КП и смета скачиваются; последний кадр GIF — страница КП (make_gif.py).
  await tab(page, 'Смета');
  await scrollTo(page, page.getByText('Документы', { exact: true }), 200);
  const pdf = page.getByRole('button', { name: 'КП (PDF)', exact: true });
  await frame(page, 'documents', 2000, pdf);
  await download(page, 'КП (PDF)', '%PDF');
  await download(page, 'КП (DOCX)', 'PK');
  await download(page, 'Смета (XLSX)', 'PK');

  // Сравнение и план–факт — для README.
  await nav(page, 'Сравнение');
  await shot(page, '06_compare.png');
  await project(page, 'Слайд 3: план и факт');
  await nav(page, 'План–факт');
  await scrollTo(page, page.getByText('План → факт', { exact: true }));
  await shot(page, '07_plan_fact.png');

  // Узкий экран.
  await nav(page, 'Результат');
  await page.setViewportSize(NARROW);
  await settle(page);
  await page.locator('[data-testid="stSidebarCollapseButton"] button').first().click().catch(() => {});
  await settle(page);
  await noHorizontalScroll(page, 'Результат');
  await shot(page, '09_result_narrow.png');
  await browser.close();

  // Тёмная тема.
  const dark = await chromium.launch();
  const night = await (await dark.newContext({ viewport: VIEWPORT, colorScheme: 'dark' })).newPage();
  await night.goto(URL);
  await settle(night);
  await night.getByRole('button', { name: '🎓 Загрузить демо-фирму' }).click();
  await settle(night);
  await nav(night, 'Результат');
  await shot(night, '08_result_dark.png');
  await dark.close();

  fs.writeFileSync(path.join(FRAMES, 'manifest.json'), JSON.stringify({ viewport: VIEWPORT, frames: manifest }, null, 2));
  console.log('manifest', path.join(FRAMES, 'manifest.json'));
})().catch((e) => { console.error(e); process.exit(1); });
