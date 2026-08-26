import {test, expect, loginAs, adminApi, createCompany, createProduct, provisionUser, SEED, type AdminApi} from './helpers';

// Çok-kiracılık izolasyonu — uçtan uca.
//
// A firmasının tohumladığı ürün (SEED.productName) yalnızca A'ya aittir. Ayrı
// bir B firması açıp orada bir kullanıcı tohumlar, B kullanıcısıyla UI'dan giriş
// yapar ve AYNI ürün rotasında (/urunler) A'nın kaydının GÖRÜNMEDİĞİNİ
// doğrularız. Kiracı kapsamı (company_id) her sorguda uygulanır; bu, sızıntının
// kalıcı bekçisidir.
let admin: AdminApi;
test.beforeAll(async () => {
  admin = await adminApi();
});
test.afterAll(async () => {
  await admin.dispose();
});

test('B firması kullanıcısı A firmasının ürününü göremez', async ({page}) => {
  const stamp = Date.now();
  const companyBId = await createCompany(admin, `B Firma E2E ${stamp}`);
  // B'nin KENDİ ürünü: pozitif kontrol. Listede görünmesi, ekranın gerçekten
  // veri çizdiğini kanıtlar — yani "A'nın ürünü yok" iddiası "liste boş"a değil,
  // gerçek izolasyona dayanır.
  const bProductName = `B Ürünü E2E ${stamp}`;
  await createProduct(admin, {
    name: bProductName,
    productCode: `BURN-${stamp}`,
    barcode: `900${stamp}`,
    companyId: companyBId,
  });
  const {username, password} = await provisionUser(admin, {
    username: `b_user_e2e_${stamp}`,
    role: 'yonetici',
    companyId: companyBId,
    displayName: 'B Firma Kullanıcısı',
  });

  await loginAs(page, username, password);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});

  await page.goto('/urunler');
  // Pozitif kontrol: B'nin kendi ürünü listede görünüyor (aynı zamanda doğru
  // bekleme noktası — networkidle'a gerek yok).
  await expect(page.getByText(bProductName)).toBeVisible({timeout: 15_000});
  // Asıl iddia: A firmasının tohum ürünü B'nin listesinde yok.
  await expect(page.getByText(SEED.productName)).toHaveCount(0);
});
