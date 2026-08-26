import {test as rawTest, expect as rawExpect} from '@playwright/test';
import {test, expect, login, adminApi, createCustomer, type AdminApi} from './helpers';

// U3 Makine 360 — gerçek tarayıcı sözleşmesi.
//
// DİSİPLİN (menu-groups.spec.ts ile aynı): giriş dışında `page.goto()` YOKTUR;
// makineye kenar çubuğundan → listeden SPA içi gezilerek varılır. TEK İSTİSNA
// "derin bağlantı" testidir: orada test edilen şeyin ta kendisi URL ile GİRİŞ
// ve YENİLEME olduğu için `goto`/`reload` kaçınılmazdır ve kasıtlıdır.
//
// KONSOL-TEMİZ İSTİSNASI: hata enjekte eden senaryolar (500/403) Chromium'un
// her 4xx/5xx yanıtı için düşürdüğü console.error yüzünden `helpers.test`
// sözleşmesini ihlal eder. O senaryolar bilinçli olarak sarmalanmamış
// `rawTest` kullanır — hata ÜRETMEK testin amacıdır.
//
// BİLİNÇLİ LİTERAL — ürün sözleşmesi. Bu dosya kasten `MACHINE_TAB_LABELS` ya
// da `NAV_LABELS`'tan import ETMEZ: e2e, kullanıcının ekranda gerçekten
// gördüğü metni doğrulayan dış gözlemcidir. Etiketler değişirse BURAYI DA ELLE
// GÜNCELLE.
const L = {
  serviceGroup: 'Servis',
  machines: 'Makineler',
  identity: 'Kimlik',
  readings: 'Sayaç Geçmişi',
  ownership: 'Sahiplik Tarihçesi',
  service: 'Servis / İş Emirleri',
  emptyReadings: 'Henüz sayaç okuması kaydedilmedi.',
  emptyOwnership: 'Sahiplik kaydı yok.',
  emptyService: 'Bu makine için henüz iş emri kaydı yok.',
  errReadings: 'Sayaç geçmişi yüklenemedi.',
  errOwnership: 'Sahiplik tarihçesi yüklenemedi.',
  errService: 'Servis geçmişi yüklenemedi.',
} as const;

const EMPTY_TEXTS = [L.emptyReadings, L.emptyOwnership, L.emptyService] as const;

// Seri/şasi/plaka firma içinde tekildir; çalışma başına benzersiz son ek,
// worker yeniden başlarsa tohumlamanın 409'a çarpmasını engeller.
const RUN = String(Date.now()).slice(-8);
const MACHINE = {
  brand: 'E2E Makine',
  model: 'M360',
  serial: `E2E-SERI-${RUN}`,
  chassis: `E2E-SASI-${RUN}`,
  engine: `E2E-MOTOR-${RUN}`,
  plate: `06 E2E ${RUN}`,
} as const;

// Sayfalama kanıtı için ayrı makine: 200'lük eski tavanın ÜSTÜNDE iş emri
// taşır, böylece 201. kaydın gerçekten erişilebildiği görülür.
const BULK = {brand: 'E2E Yoğun', model: 'M201', serial: `E2E-COK-${RUN}`} as const;
const BULK_WORK_ORDERS = 201;
const PAGE_SIZE = 25;
/** Sayaç sayfa boyutu; makineye KASTEN tam bir sayfa dolusu okuma konur. */
const READING_PAGE_SIZE = 25;

type Page = import('@playwright/test').Page;

const tab = (page: Page, name: string) => page.getByRole('tab', {name: new RegExp(name)});
const navLink = (page: Page, label: string) => page.getByRole('link', {name: label, exact: true});

let admin: AdminApi | undefined;
let machineId = 0;
let bulkMachineId = 0;

async function createMachine(api: AdminApi, data: Record<string, unknown>): Promise<number> {
  const created = await api.api.post('/api/machines', {headers: await api.headers(), data});
  if (!created.ok()) {
    throw new Error(`makine oluşturulamadı (${created.status()}): ${await created.text()}`);
  }
  return (await created.json()).id as number;
}

test.beforeAll(async () => {
  admin = await adminApi();
  const customerId = await createCustomer(admin, `E2E Makine Sahibi ${Date.now()}`);

  machineId = await createMachine(admin, {
    customer_id: customerId,
    brand: MACHINE.brand,
    model: MACHINE.model,
    serial_number: MACHINE.serial,
    chassis_number: MACHINE.chassis,
    engine_number: MACHINE.engine,
    registration_number: MACHINE.plate,
    model_year: 2020,
    working_hours: '1000.00',
  });

  // TAM BİR SAYFA (25) okuma: sayfa boyutunun tam katı, sınır hatalarının
  // ortaya çıktığı yer. Dolu son sayfada "Sonraki" ÇIKMAMALI.
  for (let index = 0; index < READING_PAGE_SIZE; index += 1) {
    const reading = await admin.api.post(`/api/machines/${machineId}/hour-readings`, {
      headers: await admin.headers(),
      data: {reading_hours: `${1000 + index}.50`, reading_type: 'normal'},
    });
    if (!reading.ok()) {
      throw new Error(`sayaç okuması eklenemedi (${reading.status()}): ${await reading.text()}`);
    }
  }

  // --- Sayfalama önkoşulu -------------------------------------------------
  bulkMachineId = await createMachine(admin, {
    customer_id: customerId,
    brand: BULK.brand,
    model: BULK.model,
    serial_number: BULK.serial,
    working_hours: '10.00',
  });

  const technicians = await admin.api.get('/api/work-orders/technicians', {
    headers: await admin.headers(),
  });
  if (!technicians.ok()) {
    throw new Error(`teknisyen listesi alınamadı (${technicians.status()})`);
  }
  const technicianId = (await technicians.json())[0].id as number;

  const headers = await admin.headers();
  for (let start = 0; start < BULK_WORK_ORDERS; start += 20) {
    const batch = Array.from({length: Math.min(20, BULK_WORK_ORDERS - start)}, (_unused, offset) =>
      admin!.api.post('/api/work-orders', {
        headers,
        data: {
          machine_id: bulkMachineId,
          customer_id: customerId,
          technician_id: technicianId,
          complaint: `Toplu kayıt ${start + offset + 1}`,
        },
      }),
    );
    const results = await Promise.all(batch);
    const failed = results.find((result) => !result.ok());
    if (failed) {
      throw new Error(`iş emri tohumlanamadı (${failed.status()}): ${await failed.text()}`);
    }
  }
});

test.afterAll(async () => {
  await admin?.dispose();
});

// `md` altında kenar çubuğu geçici Drawer'a döner; menü düğmesi görünürse
// önce o açılır. Bağlantıya tıklamak Drawer'ı kendisi kapatır (AppShell).
async function openSidebar(page: Page): Promise<void> {
  const size = page.viewportSize();
  // AppShell `md` (900px) altında geçici Drawer'a geçer.
  if (!size || size.width >= 900) return;
  await page.getByRole('button', {name: 'Menüyü aç'}).click();
  await expect(page.locator('.MuiDrawer-paper')).toBeVisible();
}

// Kenar çubuğu → Servis → Makineler → seri no ile filtrele → kartı aç.
async function goToMachines(page: Page): Promise<void> {
  await openSidebar(page);
  // Başlığın kendisi gezinir ve mobil çekmeceyi kapatır; burada yalnız grubu
  // açmak istediğimiz için ok düğmesi kullanılır.
  await page.locator('[data-nav-toggle="service"]').click();
  await navLink(page, L.machines).click();
  await expect(page).toHaveURL(/makineler$/);
}

// Makine listesi `sm` altında ResponsiveTable kart dalına düşer: satır yerine
// kart, çift tık yerine tek tık. Test her iki dalda da aynı işi yapar.
//
// KARARLILIK: liste 250ms debounce ile YENİDEN yükleniyor (Machines.tsx).
// Filtre uygulanmadan önce satır zaten ekranda olduğu için naif bir "görünür
// olunca tıkla" yaklaşımı, tıklamayı yeniden çizilen ızgaraya düşürüp
// gezinmeyi sessizce kaybediyordu (CI'da flaky olarak yakalandı). Bu yüzden
// önce filtre yanıtı beklenir, sonra tıklama+URL doğrulaması birlikte
// yeniden denenir.
async function openMachineRow(page: Page, serial: string, id: number): Promise<void> {
  const filtered = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === '/api/machines' && response.url().includes(serial),
    {timeout: 15_000},
  );
  await page.getByLabel('Marka, model, seri no, şasi no veya plaka ara').fill(serial);
  await filtered;

  const row = page.getByRole('row').filter({hasText: serial});
  const card = page.locator('.MuiCard-root').filter({hasText: serial});
  const target = new RegExp(`/makineler/${id}`);

  await expect(async () => {
    // Masaüstü tablo artık TEK tıkla açılır (mobil kart zaten öyleydi).
    if (await row.count()) await row.first().click();
    else await card.first().click();
    await expect(page).toHaveURL(target, {timeout: 3_000});
  }).toPass({timeout: 20_000});
}

async function openMachine(page: Page, serial: string, id: number): Promise<void> {
  await login(page);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});
  await goToMachines(page);
  await openMachineRow(page, serial, id);
}

test('makine kartı açılır ve dört sekme arasında gezilir', async ({page}) => {
  await openMachine(page, MACHINE.serial, machineId);

  await expect(tab(page, L.identity)).toHaveAttribute('aria-selected', 'true');
  for (const value of [MACHINE.serial, MACHINE.chassis, MACHINE.engine, MACHINE.plate]) {
    await expect(page.getByText(value, {exact: true}).first()).toBeVisible();
  }

  await tab(page, L.readings).click();
  await expect(page.getByText('Manuel', {exact: true}).first()).toBeVisible();
  // Tam bir sayfa dolusu (25) okuma var: dolu son sayfa "Sonraki" ÜRETMEZ.
  await expect(page.getByText(`Toplam ${READING_PAGE_SIZE} kayıt`)).toBeVisible();
  await expect(page.getByRole('button', {name: 'Sonraki'})).toHaveCount(0);

  await tab(page, L.ownership).click();
  await expect(tab(page, L.ownership)).toHaveAttribute('aria-selected', 'true');

  await tab(page, L.service).click();
  await expect(page.getByText(L.emptyService)).toBeVisible();

  // Ekler sekmesi YOKTUR (M-2 işi); yer tutucu da konmadı.
  await expect(page.getByRole('tab')).toHaveCount(4);
  await expect(page.getByRole('tab', {name: /Ekler/})).toHaveCount(0);

  await tab(page, L.identity).click();
  await expect(page.getByText(MACHINE.chassis, {exact: true}).first()).toBeVisible();
});

// --- 1) Viewport matrisi ----------------------------------------------------
for (const viewport of [
  {name: 'masaüstü', width: 1920, height: 1080},
  {name: 'tablet', width: 768, height: 1024},
  {name: 'telefon', width: 390, height: 844},
]) {
  test(`${viewport.name} (${viewport.width}px): sekmeler tıklanır, yatay taşma yok`, async ({page}) => {
    await page.setViewportSize({width: viewport.width, height: viewport.height});
    await openMachine(page, MACHINE.serial, machineId);

    for (const label of [L.readings, L.ownership, L.service]) {
      const target = tab(page, label);
      await target.click();
      await expect(target).toHaveAttribute('aria-selected', 'true');
    }

    // Gövde yatayda kaymamalı: kaydırma genişliği görünür genişliği aşmasın.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `${viewport.width}px'te yatay taşma var`).toBeLessThanOrEqual(1);
  });
}

// --- 2) Klavye --------------------------------------------------------------
test('sekmeler klavyeyle gezilir: ok tuşları odağı taşır, Enter seçer', async ({page}) => {
  await openMachine(page, MACHINE.serial, machineId);

  await tab(page, L.identity).focus();
  await expect(tab(page, L.identity)).toBeFocused();

  // MUI sekmelerinde ok tuşu ODAĞI taşır, seçimi değiştirmez.
  await page.keyboard.press('ArrowRight');
  await expect(tab(page, L.readings)).toBeFocused();
  await expect(tab(page, L.identity)).toHaveAttribute('aria-selected', 'true');

  // Görünür odak halkası: klavyeyle gelen odak `Mui-focusVisible` kazanır.
  await expect(tab(page, L.readings)).toHaveClass(/Mui-focusVisible/);

  await page.keyboard.press('Enter');
  await expect(tab(page, L.readings)).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('ArrowRight');
  await expect(tab(page, L.ownership)).toBeFocused();
  await page.keyboard.press('Space');
  await expect(tab(page, L.ownership)).toHaveAttribute('aria-selected', 'true');
});

// --- 3) Uç hatası: hata metni var, boş-durum metni yok ----------------------
// `rawTest`: 500 yanıtı Chromium'da console.error üretir, konsol-temiz
// sözleşmesi burada bilinçli olarak uygulanmaz.
// Yol eşleşmesi glob yerine YÜKLEM ile yapılır: Playwright glob'unda `?` joker
// değildir, sorgu dizeli uçlar sessizce eşleşmeden geçerdi (ve test, hata hiç
// enjekte edilmeden yeşil yanardı).
const ERROR_CASES = [
  {
    label: L.readings,
    match: (url: URL) => url.pathname.endsWith('/hour-readings'),
    message: L.errReadings,
  },
  {
    label: L.ownership,
    match: (url: URL) => url.pathname.endsWith('/ownership-history'),
    message: L.errOwnership,
  },
  {
    label: L.service,
    match: (url: URL) => url.pathname === '/api/work-orders',
    message: L.errService,
  },
] as const;

for (const {label, match, message} of ERROR_CASES) {
  rawTest(`${label}: uç 500 dönerse hata gösterilir, "kayıt yok" GÖSTERİLMEZ`, async ({page}) => {
    // Kesinti karta GİRMEDEN önce kurulur: listeler sekme açılışında değil,
    // kart yüklenirken çekiliyor.
    // Gövde KASTEN `detail`siz: uç bir mesaj döndürseydi (403 testindeki gibi)
    // onu göstermek doğru davranış olurdu; burada doğrulanan, sekmenin KENDİ
    // yedek metnine düşmesi.
    await page.route(match, (route) =>
      route.fulfill({status: 500, contentType: 'application/json', body: '{}'}),
    );
    await openMachine(page, MACHINE.serial, machineId);

    await tab(page, label).click();
    await rawExpect(page.getByText(message)).toBeVisible({timeout: 15_000});
    // Asıl sözleşme: "veri gelmedi" ile "veri yok" asla birlikte görünmez.
    for (const empty of EMPTY_TEXTS) {
      await rawExpect(page.getByText(empty)).toHaveCount(0);
    }
  });
}

// --- 4) 403 yanıtının SUNUMU -------------------------------------------------
// Bu test YETKİLENDİRMEYİ doğrulamaz; doğruladığı tek şey, uç 403 döndüğünde
// UI'ın bunu hata olarak sunması ve boş-durum diye yutmamasıdır.
//
// Neden gerçek bir rolle değil: `GET /api/work-orders` bugün `read` iznine
// düşüyor (auth.py — güvenli metotlar baseline `read`), yani HİÇBİR rol bu
// okumadan mahrum değil. Gerçek bir rolle 403 üretmek izin matrisini
// değiştirmeyi gerektirirdi; bu PR'ın kapsamı dışı ve ayrı madde olarak
// kaydedildi. Yanıt bu yüzden uçtan enjekte edilir.
rawTest('403 yanıtı hata olarak sunulur, boş-durum diye yutulmaz', async ({page}) => {
  await page.route(
    (url: URL) => url.pathname === '/api/work-orders',
    (route) =>
      route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: '{"detail":"Bu işlem için yetkiniz yok"}',
      }),
  );
  await openMachine(page, MACHINE.serial, machineId);

  await tab(page, L.service).click();
  await rawExpect(page.getByText('Bu işlem için yetkiniz yok')).toBeVisible({timeout: 15_000});
  await rawExpect(page.getByText(L.emptyService)).toHaveCount(0);
});

// --- 5) Gerçek sayfalama ----------------------------------------------------
test('200 kaydın ötesi erişilebilir: toplam görünür ve Sonraki ikinci sayfayı getirir', async ({page}) => {
  await openMachine(page, BULK.serial, bulkMachineId);
  await tab(page, L.service).click();

  const pages = Math.ceil(BULK_WORK_ORDERS / PAGE_SIZE);
  // Eski sabit 200 tavanı olsaydı toplam 201 yazamazdı.
  await expect(page.getByText(`Toplam ${BULK_WORK_ORDERS} kayıt · Sayfa 1/${pages}`)).toBeVisible({
    timeout: 15_000,
  });
  await expect(tab(page, L.service)).toContainText(`(${BULK_WORK_ORDERS})`);

  const firstPageRow = (await page.getByRole('row').nth(1).innerText()).trim();
  await page.getByRole('button', {name: 'Sonraki'}).click();
  await expect(page.getByText(`Toplam ${BULK_WORK_ORDERS} kayıt · Sayfa 2/${pages}`)).toBeVisible();
  await expect(page.getByRole('row').nth(1)).not.toHaveText(firstPageRow);

  await page.getByRole('button', {name: 'Önceki'}).click();
  await expect(page.getByText(`Toplam ${BULK_WORK_ORDERS} kayıt · Sayfa 1/${pages}`)).toBeVisible();
});

// --- 6) Derin bağlantı ve yenileme -----------------------------------------
// Bu testte `goto`/`reload` KASITLIDIR: doğrulanan şey tam olarak URL ile
// girişin ve tam yeniden yüklemenin sekmeyi koruduğudur.
test('?sekme= derin bağlantısı, yenilemede kalıcılık ve geçersiz slug', async ({page}) => {
  await login(page);

  await page.goto(`/makineler/${machineId}?sekme=servis`);
  await expect(tab(page, L.service)).toHaveAttribute('aria-selected', 'true', {timeout: 15_000});

  await page.reload();
  await expect(tab(page, L.service)).toHaveAttribute('aria-selected', 'true', {timeout: 15_000});

  // Geçersiz slug hata değil: ilk sekmeye düşer.
  await page.goto(`/makineler/${machineId}?sekme=uzayaraci`);
  await expect(tab(page, L.identity)).toHaveAttribute('aria-selected', 'true', {timeout: 15_000});
});

// --- Geri tuşu sözleşmesi (merge şartı) -------------------------------------
// Sekme değişimi URL'ye `{replace:true}` ile yazılır: adres paylaşılabilir ama
// geçmişe İTİLMEZ. Bu testin varlık sebebi, `replace`'ı `push`'a çeviren bir
// değişikliğin diğer bütün testler yeşilken davranışı sessizce bozabilmesidir
// — o durumda kullanıcı listeye dönmek için dört kez geri basardı.
test('üç sekme gezildikten sonra TEK geri makine listesine döner', async ({page}) => {
  await openMachine(page, MACHINE.serial, machineId);

  await tab(page, L.readings).click();
  await expect(page).toHaveURL(/sekme=sayac/);
  await tab(page, L.ownership).click();
  await expect(page).toHaveURL(/sekme=sahiplik/);
  await tab(page, L.service).click();
  await expect(page).toHaveURL(/sekme=servis/);

  // Üç sekme gezildi; geçmişte tek adım geride hâlâ makine listesi olmalı.
  await page.goBack();
  await expect(page).toHaveURL(/makineler$/);
  // Gerçekten liste ekranı: filtre alanı görünür.
  await expect(page.getByLabel('Marka, model, seri no, şasi no veya plaka ara')).toBeVisible();
});
