import {test, expect, login} from './helpers';
import {KAPI_GIRDILERI, ROTA_GOVDESI_TESTID, kapiTestBasligi} from './rota-envanteri';

// Rota açılış kapısı — testleri ROTA ENVANTERİNDEN üretilir.
//
// ÖLÇÜLEN BOŞLUK. `Insights` ekranına (`/analizler`) gerçek bir render çökmesi
// enjekte edildiğinde üç zorunlu CI kapısı da yeşil kaldı: `npm run build`
// (tsc + vite) çökmeyi paketin İÇİNE koyarak geçti, `playwright test` 111
// testle exit 0 verdi, `vitest run` 532 testle exit 0 verdi. Karşıt kontrol
// aynı çökmeyi KAPSANAN bir ekrana (`/`, Pano) koydu ve e2e KIRMIZI oldu
// (helpers.ts'teki "Konsol temiz olmalı" fixture'ı yakaladı). Yani düzenek
// çalışıyor; yalnızca uygulamanın çoğu ekranına hiç uğramıyordu.
//
// BU DOSYANIN İŞİ. Yeni bir mekanizma kurmaz — çökmeyi yakalayan şey zaten
// `helpers.ts` fixture'ıdır, bu dosya o fixture'ı ziyaret edilmeyen rotalara
// TAŞIR. screens.spec.ts ile aynı sözleşme, farklı rota kümesi: "sayfa açılır,
// gerçek API şekliyle çizer, tek bir console.error / uncaught exception
// üretmez".
//
// LİSTE ARTIK BURADA DEĞİL. Rota kümesi de, kapsam dışı bırakma mekanizması da
// `rota-envanteri.ts`ye taşındı; bu dosya yalnız `kapi` tasnifli girdileri
// GEZER. Sebebi ölçülmüştür (#8, KABUL EDİLEN ARTIK): liste burada dururken
// ondan bir rota eksiltmek süiti YEŞİL bırakıyordu, çünkü hiçbir şey listenin
// App.tsx ile eşitliğini savunmuyordu. Şimdi eşitliği `src/rota-kapsam-
// sozlesmesi.test.ts` (G1) statik olarak, üretilen testlerin gerçekten koştuğunu
// da `rota-kapsam-raportoru.ts` (R1/R2) çalışma zamanında savunur.
//
// HER ROTADA ÜÇ ÖLÇÜM, ÜÇÜ DE GEREKLİ:
//   1. `isaret` görünür -> sayfa GERÇEKTEN çizildi. Yalnız konsola bakmak
//      yetmez: boş ekran da konsol-temizdir.
//   2. `networkidle`     -> havada olan ağ isteklerinin sonuçlanmasını bekler,
//      böylece yoldaki bir hatanın assert öncesinde düşmesini sağlar. Test
//      gövdesi İÇİNDE çıkan hatalar helpers.ts konsol/sayfa hatası
//      dinleyicisi tarafından yakalanır. Test gövdesi BİTTİKTEN SONRA çıkan
//      hatalar — örneğin testi aşan bir zamanlayıcı (timer) — YAKALANMAZ. Bu
//      bekleme yarış penceresini daraltır; tamamen kapatmaz.
//   3. pathname aynı     -> rota bir izin duvarına çarpıp `/`'a düşmüş olmasın.
//      Bu olmadan test Pano'yu ölçer ve "kapsandı" der; kapsam sayısı şişer,
//      kapı hiçbir şeyi korumaz.
//
// `anonim` girdiler giriş YAPILMADAN ölçülür (publicPaths.ANONYMOUS_PATHS):
// sınanan şey oturumsuz ziyaretçinin gördüğü ekrandır. `/sifre-sifirla`
// TOKEN'SIZ ziyaret edilir ve bu bilinçlidir: token yalnız FORM GÖNDERİLİNCE
// kullanılır (`pages/ResetPassword.tsx` -> submit), açılışta hiçbir API çağrısı
// yoktur. Uydurma bir token koymak, ölçülen şeyi "sayfa çiziliyor mu"dan "token
// geçerli mi"ye kaydırırdı.

for (const girdi of KAPI_GIRDILERI) {
  test(kapiTestBasligi(girdi), async ({page}) => {
    if (girdi.oturum === 'oturumlu') await login(page);
    await page.goto(girdi.rota);

    // İŞARET SAYFA GÖVDESİNDE ARANIR — SAYFANIN TAMAMINDA DEĞİL.
    //
    // Ölçülen boşluk (bkz. `rota-envanteri.ts`, ROTA_GOVDESI_TESTID): işaretlerin
    // büyük kısmı aynı zamanda kenar çubuğu etiketidir ve AppShell aktif grubu
    // her zaman AÇIK gösterir. Sayfanın TAMAMINDA yapılan bir metin araması
    // gövde boşken bile o etiketi bulurdu; kapı çökmüş bir ekranı YEŞİL sayardı.
    const govde = page.getByTestId(ROTA_GOVDESI_TESTID);
    await expect(
      govde,
      `${girdi.rota}: rota gövdesi kökü (data-testid="${ROTA_GOVDESI_TESTID}") tam olarak BİR kez bulunmalı; ` +
        'oturumlu rotalarda AppShell, oturumsuz rotalarda sayfa bileşeni onu taşır',
    ).toHaveCount(1, {timeout: 15_000});
    await expect(
      govde.getByText(girdi.isaret).first(),
      `${girdi.rota} çizilmedi (işaret: "${girdi.isaret}" sayfa GÖVDESİNDE yok; ` +
        'kenar çubuğunda görünmesi kapsam kanıtı değildir)',
    ).toBeVisible({timeout: 15_000});
    await page.waitForLoadState('networkidle');
    expect(
      new URL(page.url()).pathname,
      `${girdi.rota} başka bir rotaya yönlendirildi (izin duvarı?)`,
    ).toBe(girdi.rota);
  });
}
