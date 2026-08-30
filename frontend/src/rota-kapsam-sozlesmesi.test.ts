import {existsSync, readFileSync, readdirSync, statSync} from 'node:fs';
import {join, resolve} from 'node:path';

import {describe, expect, it} from 'vitest';

import {
  metindekiRouteEtiketSayisi,
  routeEtiketleriniAyristir,
  yasakBeyanVarMi,
} from '../e2e/rota-ayristirici';
import {
  kaliciZiyaretler,
  yoluRotayaCoz,
  type ZiyaretKaydi,
} from '../e2e/rota-ziyaret-kaydi';
import {
  KAPI_GIRDILERI,
  KAPSAM_KAPISI_DOSYASI,
  MUAF_GIRDILERI,
  ROTA_ENVANTERI,
  ROTA_GOVDESI_TESTID,
  SPEC_GIRDILERI,
} from '../e2e/rota-envanteri';

// ROTA KAPSAM SÖZLEŞMESİ — statik yarısı (G1-G10).
//
// İŞ BÖLÜMÜ. Bu dosya envanterin App.tsx ile ve diskteki spec dosyalarıyla
// TUTARLI olduğunu ölçer; envanterin bildirdiği testlerin gerçekten KOŞTUĞUNU ve
// GEÇTİĞİNİ (ve rotayı GERÇEKTEN ziyaret ettiğini) ölçen şey
// `e2e/rota-kapsam-raportoru.ts`dir (R1-R6). İkisi ayrı
// olmak zorunda: statik bir test "bu test koştu mu" diyemez, bir raportör de
// "App.tsx'te kaç rota var" demek için tarayıcı açmak zorunda kalırdı.
//
// DONMUŞ SAYISAL TABAN YOK. Hiçbir kapı "kapsam >= N" demez; G1 KÜME EŞİTLİĞİ
// ister. Yeni bir rota kapsamı düşürmez, envanteri EKSİK bırakır ve kapı kırmızı
// yanar; bir rotayı envanterden silmek de aynı kapıyı kırar. Sayı ölçülen bir
// SONUÇTUR, korunan bir sabit değil.

// KÖK, VARSAYILMAZ — DOĞRULANIR. vitest `frontend/` içinden koşar; yine de
// kökü paket kimliğinden teyit ederiz, yoksa dosya okumaları sessizce boş
// kümeye düşer ve kapı "hiçbir şey bulunamadı" ile yeşil kalırdı.
const FRONTEND_KOKU = process.cwd();
const yol = (gorecelYol: string): string => resolve(FRONTEND_KOKU, gorecelYol);
const oku = (gorecelYol: string): string => readFileSync(yol(gorecelYol), 'utf8');

if (!/"name":\s*"yerel-hesap-pro-next"/.test(oku('package.json'))) {
  throw new Error(
    `rota kapsam sözleşmesi yanlış kökten koşuyor: ${FRONTEND_KOKU} (frontend/ bekleniyordu)`,
  );
}

const APP_KAYNAGI = oku('src/App.tsx');
const PLAYWRIGHT_YAPILANDIRMASI = oku('playwright.config.ts');

/** Bir literalden (tek/çift tırnak ya da şablon) düz metni ya da şablon deseni
 *  çıkarır. Şablonlarda `${...}` yerleri `.+` olur: çalışma zamanında ne gelirse
 *  gelsin eşleşsin, ama sabit parçalar HARFİ HARFİNE tutsun. */
function literalDeseni(kaynak: string, baslangic: number): RegExp | null {
  const tirnak = kaynak[baslangic];
  if (tirnak !== "'" && tirnak !== '"' && tirnak !== '`') return null;
  let i = baslangic + 1;
  let desen = '';
  while (i < kaynak.length) {
    const karakter = kaynak[i];
    if (karakter === '\\') {
      desen += kaynak[i + 1].replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      i += 2;
      continue;
    }
    if (karakter === tirnak) return new RegExp(`^${desen}$`);
    if (tirnak === '`' && karakter === '$' && kaynak[i + 1] === '{') {
      // İç içe süslü parantezleri say: `${a ? {x:1}.x : b}` gibi bir ifade de
      // doğru yerde bitsin.
      let derinlik = 1;
      i += 2;
      while (i < kaynak.length && derinlik > 0) {
        if (kaynak[i] === '{') derinlik += 1;
        else if (kaynak[i] === '}') derinlik -= 1;
        i += 1;
      }
      desen += '.+';
      continue;
    }
    desen += karakter.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    i += 1;
  }
  return null;
}

/** Kaynaktaki `cagri(` çağrılarının İLK argüman literallerini desen olarak
 *  toplar. `test(...)`, `test.describe.serial(...)` gibi biçimler için. */
function baslikDesenleri(kaynak: string, cagrilar: readonly string[]): RegExp[] {
  const desenler: RegExp[] = [];
  for (const cagri of cagrilar) {
    let konum = kaynak.indexOf(`${cagri}(`);
    while (konum !== -1) {
      const oncekiKarakter = konum === 0 ? '' : kaynak[konum - 1];
      // `expect(...)` içindeki `test(` gibi alt dizeleri ele: çağrı adı bir
      // tanımlayıcı karakterinin ardından geliyorsa bu başka bir çağrıdır.
      if (!/[\w$.]/.test(oncekiKarakter)) {
        const desen = literalDeseni(kaynak, konum + cagri.length + 1);
        if (desen) desenler.push(desen);
      }
      konum = kaynak.indexOf(`${cagri}(`, konum + 1);
    }
  }
  return desenler;
}

const TEST_CAGRILARI = ['test', 'test.skip', 'test.only', 'test.fixme', 'rawTest'] as const;
const DESCRIBE_CAGRILARI = [
  'test.describe',
  'test.describe.serial',
  'test.describe.only',
  'test.describe.serial.only',
  'rawTest.describe',
] as const;

/** App.tsx'in rota beyanı — KAYNAĞIN YAPISINDAN, metninden değil.
 *
 *  YALNIZ REGEX ARTIK YETMİYOR — ÖLÇÜLDÜ. Eski desen (`/<Route\s+path="([^"]+)"/g`)
 *  `path` özniteliğinin İLK SIRADA ve ÇİFT TIRNAKLI olmasına bağlıydı; sıra ters
 *  çevrilmiş ya da tek tırnaklı bir rota kapıdan sessizce kaçıyordu. Ayrıştırma
 *  artık TypeScript'in kendi AST'sinden okunur (bkz. `e2e/rota-ayristirici.ts`):
 *  AST'de öznitelik sırası ve tırnak biçimi diye bir kavram yoktur.
 *
 *  App.tsx'i ÇALIŞTIRMAK yine tercih edilmedi: ölçüm o zaman React Router'ın
 *  eşleme davranışına bağlanırdı. Sözleşmenin savunduğu şey KAYNAKTAKİ BEYAN —
 *  bir geliştirici dosyaya rota eklediğinde envanteri de güncellemek zorunda
 *  kalmalı. */
function appRotalari(): {
  rotalar: string[];
  toplamRouteEtiketi: number;
  duzenSayisi: number;
  indexSayisi: number;
  yakalayiciSayisi: number;
} {
  const sonuc = routeEtiketleriniAyristir(yol('src/App.tsx'), APP_KAYNAGI);
  return {
    rotalar: [...sonuc.rotalar],
    toplamRouteEtiketi: sonuc.hepsi.length,
    duzenSayisi: sonuc.duzenSayisi,
    indexSayisi: sonuc.indexSayisi,
    yakalayiciSayisi: sonuc.yakalayiciSayisi,
  };
}

/** `src/` altında `<Route` beyan eden TÜM dosyalar. */
function routeBeyanEdenDosyalar(): string[] {
  const bulunan: string[] = [];
  const tara = (dizin: string): void => {
    for (const girdi of readdirSync(dizin)) {
      const mutlak = join(dizin, girdi);
      if (statSync(mutlak).isDirectory()) {
        tara(mutlak);
        continue;
      }
      // Birim testleri kendi `MemoryRouter` ağaçlarını kurar; uygulamanın rota
      // beyanı değildirler ve kapsam kapısının konusu da değiller.
      if (!/\.(tsx|jsx)$/.test(girdi) || /\.(test|spec)\.(tsx|jsx)$/.test(girdi)) continue;
      if (metindekiRouteEtiketSayisi(readFileSync(mutlak, 'utf8')) > 0) {
        bulunan.push(mutlak.slice(FRONTEND_KOKU.length + 1).split('\\').join('/'));
      }
    }
  };
  tara(yol('src'));
  return bulunan.sort();
}

const ENVANTER_ROTALARI = ROTA_ENVANTERI.map(girdi => girdi.rota);

describe('rota kapsam sözleşmesi', () => {
  it('G1: App.tsx rota kümesi ile envanter rota kümesi İKİ YÖNDE de eşit', () => {
    const {rotalar, toplamRouteEtiketi, duzenSayisi, indexSayisi, yakalayiciSayisi} = appRotalari();

    // ÖNCE AYRIŞTIRMANIN KENDİSİ. Ayrıştırıcı fail-closed'dır (anlamadığı bir
    // `<Route>` biçiminde İSTİSNA fırlatır), ama tasnif toplamı yine de
    // yazılır: her `<Route>` etiketi tam olarak BİR kovaya düşmeli.
    expect(
      rotalar.length + duzenSayisi + yakalayiciSayisi,
      'App.tsx ayrıştırması eksik: <Route etiketi sayısı, tasniflerin toplamına eşit değil',
    ).toBe(toplamRouteEtiketi);
    // Kaynaktaki `<Route` GEÇİŞ sayısı ile AST'nin bulduğu etiket sayısı da
    // ayrışmamalı: ayrışıyorsa bir etiket yorum içinde ya da ayrıştırıcının
    // görmediği bir yerdedir ve kapı kör kalmış olur.
    expect(
      metindekiRouteEtiketSayisi(APP_KAYNAGI),
      'App.tsx içindeki `<Route` geçiş sayısı AST sayımıyla tutmuyor',
    ).toBe(toplamRouteEtiketi);
    // `index` rotası tam olarak `/`yi üretir ve `path="*"` bir rota DEĞİL,
    // yakalayıcıdır (`<Navigate to="/">`); ikisinin ayrımı korunur.
    expect(indexSayisi, 'App.tsx bir `index` rotası bildirmeli (Pano)').toBeGreaterThan(0);
    expect(rotalar.filter(rota => rota === '/').length).toBe(indexSayisi);
    expect(rotalar, '`path="*"` rota sayılamaz').not.toContain('*');
    expect(yakalayiciSayisi, 'App.tsx `path="*"` yakalayıcısını bildirmeli').toBe(1);

    // ROTA BEYANI TEK DOSYADA. Ayrıştırıcı App.tsx'i okur; başka bir dosyada
    // açılan bir `<Route>` ağacı kapının GÖRÜŞ ALANI DIŞINDA kalırdı.
    expect(
      routeBeyanEdenDosyalar(),
      'rota beyanı yalnız src/App.tsx içinde olmalı; başka bir dosyadaki <Route> ağacı kapsam kapısının görüş alanı dışında kalır',
    ).toEqual(['src/App.tsx']);

    const appKumesi = [...new Set(rotalar)].sort();
    const envanterKumesi = [...new Set(ENVANTER_ROTALARI)].sort();

    const envanterdeEksik = appKumesi.filter(rota => !envanterKumesi.includes(rota));
    const appteEksik = envanterKumesi.filter(rota => !appKumesi.includes(rota));

    expect(
      envanterdeEksik,
      'App.tsx bu rotaları tanımlıyor ama envanterde yoklar; her rota kapi/spec/muaf tasniflerinden BİRİNİ almalı',
    ).toEqual([]);
    expect(
      appteEksik,
      'envanter bu rotaları bildiriyor ama App.tsx onları tanımlamıyor; ölü kayıt kapsamı şişirir',
    ).toEqual([]);
    expect(appKumesi.length).toBe(rotalar.length);
  });

  it('G2: parametreli (`:` taşıyan) rotaların TAMAMI envanterde açıkça temsil edilir', () => {
    const {rotalar} = appRotalari();
    const appParametreli = rotalar.filter(rota => rota.includes(':')).sort();
    const envanterParametreli = ENVANTER_ROTALARI.filter(rota => rota.includes(':')).sort();

    // Sayı BURADA donmuş bir taban değil, App.tsx'ten OKUNAN bir olgudur; iki
    // taraf da aynı kaynaktan türer ve eşitlik zorlanır.
    expect(envanterParametreli).toEqual(appParametreli);
    expect(
      appParametreli.length,
      'parametreli rota sayısı App.tsx ile envanter arasında ayrıştı',
    ).toBe(envanterParametreli.length);
    // Hiçbiri sessizce `muaf`a kaçamaz: her biri bir tasnif ve — muafsa —
    // gerekçe taşır. Bu, aşağıdaki G5/G6 ile birlikte zorlanır.
    for (const rota of envanterParametreli) {
      const girdi = ROTA_ENVANTERI.find(aday => aday.rota === rota);
      expect(girdi, `${rota} envanterde yok`).toBeDefined();
      expect(['kapi', 'spec', 'muaf']).toContain(girdi?.tur);
    }
  });

  it('G3: her `spec` tasnifi GERÇEK bir dosyayı ve o dosyada GERÇEK bir testi adlandırır', () => {
    const kusurlar: string[] = [];
    for (const girdi of SPEC_GIRDILERI) {
      const mutlak = yol(girdi.dosya);
      if (!existsSync(mutlak)) {
        kusurlar.push(`${girdi.rota}: dosya YOK -> ${girdi.dosya}`);
        continue;
      }
      const kaynak = readFileSync(mutlak, 'utf8');
      const parcalar = girdi.testAdi.split(' > ');
      const testBasligi = parcalar[parcalar.length - 1];
      const describeBasliklari = parcalar.slice(0, -1);

      const testDesenleri = baslikDesenleri(kaynak, TEST_CAGRILARI);
      if (!testDesenleri.some(desen => desen.test(testBasligi))) {
        kusurlar.push(`${girdi.rota}: ${girdi.dosya} içinde "${testBasligi}" adlı test BEYAN EDİLMEMİŞ`);
      }
      const describeDesenleri = baslikDesenleri(kaynak, DESCRIBE_CAGRILARI);
      for (const baslik of describeBasliklari) {
        if (!describeDesenleri.some(desen => desen.test(baslik))) {
          kusurlar.push(`${girdi.rota}: ${girdi.dosya} içinde "${baslik}" adlı describe BEYAN EDİLMEMİŞ`);
        }
      }
      expect(girdi.gerekce.trim().length, `${girdi.rota}: gerekçe boş`).toBeGreaterThan(0);
    }
    expect(kusurlar).toEqual([]);
  });

  it('G4: `kapi` işaretleri boş DEĞİL ve BİRİCİK', () => {
    const bos = KAPI_GIRDILERI.filter(girdi => girdi.isaret.trim().length === 0).map(g => g.rota);
    expect(bos, 'işaretsiz kapı hiçbir şey ölçmez: boş ekran da konsol-temizdir').toEqual([]);

    const isaretler = KAPI_GIRDILERI.map(girdi => girdi.isaret);
    const yinelenen = [...new Set(isaretler.filter((isaret, i) => isaretler.indexOf(isaret) !== i))];
    expect(
      yinelenen,
      'aynı işaret iki rotada kullanılamaz: biri diğerinin ekranında da görünüyor olabilir ve kapı yanlış sayfayı ölçer',
    ).toEqual([]);

    for (const girdi of KAPI_GIRDILERI) {
      expect(['oturumlu', 'anonim']).toContain(girdi.oturum);
    }
  });

  it('G5: `muaf` kümesi DONMUŞ ve gerekçeleri boşaltılamaz', () => {
    // DONMUŞ KÜME. Muafiyet kapsamın ölçülmüş SINIRIdır; büyümesi bilinçli bir
    // karar olmalı, sessiz bir eksilme değil. Bu liste değişecekse buradaki
    // beyan da elle değiştirilmelidir — ve o değişiklik incelemede görünür.
    const BEKLENEN_MUAF = [
      '/depo-transferleri/:id',
      '/depolar/:id',
      '/hayvancilik/hayvanlar/:id',
      '/saha/:id',
      '/stok-sayimlari/:id',
      '/tedarikciler/:id',
      '/urunler/:id',
      '/yedekler',
    ];
    expect(MUAF_GIRDILERI.map(girdi => girdi.rota).sort()).toEqual(BEKLENEN_MUAF);

    // GEREKÇE BOŞALTILAMAZ. #8'de bu koruma `rota-render-kapisi.spec.ts`
    // içindeki donmuş `KAPSANMAYAN` sözlüğüydü; mekanizma envantere taşındı,
    // koruma taşınmadan bırakılmadı.
    for (const girdi of MUAF_GIRDILERI) {
      expect(girdi.gerekce.trim().length, `${girdi.rota}: gerekçe boş`).toBeGreaterThan(20);
    }
    expect(
      MUAF_GIRDILERI.find(girdi => girdi.rota === '/yedekler')?.gerekce,
      "/yedekler gerekçesi #8'de ölçüldü; metni değişecekse ölçüm de yenilenmeli",
    ).toBe(
      "platform operatörü ortam değişkeni e2e sunucusunda kurulmuyor; rota Protected içinde /'a düşer",
    );
  });

  it('G6: bir rota BİRDEN FAZLA tasnifte görünemez', () => {
    const yinelenen = [
      ...new Set(ENVANTER_ROTALARI.filter((rota, i) => ENVANTER_ROTALARI.indexOf(rota) !== i)),
    ];
    expect(
      yinelenen,
      'aynı rota iki girdi taşıyor: hangi tasnifin geçerli olduğu belirsiz kalır',
    ).toEqual([]);

    // Üç liste envanteri BÖLER: kesişim boş, birleşim tam.
    expect(KAPI_GIRDILERI.length + SPEC_GIRDILERI.length + MUAF_GIRDILERI.length).toBe(
      ROTA_ENVANTERI.length,
    );
    const kapiKumesi = new Set(KAPI_GIRDILERI.map(girdi => girdi.rota));
    const specKumesi = new Set(SPEC_GIRDILERI.map(girdi => girdi.rota));
    const muafKumesi = new Set(MUAF_GIRDILERI.map(girdi => girdi.rota));
    for (const rota of ENVANTER_ROTALARI) {
      const sayi =
        (kapiKumesi.has(rota) ? 1 : 0) + (specKumesi.has(rota) ? 1 : 0) + (muafKumesi.has(rota) ? 1 : 0);
      expect(sayi, `${rota} tam olarak BİR tasnifte olmalı`).toBe(1);
    }
  });

  it('G7: kapsam raportörü playwright.config.ts içinde BİLDİRİLMİŞ', () => {
    const raportorYolu = './e2e/rota-kapsam-raportoru.ts';
    expect(
      existsSync(yol('e2e/rota-kapsam-raportoru.ts')),
      'raportör dosyası yok',
    ).toBe(true);

    const bildirimSayisi = PLAYWRIGHT_YAPILANDIRMASI.split(raportorYolu).length - 1;
    expect(
      bildirimSayisi,
      'raportör CI ve yerel raportör listelerinin İKİSİNDE de bildirilmeli; yalnız birinde olması, yerelde yeşil görünen bir dalın CI\'da kırmızı olması demektir',
    ).toBeGreaterThanOrEqual(2);

    // Var olan raportörler yerinde kalmalı: kapsam raportörü onların YERİNE
    // geçmez, yanlarına eklenir.
    expect(PLAYWRIGHT_YAPILANDIRMASI).toContain("['list']");
    expect(PLAYWRIGHT_YAPILANDIRMASI).toContain("['html', {open: 'never'}]");

    // Raportörün ölçtüğü kapı dosyası da gerçekten var olmalı.
    expect(
      existsSync(yol(KAPSAM_KAPISI_DOSYASI)),
      `kapsam kapısı dosyası yok: ${KAPSAM_KAPISI_DOSYASI}`,
    ).toBe(true);

    // RAPORTÖR DEVRE DIŞI BIRAKILAMAZ. Bu kapının kendi ölçülmüş boşluğu:
    // `--reporter=list` raportörü hiç yüklemeden süiti yeşil bitirebiliyordu ve
    // G7'nin saydığı yapılandırma metni değişmediği için o da yeşil kalıyordu.
    // Kapatan şey `globalTeardown`dur: yapılandırmadan gelir, komut satırından
    // ezilemez ve raportörün bıraktığı makbuzu arar.
    expect(
      existsSync(yol('e2e/rota-kapsam-teardown.ts')),
      'koşum sonu kapısı dosyası yok',
    ).toBe(true);
    expect(
      PLAYWRIGHT_YAPILANDIRMASI,
      "raportör komut satırından kapatılabilir olmamalı: `globalTeardown` bildirilmemiş",
    ).toContain("globalTeardown: './e2e/rota-kapsam-teardown.ts'");
    // globalSetup yerinde kalmalı: teardown onun YERİNE geçmez, yanına eklenir.
    expect(PLAYWRIGHT_YAPILANDIRMASI).toContain("globalSetup: './e2e/global-setup.ts'");
    // Teardown makbuzu raportörden okur; ikisi aynı modülü paylaşmalı, yoksa
    // kapı iki ayrı yerde iki ayrı dosya adına bakar ve sessizce hiç ölçmez.
    const teardownKaynagi = oku('e2e/rota-kapsam-teardown.ts');
    const raportorKaynagi = oku('e2e/rota-kapsam-raportoru.ts');
    expect(teardownKaynagi).toContain("from './rota-kapsam-makbuzu'");
    expect(raportorKaynagi).toContain("from './rota-kapsam-makbuzu'");
    expect(
      raportorKaynagi,
      'makbuz `onBegin` içinde yazılmalı: `globalTeardown` raportörün `onEnd`inden ÖNCE koşar',
    ).toContain('makbuzYaz()');
  });

  it('G8: `kapi` işareti sayfa GÖVDESİNDE aranır — kenar çubuğu metni kabul edilmez', () => {
    // ÖLÇÜLEN BOŞLUK. İşaret daha önce `page.getByText(...)` ile SAYFANIN
    // TAMAMINDA aranıyordu. İşaretlerin çoğu aynı zamanda bir kenar çubuğu
    // etiketidir ve AppShell aktif grubu daima açık gösterir; gövde hiç
    // çizilmese bile kapı yeşil kalabiliyordu.
    const kapiKaynagi = oku(KAPSAM_KAPISI_DOSYASI);

    // 1. Kapı, işareti YALNIZ gövde kökünün altında arar.
    expect(
      kapiKaynagi,
      'kapı rota gövdesi kökünü kullanmalı',
    ).toContain('page.getByTestId(ROTA_GOVDESI_TESTID)');
    expect(
      kapiKaynagi.includes('page.getByText('),
      'kapıda `page.getByText(` KALMAMALI: kenar çubuğu/üst çubuk metni kapsam kanıtı değildir',
    ).toBe(false);
    // Genel bir kaçış yolu da bırakılmasın.
    expect(kapiKaynagi.includes('page.locator(')).toBe(false);
    expect(kapiKaynagi.includes('page.getByRole(')).toBe(false);

    // 2. Kök TAM OLARAK BİR kez bulunmalı — kapının kendi çalışma zamanı
    //    iddiası budur; burada o iddianın kaynakta durduğu ölçülür.
    expect(kapiKaynagi).toContain('.toHaveCount(1');

    // 3. Kök uygulama tarafında GERÇEKTEN bildirilmiş olmalı: oturumlu
    //    rotalarda AppShell'in `<Outlet/>`ü saran kutusunda, oturumsuz `kapi`
    //    rotalarında sayfa bileşeninin kendi kökünde.
    const nitelik = `data-testid="${ROTA_GOVDESI_TESTID}"`;
    const kabukKaynagi = oku('src/components/AppShell.tsx');
    expect(
      kabukKaynagi.split(nitelik).length - 1,
      `AppShell tam olarak BİR ${nitelik} bildirmeli`,
    ).toBe(1);
    expect(
      kabukKaynagi,
      'gövde kökü `<Outlet/>`ü saran kutuda olmalı',
    ).toContain('<Outlet/>');

    const OTURUMSUZ_KOK_DOSYALARI: Record<string, string> = {
      '/sifremi-unuttum': 'src/pages/ForgotPassword.tsx',
      '/sifre-sifirla': 'src/pages/ResetPassword.tsx',
    };
    for (const girdi of KAPI_GIRDILERI) {
      if (girdi.oturum !== 'anonim') continue;
      const dosya = OTURUMSUZ_KOK_DOSYALARI[girdi.rota];
      expect(
        dosya,
        `${girdi.rota} oturumsuz bir kapı ama gövde kökünü taşıyan sayfası bildirilmemiş; ` +
          'AppShell bu rotada çizilmez, kökü sayfa bileşeni taşımalı',
      ).toBeDefined();
      expect(
        oku(dosya).split(nitelik).length - 1,
        `${dosya} tam olarak BİR ${nitelik} bildirmeli`,
      ).toBe(1);
    }
  });

  it('G9: rota ayrıştırıcısı öznitelik SIRASINDAN ve TIRNAK biçiminden bağımsız', () => {
    // Ayrıştırıcının kendi sözleşmesi. G1 App.tsx'i ölçer; burada ölçülen şey
    // ÖLÇÜM ARACININ kendisi — eski regex tam olarak bu üç biçimde kör kalıyordu.
    const ornek = [
      '<Routes>',
      '  <Route path="ilk" element={<A/>}/>',
      '  <Route element={<B/>} path="sira-ters"/>',
      "  <Route path='tek-tirnak' element={<C/>}/>",
      '  <Route path={"suslu-parantez"} element={<D/>}/>',
      '  <Route element={<Duzen/>}>',
      '    <Route index element={<E/>}/>',
      '    <Route path="ic-ice" element={<F/>}/>',
      '  </Route>',
      '  <Route path="*" element={<Navigate to="/" replace/>}/>',
      '</Routes>',
    ].join('\n');
    const sonuc = routeEtiketleriniAyristir('ornek.tsx', `const X = () => ${ornek};`);
    expect([...sonuc.rotalar].sort()).toEqual(
      ['/', '/ic-ice', '/ilk', '/sira-ters', '/suslu-parantez', '/tek-tirnak'].sort(),
    );
    expect(sonuc.yakalayiciSayisi).toBe(1);
    expect(sonuc.duzenSayisi).toBe(1);
    expect(sonuc.indexSayisi).toBe(1);

    // FAIL-CLOSED: çözülemeyen bir `path` sessizce ATLANMAZ.
    expect(() =>
      routeEtiketleriniAyristir('ornek.tsx', 'const X = () => <Route path={YOL} element={<A/>}/>;'),
    ).toThrow(/ROTA AYRIŞTIRILAMADI/);
    expect(() =>
      routeEtiketleriniAyristir('ornek.tsx', 'const X = () => <Route {...tanim}/>;'),
    ).toThrow(/ROTA AYRIŞTIRILAMADI/);

    // FAIL-CLOSED: createElement(Route, ...) ve useRoutes([...]).
    // Mimari kural: uygulama rota beyanları YALNIZ src/App.tsx içindeki JSX
    // <Route> hiyerarşisi olabilir. Bu iki biçim kapsam kapısının görüş
    // alanının tamamen dışında kalır; ayrıştırıcı onları sessizce geçemez.
    //
    // ÖLÇÜLEN KAÇIŞ (Cursor final runtime review, cc10c27 sonrası):
    // React.createElement(Route, {path: 'kacak', element: <Login/>}) şeklinde
    // eklenen bir rota G1'i YEŞIL bırakıyordu çünkü AST gezgini yalnız JSX
    // açma etiketlerini arıyordu; CallExpression düğümleri görünmüyordu.
    expect(() =>
      routeEtiketleriniAyristir(
        'ornek.tsx',
        "const X = () => React.createElement(Route, {path: 'mutasyon-create-element', element: null});",
      ),
    ).toThrow(/ROTA AYRIŞTIRILAMADI/);
    expect(() =>
      routeEtiketleriniAyristir(
        'ornek.tsx',
        "const X = () => { return useRoutes([{path: 'mutasyon-use-routes', element: null}]); };",
      ),
    ).toThrow(/ROTA AYRIŞTIRILAMADI/);
  });

  it('G10: ziyaret kanıtı — olumsuz yönlendirme kapsam sayılmaz, `:id` deseni çözülür', () => {
    const kayit = (
      yol: string,
      kaynak: ZiyaretKaydi['kaynak'],
      etkilesim = false,
      sekme = 0,
    ): ZiyaretKaydi => ({sekme, yol, kaynak, etkilesim});
    const yollar = (kayitlar: readonly ZiyaretKaydi[]): string[] =>
      kaliciZiyaretler(kayitlar).map(girdi => girdi.yol);

    // OLUMSUZ YÖNLENDİRME: rotaya varıldı, sayfayla HİÇ etkileşilmeden
    // `replace` ile düşürüldü. Kapsam kanıtı DEĞİLDİR.
    expect(
      yollar([kayit('/nakit-yonetimi', 'yukleme'), kayit('/', 'replace')]),
      'izin duvarına çarpıp düşen rota kapsanmış sayılamaz',
    ).toEqual(['/']);

    // GERÇEK ZİYARET: rotada iş yapıldı (form dolduruldu, düğmeye basıldı) ve
    // uygulama BUNUN ARDINDAN `replace` etti. Ziyaret gerçektir.
    expect(
      yollar([
        kayit('/giris', 'yukleme'),
        kayit('/sifre-degistir', 'replace', true),
        kayit('/', 'replace', true),
      ]),
    ).toEqual(['/giris', '/sifre-degistir', '/']);

    // `push` bir öncekini geçersiz KILMAZ; aynı yola `replace` de kılmaz.
    expect(yollar([kayit('/alislar', 'yukleme'), kayit('/satislar', 'push')])).toEqual([
      '/alislar',
      '/satislar',
    ]);
    expect(yollar([kayit('/faturalar', 'yukleme'), kayit('/faturalar', 'replace')])).toEqual([
      '/faturalar',
      '/faturalar',
    ]);

    // Sekmeler AYRI geçmişlerdir: ikinci sekmedeki bir `replace`, birinci
    // sekmedeki ziyareti düşürmez.
    expect(
      yollar([kayit('/saha', 'yukleme', false, 1), kayit('/', 'replace', false, 0)]),
    ).toEqual(['/saha', '/']);

    // DESEN ÇÖZÜMÜ: gerçek URL rota desenine indirgenir; sabit segment
    // parametreye yeğlenir; bilinmeyen yol kanıt üretmez.
    const desenler = ROTA_ENVANTERI.map(girdi => girdi.rota);
    expect(yoluRotayaCoz('/musteriler/7', desenler)).toBe('/musteriler/:id');
    expect(yoluRotayaCoz('/musteriler', desenler)).toBe('/musteriler');
    expect(yoluRotayaCoz('/tarla/parseller/12', desenler)).toBe('/tarla/parseller/:id');
    expect(yoluRotayaCoz('/', desenler)).toBe('/');
    expect(yoluRotayaCoz('/boyle-bir-rota-yok', desenler)).toBeNull();
    expect(yoluRotayaCoz('/tarla/ciftlikler', desenler)).toBe('/tarla/ciftlikler');
    expect(
      yoluRotayaCoz('/depolar/9', ['/depolar/:id', '/depolar/yeni']),
      'sabit segment parametreye yeğlenmeli ama yalnız GERÇEKTEN tutuyorsa',
    ).toBe('/depolar/:id');
    expect(yoluRotayaCoz('/depolar/yeni', ['/depolar/:id', '/depolar/yeni'])).toBe(
      '/depolar/yeni',
    );
  });

  it('G11: src/ altında `useRoutes` ve `createElement(Route, ...)` beyanı yasak', () => {
    // MİMARİ KURAL — yorumda bırakılmaz, makine tarafından zorunlu kılınır.
    // Uygulama rota beyanları YALNIZ src/App.tsx içindeki JSX <Route>
    // hiyerarşisi olabilir. Aşağıdaki biçimler kapsam kapısının görüş
    // alanının tamamen dışında kalır:
    //
    //   - React.createElement(Route, ...)   ← AST gezgini CallExpression'ı görmez
    //   - useRoutes([...])                  ← aynı kör nokta
    //   - başka src dosyasında <Route>      ← zaten G1'de routeBeyanEdenDosyalar()
    //
    // Tarama metin tabanlıdır (AST değil); yorumlar ve dize değişmezleri ayırt
    // edilmez. Bu kasıtlı bir seçimdir: bu kalıpların gerçek bir kaynak dosyada
    // metin olarak geçmesi zaten kural ihlalidir ve elle incelenmesini gerektirir.
    const ihlaller: string[] = [];
    const tara = (dizin: string): void => {
      for (const girdi of readdirSync(dizin)) {
        const mutlak = join(dizin, girdi);
        if (statSync(mutlak).isDirectory()) {
          tara(mutlak);
          continue;
        }
        if (
          !/\.(tsx|jsx|ts|js)$/.test(girdi) ||
          /\.(test|spec)\.(tsx|jsx|ts|js)$/.test(girdi)
        ) {
          continue;
        }
        const kaynak = readFileSync(mutlak, 'utf8');
        if (yasakBeyanVarMi(kaynak)) {
          ihlaller.push(mutlak.slice(FRONTEND_KOKU.length + 1).split('\\').join('/'));
        }
      }
    };
    tara(yol('src'));
    expect(
      ihlaller,
      'src/ altında yasaklı rota beyan biçimi bulundu. ' +
        'Uygulama rota beyanları yalnız src/App.tsx içindeki JSX `<Route>` ' +
        'hiyerarşisi olabilir. `React.createElement(Route, ...)` ve ' +
        '`useRoutes([...])` kapsam kapısının görüş alanı dışındadır.',
    ).toEqual([]);
  });
});
