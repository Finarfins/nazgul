import {defineConfig, devices} from '@playwright/test';

// E2E katmanı: gerçek backend (uvicorn + taze SQLite) + gerçek build edilmiş
// frontend (backend SPA olarak servis eder) + gerçek Chromium. Amaç, bugünkü
// prod POS çökmesi sınıfını CI'da yakalamak: jsdom/mock hiçbir katmanda yok,
// her spec konsol-temiz biter (bkz. e2e/helpers.ts).
//
// Önkoşul: `npm run build` (backend, frontend/dist'i servis eder).
export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  // RAPORTÖR DEVRE DIŞI BIRAKILAMASIN. `globalTeardown` yapılandırmadan gelir ve
  // komut satırından ezilemez; kapsam raportörünün bu koşuda YÜKLENDİĞİNİ ölçer.
  // `--reporter=list` ile yapılan bir koşu raportörü hiç yüklemeden yeşil
  // bitiyordu; artık kapalı düşer. Bkz. `e2e/rota-kapsam-makbuzu.ts`.
  globalTeardown: './e2e/rota-kapsam-teardown.ts',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  // ROTA KAPSAM RAPORTÖRÜ HER İKİ KİPTE DE KOŞAR — CI'da ve yerelde.
  //
  // Sözleşme yalnız CI'da kurulsaydı, yerelde yeşil gördüğü bir daldan CI'a
  // kırmızı gönderirdi ve kapı "sürpriz" olurdu. Raportörün kendisi hangi
  // koşumda ölçüp hangisinde susacağını DİSKTEN türetir (bkz. dosya başlığı:
  // daraltılmış koşum ayrımı), bu yüzden CI'ın ikinci — yalnız
  // `touch-targets.spec.ts` çağıran — Playwright koşumunu KIRMAZ.
  //
  // `list` ve `html` raportörleri AYNEN yerinde: kapsam raportörü onların
  // yerine geçmez, yanlarına eklenir.
  reporter: process.env.CI
    ? [['list'], ['html', {open: 'never'}], ['./e2e/rota-kapsam-raportoru.ts']]
    : [['list'], ['./e2e/rota-kapsam-raportoru.ts']],
  use: {
    baseURL: 'http://127.0.0.1:5599',
    trace: 'retain-on-failure',
  },
  // TARAYICI SAAT DİLİMİ BİLDİRİLİR — devralınmaz.
  //
  // Bildirilmediğinde Chromium işletim sisteminin dilimini alıyordu: geliştirici
  // makinelerinde Europe/Istanbul, CI koşucularında UTC. Bu projenin ürettiği HER
  // e2e yeşili, söylenmemiş bir koşul taşıyordu; üç ayrı yerel üretim denemesinin
  // yeşil dönmesinin sebebi de buydu — duvar saati penceresini yakaladılar ama
  // tarayıcı dilimini hiç kurmadılar. (`TZ` süreç değişkeni Chromium'a GEÇMEZ.)
  //
  // İKİ dilim de koşar ve bu BİLİNÇLİ:
  //   * Süitin tamamı İSTANBUL DIŞI bir dilimde ölçülür. Yalnız İstanbul'a
  //     sabitlemek CI'ı deterministik yapardı ama `localToday()` ile
  //     `business_today()` yalnız dilimler AYRIŞTIĞINDA farklılaştığı için bu
  //     kusur sınıfını kalıcı olarak görünmez kılardı.
  //   * İş tarihi spec'i ayrıca İstanbul'da da koşar: kullanıcıların çoğunun
  //     bulunduğu dilimde davranışın bozulmadığını gösterir ve "yalnız İstanbul
  //     ölçümü bu kusuru sertifikalayamaz" savını ölçülebilir kılar.
  // İKİ proje, İKİ dilim. Sıraya BAĞLI DEĞİLDİR: iş tarihi spec'i normal
  // projenin içinde, alfabetik yerinde koşar. Konumdan bağımsızlık ölçüldü —
  // spec dosyası başa, ortaya ve sona alınarak süit üç kez koşuldu, üçünde de
  // aynı sonuç. Bir kapının doğruluğu dizi sırasına dayanamaz: sıra hiçbir
  // yerde bildirilmez ve hiçbir şey onu zorlamaz.
  projects: [
    {
      name: 'chromium-utc',
      // AYGIT PİKSEL ORANI BİLDİRİLİR — devralınmaz.
      //
      // `devices['Desktop Chrome']` bugün `deviceScaleFactor: 1` getiriyor, ama
      // bunu HİÇBİR YERDE yazmıyorduk: dokunma hedefi kapısının ürettiği her
      // yeşil, kimsenin kaydetmediği bir ölçek koşulu taşıyordu. Saat diliminde
      // aynı dersi pahalıya öğrendik; bildirilmemiş koşul ölçülmemiş koşuldur.
      //
      // ÖLÇÜLDÜ: `min-height:44px` bir kutu 1 / 1.25 / 1.5 / 1.75 / 2 / 2.5 / 3
      // ölçek faktörlerinin HEPSİNDE tam 44.000000 döndürüyor. Yani kapının
      // uyguladığı birim CSS PİKSELİDİR ve aygıt piksel oranından bağımsızdır.
      // Bu satır o bağımsızlığı korumak için değil, koşulu KAYDA GEÇİRMEK için
      // var: ön ayar bir gün değişirse ölçüm sessizce kaymasın.
      use: {...devices['Desktop Chrome'], timezoneId: 'UTC', deviceScaleFactor: 1},
    },
    {
      name: 'saat-dilimi-istanbul',
      use: {...devices['Desktop Chrome'], timezoneId: 'Europe/Istanbul', deviceScaleFactor: 1},
      testMatch: /isletme-tarihi-saat-dilimi\.spec\.ts$/,
    },
  ],
  webServer: {
    command: 'python e2e/serve.py',
    url: 'http://127.0.0.1:5599/api/health',
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
