// KOŞUM SONU KAPISI — raportör yüklenmediyse koşu KIRMIZI biter.
//
// Gerekçe ve tasarım için bkz. `rota-kapsam-makbuzu.ts`. Burada tek bir şey
// ölçülür: bu koşuda kapsam raportörü GERÇEKTEN yüklendi mi.
//
// `globalTeardown` yapılandırmadan gelir; `--reporter=list` gibi bir komut
// satırı bayrağı onu devre dışı bırakamaz. Raportör listesi ezilmişse makbuz
// yazılmamıştır ve buradan fırlatılan istisna koşuyu düşürür.

import {makbuzuTuket} from './rota-kapsam-makbuzu';

export default async function rotaKapsamTeardown(): Promise<void> {
  // `--list` kipinde hiçbir test koşmaz; ölçülecek bir koşu da yoktur.
  if (process.argv.includes('--list')) return;

  const makbuz = makbuzuTuket();
  if (makbuz === null) {
    throw new Error(
      'ROTA KAPSAM SÖZLEŞMESİ KOŞMADI — kapsam raportörü bu koşuda YÜKLENMEDİ.\n' +
        '  Playwright raportör listesi komut satırından ezilmiş olabilir ' +
        '(ör. `--reporter=list`).\n' +
        '  Sözleşmenin çalışma zamanı yarısı olmadan geçen bir koşu, geçmiş bir koşu ' +
        'DEĞİLDİR; kapı kapalı düşer.\n' +
        '  Raportörü koşuya dahil edin: `npx playwright test` (yapılandırmadaki ' +
        'raportör listesi zaten içerir).',
    );
  }
  if (makbuz.pid !== process.pid) {
    throw new Error(
      `ROTA KAPSAM SÖZLEŞMESİ ÖLÇEMEDİ — makbuz BAŞKA bir sürece ait ` +
        `(makbuz pid=${makbuz.pid}, koşu pid=${process.pid}).`,
    );
  }
}
