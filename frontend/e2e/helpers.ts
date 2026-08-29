import {test as base, expect, request as apiRequest, type APIRequestContext, type Page} from '@playwright/test';

import {ziyaretKaydiniKur} from './rota-ziyaret-kaydi';

// Tohum belleği ayrı bir modülde: gerekçe, ölçüm ve tek-üretici güvencesi
// için bkz. `tohum-bellegi.ts`. Buradan yeniden dışa aktarılıyor ki spec'lerin
// içe aktarma yüzeyi değişmesin.
export {BEKLEME_SINIRI_MS, TOHUM_DIZINI, tohumHatirla} from './tohum-bellegi';

// Bootstrap admin ilk girişte şifre değiştirmek zorunda; global-setup bunu API
// üzerinden yapar ve tüm spec'ler bu şifreyle giriş yapar.
export const E2E_USERNAME = 'admin';
export const E2E_PASSWORD = 'E2e!Sungur2026#kapi';

// Global-setup'ın tohumladığı deterministik veri (bkz. global-setup.ts).
export const SEED = {
  productName: 'Yağ Filtresi',
  productCode: 'YF-8',
  barcode: '8690000000086',
  unitPrice: '18.00',
  secondWarehouse: 'İkinci Şube Depo',
} as const;

/**
 * Konsol-temiz sözleşmesi: her testin sonunda tek bir console.error veya
 * yakalanmamış istisna bile kalmamalı. Bugünkü prod POS çökmesi
 * ("d.replace is not a function") tam olarak bu assert'in yakalayacağı
 * sınıftır — sayfa render olmaya devam etse bile test kırmızı yanar.
 */
export const test = base.extend({
  // ROTA ZİYARET KAYDI — `spec` tasnifinin çalışma zamanı kanıtı.
  //
  // BAĞLAM SEVİYESİNDE, SAYFA SEVİYESİNDE DEĞİL: bir spec ikinci bir sekme
  // açabilir (`/saha` ölçümü tam olarak bunu yapar) ve o sekmedeki gezinti de
  // gerçek bir ziyarettir. Bildirim ile başlangıç betiği bağlama kurulunca
  // bağlamda AÇILAN HER sayfa kayda girer; `page` fixture'ı zaten bu bağlamdan
  // türer, dolayısıyla kurulum sayfa yaratılmadan ÖNCE tamamlanır.
  //
  // Gerekçesi ve "olumsuz yönlendirme kanıt sayılmaz" kuralı için bkz.
  // `rota-ziyaret-kaydi.ts`.
  context: async ({context}, use, testInfo) => {
    const kaydiIlistir = await ziyaretKaydiniKur(context, testInfo);
    await use(context);
    await kaydiIlistir();
  },
  page: async ({page}, use) => {
    const errors: string[] = [];
    page.on('console', (message) => {
      if (message.type() !== 'error') return;
      const text = message.text();
      const url = message.location()?.url ?? '';
      // Chromium her 4xx ağ yanıtını da console.error olarak düşürür. Oturum
      // öncesindeki /api/auth/* 401/403'leri (me/refresh denemeleri) meşru
      // akıştır; YALNIZCA onları ele. Diğer her şey (React hataları, CSP
      // ihlalleri, 500'ler, auth dışı 4xx) kırmızıdır.
      if (/status of (401|403)/.test(text) && url.includes('/api/auth/')) return;
      // İş emri detayı, faturalandırmaya hazır olup olmadığını backend'e sorar;
      // "hazır değil" yanıtı 409'dur (billing_service.py:123) ve UI bunu
      // "Faturalandırmaya hazır değil" Alert'ine çevirir. Yukarıdaki auth
      // istisnasıyla aynı sınıf: meşru akışın taşıyıcısı olan 4xx.
      if (/status of 409/.test(text) && /\/api\/work-orders\/\d+\/invoice\b/.test(url)) return;
      errors.push(url ? `${text} [${url}]` : text);
    });
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
    await use(page);
    expect(errors, 'Konsol temiz olmalı (console.error / uncaught exception yok)').toEqual([]);
  },
});

export {expect};

export async function login(page: Page): Promise<void> {
  await page.goto('/giris');
  await page.getByLabel('Kullanıcı adı').fill(E2E_USERNAME);
  await page.getByLabel('Şifre').fill(E2E_PASSWORD);
  await page.getByRole('button', {name: 'Giriş Yap'}).click();
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});
}

// tr-TR para biçimi (frontend api.money ile birebir). Spec'ler beklenen tutarı
// UI'daki metinle karşılaştırırken kullanır.
export const money = (value: number | string): string =>
  new Intl.NumberFormat('tr-TR', {style: 'currency', currency: 'TRY'}).format(Number(value || 0));

// ---------------------------------------------------------------------------
// API tohumlama yardımcıları
//
// Bazı senaryolar (veresiye cari, RBAC rolü, ikinci firma) global-setup'ın
// sabit tohumunda olmayan önkoşul veri ister. Bunları gerçek API üzerinden,
// global-setup ile birebir aynı başlık kurgusuyla (Bearer + X-Company-ID +
// CSRF) deterministik oluştururuz — mock/kısayol yok, uçtan uca gerçek.
// ---------------------------------------------------------------------------
const E2E_BASE_URL = 'http://127.0.0.1:5599';
// Sağlanan (bootstrap olmayan) kullanıcıların zorunlu ilk-giriş rotasyonundan
// sonraki ortak güçlü şifresi.
export const PROVISIONED_PASSWORD = 'E2e!Rota2026#kapi';
const PROVISION_INITIAL_PASSWORD = 'Init!Sungur2026#kapi';

async function csrfHeader(api: APIRequestContext): Promise<Record<string, string>> {
  const state = await api.storageState();
  const token = state.cookies.find((cookie) => cookie.name === 'yhp_csrf_token')?.value;
  return token ? {'X-CSRF-Token': token} : {};
}

export interface AdminApi {
  api: APIRequestContext;
  token: string;
  companyId: string;
  // Verilen firma (varsayılan: admin'in ilk firması) için yazma başlıkları.
  headers(companyId?: string): Promise<Record<string, string>>;
  dispose(): Promise<void>;
}

// global-setup tarafından zaten E2E_PASSWORD'e rotate edilmiş bootstrap admin
// olarak kimlik doğrulanmış bir API bağlamı açar.
export async function adminApi(baseURL: string = E2E_BASE_URL): Promise<AdminApi> {
  const api = await apiRequest.newContext({baseURL});
  const res = await api.post('/api/auth/login', {
    data: {username: E2E_USERNAME, password: E2E_PASSWORD},
  });
  if (!res.ok()) {
    throw new Error(`adminApi girişi başarısız (${res.status()}): ${await res.text()}`);
  }
  const body = await res.json();
  const token = body.access_token as string;
  const defaultCompanyId = String(body.companies[0].id);
  const headers = async (companyId: string = defaultCompanyId) => ({
    Authorization: `Bearer ${token}`,
    'X-Company-ID': companyId,
    ...(await csrfHeader(api)),
  });
  return {api, token, companyId: defaultCompanyId, headers, dispose: () => api.dispose()};
}

// Verilen firmada bir müşteri oluşturur ve id'sini döndürür.
export async function createCustomer(
  admin: AdminApi,
  name: string,
  companyId?: string,
): Promise<number> {
  const res = await admin.api.post('/api/customers', {
    headers: await admin.headers(companyId),
    data: {name},
  });
  if (!res.ok()) {
    throw new Error(`createCustomer başarısız (${res.status()}): ${await res.text()}`);
  }
  return (await res.json()).id as number;
}

// Yeni bir firma oluşturur (yalnız admin) ve id'sini string olarak döndürür.
// Oluşturan admin firmaya (varsayılan olmayan) üye yapılır; böylece
// X-Company-ID'yi bu firmaya ayarlayıp içine kullanıcı tohumlayabilir.
export async function createCompany(admin: AdminApi, name: string): Promise<string> {
  const res = await admin.api.post('/api/companies', {
    headers: await admin.headers(),
    data: {name},
  });
  if (!res.ok()) {
    throw new Error(`createCompany başarısız (${res.status()}): ${await res.text()}`);
  }
  return String((await res.json()).id);
}

// Verilen firmada bir ürün oluşturur ve id'sini döndürür. Kod/barkod çağıran
// tarafça benzersiz verilmeli (aynı firmada çakışmasın). global-setup'ın ürün
// gövdesiyle aynı şekli kullanır.
export async function createProduct(
  admin: AdminApi,
  opts: {name: string; productCode: string; barcode: string; companyId?: string},
): Promise<number> {
  const res = await admin.api.post('/api/products', {
    headers: await admin.headers(opts.companyId),
    data: {
      name: opts.name,
      product_code: opts.productCode,
      barcode: opts.barcode,
      purchase_price: '5.00',
      sale_price: '10.00',
      vat_rate: 20,
      stock: '5',
      unit: 'Adet',
    },
  });
  if (!res.ok()) {
    throw new Error(`createProduct başarısız (${res.status()}): ${await res.text()}`);
  }
  return (await res.json()).id as number;
}

// Kullanılabilir bir giriş sağlar: kullanıcıyı `companyId` içinde oluşturur,
// sonra zorunlu ilk-giriş şifresini API üzerinden rotate eder (global-setup'ın
// admin için yaptığı gibi) — böylece hesap UI'dan /sifre-degistir'e düşmeden
// giriş yapabilir.
export async function provisionUser(
  admin: AdminApi,
  opts: {username: string; role: string; companyId?: string; displayName?: string},
): Promise<{username: string; password: string; userId: number}> {
  const created = await admin.api.post('/api/users', {
    headers: await admin.headers(opts.companyId),
    data: {
      username: opts.username,
      display_name: opts.displayName ?? opts.username,
      password: PROVISION_INITIAL_PASSWORD,
      role: opts.role,
    },
  });
  if (!created.ok()) {
    throw new Error(`provisionUser oluşturma başarısız (${created.status()}): ${await created.text()}`);
  }
  const userId = (await created.json()).id as number;

  const userApi = await apiRequest.newContext({baseURL: E2E_BASE_URL});
  try {
    const loginRes = await userApi.post('/api/auth/login', {
      data: {username: opts.username, password: PROVISION_INITIAL_PASSWORD},
    });
    if (!loginRes.ok()) {
      throw new Error(`provisionUser girişi başarısız (${loginRes.status()}): ${await loginRes.text()}`);
    }
    const body = await loginRes.json();
    const change = await userApi.post('/api/auth/change-password', {
      headers: {
        Authorization: `Bearer ${body.access_token}`,
        'X-Company-ID': String(body.companies[0].id),
        ...(await csrfHeader(userApi)),
      },
      data: {current_password: PROVISION_INITIAL_PASSWORD, new_password: PROVISIONED_PASSWORD},
    });
    if (!change.ok()) {
      throw new Error(`provisionUser şifre rotasyonu başarısız (${change.status()}): ${await change.text()}`);
    }
  } finally {
    await userApi.dispose();
  }
  return {username: opts.username, password: PROVISIONED_PASSWORD, userId};
}

// Belirtilen kullanıcıyla UI'dan giriş yapar. Panele varışı doğrulamaz (rol
// bazlı ilk ekran değişebilir); onu çağıran spec kendi işaretiyle bekler.
export async function loginAs(page: Page, username: string, password: string): Promise<void> {
  await page.goto('/giris');
  await page.getByLabel('Kullanıcı adı').fill(username);
  await page.getByLabel('Şifre').fill(password);
  await page.getByRole('button', {name: 'Giriş Yap'}).click();
}
