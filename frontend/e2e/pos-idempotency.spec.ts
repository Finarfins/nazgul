import {test, expect, login, SEED} from './helpers';

// POS çift-gönderim — "kullanıcı ne yaparsa yapsın tek sipariş".
//
// İki savunma katmanı var:
//   1) İstemci: completeSale re-entry guard'ı (submittingRef) aynı senkron
//      tick içindeki ikinci tıkı yutar; gereksiz ikinci HTTP isteği hiç atılmaz.
//   2) Sunucu: pos_idempotency benzersizlik kısıtı, katman 1'i aşan herhangi bir
//      ikinci isteği aynı sipariş id'sine indirger (idempotent replay).
//
// Butonu tek tick'te iki kez tıkla (React butonu disable etmeden önce), sonuç
// TEK sipariş id'si olmalı. Sunucu eşzamanlılık kısıtının kendisi backend
// testlerinde ayrıca kanıtlanır; buradaki sözleşme davranışsaldır: kullanıcı ne
// yaparsa yapsın tek sipariş.
test('kullanıcı hızlı çift tıklasa da tek sipariş oluşur', async ({page}) => {
  await login(page);
  await page.goto('/hizli-satis');
  await expect(page.getByRole('heading', {name: 'Hızlı Satış'})).toBeVisible();

  // Sepete bir ürün ekle (barkodla, deterministik).
  const barcode = page.getByLabel('Barkod');
  await barcode.fill(SEED.barcode);
  await barcode.press('Enter');
  await expect(page.getByLabel(`${SEED.productName} miktar`)).toBeVisible();

  // Başarılı her satış yanıtındaki sipariş id'sini topla. Filtre gevşek
  // (response.ok()): sunucu replay'i 200 ile dönmeye başlarsa test sessizce
  // yanlış sonuç vermesin.
  const saleIds = new Set<number>();
  page.on('response', async (response) => {
    if (new URL(response.url()).pathname !== '/api/pos/sale') return;
    if (response.request().method() !== 'POST' || !response.ok()) return;
    saleIds.add((await response.json()).sale_id as number);
  });

  // İki tıkı TEK senkron tick'te gönder: butonu disable eden setBusy(true) ancak
  // handler döndükten sonra React tarafından flush edilir, yani gerçek çift-
  // dokunuş bu flush'tan hızlı olabilir. re-entry guard bunu istemcide durdurur.
  await page.getByRole('button', {name: 'Satışı Tamamla'}).evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });

  await expect(page.getByText(/Satış #\d+ tamamlandı/)).toBeVisible({timeout: 15_000});
  // Tek sözleşme: ne olursa olsun tek sipariş id'si.
  await expect.poll(() => saleIds.size, {timeout: 10_000}).toBe(1);
});
