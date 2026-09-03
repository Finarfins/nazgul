/**
 * `farmApi` sözleşmesi — üç iddia.
 *
 * 1. **409 sıradan bir hata değildir.** Çakışmayı "kaydedilemedi" diye
 *    göstermek kullanıcıyı tekrar denemeye iter ve ikinci deneme arada
 *    yazılanı ezer. Ayrı tür fırlatılmalı ve mesaj "yenile" demeli.
 * 2. **Tarih kaydırmaz.** `'2026-03-01'` üzerinde `new Date()` kullanmak
 *    UTC gece yarısı ayrıştırması yüzünden Türkiye'de 29 Şubat'ı gösterirdi.
 * 3. **Hesaplanamayan oran sıfır değildir.** Biçimlendirici `null` için '—'
 *    döner; '0' dönseydi ölçülmemiş sezon "maliyetsiz" görünürdü.
 */
import {describe, expect, it, vi} from 'vitest';

const post = vi.fn();
const put = vi.fn();
vi.mock('../api', () => ({
  api: {post: (...a: unknown[]) => post(...a), put: (...a: unknown[]) => put(...a)},
  errorDetail: (_e: unknown, fallback: string) => fallback,
}));

const {
  FarmConflictError, bitkiKatla, catalogueMatch, isoDate, money, qty, saveFarmRecord,
} = await import('./farmApi');

const hata = (status: number) => Object.assign(new Error('istek başarısız'), {response: {status}});

describe('saveFarmRecord', () => {
  it('409 için FarmConflictError fırlatır ve mesajı yeniden denemeye ÇAĞIRMAZ', async () => {
    put.mockRejectedValueOnce(hata(409));
    const err = await saveFarmRecord('put', '/farms/1', {}, 'Kaydedilemedi.').catch((e: Error) => e);
    expect(err).toBeInstanceOf(FarmConflictError);
    expect((err as Error).message).toContain('başka biri değiştirdi');
    // "Tekrar deneyin" demek, arada yazılanı ezmeye davet olurdu.
    expect((err as Error).message).not.toMatch(/tekrar deneyin|yeniden deneyin/i);
    expect((err as Error).message).toContain('yenile');
  });

  it('409 DIŞINDAKİ hatalar sıradan Error olur — çakışmayla karıştırılmaz', async () => {
    post.mockRejectedValueOnce(hata(422));
    const err = await saveFarmRecord('post', '/farms', {}, 'Çiftlik oluşturulamadı.').catch((e: Error) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(FarmConflictError);
    expect((err as Error).message).toBe('Çiftlik oluşturulamadı.');
  });

  it('başarılı yanıtta gövdeyi döndürür', async () => {
    post.mockResolvedValueOnce({data: {id: 7}});
    await expect(saveFarmRecord('post', '/farms', {code: 'A'}, 'x')).resolves.toEqual({id: 7});
    expect(post).toHaveBeenCalledWith('/farms', {code: 'A'});
  });
});

describe('isoDate', () => {
  it('ayın ilk gününü bir gün geriye KAYDIRMAZ', () => {
    // `new Date('2026-03-01')` UTC gece yarısıdır; Türkiye'de (UTC+3) yerel
    // gösterimi hâlâ 1 Mart olur ama UTC-5 bir makinede 28 Şubat'a düşerdi.
    // Bu yüzden ayrıştırma tamamen metin üzerinden yapılıyor.
    expect(isoDate('2026-03-01')).toBe('01.03.2026');
    expect(isoDate('2026-01-01')).toBe('01.01.2026');
  });

  it('zaman damgasının tarih kısmını alır', () => {
    expect(isoDate('2026-08-15T21:30:00+00:00')).toBe('15.08.2026');
  });

  it('boş değer için tire döner', () => {
    expect(isoDate(null)).toBe('—');
    expect(isoDate(undefined)).toBe('—');
  });
});

describe('biçimlendiriciler', () => {
  it('null oranı SIFIR olarak göstermez', () => {
    // Sunucu alansız sezonda null döner. '0' yazmak "maliyet yok" demek olur.
    expect(money(null)).toBe('—');
    expect(qty(null)).toBe('—');
    expect(money(0)).not.toBe('—');
    expect(qty(0)).not.toBe('—');
  });

  it('string gelen para ve miktarı doğru okur', () => {
    // Sözleşme gereği sunucu string gönderiyor.
    expect(money('2500.00')).toContain('2.500,00');
    expect(qty('18000')).toBe('18.000');
    expect(qty('55.5556')).toBe('55,5556');
  });
});

/**
 * ÖNİZLEME İLE SUNUCU AYNI KURALI UYGULUYOR MU?
 *
 * Bu blok ön yüzü kendi başına değil, SUNUCUYA KARŞI ölçüyor. Aşağıdaki çiftler
 * `backend/tests/test_farm_bku_katalogu.py` içindeki
 * `test_katalog_bitki_eslesmesi_dialektten_bagimsiz` iddialarının BİREBİR
 * AYNISI; ikisi ayrışırsa önizleme kullanıcıya sunucunun çözmeyeceği bir süre
 * gösterir (ya da tersi) ve kullanıcı yanlış rakamı ONAYLAR.
 *
 * Bu, iki tarafı ayrı ayrı doğru olan ama BİRBİRİNE göre yanlış olan hatayı
 * yakalayan tek testtir: 2026-09-01'e kadar sunucu `casefold()`, ön yüz
 * `toLocaleLowerCase('tr')` kullanıyordu ve ikisi tam İ karakterinde
 * ayrılıyordu — iki taraflı hiçbir test bunu görmüyordu.
 */
describe('bitkiKatla — sunucudaki `_bitki_katla` ile İKİZ', () => {
  /** `[a, b, eşleşmeli mi]` — sunucu testindeki tabloyla AYNI. */
  const TABLO: [string, string, boolean][] = [
    ['Domates', 'domates', true],
    ['DOMATES', 'Domates', true],
    ['Domates', 'Biber', false],
    // Türkçe klavyede BÜYÜK yazmak İ üretir; `casefold()` bunları ıskalardı.
    ['\u0130NC\u0130R', 'incir', true],
    ['\u0130ncir', 'incir', true],
    ['B\u0130BER', 'biber', true],
    ['ZEYT\u0130N', 'zeytin', true],
    // Ters yön: Türkçe'de `I`nın küçüğü `ı`dır.
    ['MISIR', 'm\u0131s\u0131r', true],
    ['PATLICAN', 'patl\u0131can', true],
    ['ISPANAK', '\u0131spanak', true],
    ['FINDIK', 'f\u0131nd\u0131k', true],
    // `casefold()` DEĞİL: ß 'ss'e AÇILMAZ.
    ['Wei\u00dfkohl', 'weisskohl', false],
    // BİLEREK KAYBEDİLEN: Latin yazımlı `I`.
    ['Iceberg', 'iceberg', false],
    // Sıra: `I` + U+0307 birleşik dizisi `i`ye iner.
    ['I\u0307ncir', 'incir', true],
  ];

  it.each(TABLO)('%s ~ %s -> %s', (a, b, beklenen) => {
    expect(bitkiKatla(a) === bitkiKatla(b)).toBe(beklenen);
  });

  it('boşluk kırpıyor — sunucu `_metin` ile aynı normalleştirme', () => {
    expect(bitkiKatla('  DOMATES  ')).toBe(bitkiKatla('Domates'));
  });

  it('`toLowerCase()`a dönülürse İ iddiası DÜŞER (kuralın çivisi)', () => {
    // Bu satır kuralın ne OLMADIĞINI kayda geçiriyor: yerel ayarsız küçültme
    // İ'yi i + U+0307 yapar ve 'incir'i BULAMAZ. Sunucu tarafındaki
    // `assert "İNCİR".lower() != "incir"` satırının aynası.
    expect('\u0130NC\u0130R'.toLowerCase()).not.toBe('incir');
    expect('MISIR'.toLowerCase()).not.toBe('m\u0131s\u0131r');
  });
});

describe('catalogueMatch — Türkçe katlama SATIR SEÇİMİNDE de geçerli', () => {
  const satir = (id: number, crop: string, gun: number) => ({
    id, product_id: 77, product_name: 'ORNEK BKU', crop, registration_no: null,
    preharvest_interval_days: gun, reentry_interval_days: null, notes: null,
    status: 'ACTIVE', updated_at: '2026-08-01T10:00:00+00:00',
    // Göç 20260902_0065 (PR #23) bu iki sütunu ZORUNLU kıldı; katlamayla
    // ilgisi yok ama eksik alanla `tsc -b` derlemiyor.
    origin: 'MANUAL' as const, origin_reference: null,
  });

  it('BÜYÜK harfli sezon bitkisi küçük harfli katalog satırını BULUR', () => {
    // Sunucu bu sezonda 21 çözüyor; önizleme 14 gösterseydi kullanıcı yanlış
    // süreyi onaylardı.
    const satirlar = [satir(1, '', 14), satir(2, 'incir', 21)];
    expect(catalogueMatch(satirlar, '\u0130NC\u0130R')?.preharvest_interval_days).toBe(21);
    expect(catalogueMatch(satirlar, 'M\u0131s\u0131r')?.preharvest_interval_days).toBe(14);
  });

  it('eşleşme yoksa bitkiden bağımsız satıra düşer, uydurmaz', () => {
    expect(catalogueMatch([satir(1, '', 14)], 'Iceberg')?.preharvest_interval_days).toBe(14);
    expect(catalogueMatch([satir(2, 'incir', 21)], 'Iceberg')).toBeNull();
  });
});
