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

const {FarmConflictError, isoDate, money, qty, saveFarmRecord} = await import('./farmApi');

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
