import {test, expect, login, money, adminApi, createCustomer, SEED, type AdminApi} from './helpers';

// POS veresiye satış → cari borç artışı — uçtan uca.
//
// Seçili bir müşteriye "Veresiye" ödeme tipiyle satış yapıldığında sipariş
// tahsil edilmeden kapanır (paid_amount=0) ve müşterinin AÇIK BAKİYESİ satışın
// final_total'ı kadar artar. Müşteri kartındaki "Açık Bakiye" KPI'ı bu tutarı
// birebir göstermelidir — cari borç muhasebesinin bekçisi.
let admin: AdminApi;
test.beforeAll(async () => {
  admin = await adminApi();
});
test.afterAll(async () => {
  await admin.dispose();
});

test('veresiye satış müşterinin açık bakiyesini satış tutarı kadar artırır', async ({page}, testInfo) => {
  // Müşteri adı HER DENEMEDE benzersizdir. Sabit ad kullanıldığında test bir
  // kez düşünce geride bir müşteri bırakıyor, Playwright'ın tekrar denemesi
  // ikincisini yaratıyor ve aşağıdaki seçenek tıklaması "strict mode violation:
  // resolved to N elements" ile patlıyordu. Bu, ASIL hatayı maskeliyordu:
  // raporda görünen tek hata çakışma oluyor, ilk denemedeki gerçek sebep
  // (zaman aşımı) gözden kaçıyordu. Çakışma sınıfı böylece tamamen kalkar.
  const musteriAdi = `Veresiye E2E ${testInfo.workerIndex}-${testInfo.retry}-${Date.now()}`;
  const customerId = await createCustomer(admin, musteriAdi);

  await login(page);
  await page.goto('/hizli-satis');
  await expect(page.getByRole('heading', {name: 'Hızlı Satış'})).toBeVisible();

  // Ürünü sepete ekle.
  const barcode = page.getByLabel('Barkod');
  await barcode.fill(SEED.barcode);
  await barcode.press('Enter');
  await expect(page.getByLabel(`${SEED.productName} miktar`)).toBeVisible();

  // Müşteriyi seç (boş bırakılırsa perakende olurdu; veresiye müşteri ister).
  const customerBox = page.getByRole('combobox', {name: 'Müşteri'});
  await customerBox.click();
  await customerBox.fill(musteriAdi);
  // Seçeneğin GÖRÜNDÜĞÜNÜ ayrıca doğrula. Doğrudan tıklamak, arama sonucu hiç
  // gelmediğinde 60 sn'lik test zaman aşımıyla düşüyor ve rapor yalnızca
  // "locator.click: Test timeout" diyor — listenin boş mu geldiği, yoksa
  // tıklamanın mı takıldığı anlaşılmıyordu. Bu bekleme, ilk denemede yaşanan
  // arızanın hangisi olduğunu raporda ayırt edilebilir kılar.
  // exact:true ŞART. Varsayılan eşleşme alt-dizedir ve açılır listede yazdığın
  // metni İÇEREN başka bir seçenek (MUI'nin "yeni ekle" satırı) de bulunur;
  // alt-dize eşleşmesi ikisini birden yakalayıp yine strict mode ihlali verir.
  const musteriSecenegi = page.getByRole('option', {name: musteriAdi, exact: true});
  await expect(musteriSecenegi).toBeVisible({timeout: 20_000});
  await musteriSecenegi.click();

  // Ödeme tipini "Veresiye" yap.
  await page.getByRole('combobox', {name: 'Ödeme'}).click();
  await page.getByRole('option', {name: 'Veresiye'}).click();

  // Satışın final_total'ını yakala (beklenen açık bakiye tam bu tutardır).
  let finalTotal: string | undefined;
  page.on('response', async (response) => {
    if (new URL(response.url()).pathname !== '/api/pos/sale') return;
    if (response.request().method() !== 'POST' || response.status() !== 201) return;
    finalTotal = String((await response.json()).final_total);
  });

  await page.getByRole('button', {name: 'Satışı Tamamla'}).click();
  await expect(page.getByText(/Satış #\d+ tamamlandı/)).toBeVisible({timeout: 15_000});
  await expect.poll(() => finalTotal, {timeout: 10_000}).not.toBeUndefined();

  const expectedBalance = money(finalTotal as string);
  expect(expectedBalance).not.toBe(money(0)); // veresiye satış borç yaratmalı

  // Müşteri kartında Açık Bakiye = satışın final_total'ı.
  await page.goto(`/musteriler/${customerId}`);
  const balanceCard = page.getByRole('button', {name: /^Açık Bakiye:/});
  await expect(balanceCard).toBeVisible({timeout: 15_000});
  await expect(balanceCard).toContainText(expectedBalance);
});
