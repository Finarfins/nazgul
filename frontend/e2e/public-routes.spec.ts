import {test, expect, adminApi, loginAs} from './helpers';

// Oturum gerektirmeyen sayfalar vs. gerektirenler — yönlendirme sözleşmesi.
//
// Regresyon: `api.ts` içindeki `expireBrowserSession`, refresh başarısız olunca
// koşulsuz `/giris`'e yönlendiriyordu. Anonim ziyaretçide ilk `/auth/me` çağrısı
// zaten 401 döndüğü için bu yol her genel sayfada tetikleniyordu; sonuç olarak
// tanıtım sayfası açılmıyor, self-servis kayıt ve e-postadaki doğrulama
// bağlantısı çalışmıyordu.
//
// Düzeltmenin İKİ yönü de burada kilitlenir:
//   (a) oturumsuz sayfalarda yönlendirme YOK
//   (b) oturumlu sayfalarda yönlendirme HÂLÂ VAR  <- bu PR'ın kırabileceği asıl
//       davranış; `/sifre-degistir` özellikle sınav konusu, çünkü kabuk dışıdır
//       ama oturum ister.

const ANONYMOUS_ROUTES = [
  {path: '/tanitim', label: 'tanıtım sayfası'},
  {path: '/kayit', label: 'self-servis kayıt'},
  // Token'sız gidilir: uydurma bir token gerçek bir doğrulama çağrısı tetikler
  // ve 400 döner (beklenen), bu da konsol-temiz sözleşmesini gereksiz yere
  // meşgul ederdi. Kanıtlanmak istenen şey token geçerliliği değil, sayfanın
  // anonim ziyaretçiyi girişe fırlatmaması.
  {path: '/eposta-dogrula', label: 'e-posta doğrulama bağlantısı'},
] as const;

// --- (a) Oturumsuz sayfalar: yönlendirme YOK --------------------------------

for (const route of ANONYMOUS_ROUTES) {
  test(`anonim ziyaretçi ${route.label} sayfasında kalır, girişe fırlatılmaz`, async ({page}) => {
    await page.goto(route.path);

    // Yönlendirme asenkron (401 -> refresh -> expireBrowserSession) olduğu için
    // sadece anlık URL'e bakmak yetmez; bir süre boyunca /giris'e düşmediğini
    // doğrula.
    await page.waitForTimeout(2_000);

    expect(page.url(), `${route.label} girişe yönlendirdi`).not.toMatch(/\/giris/);
    await expect(
      page.getByRole('button', {name: 'Giriş Yap'}),
      'oturumsuz sayfada giriş formu çizilmemeli',
    ).toHaveCount(0);
  });
}

test('giriş sayfası anonim erişimde beklendiği gibi açılır', async ({page}) => {
  // Karşıt kontrol: /giris'in kendisi elbette giriş formunu gösterir.
  await page.goto('/giris');
  await expect(page.getByRole('button', {name: 'Giriş Yap'})).toBeVisible();
});

// --- (d) Sonsuz döngü / sessiz yutma yok ------------------------------------

test('oturumsuz sayfa 401 aldığında yeniden deneme döngüsüne girmez', async ({page}) => {
  // `expireBrowserSession` artık yönlendirmediğine göre, yerine sessiz bir
  // 401 -> refresh -> 401 döngüsü GEÇMEMELİ. Auth çağrılarını sayarak ölç.
  const authCalls: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (url.includes('/api/auth/')) authCalls.push(url.replace(/^https?:\/\/[^/]+/, ''));
  });

  await page.goto('/tanitim');
  await page.waitForTimeout(5_000);

  // Açılışta bir /auth/me ve en fazla bir /auth/refresh beklenir. Döngü olsaydı
  // bu sayı saniyeler içinde onlarca olurdu.
  expect(
    authCalls.length,
    `auth çağrıları döngüye girdi: ${JSON.stringify(authCalls)}`,
  ).toBeLessThanOrEqual(6);

  // Sessiz yutma da olmamalı: sayfa hâlâ ayakta ve tanıtım rotasında.
  expect(page.url()).not.toMatch(/\/giris/);
});

// --- (b) REGRESYON: oturumlu sayfada yönlendirme HÂLÂ çalışıyor -------------

test('REGRESYON: oturumu biten kullanıcı korumalı sayfadan girişe atılır', async ({page, context}) => {
  const admin = await adminApi();
  const stamp = Date.now();
  const username = `oturum_biten_${stamp}`;
  const initial = 'Init!Sungur2026#kapi';
  const rotated = 'E2e!Biten2026#kapi';

  try {
    const created = await admin.api.post('/api/users', {
      headers: await admin.headers(),
      data: {username, display_name: 'Oturum Biten', password: initial, role: 'rapor'},
    });
    expect(created.ok(), await created.text()).toBeTruthy();
  } finally {
    await admin.dispose();
  }

  // Zorunlu ilk rotasyonu UI'dan geç, sonra korumalı bir sayfaya yerleş.
  await loginAs(page, username, initial);
  await expect(page).toHaveURL(/sifre-degistir/, {timeout: 15_000});
  await page.getByLabel('Mevcut şifre').fill(initial);
  await page.getByLabel('Yeni şifre', {exact: true}).fill(rotated);
  await page.getByLabel('Yeni şifre tekrar').fill(rotated);
  await page.getByRole('button', {name: 'Şifreyi Değiştir'}).click();
  await expect(page).not.toHaveURL(/sifre-degistir/, {timeout: 15_000});

  await page.goto('/urunler');
  await expect(page).not.toHaveURL(/giris/);

  // Oturumu düşür ve korumalı sayfayı yeniden yükle: kullanıcı girişe
  // atılmalı. Bu, düzeltmenin KIRMAMASI gereken davranış.
  await context.clearCookies();
  await page.goto('/urunler');
  await expect(page, 'korumalı sayfa oturumsuz açılmamalı').toHaveURL(/giris/, {timeout: 15_000});
});

// --- (c) /sifre-degistir oturumludur: yönlendirme çalışmalı -----------------

test('REGRESYON: oturumu biten kullanıcı /sifre-degistir ekranında takılı kalmaz', async ({page, context}) => {
  const admin = await adminApi();
  const stamp = Date.now();
  const username = `sifre_ekrani_${stamp}`;
  const initial = 'Init!Sungur2026#kapi';

  try {
    const created = await admin.api.post('/api/users', {
      headers: await admin.headers(),
      data: {username, display_name: 'Şifre Ekranı', password: initial, role: 'rapor'},
    });
    expect(created.ok(), await created.text()).toBeTruthy();
  } finally {
    await admin.dispose();
  }

  // Rotasyonu YAPMADAN giriş: kullanıcı /sifre-degistir ekranına düşer.
  await loginAs(page, username, initial);
  await expect(page).toHaveURL(/sifre-degistir/, {timeout: 15_000});

  // Oturumu düşür ve parolayı değiştirmeyi dene. İstek 401 döner; kullanıcı
  // ekranda TAKILI KALMAMALI, giriş ekranına gitmeli.
  //
  // `/sifre-degistir` ANONYMOUS_PATHS'e konsaydı bu test kırılırdı: yönlendirme
  // tetiklenmez, kullanıcı sonsuza dek parola değiştiremediği bir ekranda
  // kalırdı (ölü ekran).
  await context.clearCookies();
  await page.getByLabel('Mevcut şifre').fill(initial);
  await page.getByLabel('Yeni şifre', {exact: true}).fill('E2e!Olu2026#kapi');
  await page.getByLabel('Yeni şifre tekrar').fill('E2e!Olu2026#kapi');
  await page.getByRole('button', {name: 'Şifreyi Değiştir'}).click();

  await expect(page, 'oturum bitince şifre ekranında takılı kalınmamalı').toHaveURL(
    /giris/,
    {timeout: 15_000},
  );
});
