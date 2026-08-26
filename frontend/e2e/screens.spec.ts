import {test, expect, login} from './helpers';

// Dört ana ekran gerçek veriyle konsol-temiz render olmalı. Amaç ekran başına
// derin senaryo değil; "sayfa açılıyor, gerçek API şekliyle çizim yapıyor ve
// tek bir console.error / uncaught exception üretmiyor" sözleşmesidir
// (bugünkü POS çökmesi sınıfının ekran-genel bekçisi).
const SCREENS = [
  {path: '/', marker: 'Son Satışlar'},
  {path: '/urunler', marker: 'Yeni Ürün'},
  {path: '/musteriler', marker: 'Müşteriler'},
  {path: '/satislar', marker: 'Satışlar'},
  {path: '/tahsis-defteri', marker: 'Tahsis Defteri'},
  {path: '/raporlar', marker: 'Raporlar'},
  // Taze veritabanında pano tamamen boş veriyle açılır: grafiklerin ve
  // kırılımların boş-durum yolu da bu yüzden konsol-temiz olmak zorunda.
  {path: '/raporlar/satin-alma-panosu', marker: 'Satın Alma Panosu'},
] as const;

for (const screen of SCREENS) {
  test(`${screen.path} ekranı konsol-temiz açılır`, async ({page}) => {
    await login(page);
    await page.goto(screen.path);
    await expect(page.getByText(screen.marker).first()).toBeVisible({timeout: 15_000});
    // Geç gelen istekler (grafikler, listeler) hatayı testin sonunda üretmesin
    // diye ağın durulmasını bekle; konsol assert'i fixture'da koşar.
    await page.waitForLoadState('networkidle');
  });
}
