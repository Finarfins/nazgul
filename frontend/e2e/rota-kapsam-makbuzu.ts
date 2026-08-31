// RAPORTÖR MAKBUZU — "raportör hiç yüklenmedi" durumunu KAPALI DÜŞÜRÜR.
//
// ÖLÇÜLEN BOŞLUK. Sözleşmenin çalışma zamanı yarısı bir Playwright raportörüdür
// ve raportör listesi KOMUT SATIRINDAN ezilebilir:
//
//     npx playwright test --reporter=list
//
// Bu koşuda süit tamamen koşar, exit 0 döner ve kapsam sözleşmesinin çalışma
// zamanı yarısı hiç ÇALIŞMAZ. G7 yalnız yapılandırma dosyasındaki metni sayar;
// dosya doğru olduğu için o da yeşil kalır. Yani kapı, kendisini devre dışı
// bırakan koşuyu göremiyordu.
//
// ÇÖZÜM: KOŞUNUN KENDİSİNDEN GELEN BİR MAKBUZ. Raportör yüklendiğinde
// `onBegin`de bir makbuz yazar; `globalTeardown` (yapılandırmadadır ve komut
// satırından ezilemez) koşunun sonunda o makbuzu arar. Makbuz yoksa raportör
// yüklenmemiştir ve koşu KIRMIZI biter.
//
// NEDEN `onBegin`, `onEnd` DEĞİL. Playwright'ta `globalTeardown`, raportörün
// `onEnd`inden ÖNCE koşar; `onEnd`de yazılan bir makbuzu teardown göremezdi.
// `onBegin` zaten kanıtlanması gereken şeyi kanıtlar: raportör YÜKLENDİ. Verdict
// (ihlal var mı) `onEnd`in işidir ve o kendi çıkış durumunu döndürür.
//
// NEDEN SÜREÇ KİMLİĞİ (pid). Raportörler ve `globalTeardown` Playwright'ın ANA
// sürecinde koşar. Makbuz adı pid taşır: bir önceki koşumdan kalan bayat bir
// dosya bu koşuyu geçiremez ve paralel koşan iki Playwright birbirinin
// makbuzunu okuyamaz. Teardown makbuzu okuduktan sonra SİLER.
//
// MEŞRU DARALTILMIŞ KOŞUM BOZULMAZ. CI'ın ikinci — yalnız `touch-targets`
// çağıran — koşumunda da raportör yapılandırmadan yüklenir, makbuz yazılır ve
// teardown geçer. Makbuz raportörün YÜKLENDİĞİNİ ölçer, ne ölçtüğünü değil.

import {existsSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

export interface Makbuz {
  readonly pid: number;
  readonly zaman: number;
  readonly surum: 1;
}

/** Bu sürece ait makbuz dosyası. */
export function makbuzYolu(pid: number = process.pid): string {
  return join(tmpdir(), `rota-kapsam-makbuzu-${pid}.json`);
}

export function makbuzYaz(): void {
  const makbuz: Makbuz = {pid: process.pid, zaman: Date.now(), surum: 1};
  writeFileSync(makbuzYolu(), JSON.stringify(makbuz), 'utf8');
}

/** Makbuzu okur ve SİLER. Yoksa `null`. */
export function makbuzuTuket(): Makbuz | null {
  const yol = makbuzYolu();
  if (!existsSync(yol)) return null;
  try {
    return JSON.parse(readFileSync(yol, 'utf8')) as Makbuz;
  } finally {
    rmSync(yol, {force: true});
  }
}
