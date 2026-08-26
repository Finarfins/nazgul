// TEK ÜRETİCİ GÜVENCESİ — İDDİA DEĞİL, ÖLÇÜM.
//
// İnceleme haklı olarak şunu söyledi: paylaşılan bir dosya yalnız süreç-içi
// bellek kaybını çözer, oku-sonra-yaz yarışını YAPISAL OLARAK çözmez. İki işçi
// de dosyayı bulamaz, ikisi de veritabanına yazan üreticiyi koşturur.
//
// Bu yüzden burada gerçek, EŞZAMANLI süreçler başlatılıyor. Tarayıcı yok,
// veritabanı yok: ölçülen şey yalnız sahiplenmenin atomikliği. Üretici bilerek
// yavaş, ki yarış penceresi zamanlama şansına bırakılmasın.
import {spawn} from 'node:child_process';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';

import {expect, TOHUM_DIZINI, test} from './helpers';

const COCUK = join(process.cwd(), 'e2e', 'tohum-yaris-cocuk.ts');
const ISCI_SAYISI = 6;
const URETIM_SURESI_MS = 500;

function calistir(ad: string, tanik: string): Promise<string> {
  return new Promise((cozul, reddet) => {
    const cocuk = spawn(
      process.execPath,
      [COCUK, ad, tanik, String(URETIM_SURESI_MS)],
      {cwd: process.cwd(), stdio: ['ignore', 'pipe', 'pipe']},
    );
    let cikti = '';
    let hata = '';
    cocuk.stdout.on('data', parca => {
      cikti += parca;
    });
    cocuk.stderr.on('data', parca => {
      hata += parca;
    });
    cocuk.on('error', reddet);
    cocuk.on('close', kod => {
      if (kod === 0) cozul(cikti);
      else reddet(new Error(`çocuk süreç ${kod} ile düştü: ${hata}`));
    });
  });
}

test('EŞZAMANLI işçilerden YALNIZ BİRİ üreticiyi koşturur', async () => {
  test.setTimeout(180_000);
  // S7 KANITI, KOŞUCUNUN KENDİSİNDEN. `wx` atomikliği YEREL dosya sistemi
  // varsayar. Bu spec CI'da da koştuğu için varsayım her koşuda SINANIYOR:
  // dizin ağ diskine taşınır ve `wx` atomikliğini yitirirse aşağıdaki
  // "üretici TAM BİR KEZ" iddiası CI'da KIRMIZI olur. Açılma koşulu bu
  // yüzden bir not değil, çalışan bir kapıdır. Aşağıdaki satır hangi
  // platformda ve hangi yolda ölçüldüğünü kayda geçirir.
  console.log(`TOHUM_YARIS_ORTAM platform=${process.platform} dizin=${TOHUM_DIZINI}`);
  const ad = `yaris-${Date.now()}`;
  const tanik = join(TOHUM_DIZINI, `${ad}.tanik`);
  await mkdir(TOHUM_DIZINI, {recursive: true});
  await writeFile(tanik, '', 'utf8');

  // AYNI ANDA başlat: hepsi `Promise.all` ile, aralarında bekleme yok.
  const ciktilar = await Promise.all(
    Array.from({length: ISCI_SAYISI}, () => calistir(ad, tanik)),
  );

  const uretenler = (await readFile(tanik, 'utf8')).split('\n').filter(Boolean);
  console.log(`TOHUM_YARIS işçi=${ISCI_SAYISI} üretici koşumu=${uretenler.length} ${uretenler.join(' | ')}`);

  // ASIL GÜVENCE.
  expect(uretenler.length, 'üretici TAM BİR KEZ koşmalıydı').toBe(1);

  // Ve herkes AYNI tohumu almalı: kaybedenler kendi değerlerini üretmemeli.
  const tekil = new Set(ciktilar.map(c => c.trim()));
  expect(tekil.size, 'her işçi AYNI tohumu almalı').toBe(1);
  expect([...tekil][0], 'tohum boş dönmemeli').toContain('tek-uretici');

  // BOŞA DÜŞME ÇAPASI: altı süreç gerçekten koştu mu.
  expect(ciktilar.length).toBe(ISCI_SAYISI);
});

test('ÜRETİCİ DÜŞERSE bekleyenler de KAPALI düşer, sessizce üretmez', async () => {
  test.setTimeout(180_000);
  const ad = `yaris-hata-${Date.now()}`;
  const tanik = join(TOHUM_DIZINI, `${ad}.tanik`);
  await mkdir(TOHUM_DIZINI, {recursive: true});
  await writeFile(tanik, '', 'utf8');

  // Çocuk, üretim süresi -1 aldığında bilerek patlar.
  const sonuclar = await Promise.allSettled(
    Array.from({length: ISCI_SAYISI}, () => new Promise<string>((cozul, reddet) => {
      const cocuk = spawn(process.execPath, [COCUK, ad, tanik, '-1'],
        {cwd: process.cwd(), stdio: ['ignore', 'pipe', 'pipe']});
      let hata = '';
      cocuk.stderr.on('data', parca => {
        hata += parca;
      });
      cocuk.on('close', kod => (kod === 0 ? cozul('') : reddet(new Error(hata))));
    })),
  );

  const dusen = sonuclar.filter(s => s.status === 'rejected');
  const uretenler = (await readFile(tanik, 'utf8')).split('\n').filter(Boolean);
  console.log(`TOHUM_YARIS_HATA düşen=${dusen.length}/${ISCI_SAYISI} üretici koşumu=${uretenler.length}`);

  expect(uretenler.length, 'üretici düşse bile TAM BİR KEZ koşmalı').toBe(1);
  expect(dusen.length, 'hiçbir işçi sessizce başarılı dönmemeli').toBe(ISCI_SAYISI);
});
