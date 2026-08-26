// Yarış ölçümünün ÇOCUK sürecidir; test değildir (Playwright yalnız
// `*.spec.ts` toplar). Her çocuk `tohumHatirla`yı çağırır ve ÜRETİCİ koşarsa
// tanık dosyasına bir satır ekler. Üretici bilerek YAVAŞTIR: yarış penceresi
// açık kalsın, ölçüm zamanlama şansına bağlı olmasın.
import {appendFile} from 'node:fs/promises';

import {tohumHatirla} from './tohum-bellegi.ts';

const ad = process.argv[2];
const tanik = process.argv[3];
const uretimSuresi = Number(process.argv[4] ?? '400');

const deger = await tohumHatirla(ad, async () => {
  await appendFile(tanik, `URETTI ${process.pid}\n`, 'utf8');
  if (uretimSuresi < 0) {
    // Bilerek DÜŞEN üretici: bekleyenlerin de KAPALI düştüğü ölçülsün.
    throw new Error('üretici bilerek düştü');
  }
  await new Promise(cozul => setTimeout(cozul, uretimSuresi));
  return {damga: 'tek-uretici', uretenPid: process.pid};
});

process.stdout.write(JSON.stringify(deger));
