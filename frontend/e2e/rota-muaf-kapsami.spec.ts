import {type Page} from '@playwright/test';

import {
  adminApi,
  createCompany,
  createProduct,
  createSupplier,
  createWarehouse,
  expect,
  loginAs,
  provisionUser,
  test,
  type AdminApi,
} from './helpers';
import {renderKanitiniDogrula} from './rota-render-kaniti';

// RENDER KONTRATINDAKİ ÜÇ POZİTİF SPEC — muaf'tan spec'e taşınan `:id` rotaları.
//
// ÖLÇÜLEN BOŞLUK. `/tedarikciler/:id`, `/urunler/:id`, `/depolar/:id` üçü de
// önceden `muaf`tı: "tohumlanmış kayıt ister" gerekçesiyle kapsam DIŞINDA
// duruyorlardı. Kapsamın ÖLÇÜLMÜŞ SINIRI olan muafiyet, bu üç rotanın hiçbir
// testte açılmadığını söylüyordu — bir render çökmesi bu ekranlarda hiç
// yakalanamazdı. PR #14 bunları kapsama taşır.
//
// NEDEN AYRI DOSYA. Bu üç rota RENDER KONTRATINDAKİ girdilerdir: raportör (R5)
// onlar için ziyaret + render kanıtını BİRLİKTE ister (bkz. rota-render-kaniti.ts).
// Kontrat, kapsamı yalnız "rota açıldı"dan "sayfa ÇİZİLDİ"ye taşır. Bu dosya tam
// o üç testi üretir; envanterdeki `testAdi` alanları buradaki test başlıklarıyla
// birebir eşleşir yoksa raportör testi bulamaz (R3) ve kapı kırmızı düşer.
//
// HER TESTİN DÖRT ÖLÇÜMÜ:
//   1. gerçek deterministic fixture — testin ÜRETTİĞİ kayıt, tohum değil. Ad,
//      envanterdeki `isaret` ile birebir aynı LITERALdir (ROTAKAPSAM ...); adı
//      değiştirmek envanteri de bozar (G13 bu ikisini birbirine bağlar).
//   2. gerçek URL ve pathname eşitliği — rota bir izin duvarına çarpıp başka
//      bir yola düşmüş olmasın (kabuk varsayılanı `/`'a düşmektir).
//   3. `rota-govdesi` count 1 + işaret kökte görünür — renderKanitiniDogrula
//      bunu ölçer ve kanıtı testInfo EKİ olarak bırakır (ROTA_RENDER_EKI).
//   4. R5/R6 ziyaret kaydı — helpers `context` fixture'ı bağlam seviyesinde
//      gezinti kaydını otomatik iliştirir; `page.goto` bu üç rotayı GERÇEKTEN
//      açar, `yoluRotayaCoz` onları `/.../:id` desenine indirger.
//
// KONSOL-TEMİZ GARANTİSİ. `test` fixture'ı her console.error / uncaught
// exception'ı kırmızıya çevirir. Üç ekran da taze kayıtla TÜM istekleri 200 ile
// bitirir: supplier detail `/payments/accounts` (yonetici izinli) +
// `/suppliers/{id}`; product detail `/products/{id}` + `/products/{id}/current`
// (supersession yoksa `resolved:false` döner — 404 DEĞİL) + `/warehouses`;
// warehouse detail `/warehouses/{id}` + `/warehouses/counts`. Hiçbiri meşru 4xx
// üretmez.

// KIRACI İZOLASYONU — GERİLEME KANITIYLA GEREKLİ.
//
// Bu üç fixture daha önce TOHUM FİRMASINA yazıyordu. serve.py her Playwright
// çağrısı için TEK bir SQLite açar, workers=1 ve fullyParallel=false: depo
// detayı testi tohum firmaya ÜÇÜNCÜ depoyu ekliyor, `touch-targets.spec.ts`
// ise Şubeler Arası Transfer ekranında tam iki tohum depo bekliyordu
// (touch-targets.spec.ts:2442, `toHaveCount(2)`). Kontrollü A/B ölçümü:
//   A) `npx playwright test e2e/touch-targets.spec.ts`      -> 47 yeşil
//   B) `rota-muaf-kapsami + touch-targets`                  -> 1 KIRMIZI,
//      "Expected: 2 / Received: 3" (touch-targets.spec.ts:2442)
//   C) B'nin aynısı, ÜÇÜNCÜ depo satırı olmadan             -> yeşil
// Yani PR #14'ün fikstürü paylaşılan tohum kiracısını KİRLETİYORDU. Düzeltme
// tohumu zayıflatmak DEĞİL, fikstürü KENDİ firmasına taşımaktır: `createCompany`
// + `provisionUser` (tenant-isolation.spec.ts'nin kanıtlı deseni) ile yeni bir
// firma açılır, üç kayıt oraya yazılır ve tarayıcı o firmanın kullanıcısıyla
// açılır. Böylece `toHaveCount(2)` AYNEN kalır ve tohum firma iki depoda
// kalır; izole firmada `ensure_company_default_warehouse` zaten bir depo
// açtığı için bu testin deposu orada İKİNCİ olur.

// Envanterdeki `isaret` alanlarıyla BİREBİR aynı literaller. Bunlar testin
// oluşturduğu kaydın ADIDIR (kenar çubuğu etiketi değil); ad değişirse envanter
// G13'ün bağıyla kırmızı düşer. `src/` ağacında bu metinler HİÇ geçmez (bkz.
// rota-render-kaniti.ts) — marker yalnız gövdede görünür.
const MARKER_SUPPLIER = 'ROTAKAPSAM Tedarikci';
const MARKER_PRODUCT = 'ROTAKAPSAM Urun';
const MARKER_WAREHOUSE = 'ROTAKAPSAM Depo';

// Deterministik koşu-içi kodlar: aynı firmada çakışmasın. Envanterdeki `isaret`
// yalnız ADI bağlar; kod/barkod serbestçe yapılır.
const RUN_TAG = 'RKM';

// İzole firma ve kullanıcısı: her koşuda TAZE bir SQLite üzerinde kurulur.
// Firma adı tarayıcı oturumunun aktif firma ölçümünde tam metin olarak aranır.
const ISOLATED_COMPANY_NAME = 'ROTAKAPSAM Firma';
const ISOLATED_USERNAME = 'rotakapsam_kullanici';

let admin: AdminApi;
let isolatedCompanyId: string;
let isolatedSession: {username: string; password: string};

test.beforeAll(async () => {
  admin = await adminApi();
  isolatedCompanyId = await createCompany(admin, ISOLATED_COMPANY_NAME);
  isolatedSession = await provisionUser(admin, {
    username: ISOLATED_USERNAME,
    role: 'yonetici',
    companyId: isolatedCompanyId,
    displayName: 'ROTAKAPSAM Kullanıcısı',
  });
});

test.afterAll(async () => {
  await admin.dispose();
});

/** Yalnız izole firmada oturum açar ve oturumun O firmaya bağlandığını ölçer.
 *
 *  Bu ölçüm olmadan "POST /api/warehouses id döndürdü" firmaya bağlanmak için
 *  yeterli DEĞİLDİR: API çağrısı `X-Company-ID` başlığıyla izole firmaya yazarken
 *  tarayıcı oturumu tohum firmada kalabilir ve detay GET'i kaydı bulamayabilirdi.
 *  Uçtan uca bağ şu iki ölçümle kurulur:
 *    1. kabuğun firma seçicisi İZOLE firmayı gösterir (bu fonksiyon),
 *    2. detay GET'i izole firmadaki kaydı bulup işareti çizer
 *       (testin kendisi, renderKanitiniDogrula).
 */
async function izoleOturumAc(page: Page): Promise<void> {
  await loginAs(page, isolatedSession.username, isolatedSession.password);
  await expect(
    page.getByText(ISOLATED_COMPANY_NAME, {exact: true}),
    'tarayıcı oturumu izole firmaya bağlanmalı (kabuğun aktif firma seçicisi)',
  ).toBeVisible({timeout: 15_000});
}

test('tedarikçi detayı: testin ürettiği tedarikçi adı gövdede görünür ve rota korunur', async ({page}, testInfo) => {
  const supplierId = await createSupplier(admin, MARKER_SUPPLIER, isolatedCompanyId);
  await izoleOturumAc(page);
  const rota = `/tedarikciler/${supplierId}`;
  await page.goto(rota);
  await page.waitForLoadState('networkidle');
  await renderKanitiniDogrula(page, rota, MARKER_SUPPLIER, testInfo);

  expect(
    new URL(page.url()).pathname,
    `${rota} başka bir rotaya yönlendirildi (izin duvarı?)`,
  ).toBe(rota);
});

test('ürün detayı: testin ürettiği ürün adı gövdede görünür ve rota korunur', async ({page}, testInfo) => {
  const productId = await createProduct(admin, {
    name: MARKER_PRODUCT,
    productCode: `${RUN_TAG}-URUN`,
    barcode: `8610000000001`,
    companyId: isolatedCompanyId,
  });
  await izoleOturumAc(page);
  const rota = `/urunler/${productId}`;
  await page.goto(rota);
  await page.waitForLoadState('networkidle');
  await renderKanitiniDogrula(page, rota, MARKER_PRODUCT, testInfo);

  expect(
    new URL(page.url()).pathname,
    `${rota} başka bir rotaya yönlendirildi (izin duvarı?)`,
  ).toBe(rota);
});

test('depo detayı: testin ürettiği depo adı gövdede görünür ve rota korunur', async ({page}, testInfo) => {
  const warehouseId = await createWarehouse(admin, MARKER_WAREHOUSE, `${RUN_TAG}-01`, isolatedCompanyId);
  await izoleOturumAc(page);
  const rota = `/depolar/${warehouseId}`;
  await page.goto(rota);
  await page.waitForLoadState('networkidle');
  await renderKanitiniDogrula(page, rota, MARKER_WAREHOUSE, testInfo);

  expect(
    new URL(page.url()).pathname,
    `${rota} başka bir rotaya yönlendirildi (izin duvarı?)`,
  ).toBe(rota);
});
