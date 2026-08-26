import {test, expect, loginAs, adminApi, provisionUser, type AdminApi} from './helpers';

// Satış rolü tahsilat yapabilir; kasa/banka, virman ve diğer hazine
// operasyonlarını ne menüde görebilir ne de doğrudan URL ile açabilir.
let admin: AdminApi;
test.beforeAll(async () => {
  admin = await adminApi();
});
test.afterAll(async () => {
  await admin.dispose();
});

test('satış rolü tahsilata erişir, finans ekranına erişemez', async ({page}) => {
  const {username, password} = await provisionUser(admin, {
    username: `satis_finance_e2e_${Date.now()}`,
    role: 'satis',
    displayName: 'Satış Finans E2E',
  });

  await loginAs(page, username, password);
  // Satış rolü panoyu ve kendisine verilen tahsilat alanını görür.
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});
  await expect(page.getByRole('link', {name: 'Hızlı Satış'})).toBeVisible();

  // Menü gruplu: Finans grubu varsayılan olarak kapalı, başlığa tıklayınca açılır.
  // `exact: true` şart: Playwright ad sorgusu varsayılan olarak alt dize eşler.
  await page.getByRole('button', {name: 'Finans', exact: true}).click();
  await expect(page.getByRole('link', {name: 'Tahsilat / Ödeme'})).toBeVisible();
  // Grup açıkken bile hazine maddesi bu rolde hiç çizilmez.
  await expect(page.getByRole('link', {name: 'Nakit Yönetimi'})).toHaveCount(0);

  // Tahsilat ekranındaki liste, özet ve dar hesap seçici gerçek API'den yüklenir.
  await page.getByRole('link', {name: 'Tahsilat / Ödeme'}).click();
  await expect(page).toHaveURL(/odemeler/);
  await expect(page.getByRole('heading', {name: 'Tahsilat / Ödeme'})).toBeVisible();

  // Doğrudan URL denemesi: finance olmadan panoya yönlendirilir.
  await page.goto('/nakit-yonetimi');
  await expect(page).not.toHaveURL(/nakit-yonetimi/);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});
});
