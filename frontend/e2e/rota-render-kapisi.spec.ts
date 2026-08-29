import {test, expect, login} from './helpers';

// Rota açılış kapısı — e2e'nin HİÇ ZİYARET ETMEDİĞİ ekranlar.
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
// HER ROTADA ÜÇ ÖLÇÜM, ÜÇÜ DE GEREKLİ:
//   1. `marker` görünür  -> sayfa GERÇEKTEN çizildi. Yalnız konsola bakmak
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
// KAPSAM DIŞI (bilerek): `:id` alan yedi rota — /tedarikciler/:id, /urunler/:id,
// /saha/:id, /hayvancilik/hayvanlar/:id, /depo-transferleri/:id,
// /stok-sayimlari/:id, /depolar/:id. Bunlar tohumlanmış kayıt ister; ayrı bir
// tur. Burada tohum GEREKTİRMEYEN rotalar bitirilir.

/** Oturum açmış admin'in doğrudan gidebildiği, bugüne dek ziyaret edilmemiş rotalar. */
const OTURUMLU_ROTALAR = [
  {path: '/alislar', marker: 'Alışlar'},
  {path: '/belge-akislari', marker: 'Belge Akışları'},
  {path: '/tedarikciler', marker: 'Tedarikçiler'},
  {path: '/parca-supersession', marker: 'Parça Supersession'},
  {path: '/is-emirleri', marker: 'İş Emirleri'},
  {path: '/tarla/ciftlikler', marker: 'Çiftlikler & Parseller'},
  {path: '/tarla/sezonlar', marker: 'Ekim Sezonları'},
  {path: '/tarla/gorevler', marker: 'Tarla Görevleri'},
  {path: '/tarla/hasat', marker: 'Hasat Kayıtları'},
  {path: '/tarla/olay-kuyrugu', marker: 'Olay Kuyruğu'},
  {path: '/tarla/hizli-giris', marker: 'Hızlı Faaliyet Girişi'},
  {path: '/hayvancilik/hayvanlar', marker: 'Hayvanlar & Sürüler'},
  {path: '/hayvancilik/saglik', marker: 'Sürü Sağlığı'},
  {path: '/hayvancilik/doller', marker: 'Döl Verimi'},
  {path: '/hayvancilik/verim', marker: 'Süt & Besi'},
  {path: '/tanimlar/maliyet-oranlari', marker: 'Maliyet Oranları'},
  {path: '/alacaklar', marker: 'Harman Vadesi / Alacaklar'},
  {path: '/tanimlar/harman-sezon', marker: 'Harman Sezon Takvimi'},
  {path: '/stok-hareketleri', marker: 'Stok Hareketleri'},
  {path: '/stok-sayimlari', marker: 'Stok Sayımları'},
  {path: '/sezonsal-stok-plani', marker: 'Sezonsal Stok Planı'},
  {path: '/raporlar/tedarikci-karsilastirma', marker: 'Tedarikçi Fiyat Karşılaştırma'},
  {path: '/raporlar/emilim-orani', marker: 'Emilim Oranı (Absorption Rate)'},
  {path: '/analizler', marker: 'Akıllı Analizler'},
  {path: '/firmalar', marker: 'Firma ve Şubeler'},
  {path: '/kullanicilar', marker: 'Kullanıcılar ve Yetkiler'},
  {path: '/islem-gecmisi', marker: 'İşlem Geçmişi'},
  {path: '/tedarikci-fiyatlari', marker: 'Tedarikçi Fiyatları'},
] as const;

// Oturum GEREKTİRMEYEN iki uç rota (publicPaths.ANONYMOUS_PATHS). Giriş
// yapılmadan ölçülürler; giriş yapılmış olsaydı da anlamlı olmazdı, çünkü
// sınanan şey oturumsuz ziyaretçinin gördüğü ekran.
//
// `/sifre-sifirla` TOKEN'SIZ ziyaret edilir ve bu bilinçlidir: token yalnız
// FORM GÖNDERİLİNCE kullanılır (`pages/ResetPassword.tsx` -> submit), açılışta
// hiçbir API çağrısı yoktur. Uydurma bir token koymak, ölçülen şeyi "sayfa
// çiziliyor mu"dan "token geçerli mi"ye kaydırır ve public-routes.spec.ts'in
// `/eposta-dogrula` için yazdığı gerekçenin aynısıyla 400'lü bir yanıtı
// konsol-temiz sözleşmesinin önüne atardı.
const ANONIM_ROTALAR = [
  {path: '/sifremi-unuttum', marker: 'Şifremi unuttum'},
  {path: '/sifre-sifirla', marker: 'Yeni şifre belirleyin'},
] as const;

// KAPSANMAYAN, GEREKÇESİYLE GÖRÜNÜR: /yedekler.
//
// Rota `platform` iznine bağlıdır ve bu izin `*` ile GELMEZ: `AuthContext.can`
// onu tek başına `is_platform_operator` bayrağına bağlar, bayrak da backend'de
// `SUNGUR_PLATFORM_OPERATORS` ortam değişkeninde ADI GEÇEN kullanıcı
// kimliklerine (`platform_access.is_platform_operator`). E2E sunucusu
// (`e2e/serve.py`) bu değişkeni KURMAZ, yani e2e admin'i platform operatörü
// DEĞİLDİR ve `/yedekler` `Protected` içinde `/`'a düşer. ÖLÇÜLDÜ.
//
// Değişkeni e2e sunucusunda açmak kapsamı bir satırda büyütürdü ama ölçülen
// güvenlik duruşunu da değiştirirdi: bütün spec'lerin admin'i platform
// operatörü olurdu. Kapsam uğruna sertifikalanan duruşu değiştirmek, kapının
// kendisini zayıflatmaktır. Bu yüzden rota BURADA DEĞİL ve sebebi burada yazılı.
const KAPSANMAYAN = {
  '/yedekler': 'platform operatörü ortam değişkeni e2e sunucusunda kurulmuyor; rota Protected içinde /\'a düşer',
} as const;

test('kapsanmayan rota listesi boş sayılmaz (gerekçe kaybolmasın)', () => {
  // Bu test kapsam ölçmez; KAYDIN kendisini ve gerekçesini korur. Gerekçe
  // silinir veya içeriği boşaltılırsa, sözleşme sessiz bir eksilme yaşamış olur.
  expect(KAPSANMAYAN).toEqual({
    '/yedekler':
      "platform operatörü ortam değişkeni e2e sunucusunda kurulmuyor; rota Protected içinde /'a düşer",
  });
});

for (const rota of OTURUMLU_ROTALAR) {
  test(`${rota.path} rotası konsol-temiz açılır`, async ({page}) => {
    await login(page);
    await page.goto(rota.path);
    await expect(
      page.getByText(rota.marker).first(),
      `${rota.path} çizilmedi (işaret: "${rota.marker}")`,
    ).toBeVisible({timeout: 15_000});
    await page.waitForLoadState('networkidle');
    expect(
      new URL(page.url()).pathname,
      `${rota.path} başka bir rotaya yönlendirildi (izin duvarı?)`,
    ).toBe(rota.path);
  });
}

for (const rota of ANONIM_ROTALAR) {
  test(`${rota.path} rotası oturumsuz konsol-temiz açılır`, async ({page}) => {
    await page.goto(rota.path);
    await expect(
      page.getByText(rota.marker).first(),
      `${rota.path} çizilmedi (işaret: "${rota.marker}")`,
    ).toBeVisible({timeout: 15_000});
    await page.waitForLoadState('networkidle');
    expect(
      new URL(page.url()).pathname,
      `${rota.path} başka bir rotaya yönlendirildi`,
    ).toBe(rota.path);
  });
}
