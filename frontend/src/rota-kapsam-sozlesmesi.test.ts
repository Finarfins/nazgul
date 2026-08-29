import {existsSync, readFileSync} from 'node:fs';
import {resolve} from 'node:path';

import {describe, expect, it} from 'vitest';

import {
  KAPI_GIRDILERI,
  KAPSAM_KAPISI_DOSYASI,
  MUAF_GIRDILERI,
  ROTA_ENVANTERI,
  SPEC_GIRDILERI,
} from '../e2e/rota-envanteri';

// ROTA KAPSAM SÖZLEŞMESİ — statik yarısı (G1-G7).
//
// İŞ BÖLÜMÜ. Bu dosya envanterin App.tsx ile ve diskteki spec dosyalarıyla
// TUTARLI olduğunu ölçer; envanterin bildirdiği testlerin gerçekten KOŞTUĞUNU ve
// GEÇTİĞİNİ ölçen şey `e2e/rota-kapsam-raportoru.ts`dir (R1-R4). İkisi ayrı
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

/** App.tsx'ten adlandırılmış rota kümesi.
 *
 *  YALNIZ REGEX, BİLEREK: App.tsx'i çalıştırıp `Routes` ağacını gezmek jsdom'da
 *  mümkündü ama ölçüm o zaman React Router'ın eşleme davranışına bağlanırdı.
 *  Sözleşmenin savunduğu şey KAYNAKTAKİ BEYAN: bir geliştirici dosyaya rota
 *  eklediğinde envanteri de güncellemek zorunda kalmalı. */
function appRotalari(): {rotalar: string[]; toplamRouteEtiketi: number; elementOlanlar: number} {
  const yollar = [...APP_KAYNAGI.matchAll(/<Route\s+path="([^"]+)"/g)].map(eslesme => eslesme[1]);
  const indexSayisi = [...APP_KAYNAGI.matchAll(/<Route\s+index\b/g)].length;
  const elementOlanlar = [...APP_KAYNAGI.matchAll(/<Route\s+element=/g)].length;
  const toplamRouteEtiketi = [...APP_KAYNAGI.matchAll(/<Route\b/g)].length;
  const rotalar = yollar
    // `path="*"` bir rota DEĞİL, yakalayıcıdır: `<Navigate to="/">` ile ana
    // sayfaya düşürür ve ekran çizmez.
    .filter(yol => yol !== '*')
    .map(yol => (yol.startsWith('/') ? yol : `/${yol}`));
  for (let i = 0; i < indexSayisi; i += 1) rotalar.push('/');
  return {rotalar, toplamRouteEtiketi, elementOlanlar};
}

const ENVANTER_ROTALARI = ROTA_ENVANTERI.map(girdi => girdi.rota);

describe('rota kapsam sözleşmesi', () => {
  it('G1: App.tsx rota kümesi ile envanter rota kümesi İKİ YÖNDE de eşit', () => {
    const {rotalar, toplamRouteEtiketi, elementOlanlar} = appRotalari();

    // ÖNCE AYRIŞTIRMANIN KENDİSİ. Regex sessizce bir `<Route` biçimini
    // kaçırırsa kapı "eşit" deyip hiçbir şey ölçmemiş olurdu. Her `<Route`
    // etiketi ya adlandırılmış bir rotadır ya da `element=` taşıyan düzen
    // rotasıdır; toplam tutmuyorsa App.tsx yeni bir biçim kullanıyor demektir
    // ve ayrıştırıcı ONA göre güncellenmeli.
    expect(
      rotalar.length + elementOlanlar + 1,
      'App.tsx ayrıştırması eksik: <Route etiketi sayısı adlandırılmış rota + düzen rotası + `path="*"` toplamına eşit değil',
    ).toBe(toplamRouteEtiketi);

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
  });
});
