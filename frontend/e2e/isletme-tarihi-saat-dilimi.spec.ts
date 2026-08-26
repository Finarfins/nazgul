/**
 * İŞ TARİHİ, TARAYICI SAATİNDEN TÜRETİLMEZ.
 *
 * Arka uç "bugün"ü Europe/Istanbul'da tanımlar (`app/business_time.py`).
 * Alacak yaşlandırma ekranı ise `as_of` varsayılanını tarayıcının saatinden
 * hesaplıyordu. CI tarayıcısı UTC koştuğu için 21:00–23:59 UTC arasında sayfa
 * bir gün geriyi soruyor, o güne düşen alacak rapora hiç girmiyordu: 20:47 UTC
 * koşusu geçti, 21:18 UTC koşusu düştü. Bu bir test artefaktı DEĞİL — tarayıcı
 * saat dilimi İstanbul'dan farklı olan HER kullanıcı yanlış varsayılan tarih
 * görür.
 *
 * İKİ YÖN de sınanır:
 *   - İstanbul'dan FARKLI bir dilim (bu spec) → düzeltmeden önce kırmızı,
 *     sonra yeşil.
 *   - İstanbul (süitin geri kalanı, `playwright.config.ts` → timezoneId)
 *     → baştan sona yeşil.
 *
 * YÖNTEM NOTU: `TZ` süreç değişkeni Chromium'a GEÇMEZ. Bu spec saat dilimini
 * `test.use({timezoneId})` ile kurar ve kurulduğunu TARAYICININ İÇİNDEN
 * doğrular; ortamın taşıdığını varsaymaz.
 */
import {test, expect, adminApi, login} from './helpers';

// SAAT DONDURMA KENDİ BAĞLAMINDA YAPILIR VE O BAĞLAM BURADA KAPATILIR.
//
// `Clock` arayüzünde `uninstall()` YOK (ölçüldü: playwright-core types.d.ts →
// fastForward/install/pauseAt/resume/runFor/setFixedTime/setSystemTime).
// Yani kurulan saati "geri almanın" temiz bir yolu yok; kurulduğu YÜZEYİ yok
// etmek var. Fixture'ın paylaşılan bağlamına dokunmak, sızıntıyı sonraki
// testlere taşıyordu — bağımsız ölçümde `saat-dilimi-utc` projesi
// `chromium-utc`ten ÖNCE koşturulduğunda sonraki bağlamlar bozuldu ve
// ECONNREFUSED 127.0.0.1:5599 ile beş test düştü.
//
// Bu yüzden bağlamı BU TEST yaratır ve `finally` içinde kapatır. Böylece
// spec'in dizideki KONUMU önemsizleşir; doğruluk sıraya değil sahipliğe
// dayanır. (Sıra hiçbir yerde bildirilmez ve hiçbir şey onu zorlamaz.)

// Dilim BU DOSYADA seçilmez; `playwright.config.ts` iki projeyi bildirir ve
// spec hangi projede koştuğunu oradan OKUR. Böylece "hangi saatte ölçtük"
// sorusunun cevabı tek yerde durur ve devralınamaz.
const ISTANBUL = 'Europe/Istanbul';
// İstanbul dışı projede tarayıcı saati sabitlenir: 21:30Z, UTC'de 12 Ağustos,
// İstanbul'da 13 Ağustos. Gün farkı duvar saatine bağlı kalmaz.
const SABIT_AN = new Date('2026-08-12T21:30:00.000Z');

test('tarayıcı saat dilimi projede BİLDİRİLDİĞİ gibi kuruldu', async ({page}, testInfo) => {
  const bildirilen = (testInfo.project.use as {timezoneId?: string}).timezoneId;
  expect(bildirilen, 'proje saat dilimini bildirmeli — bildirilmemişse ölçüm yoktur').toBeTruthy();
  // `/` oturumsuzken istemci tarafında `/giris`e yönlenir; `evaluate` o
  // yönlendirmeye yakalanıp "Execution context was destroyed" verebiliyordu
  // (CI'da ilk denemede düştü, retry ile yeşile döndü). Doğrudan hedefe gidip
  // yüklenmeyi bekliyoruz: retry ile geçen bir kapı kapı değildir.
  await page.goto('/giris');
  await page.waitForLoadState('domcontentloaded');
  const olculen = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  expect(
    olculen,
    'tarayıcı dilimi kurulmadıysa bu spec hiçbir şey kanıtlamaz (TZ değişkeni Chromium a geçmez)',
  ).toBe(bildirilen);
});

test('rapor tarihi arka ucun iş günüdür, tarayıcınınki değil', async ({browser}, testInfo) => {
  test.setTimeout(120_000);
  const dilim = (testInfo.project.use as {timezoneId?: string}).timezoneId as string;

  const admin = await adminApi();
  let isGunu: string;
  try {
    const rapor = await admin.api.get('/api/reports/receivables-aging', {
      headers: await admin.headers(),
    });
    expect(rapor.ok(), await rapor.text()).toBeTruthy();
    isGunu = (await rapor.json()).as_of as string;
  } finally {
    await admin.dispose();
  }

  // Kendi bağlamımız: dilim projeden gelir, saat burada dondurulur, bağlam
  // testin sonunda KESİN olarak kapanır.
  const baglam = await browser.newContext({timezoneId: dilim});
  try {
    const sayfa = await baglam.newPage();
    const konsolHatalari: string[] = [];
    sayfa.on('console', (m) => {
      if (m.type() === 'error' && !/status of (401|403)/.test(m.text())) {
        konsolHatalari.push(m.text());
      }
    });

    if (dilim !== ISTANBUL) {
      // Ayrışmayı garanti et; duvar saatinin 21:00–24:00 UTC penceresine
      // düşmesini beklemek kapıyı günün 21 saati kör bırakırdı.
      await sayfa.clock.setFixedTime(SABIT_AN);
    }
    await login(sayfa);
    await sayfa.goto('/raporlar/alacak-yaslandirma');

    const tarayiciGunu = await sayfa.evaluate(() => {
      const simdi = new Date();
      const kaydirma = simdi.getTimezoneOffset() * 60_000;
      return new Date(simdi.getTime() - kaydirma).toISOString().slice(0, 10);
    });
    if (dilim !== ISTANBUL) {
      expect(
        tarayiciGunu,
        `${dilim}: tarayıcı günü iş günüyle aynıysa bu proje ayrımı ÖLÇEMEZ`,
      ).not.toBe(isGunu);
    }

    await expect(
      sayfa.getByLabel('Rapor Tarihi'),
      `${dilim}: rapor tarihi arka ucun iş gününü göstermeli`,
    ).toHaveValue(isGunu);
    expect(konsolHatalari, 'sayfa konsol-temiz açılmalı').toEqual([]);
  } finally {
    await baglam.close();
  }
});
