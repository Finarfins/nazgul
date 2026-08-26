import {test, expect, login, SEED} from './helpers';

// Bugünkü prod çökmesinin birebir E2E reprodüksiyonu: quick-pick çipine
// dokunmak. Hotfix öncesi frontend'de bu tıklama "d.replace is not a function"
// ile POS'u çökertiyordu; konsol-temiz sözleşmesi (helpers.ts) sayesinde bu
// test o durumda kırmızı yanar.
test.beforeEach(async ({page}) => {
  await login(page);
  await page.goto('/hizli-satis');
  await expect(page.getByRole('heading', {name: 'Hızlı Satış'})).toBeVisible();
});

test('öğrenilmiş hızlı seçim çipi sepete ekler ve toplam hesaplanır', async ({page}) => {
  const chip = page.getByText(`${SEED.productName} · ${SEED.productCode}`);
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(page.getByLabel(`${SEED.productName} miktar`)).toHaveValue('1');
  await chip.click();
  await expect(page.getByLabel(`${SEED.productName} miktar`)).toHaveValue('2');
  // 2 x 18.00 = ₺36,00 genel toplam (tr-TR para biçimi).
  await expect(page.getByText('₺36,00').first()).toBeVisible();
});

test('barkodla ürün eklenir ve satış tamamlanır', async ({page}) => {
  const barcode = page.getByLabel('Barkod');
  await barcode.fill(SEED.barcode);
  await barcode.press('Enter');
  await expect(page.getByLabel(`${SEED.productName} miktar`)).toBeVisible();
  await page.getByRole('button', {name: 'Satışı Tamamla'}).click();
  await expect(page.getByText(/Satış #\d+ tamamlandı/)).toBeVisible();
});

test('iskonto 111 girilince satış gönderilmez ve alan doğrulama hatası gösterir', async ({page}) => {
  await page.getByText(`${SEED.productName} · ${SEED.productCode}`).click();
  const discount = page.getByLabel(`${SEED.productName} iskonto`);
  await discount.fill('111');
  await expect(discount).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByText('iskonto 0–100 arası olmalı')).toBeVisible();

  let saleRequests = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/pos/sale') saleRequests += 1;
  });
  const complete = page.getByRole('button', {name: 'Satışı Tamamla'});
  await expect(complete).toBeDisabled();
  await complete.click({force: true});
  await expect.poll(() => saleRequests).toBe(0);
  await expect(page.getByText(/Satış #\d+ tamamlandı/)).toHaveCount(0);
});
