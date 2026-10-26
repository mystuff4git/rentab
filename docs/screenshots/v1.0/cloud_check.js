// Проверка приватности и работы задеплоенного демо (ADR 0010, Definition of Done).
//
// Снаружи, как посетитель, через браузер:
// - на главной дисклеймер «Данные не покидают вашу сессию»; на Streamlit
//   Community Cloud — и оговорка о статистике Streamlit (Cloud включает её сам);
// - версия Rentab внизу главной;
// - демо-фирма загружается, NNE слайда 3 — 225 500 ₽ (golden-сценарий);
// - КП в PDF и DOCX и смета XLSX скачиваются; шрифты в PDF встроены;
// - сессии изолированы: новая сессия и перезагрузка страницы видят пустую фирму;
// - к каким хостам обращается браузер (шрифтов со сторонних серверов нет).
//
// Запись на диск внутри контейнера Cloud снаружи не видна: её проверяют тот же
// коммит в Docker с файловой системой только для чтения и аудит-хук (ADR 0010).
//
//   RENTAB_URL=https://rentab.streamlit.app/ NODE_PATH=$(npm root -g) \
//     node docs/screenshots/v1.0/cloud_check.js
const fs = require('fs');
const path = require('path');
const os = require('os');
const { chromium } = require('playwright');

const URL = process.env.RENTAB_URL;
const VERSION = process.env.RENTAB_VERSION || '1.0.0';
const VIEWPORT = { width: 1280, height: 900 };
const WAKE_TIMEOUT = 240000;
const THIRD_PARTY_FONTS = ['fonts.googleapis.com', 'fonts.gstatic.com'];

function expect(condition, message) {
  if (!condition) throw new Error('CHECK FAILED: ' + message);
  console.log('ok:', message);
}
// Приложение на *.streamlit.app живёт во фрейме; локально — в самой странице.
async function appFrame(page) {
  const deadline = Date.now() + WAKE_TIMEOUT;
  while (Date.now() < deadline) {
    const wake = page.getByRole('button', { name: /get this app back up/i });
    if (await wake.count()) {
      console.log('приложение спало — будим');
      await wake.first().click();
    }
    for (const frame of page.frames()) {
      if (await frame.locator('[data-testid="stApp"]').count().catch(() => 0)) {
        await frame.waitForSelector('[data-testid="stAppViewContainer"]', { timeout: 60000 });
        return frame;
      }
    }
    await page.waitForTimeout(2000);
  }
  throw new Error('приложение не открылось за ' + WAKE_TIMEOUT / 1000 + ' с');
}
async function settle(frame) {
  await frame.page().waitForTimeout(500);
  await frame.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 60000 }).catch(() => {});
  await frame.page().waitForTimeout(800);
}
async function open(context, hosts) {
  const page = await context.newPage();
  page.on('request', (request) => hosts.add(new globalThis.URL(request.url()).host));
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  const app = await appFrame(page);
  await settle(app);
  return { page, app };
}
async function download(page, app, name, signature) {
  const [file] = await Promise.all([
    page.waitForEvent('download', { timeout: 60000 }),
    app.getByRole('button', { name, exact: true }).click(),
  ]);
  const target = path.join(os.tmpdir(), `rentab-${Date.now()}`);
  await file.saveAs(target);
  const data = fs.readFileSync(target);
  fs.unlinkSync(target);
  expect(data.subarray(0, signature.length).equals(Buffer.from(signature)), `«${name}» скачан: ${data.length} байт`);
  return data;
}

(async () => {
  if (!URL) throw new Error('задайте RENTAB_URL');
  const cloud = new globalThis.URL(URL).host.endsWith('.streamlit.app');
  const hosts = new Set();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: VIEWPORT, acceptDownloads: true });
  const { page, app } = await open(context, hosts);

  const disclaimer = await app.locator('[data-testid="stAlertContentInfo"]').first().innerText();
  expect(disclaimer.includes('Данные не покидают вашу сессию.'), 'дисклеймер о приватности на главной');
  expect(disclaimer.includes('ничего не записывает на диск сервера'), 'дисклеймер: на диск сервера ничего не пишется');
  const statsNote = disclaimer.includes('включена анонимная статистика использования Streamlit');
  expect(statsNote === cloud, cloud ? 'оговорка о статистике Streamlit на Cloud есть' : 'оговорки о статистике нет (статистика выключена)');
  expect(await app.getByText(`Rentab ${VERSION} ·`).count() === 1, `версия Rentab ${VERSION} внизу главной`);

  await app.getByRole('button', { name: '🎓 Загрузить демо-фирму' }).click();
  await settle(app);
  expect(await app.getByText('Загружена демо-фирма').count() === 1, 'демо-фирма загружена');
  expect(await app.getByText('225 500 ₽').count() >= 1, 'NNE слайда 3 по плану — 225 500 ₽');

  await app.getByRole('link', { name: 'Результат', exact: true }).first().click();
  await settle(app);
  const pdf = (await download(page, app, 'КП (PDF)', '%PDF')).toString('latin1');
  expect(pdf.includes('/FontFile2') && !pdf.includes('/Helvetica'), 'в PDF встроены свои шрифты, системных нет');
  await download(page, app, 'КП (DOCX)', 'PK');
  await download(page, app, 'Смета (XLSX)', 'PK');

  // Другая сессия (новый контекст браузера) и перезагрузка — данных нет.
  const other = await browser.newContext({ viewport: VIEWPORT });
  const second = await open(other, hosts);
  expect(await second.app.getByText('Без названия · 1').count() >= 1, 'новая сессия: пустой проект, данных демо-фирмы нет');
  expect(await second.app.getByText('225 500 ₽').count() === 0, 'новая сессия не видит смету первой');
  await page.reload({ waitUntil: 'domcontentloaded' });
  const reloaded = await appFrame(page);
  await settle(reloaded);
  expect(await reloaded.getByText('225 500 ₽').count() === 0, 'после перезагрузки страницы данных сессии нет');

  const list = [...hosts].sort();
  console.log('хосты, к которым обращался браузер:', list.join(', '));
  expect(!list.some((host) => THIRD_PARTY_FONTS.includes(host)), 'шрифты — не со сторонних серверов');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
