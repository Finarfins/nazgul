import {test, expect, login, adminApi, loginAs, E2E_USERNAME, E2E_PASSWORD} from './helpers';

// Oturum yaşam döngüsü — RC_FRONTEND_DEPLOYMENT_CHECKLIST'in
// "Login, refresh rotation, logout, expiry, password-change-required, and
// multi-tab behavior pass smoke tests" maddesi.
//
// login.spec.ts yalnız girişi ve hatalı şifreyi kapsıyordu; kalan akışlar
// burada, gerçek cookie/CSRF zinciriyle ve gerçek Chromium'da doğrulanır.
// Mock yok: her senaryo backend'in bastığı gerçek çerezler üzerinden ilerler.

const ACCESS_COOKIE = 'yhp_access_token';
const REFRESH_COOKIE = 'yhp_refresh_token';
const INITIAL_PASSWORD = 'Init!Sungur2026#kapi';

test('zorunlu ilk şifre değişimi: yeni hesap panele değil /sifre-degistir ekranına düşer', async ({page}) => {
  // provisionUser şifreyi API'den rotate ettiği için burada KULLANILMAZ:
  // amaç tam da rotate edilmemiş hesabın UI davranışını görmek.
  const admin = await adminApi();
  const username = `oturum_ilk_giris_${Date.now()}`;
  try {
    const created = await admin.api.post('/api/users', {
      headers: await admin.headers(),
      data: {
        username,
        display_name: 'Oturum İlk Giriş',
        password: INITIAL_PASSWORD,
        role: 'rapor',
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
  } finally {
    await admin.dispose();
  }

  await loginAs(page, username, INITIAL_PASSWORD);

  // Zorunlu rotasyon: panele geçemez, şifre ekranına yönlenir.
  await expect(page).toHaveURL(/sifre-degistir/, {timeout: 15_000});
  await expect(page.getByText('Şifrenizi Değiştirin')).toBeVisible();

  const rotated = 'E2e!Oturum2026#kapi';
  await page.getByLabel('Mevcut şifre').fill(INITIAL_PASSWORD);
  await page.getByLabel('Yeni şifre', {exact: true}).fill(rotated);
  await page.getByLabel('Yeni şifre tekrar').fill(rotated);
  await page.getByRole('button', {name: 'Şifreyi Değiştir'}).click();

  // Rotasyon sonrası uygulamaya girer ve bir daha şifre ekranına düşmez.
  await expect(page).not.toHaveURL(/sifre-degistir/, {timeout: 15_000});
});

test('çıkış: oturum çerezleri düşer ve korumalı sayfa girişe yönlendirir', async ({page}) => {
  await login(page);

  await page.getByRole('button', {name: 'Profil menüsü'}).click();
  await page.getByRole('menuitem', {name: 'Çıkış Yap'}).click();

  await expect(page).toHaveURL(/giris/, {timeout: 15_000});

  const cookies = await page.context().cookies();
  const names = cookies.map((cookie) => cookie.name);
  expect(names, 'çıkıştan sonra access çerezi kalmamalı').not.toContain(ACCESS_COOKIE);

  // Korumalı sayfaya doğrudan gitmek de girişe düşmeli.
  await page.goto('/urunler');
  await expect(page).toHaveURL(/giris/, {timeout: 15_000});
});

test('refresh rotasyonu: access çerezi düşse de oturum refresh ile ayakta kalır', async ({page, context}) => {
  await login(page);

  const before = await context.cookies();
  expect(before.map((c) => c.name)).toContain(REFRESH_COOKIE);

  // Access token'ın süresinin dolmasını taklit et: YALNIZ access çerezini
  // düşür, refresh + csrf yerinde kalsın. Uygulama sessizce yenilemeli.
  await context.clearCookies({name: ACCESS_COOKIE});
  expect((await context.cookies()).map((c) => c.name)).not.toContain(ACCESS_COOKIE);

  // 401 -> /auth/refresh zincirini (api.ts interceptor) bekleyerek doğrula;
  // yalnız URL'e bakmak yarış yaratır (yönlendirme henüz olmamış olabilir).
  const refreshed = page.waitForResponse(
    (response) =>
      response.url().includes('/api/auth/refresh') && response.request().method() === 'POST',
    {timeout: 15_000},
  );
  await page.goto('/urunler');
  const refreshResponse = await refreshed;
  expect(refreshResponse.status(), 'refresh 200 dönmeli').toBe(200);

  // Girişe düşmemeli ve yeni access çerezi basılmış olmalı.
  await expect(page).not.toHaveURL(/giris/, {timeout: 15_000});
  await expect
    .poll(async () => (await context.cookies()).map((cookie) => cookie.name), {timeout: 10_000})
    .toContain(ACCESS_COOKIE);
});

test('oturum bitişi: tüm çerezler gidince korumalı sayfa girişe düşer', async ({page, context}) => {
  await login(page);

  await context.clearCookies();
  await page.goto('/urunler');

  await expect(page).toHaveURL(/giris/, {timeout: 15_000});
});

test('çoklu sekme: bir sekmede çıkış yapılınca diğer sekme de korumalı sayfada kalamaz', async ({page, context}) => {
  await login(page);

  const second = await context.newPage();
  try {
    await second.goto('/urunler');
    await expect(second).not.toHaveURL(/giris/, {timeout: 15_000});

    // Birinci sekmede çıkış: çerezler bağlam genelinde paylaşıldığı için
    // ikinci sekme de yetkisiz kalmalı.
    await page.getByRole('button', {name: 'Profil menüsü'}).click();
    await page.getByRole('menuitem', {name: 'Çıkış Yap'}).click();
    await expect(page).toHaveURL(/giris/, {timeout: 15_000});

    // Çıkışın tetiklediği yönlendirme bu gezinmeyi iptal edebilir
    // (net::ERR_ABORTED). Önemli olan gezinmenin kendisi değil, VARILAN yer:
    // iptali yut, sonucu doğrula.
    await second.goto('/urunler', {waitUntil: 'domcontentloaded'}).catch(() => undefined);
    await expect(second).toHaveURL(/giris/, {timeout: 15_000});
  } finally {
    await second.close();
  }
});

// Not: "Authenticated dashboard smoke" maddesi ayrıca yazılmadı — screens.spec.ts
// zaten /, /urunler, /musteriler, /satislar, /tahsis-defteri ve /raporlar
// ekranlarını giriş yapmış oturumda konsol-temiz sözleşmesiyle açıyor.
