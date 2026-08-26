// TOHUM BELLEĞİ — İŞÇİLER ARASI, TEK ÜRETİCİLİ.
//
// NEDEN AYRI MODÜL. Playwright'a bağımlı değil; böylece aşağıdaki tek-üretici
// güvencesi GERÇEK süreçlerle, tarayıcı açmadan ölçülebiliyor
// (bkz. `tohum-yaris.spec.ts`). Bir eşzamanlılık güvencesi, eşzamanlı
// ölçülmediği sürece iddiadır.
//
// KUSUR 1 — SÜREÇ İÇİ BELLEK. Tohumlar modül düzeyinde bir değişkende
// tutuluyordu. Playwright tekrar denemeyi YENİ bir işçi sürecinde koşar; orada
// değişken boştur ve tohum AYNI veritabanına ikinci kez yazmaya kalkar. Bunun
// TEK bir noktada değil ARDIŞIK benzersizlik kısıtlarında düştüğü ölçüldü:
//
//     Idempotency-Key farklı bir satış gövdesiyle yeniden kullanılamaz
//     Bu seri numarası bu firmada zaten kayıtlı
//
// Yani tohum bir bütün olarak yeniden koşulabilir DEĞİL ve her düşüşü tekrar
// denemeyi ASIL başarısızlığın üstüne yazıyor.
//
// KUSUR 2 — OKU-SONRA-YAZ YARIŞI. Belleği dosyaya almak tek başına YETMEZ:
// oku → üret → yaz sırasında iki işçi de dosyayı BULAMAZ, ikisi de üreticiyi
// koşturur, sonra ikisi de yazar. Paylaşılan dosya yalnız süreç-içi bellek
// kaybını çözer, eşzamanlı ilk-yazan yarışını YAPISAL OLARAK çözmez.
//
// ÇÖZÜM — ATOMİK SAHİPLENME. Üretmeden önce kilit dosyası `wx` bayrağıyla
// açılır: bu, dosya YOKSA oluşturan, VARSA hata veren TEK bir çekirdek
// çağrısıdır (`O_CREAT|O_EXCL`), yani kontrol ile oluşturma arasında pencere
// yoktur. Kilidi alan TEK süreç üretir; alamayan ÜRETMEZ, yazılmasını BEKLER ve
// süre dolarsa KAPALI DÜŞER. Değer geçici dosyaya yazılıp `rename` ile
// yayımlanır, böylece bekleyen hiçbir süreç yarım JSON okuyamaz.
import {mkdir, readFile, rename, writeFile} from 'node:fs/promises';
import {join} from 'node:path';

export const TOHUM_DIZINI = join(process.cwd(), 'test-results', '.tohum');

// Üretici gerçek bir veritabanı tohumlaması; ağır olabilir. Bekleyenin sınırı
// bu yüzden cömert, ama SONSUZ DEĞİL: süre dolarsa kapalı düşülür.
export const BEKLEME_SINIRI_MS = 180_000;
const YOKLAMA_MS = 50;

async function oku<T>(yol: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(yol, 'utf8')) as T;
  } catch {
    return null;
  }
}

async function bekle<T>(ad: string, yol: string, hataYolu: string): Promise<T> {
  const bitis = Date.now() + BEKLEME_SINIRI_MS;
  while (Date.now() < bitis) {
    const deger = await oku<T>(yol);
    if (deger !== null) return deger;
    const hata = await readFile(hataYolu, 'utf8').catch(() => null);
    if (hata !== null) {
      throw new Error(`tohum '${ad}': ÜRETİCİ BAŞARISIZ oldu, bekleyen de düşüyor — ${hata}`);
    }
    await new Promise(cozul => setTimeout(cozul, YOKLAMA_MS));
  }
  throw new Error(
    `tohum '${ad}': sahiplenme başka bir sürece geçti ve ${BEKLEME_SINIRI_MS}ms ` +
    'içinde yazılmadı; KAPALI DÜŞÜLDÜ (sessizce ikinci kez üretilmedi)',
  );
}

export async function tohumHatirla<T>(ad: string, uret: () => Promise<T>): Promise<T> {
  const yol = join(TOHUM_DIZINI, `${ad}.json`);
  const hazir = await oku<T>(yol);
  if (hazir !== null) return hazir;

  await mkdir(TOHUM_DIZINI, {recursive: true});
  const kilit = join(TOHUM_DIZINI, `${ad}.kilit`);
  const hataYolu = join(TOHUM_DIZINI, `${ad}.hata`);

  // ATOMİK SAHİPLENME. 'wx' = O_CREAT|O_EXCL: kontrol ile oluşturma TEK
  // çağrıda, arada pencere yok.
  let sahip = true;
  try {
    await writeFile(kilit, String(process.pid), {flag: 'wx'});
  } catch {
    sahip = false;
  }
  if (!sahip) return bekle<T>(ad, yol, hataYolu);

  try {
    const deger = await uret();
    // ATOMİK YAYIN: bekleyen yarım JSON okuyamaz.
    const gecici = `${yol}.${process.pid}.tmp`;
    await writeFile(gecici, JSON.stringify(deger), 'utf8');
    await rename(gecici, yol);
    return deger;
  } catch (hata) {
    // ÜRETİCİ DÜŞSE BİLE KİLİT BIRAKILMAZ — ÖLÇÜMLE ÖĞRENİLDİ.
    //
    // İlk hâlde kilit siliniyordu. Ölçüm: altı eşzamanlı işçiden ÜRETİCİ BEŞ
    // KEZ koştu — sıradaki bekleyen kilidi kapıp yeniden üretti, yani
    // "tam bir kez" güvencesi hata yolunda çöküyordu. Kilit duruyor,
    // bekleyenler hata İŞARETİNİ görüp hızla kapalı düşüyor.
    const mesaj = hata instanceof Error ? hata.message : String(hata);
    await writeFile(hataYolu, mesaj, 'utf8').catch(() => undefined);
    throw hata;
  }
}
