import {test, expect, login, SEED} from './helpers';

// Şubeler arası transfer akışı: parça seç -> stoğu olan depodan gönder ->
// diyalogda hedef/miktar -> transfer geçmişinde kayıt. Tek yazma yolu
// POST /warehouses/transfers'tır; fiziksel stok korumaları backend'dedir.
test('stoğu olan depodan ikinci şubeye transfer oluşturulur', async ({page}) => {
  await login(page);
  await page.goto('/sube-transfer');
  await expect(page.getByRole('heading', {name: 'Şubeler Arası Transfer'})).toBeVisible();

  await page.getByLabel('Parça ara (ad / kod)').fill(SEED.productName);
  await page.getByRole('option', {name: new RegExp(SEED.productCode)}).click();

  // Tohum: 8 girdi, 2'si satıldı -> kaynak depoda 6 var; buton yalnız stoklu
  // satırda etkindir.
  const send = page.locator('button:not([disabled])', {hasText: 'Bu depodan gönder'});
  await expect(send).toHaveCount(1);
  await send.click();

  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Depolar Arası Transfer')).toBeVisible();
  await expect(dialog.getByLabel('Hedef depo')).toBeVisible();
  await dialog.getByLabel('Transfer miktarı').fill('2');
  await dialog.getByRole('button', {name: 'Transfer Et'}).click();
  await expect(dialog).toBeHidden({timeout: 10_000});

  // Geçmiş tablosunda kayıt: boş durum kaybolur, detay bağlantısı doğar.
  await expect(page.getByText('Henüz transfer yapılmadı.')).toBeHidden();
  await expect(page.locator('a[href^="/depo-transferleri/"]').first()).toBeVisible();
});
