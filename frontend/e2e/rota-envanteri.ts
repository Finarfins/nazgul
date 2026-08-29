// ROTA ENVANTERİ — rota kapsamının TEK KAYNAĞI.
//
// NEDEN VAR. Kapsam bugüne dek üç ayrı yerde, üç ayrı biçimde duruyordu:
// `rota-render-kapisi.spec.ts` içindeki iki dizi, aynı dosyadaki `KAPSANMAYAN`
// sözlüğü ve geri kalan spec'lerin gövdesine dağılmış `page.goto()` çağrıları.
// Hiçbiri diğerini bilmiyordu; bir rota App.tsx'e eklendiğinde üç yerin de
// sessiz kalması mümkündü. Bu dosya o üç kaynağı BİRE indirir: App.tsx'teki her
// adlandırılmış rota BURADA bir kez görünür ve ÜÇ tasniften TAM OLARAK BİRİNİ
// alır.
//
// ÜÇ TASNİF, ÜÇ FARKLI KANIT SEVİYESİ — ve bu ayrım bilinçli:
//   * `kapi` : `rota-render-kapisi.spec.ts` bu rotaya GİDER ve üç ölçüm yapar
//              (işaret görünür, `networkidle`, pathname aynı). Testi bu dosyadan
//              ÜRETİLİR; envanterden düşen rota testi de kaybeder ve raportör
//              (R1) bunu kırmızıya çevirir.
//   * `spec` : rota BAŞKA bir spec tarafından, daha derin bir senaryonun İÇİNDE
//              ziyaret edilir. Burada o spec'in dosyası ve test ADI yazılır;
//              raportör (R3) o testin GERÇEKTEN koştuğunu ve GEÇTİĞİNİ ölçer.
//              Yazılı bir ad, koşmuş bir testin yerine geçmez.
//   * `muaf` : rota bilerek ölçülmez ve GEREKÇESİ burada yazılıdır. Muafiyet bir
//              kapsam değil, kapsamın ÖLÇÜLMÜŞ SINIRIDIR; gerekçesi silinemesin
//              diye `src/rota-kapsam-sozlesmesi.test.ts` (G5) onu dondurur.
//
// DONMUŞ SAYISAL TABAN YOKTUR VE BU BİLİNÇLİDİR. "Kapsam >= %90" biçiminde bir
// eşik, sabiti olduğu yerde bırakıp testleri silerek yenilebilir. Buradaki
// sözleşme sayıya değil KÜME EŞİTLİĞİNE dayanır: App.tsx'in rota kümesi ile bu
// envanterin rota kümesi İKİ YÖNDE de eşit olmak zorundadır (G1) ve her tasnifin
// karşılığı çalışma zamanında aranır (R1-R4). Yeni bir rota eklemek kapsamı
// otomatik büyütmez; envanterde bir tasnif seçmeye ZORLAR.

/** Kapsam kapısının kendi dosyası — `frontend/` köküne göre. Raportör (R4) bu
 *  dosyanın koşuda VAR OLDUĞUNU ölçer: kapının kendisi düşerse sözleşme sessizce
 *  yeşile dönmemeli. */
export const KAPSAM_KAPISI_DOSYASI = 'e2e/rota-render-kapisi.spec.ts';

/** `kapi` rotasının oturum koşulu. `anonim` rotalar giriş YAPILMADAN ölçülür;
 *  sınanan şey oturumsuz ziyaretçinin gördüğü ekrandır. */
export type Oturum = 'oturumlu' | 'anonim';

export interface KapiTasnifi {
  readonly tur: 'kapi';
  /** Sayfaya ÖZGÜ, ekranda görünen metin. Yalnız konsola bakmak yetmez: boş
   *  ekran da konsol-temizdir. Menüde de geçen bir etiket seçilmez — o, rotayı
   *  değil kabuğu ölçerdi. */
  readonly isaret: string;
  readonly oturum: Oturum;
}

export interface SpecTasnifi {
  readonly tur: 'spec';
  /** `frontend/` köküne göre spec dosyası, ör. `e2e/screens.spec.ts`. */
  readonly dosya: string;
  /** Playwright'ın çalışma zamanında ürettiği TAM başlık: iç içe `describe`
   *  başlıkları ve test başlığı ` > ` ile birleştirilir. Şablon literalinden
   *  gelen parçalar ÇÖZÜLMÜŞ yazılır (ör. `mobile-390: ...`), çünkü raportör
   *  koşan testi tam bu adla arar. */
  readonly testAdi: string;
  /** Rotanın o test İÇİNDE nasıl ziyaret edildiği — ve varsa ölçümün sınırı. */
  readonly gerekce: string;
}

export interface MuafTasnifi {
  readonly tur: 'muaf';
  readonly gerekce: string;
}

export type RotaGirdisi =
  | ({readonly rota: string} & KapiTasnifi)
  | ({readonly rota: string} & SpecTasnifi)
  | ({readonly rota: string} & MuafTasnifi);

/** Tohumlanmış kayıt isteyen `:id` rotalarının ORTAK gerekçesi. Tek metin: sekiz
 *  yerde yeniden yazılsaydı biri sessizce ayrışabilirdi. */
const TOHUM_GEREKTIREN_ID =
  'tohumlanmış kayıt ister; kapı tohum GEREKTİRMEYEN rotaları bitirir, bu rota ayrı bir turdur';

export const ROTA_ENVANTERI: readonly RotaGirdisi[] = [
  // --- Oturumsuz uç rotalar --------------------------------------------------
  {
    rota: '/tanitim',
    tur: 'spec',
    dosya: 'e2e/public-routes.spec.ts',
    testAdi: 'anonim ziyaretçi tanıtım sayfası sayfasında kalır, girişe fırlatılmaz',
    gerekce:
      'anonim ziyaretçi olarak açılır; SINIR: ölçüm OLUMSUZDUR — girişe yönlendirilmediği ve giriş formunun çizilmediği ölçülür, sayfaya ÖZGÜ bir işaret ARANMAZ',
  },
  {
    rota: '/giris',
    tur: 'spec',
    dosya: 'e2e/login.spec.ts',
    testAdi: 'giriş yapılır ve panel konsol-temiz açılır',
    gerekce: 'giriş formu doldurulup gönderilir; helpers.login her spec\'in de giriş yoludur',
  },
  {
    rota: '/kayit',
    tur: 'spec',
    dosya: 'e2e/public-routes.spec.ts',
    testAdi: 'anonim ziyaretçi self-servis kayıt sayfasında kalır, girişe fırlatılmaz',
    gerekce:
      'anonim ziyaretçi olarak açılır; SINIR: ölçüm OLUMSUZDUR — yalnız yönlendirme yokluğu ölçülür, sayfaya ÖZGÜ bir işaret ARANMAZ',
  },
  {
    rota: '/eposta-dogrula',
    tur: 'spec',
    dosya: 'e2e/public-routes.spec.ts',
    testAdi: 'anonim ziyaretçi e-posta doğrulama bağlantısı sayfasında kalır, girişe fırlatılmaz',
    gerekce:
      'TOKEN\'SIZ açılır — uydurma token gerçek bir doğrulama çağrısı tetikler ve ölçülen şey sayfanın çizilmesinden token geçerliliğine kayardı; SINIR: ölçüm OLUMSUZDUR, sayfaya ÖZGÜ bir işaret ARANMAZ',
  },
  {
    rota: '/sifre-degistir',
    tur: 'spec',
    dosya: 'e2e/session-lifecycle.spec.ts',
    testAdi: 'zorunlu ilk şifre değişimi: yeni hesap panele değil /sifre-degistir ekranına düşer',
    gerekce: 'yeni sağlanan hesabın zorunlu yönlendirmesiyle varılır ve ekranın çizildiği ölçülür',
  },
  {
    rota: '/sifremi-unuttum',
    tur: 'kapi',
    isaret: 'Şifremi unuttum',
    oturum: 'anonim',
  },
  {
    rota: '/sifre-sifirla',
    tur: 'kapi',
    isaret: 'Yeni şifre belirleyin',
    oturum: 'anonim',
  },

  // --- Pano ve satış ---------------------------------------------------------
  {
    rota: '/',
    tur: 'spec',
    dosya: 'e2e/screens.spec.ts',
    testAdi: '/ ekranı konsol-temiz açılır',
    gerekce: 'Pano; "Son Satışlar" işaretiyle ölçülür ve her spec\'in giriş sonrası varış noktasıdır',
  },
  {
    rota: '/satislar',
    tur: 'spec',
    dosya: 'e2e/screens.spec.ts',
    testAdi: '/satislar ekranı konsol-temiz açılır',
    gerekce: '"Satışlar" işaretiyle ölçülür',
  },
  {
    rota: '/hizli-satis',
    tur: 'spec',
    dosya: 'e2e/pos.spec.ts',
    testAdi: 'barkodla ürün eklenir ve satış tamamlanır',
    gerekce: 'POS ekranı açılır, barkodla ürün eklenir ve satış uçtan uca tamamlanır',
  },
  {
    rota: '/alislar',
    tur: 'kapi',
    isaret: 'Alışlar',
    oturum: 'oturumlu',
  },
  {
    rota: '/belge-akislari',
    tur: 'kapi',
    isaret: 'Belge Akışları',
    oturum: 'oturumlu',
  },

  // --- Cariler ---------------------------------------------------------------
  {
    rota: '/musteriler',
    tur: 'spec',
    dosya: 'e2e/screens.spec.ts',
    testAdi: '/musteriler ekranı konsol-temiz açılır',
    gerekce: '"Müşteriler" işaretiyle ölçülür',
  },
  {
    rota: '/tedarikciler',
    tur: 'kapi',
    isaret: 'Tedarikçiler',
    oturum: 'oturumlu',
  },
  {
    rota: '/musteriler/:id',
    tur: 'spec',
    dosya: 'e2e/pos-credit-sale.spec.ts',
    testAdi: 'veresiye satış müşterinin açık bakiyesini satış tutarı kadar artırır',
    gerekce: 'tohumlanan müşterinin kartı açılır ve açık bakiyesi satış tutarıyla karşılaştırılır',
  },
  {
    rota: '/tedarikciler/:id',
    tur: 'muaf',
    gerekce: TOHUM_GEREKTIREN_ID,
  },

  // --- Ürünler ---------------------------------------------------------------
  {
    rota: '/urunler',
    tur: 'spec',
    dosya: 'e2e/screens.spec.ts',
    testAdi: '/urunler ekranı konsol-temiz açılır',
    gerekce: '"Yeni Ürün" işaretiyle ölçülür',
  },
  {
    rota: '/urunler/:id',
    tur: 'muaf',
    gerekce: TOHUM_GEREKTIREN_ID,
  },
  {
    rota: '/parca-supersession',
    tur: 'kapi',
    isaret: 'Parça Supersession',
    oturum: 'oturumlu',
  },

  // --- Makineler ve iş emirleri ----------------------------------------------
  {
    rota: '/makineler',
    tur: 'spec',
    dosya: 'e2e/machine-360.spec.ts',
    testAdi: 'makine kartı açılır ve dört sekme arasında gezilir',
    gerekce:
      'listeye kenar çubuğundan SPA içi gezilerek varılır (bu dosyada giriş dışında goto YOKTUR) ve tohumlanan makinenin satırı bulunur',
  },
  {
    rota: '/makineler/:id',
    tur: 'spec',
    dosya: 'e2e/machine-360.spec.ts',
    testAdi: '?sekme= derin bağlantısı, yenilemede kalıcılık ve geçersiz slug',
    gerekce: 'makine kartına URL ile doğrudan girilir ve tam yeniden yükleme sonrası sekme korunur',
  },
  {
    rota: '/is-emirleri',
    tur: 'kapi',
    isaret: 'İş Emirleri',
    oturum: 'oturumlu',
  },
  {
    rota: '/is-emirleri/:id',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'touch target and responsive action gate > mobile-390: four screens render and preserve their actions',
    gerekce:
      'tohumlanan açık iş emri 390px\'te açılır; ek, işçilik ve teknisyen eylemleri dokunma hedefi ve yatay taşma ölçümünden geçer',
  },

  // --- Saha (servis iş emirleri) ---------------------------------------------
  {
    rota: '/saha',
    tur: 'spec',
    dosya: 'e2e/field-write-epoch-race.spec.ts',
    testAdi: 'gerçek iki sekme yarışı: logout epoch gerçek applySnapshot commitini reddeder',
    gerekce:
      'İKİNCİ sekmede (context.newPage) açılır ve "Saha İşlerim" görünürlüğü beklenir; SINIR: bu ziyaret ikincil sekmededir, `page` fixture\'ının konsol-temiz sözleşmesi o sekmeyi KAPSAMAZ',
  },
  {
    rota: '/saha/:id',
    tur: 'muaf',
    gerekce: TOHUM_GEREKTIREN_ID,
  },

  // --- Tarla Yönetimi --------------------------------------------------------
  {
    rota: '/tarla',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'touch target and responsive action gate > mobile-390: four screens render and preserve their actions',
    gerekce: 'tohumlanan aktif sezonla çiftlik panosu 390px\'te çizilir ve beyan edilen veri yüzeyi ölçülür',
  },
  {
    rota: '/tarla/ciftlikler',
    tur: 'kapi',
    isaret: 'Çiftlikler & Parseller',
    oturum: 'oturumlu',
  },
  {
    rota: '/tarla/sezonlar',
    tur: 'kapi',
    isaret: 'Ekim Sezonları',
    oturum: 'oturumlu',
  },
  {
    rota: '/tarla/faaliyetler',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'touch target and responsive action gate > mobile-390: four screens render and preserve their actions',
    gerekce: 'tohumlanan faaliyet listesi ve girdi detayı 390px\'te çizilir ve eylemleri korunur',
  },
  {
    rota: '/tarla/gorevler',
    tur: 'kapi',
    isaret: 'Tarla Görevleri',
    oturum: 'oturumlu',
  },
  {
    rota: '/tarla/hasat',
    tur: 'kapi',
    isaret: 'Hasat Kayıtları',
    oturum: 'oturumlu',
  },
  {
    rota: '/tarla/olay-kuyrugu',
    tur: 'kapi',
    isaret: 'Olay Kuyruğu',
    oturum: 'oturumlu',
  },
  {
    rota: '/tarla/hizli-giris',
    tur: 'kapi',
    isaret: 'Hızlı Faaliyet Girişi',
    oturum: 'oturumlu',
  },
  {
    rota: '/tarla/parseller/:id',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'touch target and responsive action gate > mobile-390: four screens render and preserve their actions',
    gerekce: 'tohumlanan parselin detayı 390px\'te açılır; tek faaliyetli yüzeyi ölçülür',
  },

  // --- Hayvancılık -----------------------------------------------------------
  {
    rota: '/hayvancilik',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'six-screen measured mobile batch gate > mobile-390: seeded data and required actions survive responsive rendering',
    gerekce: 'tohumlanan aktif inek ve gecikmiş aşı ile sürü panosu 390px\'te çizilir',
  },
  {
    rota: '/hayvancilik/hayvanlar',
    tur: 'kapi',
    isaret: 'Hayvanlar & Sürüler',
    oturum: 'oturumlu',
  },
  {
    rota: '/hayvancilik/hayvanlar/:id',
    tur: 'muaf',
    gerekce: TOHUM_GEREKTIREN_ID,
  },
  {
    rota: '/hayvancilik/saglik',
    tur: 'kapi',
    isaret: 'Sürü Sağlığı',
    oturum: 'oturumlu',
  },
  {
    rota: '/hayvancilik/doller',
    tur: 'kapi',
    isaret: 'Döl Verimi',
    oturum: 'oturumlu',
  },
  {
    rota: '/hayvancilik/verim',
    tur: 'kapi',
    isaret: 'Süt & Besi',
    oturum: 'oturumlu',
  },

  // --- Finans ----------------------------------------------------------------
  {
    rota: '/tanimlar/maliyet-oranlari',
    tur: 'kapi',
    isaret: 'Maliyet Oranları',
    oturum: 'oturumlu',
  },
  {
    rota: '/faturalar',
    tur: 'spec',
    dosya: 'e2e/work-order-billing.spec.ts',
    testAdi: 'iş emrine parça eklenir, tamamlanır ve faturalandırılıp fatura detayına düşer',
    gerekce: 'faturalandırma sonrası fatura listesi açılır ve üretilen fatura numarası listede aranır',
  },
  {
    rota: '/faturalar/:id',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'receivables, invoice, and activity mobile slice gate > mobile-390: seeded data and required actions survive responsive rendering',
    gerekce: 'tohumlanan kesilmiş faturanın detayı 390px\'te açılır; kalem, müşteri ve ISSUED durumu ölçülür',
  },
  {
    rota: '/odemeler',
    tur: 'spec',
    dosya: 'e2e/rbac-finance.spec.ts',
    testAdi: 'satış rolü tahsilata erişir, finans ekranına erişemez',
    gerekce:
      'menüden SPA içi varılır ve "Tahsilat / Ödeme" BAŞLIĞI ile liste/özet çizimi ölçülür; SINIR: ölçüm `satis` ROLÜNDE yapılır — admin görünümü (ör. role bağlı ek eylemler) bu rotada ölçülmez',
  },
  {
    rota: '/tahsis-defteri',
    tur: 'spec',
    dosya: 'e2e/screens.spec.ts',
    testAdi: '/tahsis-defteri ekranı konsol-temiz açılır',
    gerekce: '"Tahsis Defteri" işaretiyle, tahsis motoru KAPALI varsayılanında ölçülür',
  },
  {
    rota: '/alacaklar',
    tur: 'kapi',
    isaret: 'Harman Vadesi / Alacaklar',
    oturum: 'oturumlu',
  },
  {
    rota: '/nakit-yonetimi',
    tur: 'kapi',
    // "Nakit Yönetimi" kenar çubuğunda da geçen bir MENÜ ETİKETİdir; işaret
    // olarak seçilseydi kabuğu ölçerdik. Bu özet kartı yalnız Finance ekranında
    // çizilir ve veriden BAĞIMSIZ olarak her zaman vardır (sabit dizi üzerinde
    // map; boş veritabanında da görünür).
    isaret: 'Alınan Çek/Senet',
    oturum: 'oturumlu',
  },
  {
    rota: '/tanimlar/harman-sezon',
    tur: 'kapi',
    isaret: 'Harman Sezon Takvimi',
    oturum: 'oturumlu',
  },

  // --- Stok ------------------------------------------------------------------
  {
    rota: '/stok-hareketleri',
    tur: 'kapi',
    isaret: 'Stok Hareketleri',
    oturum: 'oturumlu',
  },
  {
    rota: '/depo-transferleri/:id',
    tur: 'muaf',
    gerekce: TOHUM_GEREKTIREN_ID,
  },
  {
    rota: '/sube-transfer',
    tur: 'spec',
    dosya: 'e2e/transfer.spec.ts',
    testAdi: 'stoğu olan depodan ikinci şubeye transfer oluşturulur',
    gerekce: 'ekran açılır ve gerçek API üzerinden uçtan uca bir transfer oluşturulur',
  },
  {
    rota: '/stok-sayimlari',
    tur: 'kapi',
    isaret: 'Stok Sayımları',
    oturum: 'oturumlu',
  },
  {
    rota: '/stok-sayimlari/:id',
    tur: 'muaf',
    gerekce: TOHUM_GEREKTIREN_ID,
  },
  {
    rota: '/depolar',
    tur: 'kapi',
    // "Depolar" hem sayfa başlığı hem MENÜ ETİKETİdir (NAV_LABELS.warehouses);
    // işaret olarak seçilseydi rota yerine kenar çubuğu ölçülürdü. "Yeni Depo"
    // yalnız bu ekranın eylem çubuğunda vardır ve listeden bağımsız çizilir.
    isaret: 'Yeni Depo',
    oturum: 'oturumlu',
  },
  {
    rota: '/depolar/:id',
    tur: 'muaf',
    gerekce: TOHUM_GEREKTIREN_ID,
  },

  // --- Raporlar --------------------------------------------------------------
  {
    rota: '/raporlar',
    tur: 'spec',
    dosya: 'e2e/screens.spec.ts',
    testAdi: '/raporlar ekranı konsol-temiz açılır',
    gerekce: '"Raporlar" işaretiyle ölçülür',
  },
  {
    rota: '/raporlar/alacak-yaslandirma',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'receivables, invoice, and activity mobile slice gate > mobile-390: seeded data and required actions survive responsive rendering',
    gerekce:
      'tohumlanan açık alacakla 390px\'te açılır; müşteri satırı, kalan tutar ve belge detayı ölçülür',
  },
  {
    rota: '/raporlar/tedarikci-karsilastirma',
    tur: 'kapi',
    isaret: 'Tedarikçi Fiyat Karşılaştırma',
    oturum: 'oturumlu',
  },
  {
    rota: '/raporlar/satin-alma-panosu',
    tur: 'spec',
    dosya: 'e2e/screens.spec.ts',
    testAdi: '/raporlar/satin-alma-panosu ekranı konsol-temiz açılır',
    gerekce: 'taze veritabanında BOŞ veriyle açılır; grafiklerin boş-durum yolu da konsol-temiz olmalı',
  },
  {
    rota: '/raporlar/emilim-orani',
    tur: 'kapi',
    isaret: 'Emilim Oranı (Absorption Rate)',
    oturum: 'oturumlu',
  },
  {
    rota: '/sezonsal-stok-plani',
    tur: 'kapi',
    isaret: 'Sezonsal Stok Planı',
    oturum: 'oturumlu',
  },
  {
    rota: '/analizler',
    tur: 'kapi',
    isaret: 'Akıllı Analizler',
    oturum: 'oturumlu',
  },

  // --- Yönetim ---------------------------------------------------------------
  {
    rota: '/firmalar',
    tur: 'kapi',
    isaret: 'Firma ve Şubeler',
    oturum: 'oturumlu',
  },
  {
    rota: '/kullanicilar',
    tur: 'kapi',
    isaret: 'Kullanıcılar ve Yetkiler',
    oturum: 'oturumlu',
  },
  {
    rota: '/islem-gecmisi',
    tur: 'kapi',
    isaret: 'İşlem Geçmişi',
    oturum: 'oturumlu',
  },
  {
    rota: '/aktivite',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'receivables, invoice, and activity mobile slice gate > mobile-390: seeded data and required actions survive responsive rendering',
    gerekce: 'tohumlanan tek POS satış olayıyla 390px\'te açılır; olay satırı ve arşivle eylemi ölçülür',
  },
  {
    rota: '/yedekler',
    tur: 'muaf',
    // ÖLÇÜLDÜ (#8): rota `platform` iznine bağlıdır ve bu izin `*` ile GELMEZ.
    // `AuthContext.can` onu tek başına `is_platform_operator` bayrağına, backend
    // de `SUNGUR_PLATFORM_OPERATORS` ortam değişkeninde ADI GEÇEN kullanıcı
    // kimliklerine bağlar (`platform_access.is_platform_operator`). `e2e/serve.py`
    // bu değişkeni KURMAZ. Değişkeni açmak kapsamı tek satırda büyütürdü ama
    // BÜTÜN spec'lerin sertifikaladığı güvenlik duruşunu değiştirirdi; kapsam
    // uğruna duruş değiştirmek kapının kendisini zayıflatmaktır.
    gerekce:
      "platform operatörü ortam değişkeni e2e sunucusunda kurulmuyor; rota Protected içinde /'a düşer",
  },
  {
    rota: '/bildirimler',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'six-screen measured mobile batch gate > mobile-390: seeded data and required actions survive responsive rendering',
    gerekce: 'tohumlanan onay bekleyen bildirim olayıyla 390px\'te açılır; kutu yüzeyi ölçülür',
  },
  {
    rota: '/bildirimler/sablonlar',
    tur: 'spec',
    dosya: 'e2e/touch-targets.spec.ts',
    testAdi:
      'six-screen measured mobile batch gate > mobile-390: seeded data and required actions survive responsive rendering',
    gerekce: 'tohumlanan aktif servis hatırlatma şablonuyla 390px\'te açılır',
  },
  {
    rota: '/tedarikci-fiyatlari',
    tur: 'kapi',
    isaret: 'Tedarikçi Fiyatları',
    oturum: 'oturumlu',
  },
];

/** `kapi` tasnifli girdiler — testleri bu listeden ÜRETİLİR. */
export const KAPI_GIRDILERI: readonly (RotaGirdisi & KapiTasnifi)[] = ROTA_ENVANTERI.filter(
  (girdi): girdi is RotaGirdisi & KapiTasnifi => girdi.tur === 'kapi',
);

/** `spec` tasnifli girdiler — raportör (R3) bunların testini koşuda arar. */
export const SPEC_GIRDILERI: readonly (RotaGirdisi & SpecTasnifi)[] = ROTA_ENVANTERI.filter(
  (girdi): girdi is RotaGirdisi & SpecTasnifi => girdi.tur === 'spec',
);

/** `muaf` tasnifli girdiler — kapsamın ÖLÇÜLMÜŞ sınırı. */
export const MUAF_GIRDILERI: readonly (RotaGirdisi & MuafTasnifi)[] = ROTA_ENVANTERI.filter(
  (girdi): girdi is RotaGirdisi & MuafTasnifi => girdi.tur === 'muaf',
);

/**
 * Bir `kapi` girdisinin ürettiği test başlığı.
 *
 * TEK ÜRETİCİ OLMAK ZORUNDA. Spec bu fonksiyonla test AÇAR, raportör aynı
 * fonksiyonla o testi ARAR. İki yerde ayrı ayrı yazılsaydı biri değişince
 * raportör testi bulamaz ya da — daha kötüsü — aramayı gevşetip bulmuş gibi
 * yapardı.
 */
export function kapiTestBasligi(girdi: RotaGirdisi & KapiTasnifi): string {
  return girdi.oturum === 'anonim'
    ? `${girdi.rota} rotası oturumsuz konsol-temiz açılır`
    : `${girdi.rota} rotası konsol-temiz açılır`;
}
