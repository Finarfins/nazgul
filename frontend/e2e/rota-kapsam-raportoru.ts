// ROTA KAPSAM RAPORTÖRÜ — sözleşmenin ÇALIŞMA ZAMANI yarısı.
//
// NEDEN RAPORTÖR, NEDEN BİR TEST DEĞİL. Bir test yalnız kendi gövdesini bilir;
// "hangi testler koştu, hangileri atlandı, hangileri geçti" sorusunu yalnız
// koşunun TAMAMINI gören bir raportör yanıtlayabilir. #8'in KABUL EDİLEN ARTIK
// bölümünde adı konan iki açık nokta tam olarak buydu: envanterden bir rota
// eksiltmek (30 test kaldı, YEŞİL) ve döngüye `test.skip` koymak (28 atlandı,
// 3 geçti, YEŞİL). Statik bir test bu ikisini göremez; raportör görür.
//
// ALTI ÖLÇÜM:
//   R1 — envanterdeki HER `kapi` girdisinin ürettiği test başlığı koşuda VAR.
//        Envanterden rota düşerse testi de düşer; başlık kaybolur, R1 kırmızı.
//   R2 — o testlerin HİÇBİRİ atlanmadı. `test.skip` ile susturulan bir kapı,
//        susturulmamış bir kapıdan ayırt edilebilir olmalı.
//   R3 — her `spec` tasnifinin adlandırdığı test koşuda VAR ve GEÇTİ. Envantere
//        bir dosya/test adı yazmak, o testin varlığını kanıtlamaz; koşu kanıtlar.
//   R4 — kapsam kapısının KENDİSİ koşuda yoksa sözleşme KAPALI DÜŞER. Kapıyı
//        silerek ya da içini boşaltarak yeşile dönmek mümkün olmamalı.
//   R5 — her `spec` tasnifinin adlandırdığı test o rotayı GERÇEKTEN ZİYARET
//        ETTİ. R3'e kadar rota ile test arasında ÇALIŞMA ZAMANI bağı yoktu:
//        `/alislar` girdisini, koşan ve geçen ama yalnız `/` açan bir teste
//        bağlamak G3'ü de R3'ü de yeşil bırakıyordu. Kanıt artık tarayıcının
//        kendi gezinti olaylarından gelir (bkz. `rota-ziyaret-kaydi.ts`).
//   R6 — üretilen her `kapi` testi de kendi rotasını ziyaret etmiş olmalı. Aynı
//        ölçüm, aynı kanıt; kapı testinin gövdesindeki pathname iddiasının
//        yanında ikinci ve BAĞIMSIZ bir kayıt.
//
// ZİYARET KANITI NASIL OKUNUR. Her test, `helpers.ts`teki bağlam fixture'ı
// sayesinde gezinti kaydını bir testInfo EKİ olarak bırakır. Raportör kaydı
// okur, "olumsuz yönlendirme" ile biten ziyaretleri eler (bkz.
// `kaliciZiyaretler`) ve kalan her yolu envanterin rota DESENLERİNE indirger —
// böylece `/musteriler/7` ziyareti `/musteriler/:id` girdisini kanıtlar.
//
// ÇOKLU KAPSAM AÇIK YAZILIR. Bir test birden fazla `spec` rotasını kapsıyorsa
// (touch-targets'ın mobil toplu kapıları tam olarak bunu yapar) özet o testi ve
// kanıtladığı rotaların TAMAMINI listeler; kapsam tek satıra saklanmaz.
//
// DARALTILMIŞ KOŞUM SORUNU — VE NASIL ÇÖZÜLDÜĞÜ.
// CI, Playwright'ı İKİ kez çağırır: bir kez tam süit, bir kez de yalnız
// `e2e/touch-targets.spec.ts` (tahsis motoru AÇIKKEN). İkinci çağrıda kapsam
// kapısı koşuya HİÇ girmez ve bu MEŞRUDUR. Raportör bu meşru daralmayı, kapının
// SESSİZCE KAYBOLMASINDAN ayırmak zorunda. Ayrım diskten okunur:
//
//   eksik = (diskteki *.spec.ts dosyaları) \ (koşuda testi olan dosyalar)
//     * eksik BOŞ                       -> tam koşum, sözleşme KURULU (armed)
//     * eksik == {kapsam kapısı}        -> HER ŞEY koştu ama kapı koşmadı:
//                                          kapı silinmiş/boşaltılmış/yeniden
//                                          adlandırılmış demektir -> R4 KIRMIZI
//     * eksik, kapıdan BAŞKA dosya da içeriyor -> gerçekten daraltılmış koşum;
//                                          sözleşme KURULMAZ ve bunu SÖYLER
//
// Bu kural `--list`, `--grep` ve tek dosya çağrılarını doğru sınıflar; `--grep`
// kapı testlerini eleyip diğer her dosyadan bir test bırakırsa eksik == {kapı}
// olur ve kapı KIRMIZI yanar — istenen davranış budur.
//
// ÖLÇÜLMEYEN (dürüstçe): raportör Playwright'ın gördüğü koşuyu ölçer. CI'ın o
// koşuyu gerçekten çağırdığını ölçmez — o, ci.yml'in ve `test_ci_gates.py`nin
// işidir ve bu değişiklik ikisine de dokunmaz.

import {existsSync, readdirSync, statSync} from 'node:fs';
import {dirname, join, relative, resolve, sep} from 'node:path';

import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from '@playwright/test/reporter';

import {makbuzYaz} from './rota-kapsam-makbuzu';
import {
  KAPI_GIRDILERI,
  KAPSAM_KAPISI_DOSYASI,
  MUAF_GIRDILERI,
  ROTA_ENVANTERI,
  SPEC_GIRDILERI,
  SPEC_RENDER_GIRDILERI,
  kapiTestBasligi,
} from './rota-envanteri';
import {ROTA_RENDER_EKI} from './rota-render-kaniti';
import {
  ZIYARET_EKI,
  kaliciZiyaretler,
  yoluRotayaCoz,
  type ZiyaretKaydi,
} from './rota-ziyaret-kaydi';

/** Playwright'ın `--list` kipinde hiçbir test KOŞMAZ; ölçülecek sonuç da yoktur.
 *  Bu kipte raportör susar — aksi halde her listeleme "hiçbir kapı testi
 *  geçmedi" diye kırmızı olurdu ve bu, ölçüm değil gürültü olurdu. */
const LISTELEME_KIPI = process.argv.includes('--list');

/** Bir testin, iç içe `describe` başlıklarını da içeren TAM adı. Envanterdeki
 *  `testAdi` ile birebir aynı biçim: ` > ` ile birleştirilmiş yol. */
function tamBaslik(test: TestCase): string {
  const parcalar: string[] = [test.title];
  let ust: Suite | undefined = test.parent;
  while (ust && ust.type === 'describe') {
    parcalar.unshift(ust.title);
    ust = ust.parent;
  }
  return parcalar.join(' > ');
}

/** `frontend/` köküne göre, ayırıcısı DAİMA `/` olan dosya yolu. Envanterdeki
 *  yollar da bu biçimde yazılır; Windows ile Linux arasında ayrışmasın. */
function koke_gore(kok: string, mutlakYol: string): string {
  return relative(kok, mutlakYol).split(sep).join('/');
}

/**
 * Envanterdeki yolların dayandığı kök: `frontend/`.
 *
 * `config.rootDir` KULLANILAMAZ — ÖLÇÜLDÜ. Playwright `rootDir`i, projelerin
 * `testDir`lerinin ORTAK ATASI olarak hesaplar; bu depoda iki projenin de
 * `testDir`i `./e2e` olduğu için `rootDir` = `frontend/e2e` çıkıyor ve o köke
 * göre alınan görece yollar `e2e/` önekini KAYBEDİYOR. İlk koşumda tam olarak bu
 * oldu: R1/R3/R4 63 ihlal saydı, oysa kusur ölçülen ağaçta değil ÖLÇÜMDEYDİ.
 * Yapılandırma dosyasının dizini bu belirsizliği taşımaz.
 */
function frontendKoku(config: FullConfig): string {
  if (config.configFile) return dirname(config.configFile);
  let aday = resolve(config.rootDir);
  for (;;) {
    if (existsSync(join(aday, 'package.json'))) return aday;
    const ust = dirname(aday);
    if (ust === aday) return resolve(config.rootDir);
    aday = ust;
  }
}

/** Projelerin `testDir`lerinin altındaki tüm `*.spec.ts` dosyaları. */
function disktekiSpecDosyalari(config: FullConfig, kok: string): Set<string> {
  const dizinler = new Set<string>();
  for (const proje of config.projects) {
    if (proje.testDir) dizinler.add(resolve(proje.testDir));
  }
  const bulunan = new Set<string>();
  const tara = (dizin: string): void => {
    if (!existsSync(dizin)) return;
    for (const girdi of readdirSync(dizin)) {
      const yol = join(dizin, girdi);
      if (statSync(yol).isDirectory()) tara(yol);
      else if (girdi.endsWith('.spec.ts')) bulunan.add(koke_gore(kok, yol));
    }
  };
  for (const dizin of dizinler) tara(dizin);
  return bulunan;
}

interface KosanTest {
  readonly dosya: string;
  readonly baslik: string;
  readonly sonuc: ReturnType<TestCase['outcome']>;
  /** Bu testin bıraktığı gezinti kaydından çözülmüş rota DESENLERİ. */
  readonly ziyaretler: ReadonlySet<string>;
  /** Kayıt hiç bulunamadıysa `false` — ölçümsüzlük, "ziyaret yok" değil. */
  readonly kayitVar: boolean;
  /** Bu test render kanıtı (ROTA_RENDER_EKI) bıraktı mı — RENDER KONTRATINDAKİ
   *  girdilerin R5 ölçümünün ikinci yarısı. */
  readonly renderVar: boolean;
}

/** Envanterdeki bütün rota desenleri — ziyaret çözümlemesinin sözlüğü. */
const ROTA_DESENLERI: readonly string[] = ROTA_ENVANTERI.map(girdi => girdi.rota);

/** Bir testin BÜTÜN denemelerinden (retry dahil) gezinti kayıtlarını toplar. */
function ziyaretKayitlari(sonuclar: readonly TestResult[]): ZiyaretKaydi[] | null {
  let bulundu = false;
  const hepsi: ZiyaretKaydi[] = [];
  for (const sonuc of sonuclar) {
    for (const ek of sonuc.attachments) {
      if (ek.name !== ZIYARET_EKI || !ek.body) continue;
      bulundu = true;
      try {
        const cozulen = JSON.parse(ek.body.toString('utf8')) as ZiyaretKaydi[];
        hepsi.push(...cozulen);
      } catch {
        // Bozuk bir ek, kayıt YOK demektir; kapı kapalı düşsün diye yutulmaz.
        return null;
      }
    }
  }
  return bulundu ? hepsi : null;
}

/** Bir test herhangi bir izinde render kanıtı (ROTA_RENDER_EKI) bıraktı mı.
 *
 *  R5'in RENDER KONTRATINDAKİ girdiler için istediği ikinci yarıyı ölçer.
 *  Attachment geçersiz JSON olsa bile yalnız VARLIĞI ölçülür: raportör kanıtın
 *  İÇERİĞİNİ değil, bırakılıp bırakılmadığını doğrular (içerik sözleşmesi
 *  rota-render-kaniti.ts'nin işidir). */
function renderKanitiVarMi(sonuclar: readonly TestResult[]): boolean {
  return sonuclar.some(sonuc =>
    sonuc.attachments.some(ek => ek.name === ROTA_RENDER_EKI && ek.body),
  );
}

/**
 * Bir testin KANITLADIĞI rota kümesi.
 *
 * İki eleme birlikte uygulanır: önce olumsuz yönlendirmeyle biten ziyaretler
 * düşer (`kaliciZiyaretler`), sonra kalan her yol envanterin desenlerine
 * indirgenir. Hiçbir desene oturmayan yol (ör. `/olmayan-sayfa`) kanıt değildir.
 */
function kanitlananRotalar(kayitlar: readonly ZiyaretKaydi[]): Set<string> {
  const kanit = new Set<string>();
  for (const ziyaret of kaliciZiyaretler(kayitlar)) {
    const desen = yoluRotayaCoz(ziyaret.yol, ROTA_DESENLERI);
    if (desen !== null) kanit.add(desen);
  }
  return kanit;
}

export default class RotaKapsamRaportoru implements Reporter {
  private config!: FullConfig;
  private kokSuite!: Suite;

  // Raportör kendi satırlarını stdout'a basar; Playwright'ın bunu bilmesi
  // gerekir, yoksa `list` raportörüyle aynı anda yazarken çıktı bozulur.
  printsToStdio(): boolean {
    return true;
  }

  onBegin(config: FullConfig, suite: Suite): void {
    this.config = config;
    this.kokSuite = suite;
    // MAKBUZ. Raportörün bu koşuda YÜKLENDİĞİNİN kanıtı; `globalTeardown` onu
    // arar ve bulamazsa koşuyu düşürür. Gerekçesi için bkz.
    // `rota-kapsam-makbuzu.ts` (kısaca: `--reporter=list` bu dosyayı hiç
    // yüklemeden süiti yeşil bitirebiliyordu).
    if (!LISTELEME_KIPI) makbuzYaz();
  }

  async onEnd(sonuc: FullResult): Promise<{status?: FullResult['status']} | void> {
    if (LISTELEME_KIPI) return;

    const kok = frontendKoku(this.config);
    if (!existsSync(join(kok, 'package.json'))) {
      // Kök yanlış çözülürse HER dosya adı kayar ve kapı ya hepsini ihlal sayar
      // ya da hiçbirini bulamaz. İkisi de ölçüm değildir; açıkça kapalı düşer.
      this.yaz(`ROTA KAPSAM SÖZLEŞMESİ ÖLÇEMEDİ — kök çözülemedi: ${kok}`);
      return {status: 'failed'};
    }
    const kosanlar: KosanTest[] = this.kokSuite.allTests().map(test => {
      const kayitlar = ziyaretKayitlari(test.results);
      return {
        dosya: koke_gore(kok, test.location.file),
        baslik: tamBaslik(test),
        sonuc: test.outcome(),
        ziyaretler: kayitlar === null ? new Set<string>() : kanitlananRotalar(kayitlar),
        kayitVar: kayitlar !== null,
        renderVar: renderKanitiVarMi(test.results),
      };
    });
    const kosanDosyalar = new Set(kosanlar.map(test => test.dosya));
    const eksikDosyalar = [...disktekiSpecDosyalari(this.config, kok)]
      .filter(dosya => !kosanDosyalar.has(dosya))
      .sort();

    // HİÇ TEST YOKSA BU BİR DARALMA DEĞİL, ÖLÇÜMSÜZLÜKTÜR. Ölçüldü: webServer
    // ayağa kalkmadığında Playwright sıfır testle çıkar; o koşuyu "meşru
    // daraltılmış koşum" saymak, hiçbir şeyin ölçülmediği bir koşuyu sözleşme
    // bakımından temiz göstermek olurdu. Playwright böyle bir koşuda zaten
    // kırmızıdır; raportör de kendi ölçüsünü KAPALI DÜŞÜRÜR.
    if (kosanlar.length === 0) {
      this.yaz(
        'ROTA KAPSAM SÖZLEŞMESİ ÖLÇEMEDİ — koşuda HİÇ test yok. ' +
          'Sözleşme kapalı düşer: ölçülmemiş bir koşu, geçmiş bir koşu değildir.',
      );
      return {status: 'failed'};
    }

    const kapiKostu = kosanDosyalar.has(KAPSAM_KAPISI_DOSYASI);
    const baskaEksikVar = eksikDosyalar.some(dosya => dosya !== KAPSAM_KAPISI_DOSYASI);

    if (!kapiKostu && baskaEksikVar) {
      // Meşru daralma (ör. CI'ın ikinci çağrısı: yalnız touch-targets).
      this.yaz(
        `ROTA KAPSAM SÖZLEŞMESİ KURULMADI — daraltılmış koşum. ` +
          `Koşan spec dosyası: ${kosanDosyalar.size}, koşmayan: ${eksikDosyalar.length}. ` +
          `Sözleşme yalnız kapsam kapısını (${KAPSAM_KAPISI_DOSYASI}) içeren koşumlarda ölçer.`,
      );
      return;
    }

    const ihlaller: string[] = [];

    // --- R4 ------------------------------------------------------------------
    if (!kapiKostu) {
      ihlaller.push(
        `R4: kapsam kapısı koşuda YOK (${KAPSAM_KAPISI_DOSYASI}). Diğer bütün spec ` +
          `dosyaları koştu, yalnız kapı koşmadı: dosya silinmiş, yeniden adlandırılmış ` +
          `ya da içindeki testler yok edilmiş olabilir. Sözleşme KAPALI DÜŞER.`,
      );
    }

    // --- R1 / R2 -------------------------------------------------------------
    for (const girdi of KAPI_GIRDILERI) {
      const beklenen = kapiTestBasligi(girdi);
      const eslesen = kosanlar.filter(
        test => test.dosya === KAPSAM_KAPISI_DOSYASI && test.baslik === beklenen,
      );
      if (eslesen.length === 0) {
        ihlaller.push(
          `R1: ${girdi.rota} için üretilmesi gereken test koşuda YOK ("${beklenen}"). ` +
            `Envanterden düşen bir rota testini de düşürür; kapsam sessizce eksilemez.`,
        );
        continue;
      }
      for (const test of eslesen) {
        if (test.sonuc === 'skipped') {
          ihlaller.push(
            `R2: ${girdi.rota} kapısı ATLANDI ("${beklenen}"). Atlanan kapı, ` +
              `kurulmamış kapıdır.`,
          );
          continue;
        }
        // R6 — kapı testi rotayı GERÇEKTEN açtı mı. Gövdedeki pathname iddiası
        // testin kendi ölçüsüdür; bu, koşudan bağımsız ikinci kayıttır.
        if (!test.kayitVar) {
          ihlaller.push(
            `R6: ${girdi.rota} kapısı gezinti KAYDI bırakmadı ("${beklenen}"). ` +
              `Test helpers.tsteki bağlam fixture'ını kullanmıyor olabilir; ` +
              `kayıt olmadan ziyaret ölçülemez ve kapı kapalı düşer.`,
          );
        } else if (!test.ziyaretler.has(girdi.rota)) {
          ihlaller.push(
            `R6: ${girdi.rota} kapısı bu rotayı ZİYARET ETMEDİ ("${beklenen}"). ` +
              `Kalıcı ziyaretler: ${[...test.ziyaretler].sort().join(', ') || '(yok)'}.`,
          );
        }
      }
    }

    // --- R3 ------------------------------------------------------------------
    // RENDER KONTRATI KÜMESİ: `olcum:'positive'` + `isaret` olan girdiler. Bunlar
    // için raportör (R5) ziyaret + render kanıtını BİRLİKTE ister. Küme, koşu
    // başına BİR kez kurulur; envanter beyanı test koşusundan bağımsız olarak
    // kapsamın RENDER tarafını belirler.
    const renderKontratKumesi = new Set(SPEC_RENDER_GIRDILERI.map(girdi => girdi.rota));
    for (const girdi of SPEC_GIRDILERI) {
      const eslesen = kosanlar.filter(
        test => test.dosya === girdi.dosya && test.baslik === girdi.testAdi,
      );
      if (eslesen.length === 0) {
        ihlaller.push(
          `R3: ${girdi.rota} için bildirilen test koşuda YOK — ` +
            `${girdi.dosya} :: "${girdi.testAdi}". Envantere yazılmış bir ad, ` +
            `koşmuş bir testin yerine geçmez.`,
        );
        continue;
      }
      let ziyaretKanitlandi = false;
      let kayitEksik = false;
      let renderKanitlandi = false;
      for (const test of eslesen) {
        if (test.sonuc !== 'expected' && test.sonuc !== 'flaky') {
          ihlaller.push(
            `R3: ${girdi.rota} için bildirilen test GEÇMEDİ (${test.sonuc}) — ` +
              `${girdi.dosya} :: "${girdi.testAdi}".`,
          );
          continue;
        }
        if (!test.kayitVar) kayitEksik = true;
        else if (test.ziyaretler.has(girdi.rota)) ziyaretKanitlandi = true;
        if (test.renderVar) renderKanitlandi = true;
      }

      // --- R5 --------------------------------------------------------------
      // Testin ADI kanıt değildir; rotanın o test İÇİNDE gerçekten açılmış
      // olması kanıttır. Olumsuz yönlendirme (rotaya gidip `replace` ile
      // düşürülme) bu kümeye GİRMEZ — bkz. `rota-ziyaret-kaydi.ts`.
      const renderKontratinda = renderKontratKumesi.has(girdi.rota);
      const gecenTestVar = eslesen.some(test => test.sonuc === 'expected' || test.sonuc === 'flaky');
      if (renderKontratinda) {
        // RENDER KONTRATINDAKİ girdi: ziyaret + render kanıtı BİRLİKTE istenir.
        // "Sayfa çizilmedi" boş ekranı, "ziyaret edilmedi" ise hiç açılmayan
        // rotayı ayrı ölçer; ikisinden biri eksikse kapı kırmızıdır.
        if (gecenTestVar && !ziyaretKanitlandi) {
          ihlaller.push(
            kayitEksik
              ? `R5: ${girdi.rota} için gezinti KAYDI yok — ${girdi.dosya} :: ` +
                  `"${girdi.testAdi}". Test helpers.ts'teki bağlam fixture'ını kullanmalı; ` +
                  `kayıt olmadan ziyaret ölçülemez ve kapı kapalı düşer.`
              : `R5: ${girdi.rota} bu testte ZİYARET EDİLMEDİ — ${girdi.dosya} :: ` +
                  `"${girdi.testAdi}". Testin koşup geçmesi rotayı kapsadığı anlamına ` +
                  `gelmez. Testin kalıcı ziyaretleri: ${
                    [...new Set(eslesen.flatMap(test => [...test.ziyaretler]))].sort().join(', ') || '(yok)'
                  }.`,
          );
        } else if (gecenTestVar && !renderKanitlandi) {
          ihlaller.push(
            `R5: ${girdi.rota} RENDER KONTRATINDA ama render KANITI YOK — ${girdi.dosya} :: ` +
              `"${girdi.testAdi}". Test rota-render-kaniti.ts'teki renderKanitiniDogrula ` +
              `çağırmalı (rota-govdesi kökü + işaret kanıtı bırakır); kanıt olmadan ` +
              `sayfanın GERÇEKTEN çizildiği ölçülemez.`,
          );
        }
      } else if (!ziyaretKanitlandi && gecenTestVar) {
        const kaliciKume = [
          ...new Set(eslesen.flatMap(test => [...test.ziyaretler])),
        ].sort();
        ihlaller.push(
          kayitEksik
            ? `R5: ${girdi.rota} için gezinti KAYDI yok — ${girdi.dosya} :: ` +
                `"${girdi.testAdi}". Test helpers.ts'teki bağlam fixture'ını kullanmalı; ` +
                `kayıt olmadan ziyaret ölçülemez ve kapı kapalı düşer.`
            : `R5: ${girdi.rota} bu testte ZİYARET EDİLMEDİ — ${girdi.dosya} :: ` +
                `"${girdi.testAdi}". Testin koşup geçmesi rotayı kapsadığı anlamına ` +
                `gelmez. Testin kalıcı ziyaretleri: ${kaliciKume.join(', ') || '(yok)'}.`,
        );
      }
    }

    this.ozetiYaz(kosanDosyalar.size, ihlaller, this.cokluKapsam(kosanlar));

    if (ihlaller.length > 0) return {status: 'failed'};
    // Süitin kendi sonucunu EZMEZ: kapı yeşilse koşunun durumu neyse odur.
    return {status: sonuc.status};
  }

  /**
   * Birden fazla `spec` rotasını tek başına kanıtlayan testler.
   *
   * AÇIK YAZILIR, SAKLANMAZ. `touch-targets`ın mobil toplu kapıları dört-beş
   * rotayı aynı gövdede açar; kapsamın hangi rotalarının TEK bir teste
   * dayandığını görmek, o test kırılgan hâle geldiğinde neyin birlikte
   * düşeceğini de göstermiş olur. Liste ziyaret KAYDINDAN türer, envanterdeki
   * beyandan değil.
   */
  private cokluKapsam(kosanlar: readonly KosanTest[]): readonly string[] {
    const satirlar: string[] = [];
    for (const test of kosanlar) {
      if (!test.kayitVar) continue;
      const bildirilen = SPEC_GIRDILERI.filter(
        girdi => girdi.dosya === test.dosya && girdi.testAdi === test.baslik,
      ).map(girdi => girdi.rota);
      if (bildirilen.length < 2) continue;
      const kanitli = bildirilen.filter(rota => test.ziyaretler.has(rota)).sort();
      const kanitsiz = bildirilen.filter(rota => !test.ziyaretler.has(rota)).sort();
      satirlar.push(
        `${test.dosya} :: "${test.baslik}" -> ${kanitli.length}/${bildirilen.length} ` +
          `rota kanıtladı: ${kanitli.join(', ')}` +
          (kanitsiz.length > 0 ? ` | KANITSIZ: ${kanitsiz.join(', ')}` : ''),
      );
    }
    return satirlar.sort();
  }

  private ozetiYaz(
    kosanDosyaSayisi: number,
    ihlaller: readonly string[],
    cokluKapsam: readonly string[],
  ): void {
    const satirlar = [
      '',
      'ROTA KAPSAM SÖZLEŞMESİ (çalışma zamanı)',
      `  envanterdeki rota      : ${ROTA_ENVANTERI.length}`,
      `  kapi (üretilen test)   : ${KAPI_GIRDILERI.length}`,
      `  spec (başka testte)    : ${SPEC_GIRDILERI.length}`,
      `  muaf (gerekçeli)       : ${MUAF_GIRDILERI.length}`,
      `  koşan spec dosyası     : ${kosanDosyaSayisi}`,
      `  ihlal                  : ${ihlaller.length}`,
    ];
    for (const ihlal of ihlaller) satirlar.push(`  - ${ihlal}`);
    if (cokluKapsam.length > 0) {
      satirlar.push('  çoklu kapsam (bir test, birden fazla `spec` rotası):');
      for (const satir of cokluKapsam) satirlar.push(`    * ${satir}`);
    }
    satirlar.push('');
    this.yaz(satirlar.join('\n'));
  }

  private yaz(metin: string): void {
    process.stdout.write(`${metin}\n`);
  }
}
