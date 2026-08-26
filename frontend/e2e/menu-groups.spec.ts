import {test, expect, loginAs, login, adminApi, provisionUser, type AdminApi} from './helpers';

// U1 gruplu menü duman testi: gerçek tarayıcıda 9 üst düzey madde, grupların
// açılır-kapanır davranışı, aktif grup sözleşmesi ve üst çubuktaki POS düğmesi.
//
// DİSİPLİN: giriş dışında `page.goto()` YOKTUR. Tüm gezinme SPA içi bağlantı
// tıklamalarıyla yapılır — çünkü kanıtlanmak istenen şey tam olarak menü
// durumunun AYNI oturum boyunca nasıl taşındığı. `goto` tam yeniden yükleme
// yapıp durumu sıfırlayacağı için sözleşmeyi gizlerdi.
//
// Not: Playwright'ta `getByRole(..., {name})` varsayılan olarak ALT DİZE eşler.
// Menüde "Satış" grubu, "Satışlar" maddesi ve "Hızlı Satış" düğmesi bir arada
// olduğu için tüm ad sorguları `exact: true` ile yapılır.

// BİLİNÇLİ LİTERAL — ürün sözleşmesi. Bu dosya kasten `NAV_LABELS`'tan import
// ETMEZ: e2e, kullanıcının ekranda gerçekten gördüğü metni doğrulayan dış
// gözlemcidir. Konfigürasyondan türetilseydi bir etiket yanlışlıkla değişse
// bile test yeşil kalırdı. NAV_LABELS değişirse BURAYI DA ELLE GÜNCELLE.
const L = {
  posButton: 'Hızlı Satış', // üst çubuktaki POS düğmesinin erişilebilir adı
  inventoryGroup: 'Stok & Ürünler',
  financeGroup: 'Finans',
  products: 'Ürünler',
  warehouses: 'Depolar',
  allocations: 'Tahsis Defteri',
} as const;

type Page = import('@playwright/test').Page;

// Grup başlıkları `data-nav-group` ile kesin seçilir: ileride kenar çubuğuna
// başka bir accordion ya da profil menüsü eklenirse bu sayım bozulmasın.
// Başlık GEZİNİR (grubun ana sayfası); açma/kapama yanındaki ok düğmesindedir.
const groupHeaders = (page: Page) => page.locator('[data-nav-group]');
const group = (page: Page, id: string) => page.locator(`[data-nav-group="${id}"]`);
const groupToggle = (page: Page, id: string) => page.locator(`[data-nav-toggle="${id}"]`);
const navLink = (page: Page, label: string) => page.getByRole('link', {name: label, exact: true});

test('admin 2 sabit madde + 9 grup görür, gruplar kapalı başlar', async ({page}) => {
  await login(page);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});

  // 9 grup başlığı; etiketleri unit testte doğrulanıyor, burada sayı yeter.
  // 7 → 8: Tarla grubu eklendi (mobil-erp#2).
  // 8 → 9: Hayvancılık grubu eklendi (mobil-erp#17).
  await expect(groupHeaders(page)).toHaveCount(9);
  // Ana Sayfa aktif olduğu için hiçbir grup aktif değil → hepsi kapalı.
  await expect(page.locator('[data-nav-toggle][aria-expanded="true"]')).toHaveCount(0);

  // Sabit maddeler doğrudan bağlantıdır (grup değil).
  await expect(navLink(page, L.posButton)).toBeVisible();
});

test('ok düğmesi grubu açar ve tekrar tıklayınca kapatır', async ({page}) => {
  await login(page);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});

  const toggle = groupToggle(page, 'inventory');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(navLink(page, L.products)).toBeVisible();
  // Ok düğmesi yalnız daraltır: sayfa değişmemeli.
  await expect(page).not.toHaveURL(/urunler/);

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(navLink(page, L.products)).toHaveCount(0);
});

// TEK TIK GEZİNME: grup başlığı hem grubun ana sayfasını açar hem grubu açar.
test('grup başlığına tek tık grubun ana sayfasına gider ve grubu açar', async ({page}) => {
  await login(page);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});

  await group(page, 'inventory').click();
  await expect(page).toHaveURL(/urunler/);
  await expect(groupToggle(page, 'inventory')).toHaveAttribute('aria-expanded', 'true');
  // Alt maddeler aynen yerinde: gezinme akordiyonu bozmaz.
  await expect(navLink(page, L.warehouses)).toBeVisible();

  // Başka bir grubun başlığı da tek tıkta kendi ana sayfasına götürür ve
  // önceki grup aktifliğini yitirdiği için kapanır.
  await group(page, 'finance').click();
  await expect(page).toHaveURL(/odemeler/);
  await expect(groupToggle(page, 'finance')).toHaveAttribute('aria-expanded', 'true');
  await expect(groupToggle(page, 'inventory')).toHaveAttribute('aria-expanded', 'false');
});

// Aktif grup sözleşmesi, uçtan uca: görünürlük `elle açık || aktif` ile
// türetilir ve aktif gruba tıklamak onu "elle açık" kümesine EKLEMEZ.
test('aktif grup elle kapatılınca görünür kalır, aktiflik bitince kapanır', async ({page}) => {
  await login(page);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});

  const inventory = groupToggle(page, 'inventory');
  const finance = groupToggle(page, 'finance');

  // Stok grubunu aç ve içinden /depolar'a SPA geçişi yap → grup aktif olur.
  await inventory.click();
  await navLink(page, L.warehouses).click();
  await expect(page).toHaveURL(/depolar/);
  await expect(inventory).toHaveAttribute('aria-expanded', 'true');

  // Aktif grubun okuna bas: kümeden düşer ama aktiflik görünürlüğü sürdürür.
  await inventory.click();
  await expect(inventory).toHaveAttribute('aria-expanded', 'true');
  await expect(navLink(page, L.products)).toBeVisible();

  // Aynı grup içinde SPA geçişi (/urunler): hâlâ aktif → hâlâ açık.
  await navLink(page, L.products).click();
  await expect(page).toHaveURL(/urunler/);
  await expect(inventory).toHaveAttribute('aria-expanded', 'true');

  // Başka gruba SPA geçişi: Finans'ı aç, içindeki bağlantıya git.
  await finance.click();
  await navLink(page, L.allocations).click();
  await expect(page).toHaveURL(/tahsis-defteri/);

  // Stok grubu artık ne aktif ne de "elle açık" → KAPANDI.
  await expect(inventory).toHaveAttribute('aria-expanded', 'false');
  await expect(navLink(page, L.products)).toHaveCount(0);
});

test('elle açılan ikinci grup, sayfa geçişinde açık kalır', async ({page}) => {
  await login(page);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});

  const inventory = groupToggle(page, 'inventory');
  const finance = groupToggle(page, 'finance');

  // Aktif olmayan iki grubu elle aç.
  await finance.click();
  await inventory.click();
  await expect(finance).toHaveAttribute('aria-expanded', 'true');

  // SPA geçişi: elle açılan Finans grubu açık kalmalı.
  await navLink(page, L.products).click();
  await expect(page).toHaveURL(/urunler/);
  await expect(finance).toHaveAttribute('aria-expanded', 'true');
  await expect(navLink(page, L.allocations)).toBeVisible();
});

test('üst çubuktaki Satış düğmesi her sayfadan POS ekranını açar', async ({page}) => {
  await login(page);
  await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});

  // Ana Sayfa dışında bir sayfaya SPA ile geç.
  await group(page, 'inventory').click();
  await navLink(page, L.products).click();
  await expect(page).toHaveURL(/urunler/);

  // Düğmenin erişilebilir adı "Hızlı Satış"; görünür metni ("Satış") adın
  // içinde geçer ve kenar çubuğundaki "Satış" grubuyla karışmaz.
  const posButton = page.getByRole('button', {name: L.posButton, exact: true});
  await expect(posButton).toBeVisible();
  await posButton.click();
  await expect(page).toHaveURL(/hizli-satis/);
  await expect(page.getByLabel('Barkod')).toBeVisible({timeout: 15_000});
});

test('depo rolünde üst çubuktaki POS düğmesi hiç çizilmez', async ({page}) => {
  let admin: AdminApi | undefined;
  try {
    admin = await adminApi();
    const {username, password} = await provisionUser(admin, {
      username: `depo_pos_e2e_${Date.now()}`,
      role: 'depo',
      displayName: 'Depo POS E2E',
    });

    await loginAs(page, username, password);
    await expect(page.getByText('Son Satışlar')).toBeVisible({timeout: 15_000});

    // `sales` izni yok: ne üst çubuk düğmesi ne de sabit menü bağlantısı.
    await expect(page.getByRole('button', {name: L.posButton, exact: true})).toHaveCount(0);
    await expect(navLink(page, L.posButton)).toHaveCount(0);
  } finally {
    await admin?.dispose();
  }
});
