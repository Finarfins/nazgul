/**
 * Tarla kuyruğu — dört iddia, dördü de sessizce bozulabilir.
 *
 * 1. **Şebeke hatasında kimlik DEĞİŞMEZ.** Bu, tüm tasarımın sebebi: istek
 *    sunucuya ulaşıp cevabı kaybolmuş olabilir. Kuyruğa yeni bir kimlikle
 *    alınsaydı sonraki gönderim ikinci bir kayıt açardı — mükerrer faaliyet,
 *    mükerrer maliyet.
 * 2. **4xx kuyruğa ALINMAZ.** Sunucu isteği anlamış ve reddetmiş; tekrar
 *    göndermek aynı cevabı alır ve kuyruğu sonsuza kadar tıkar.
 * 3. **Kuyruk kiracıya göre ayrılır.** Firma değiştiren kullanıcı diğerinin
 *    kaydını göndermemeli.
 * 4. **Reddedilen kayıt SİLİNMEZ.** Kullanıcı ne kaybettiğini görmeli.
 */
import 'fake-indexeddb/auto';
import {beforeEach, describe, expect, it, vi} from 'vitest';

const post = vi.fn();
vi.mock('../api', () => ({api: {post: (...a: unknown[]) => post(...a)}}));

const {
  flushFarmOutbox, readFarmOutbox, removeFarmOperation, saveOrQueueFarmCreate,
} = await import('./farmOutbox');

const sebekeHatasi = () => Object.assign(new Error('Network Error'), {});
const sunucuHatasi = (status: number, detail?: string) =>
  Object.assign(new Error('istek reddedildi'), {response: {status, data: {detail}}});

const FIRMA = 1;
const GOVDE = {season_id: 5, activity_type: 'SPRAYING', performed_at: '2026-04-10T08:00:00.000Z'};

async function kuyruguBosalt() {
  for (const firma of [1, 2]) {
    for (const e of await readFarmOutbox(firma)) await removeFarmOperation(e.operationId);
  }
}

beforeEach(async () => {
  vi.clearAllMocks();
  await kuyruguBosalt();
});

describe('saveOrQueueFarmCreate', () => {
  it('bağlantı varken doğrudan gönderir ve kuyruğa hiç yazmaz', async () => {
    post.mockResolvedValueOnce({data: {id: 42}});
    const sonuc = await saveOrQueueFarmCreate(FIRMA, 'activity', GOVDE);
    expect(sonuc).toEqual({durum: 'sent', kayit: {id: 42}});
    expect(await readFarmOutbox(FIRMA)).toHaveLength(0);
    // Doğrudan gönderimde bile operation_id gidiyor: cevabı kaybolan bir
    // istek sonradan kuyruktan tekrar gönderilebilsin diye.
    expect(post.mock.calls[0][1]).toHaveProperty('operation_id');
  });

  it('ŞEBEKE HATASINDA AYNI KİMLİKLE kuyruğa alır', async () => {
    post.mockRejectedValueOnce(sebekeHatasi());
    const sonuc = await saveOrQueueFarmCreate(FIRMA, 'activity', GOVDE);
    expect(sonuc.durum).toBe('queued');

    const gonderilenKimlik = (post.mock.calls[0][1] as {operation_id: string}).operation_id;
    const [kayit] = await readFarmOutbox(FIRMA);

    // ASIL İDDİA: kuyruktaki kimlik, başarısız gönderimdekiyle AYNI.
    // Farklı olsaydı sunucu iki ayrı işlem görür ve mükerrer kayıt oluşurdu.
    expect(kayit.operationId).toBe(gonderilenKimlik);
    expect((kayit.payload as {operation_id: string}).operation_id).toBe(gonderilenKimlik);
  });

  it('4xx kuyruğa alınmaz, hata çağırana döner', async () => {
    post.mockRejectedValueOnce(sunucuHatasi(422, 'Alan aşımı'));
    await expect(saveOrQueueFarmCreate(FIRMA, 'activity', GOVDE)).rejects.toBeTruthy();
    expect(await readFarmOutbox(FIRMA)).toHaveLength(0);
  });

  it('girdi kaydı doğru faaliyet adresine gider', async () => {
    post.mockResolvedValueOnce({data: {id: 7}});
    await saveOrQueueFarmCreate(FIRMA, 'activity_input', {input_name: 'Üre'}, 9);
    expect(post.mock.calls[0][0]).toBe('/field-activities/9/inputs');
  });
});

describe('flushFarmOutbox', () => {
  it('kuyruğu boşaltır ve gönderilen kaydı siler', async () => {
    post.mockRejectedValueOnce(sebekeHatasi());
    await saveOrQueueFarmCreate(FIRMA, 'harvest', {season_id: 5, quantity: '100'});
    expect(await readFarmOutbox(FIRMA)).toHaveLength(1);

    post.mockResolvedValueOnce({data: {id: 3}});
    const sonuc = await flushFarmOutbox(FIRMA);
    expect(sonuc).toEqual({sent: 1, rejected: 0, pending: 0});
    expect(await readFarmOutbox(FIRMA)).toHaveLength(0);
  });

  it('şebeke hâlâ yoksa kayıt kuyrukta kalır ve deneme sayacı artar', async () => {
    post.mockRejectedValueOnce(sebekeHatasi());
    await saveOrQueueFarmCreate(FIRMA, 'harvest', {season_id: 5});

    post.mockRejectedValueOnce(sebekeHatasi());
    const sonuc = await flushFarmOutbox(FIRMA);
    expect(sonuc.pending).toBe(1);
    const [kayit] = await readFarmOutbox(FIRMA);
    expect(kayit.attempts).toBe(1);
    expect(kayit.lastError).toBeTruthy();
    expect(kayit.rejectedReason).toBeNull();
  });

  it('4xx alan bekleyen kayıt SİLİNMEZ, reddedildi olarak işaretlenir', async () => {
    post.mockRejectedValueOnce(sebekeHatasi());
    await saveOrQueueFarmCreate(FIRMA, 'harvest', {season_id: 5});

    post.mockRejectedValueOnce(sunucuHatasi(404, 'Kayıt bulunamadı'));
    const sonuc = await flushFarmOutbox(FIRMA);
    expect(sonuc.rejected).toBe(1);

    // Silinseydi kullanıcı ne kaybettiğini asla öğrenemezdi.
    const [kayit] = await readFarmOutbox(FIRMA);
    expect(kayit.rejectedReason).toBe('Kayıt bulunamadı');

    // Ve bir daha DENENMEZ: kuyruğu sonsuza kadar tıkardı.
    post.mockClear();
    const ikinci = await flushFarmOutbox(FIRMA);
    expect(post).not.toHaveBeenCalled();
    expect(ikinci.rejected).toBe(1);
  });

  it('kuyruk kiracıya göre ayrı: başka firmanın kaydı gönderilmez', async () => {
    post.mockRejectedValueOnce(sebekeHatasi());
    await saveOrQueueFarmCreate(1, 'harvest', {season_id: 5});
    post.mockRejectedValueOnce(sebekeHatasi());
    await saveOrQueueFarmCreate(2, 'harvest', {season_id: 9});

    expect(await readFarmOutbox(1)).toHaveLength(1);
    expect(await readFarmOutbox(2)).toHaveLength(1);

    post.mockClear();
    post.mockResolvedValue({data: {id: 1}});
    const sonuc = await flushFarmOutbox(1);
    expect(sonuc.sent).toBe(1);
    // 2 numaralı firmanın kaydı YERİNDE durmalı.
    expect(await readFarmOutbox(2)).toHaveLength(1);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it('ilk geçici hatada durur — kalan kayıtlar için zaman aşımı beklenmez', async () => {
    for (const q of [1, 2, 3]) {
      post.mockRejectedValueOnce(sebekeHatasi());
      await saveOrQueueFarmCreate(FIRMA, 'harvest', {season_id: q});
    }
    expect(await readFarmOutbox(FIRMA)).toHaveLength(3);

    post.mockClear();
    post.mockResolvedValueOnce({data: {id: 1}});   // 1. gider
    post.mockRejectedValueOnce(sebekeHatasi());     // 2. düşer → dur
    const sonuc = await flushFarmOutbox(FIRMA);
    expect(sonuc).toEqual({sent: 1, rejected: 0, pending: 1});
    // 3. kayıt HİÇ denenmedi.
    expect(post).toHaveBeenCalledTimes(2);
  });
});
