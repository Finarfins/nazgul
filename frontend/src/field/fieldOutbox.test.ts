import 'fake-indexeddb/auto';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

import {
  applySnapshot, captureWriteEpoch, closeFieldDatabase, readFieldAttachmentBlob,
  readFieldConflicts, readFieldOutbox, rememberOnlineIdentity,
  resetFieldStorageForTests, unlockFieldWritesAfterLogin,
} from './fieldDatabase';
import {flushFieldOutbox, queueFieldLabor, queueFieldPhoto, queueFieldStatus} from './fieldOutbox';
import {readFieldAccess} from './store';

/**
 * Saha yazma kuyruğunun davranışı.
 *
 * Buradaki en önemli test `önbelleği bozmaz` olanı: sunucu yazmayı reddedip
 * güncel kaydı GÖNDERMEDİĞİNDE (geçersiz geçiş, 404…) elimizde onun gerçeği
 * yoktur. İlk yazışımda oraya uydurma bir iş emri koyup önbelleğe yazıyordum;
 * bu, teknisyenin elindeki doğru kaydı boş bir kayıtla ezmek olurdu.
 */

const api = vi.hoisted(() => ({get: vi.fn(), post: vi.fn()}));
vi.mock('../api', () => ({api}));

const COMPANY = 4;
const USER_ID = 11;
const user = {id: USER_ID, username: 'tech', display_name: 'Teknisyen', role: 'satis', must_change_password: false};

const order = (over: Partial<Record<string, unknown>> = {}) => ({
  id: 55, work_order_no: 'WO-55', status: 'OPEN', version: 'v1',
  updated_at: '2026-08-07T08:00:00+00:00', priority: 'NORMAL', scheduled_date: null,
  complaint: 'Çalışmıyor', diagnosis: null, repair_summary: null, technician_notes: null,
  machine: {id: 9, brand: 'CASE', model: 'CX 8', serial_number: 'SN-1', chassis_number: null, working_hours: null},
  customer: {display_name: 'Tarla Müşterisi', service_address: 'Köy yolu 4'},
  ...over,
});

async function seed() {
  await rememberOnlineIdentity({
    companyId: COMPANY, companyName: 'Firma', userId: USER_ID, user, permissions: ['field_service'],
  });
  await applySnapshot(
    {snapshot_version: 10, generated_at: new Date().toISOString(), user_id: USER_ID, company_id: COMPANY,
     work_orders: [order()]},
    COMPANY, USER_ID, await captureWriteEpoch(),
  );
}

const cachedOrder = async () => (await readFieldAccess(COMPANY, USER_ID))?.workOrders[0];

beforeEach(async () => {
  vi.clearAllMocks();
  await resetFieldStorageForTests();
  unlockFieldWritesAfterLogin();
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  await seed();
});
afterEach(() => {closeFieldDatabase(); vi.restoreAllMocks()});

describe('saha yazma kuyruğu', () => {
  it('kuyruğa alırken teknisyenin GÖRDÜĞÜ sürümü sabitler', async () => {
    const wo = await cachedOrder();
    await queueFieldStatus(COMPANY, USER_ID, wo!, 'IN_PROGRESS');
    const [entry] = await readFieldOutbox(COMPANY, USER_ID);
    expect(entry.kind).toBe('status');
    if (entry.kind !== 'status') throw new Error('durum kaydı bekleniyordu');
    expect(entry.status).toBe('IN_PROGRESS');
    // Gönderim anında tazelenmemeli: teknisyen bu veriyi görerek karar verdi.
    expect(entry.expectedVersion).toBe('v1');
    expect(entry.operationId).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
  });

  it('bekleyen değişikliği önbelleğe YAZMAZ; sunucu gerçeği kirlenmez', async () => {
    const wo = await cachedOrder();
    await queueFieldStatus(COMPANY, USER_ID, wo!, 'IN_PROGRESS');
    expect((await cachedOrder())?.status).toBe('OPEN');
  });

  it('çevrimdışıyken hiç istek yapmaz, kayıt kuyrukta kalır', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    await queueFieldStatus(COMPANY, USER_ID, (await cachedOrder())!, 'IN_PROGRESS');
    const sonuc = await flushFieldOutbox(COMPANY, USER_ID);
    expect(api.post).not.toHaveBeenCalled();
    expect(sonuc.pending).toBe(1);
    expect(await readFieldOutbox(COMPANY, USER_ID)).toHaveLength(1);
  });

  it('sunucu kabul edince kuyruk boşalır ve önbellek sunucunun cevabıyla tazelenir', async () => {
    api.post.mockResolvedValue({data: order({status: 'IN_PROGRESS', version: 'v2'})});
    await queueFieldStatus(COMPANY, USER_ID, (await cachedOrder())!, 'IN_PROGRESS');

    const sonuc = await flushFieldOutbox(COMPANY, USER_ID);

    expect(sonuc.sent).toBe(1);
    expect(await readFieldOutbox(COMPANY, USER_ID)).toHaveLength(0);
    const cached = await cachedOrder();
    expect(cached?.status).toBe('IN_PROGRESS');
    expect(cached?.version).toBe('v2');
  });

  it('409 + güncel kayıt: çakışma yazılır, önbellek SUNUCUNUN hâline döner', async () => {
    api.post.mockRejectedValue({response: {status: 409, data: {
      detail: 'İş emri siz çevrimdışıyken değişti',
      current: order({status: 'COMPLETED', version: 'v9'}),
    }}});
    await queueFieldStatus(COMPANY, USER_ID, (await cachedOrder())!, 'IN_PROGRESS');

    const sonuc = await flushFieldOutbox(COMPANY, USER_ID);

    expect(sonuc.conflicted).toBe(1);
    // Yeniden DENENMEZ: iş emri teknisyenin gördüğünden farklı bir durumda.
    expect(await readFieldOutbox(COMPANY, USER_ID)).toHaveLength(0);
    const [conflict] = await readFieldConflicts(COMPANY, USER_ID);
    expect(conflict.attemptedStatus).toBe('IN_PROGRESS');
    expect(conflict.serverStatus).toBe('COMPLETED');
    expect((await cachedOrder())?.status).toBe('COMPLETED');
  });

  it('güncel kayıt GÖNDERMEYEN 4xx önbelleği BOZMAZ', async () => {
    // Bu testin varlık sebebi bir hata: ilk yazışımda burada uydurma bir iş
    // emri üretip önbelleğe yazıyordum ve teknisyenin gerçek kaydını
    // "BİLİNMİYOR" durumlu boş bir kayıtla eziyordu.
    api.post.mockRejectedValue({response: {status: 409, data: {detail: 'Sahadan OPEN durumundan COMPLETED durumuna geçilemez'}}});
    await queueFieldStatus(COMPANY, USER_ID, (await cachedOrder())!, 'COMPLETED');

    const sonuc = await flushFieldOutbox(COMPANY, USER_ID);

    expect(sonuc.conflicted).toBe(1);
    const cached = await cachedOrder();
    expect(cached?.status).toBe('OPEN');
    expect(cached?.work_order_no).toBe('WO-55');
    expect(cached?.customer.display_name).toBe('Tarla Müşterisi');
    const [conflict] = await readFieldConflicts(COMPANY, USER_ID);
    expect(conflict.workOrderNo).toBe('WO-55');
    expect(conflict.serverStatus).toContain('geçilemez');
  });

  it('şebeke hatası geçici sayılır: kayıt kalır, deneme sayacı ilerler', async () => {
    api.post.mockRejectedValue(new Error('Network Error'));
    await queueFieldStatus(COMPANY, USER_ID, (await cachedOrder())!, 'IN_PROGRESS');

    const sonuc = await flushFieldOutbox(COMPANY, USER_ID);

    expect(sonuc.pending).toBe(1);
    const [entry] = await readFieldOutbox(COMPANY, USER_ID);
    expect(entry.attempts).toBe(1);
    expect(entry.lastError).toBe('Sunucuya ulaşılamadı');
    expect(await readFieldConflicts(COMPANY, USER_ID)).toHaveLength(0);
  });

  it('ilk geçici hatada durur; kalan kayıtlar için zaman aşımı beklemez', async () => {
    api.post.mockRejectedValue(new Error('Network Error'));
    const wo = (await cachedOrder())!;
    await queueFieldStatus(COMPANY, USER_ID, wo, 'IN_PROGRESS');
    await queueFieldStatus(COMPANY, USER_ID, wo, 'WAITING_PARTS');

    await flushFieldOutbox(COMPANY, USER_ID);

    expect(api.post).toHaveBeenCalledTimes(1);
    expect(await readFieldOutbox(COMPANY, USER_ID)).toHaveLength(2);
  });
});

describe('saha fotoğraf kuyruğu', () => {
  const foto = () => new Blob([new Uint8Array([1, 2, 3, 4])], {type: 'image/jpeg'});

  it('kayıt ve dosya BİRLİKTE yazılır', async () => {
    await queueFieldPhoto(COMPANY, USER_ID, 55, foto(), 'saha-1.jpg');
    const [entry] = await readFieldOutbox(COMPANY, USER_ID);
    expect(entry.kind).toBe('attachment');
    if (entry.kind !== 'attachment') throw new Error('ek kaydı bekleniyordu');
    expect(entry.fileName).toBe('saha-1.jpg');
    // Boyut kayıtta duruyor ki listeleyen ekran blob'u açmak zorunda kalmasın.
    expect(entry.byteSize).toBe(4);
    // İÇERİK doğrulanıyor, yalnız "null değil" değil. İlk yazışımda öyleydi ve
    // test boş yere geçiyordu: depo Blob'u bozuk döndürüyor, `size` undefined
    // geliyordu ve gönderim sessizce şebeke hatası sanılıyordu.
    const geri = await readFieldAttachmentBlob(entry.operationId);
    expect(geri).not.toBeNull();
    expect(geri!.size).toBe(4);
    expect(geri!.type).toBe('image/jpeg');
    expect(new Uint8Array(await geri!.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3, 4]));
  });

  it('gönderilince hem kayıt hem DOSYA düşer', async () => {
    // Dosya kalsaydı kimsenin silmediği megabaytlar cihazda birikirdi.
    api.post.mockResolvedValue({data: {id: 7, kind: 'photo'}});
    await queueFieldPhoto(COMPANY, USER_ID, 55, foto(), 'saha-2.jpg');
    const [entry] = await readFieldOutbox(COMPANY, USER_ID);

    const sonuc = await flushFieldOutbox(COMPANY, USER_ID);

    expect(sonuc.sent).toBe(1);
    expect(await readFieldOutbox(COMPANY, USER_ID)).toHaveLength(0);
    expect(await readFieldAttachmentBlob(entry.operationId)).toBeNull();
    // Fotoğraf gönderimi başarılıysa teknisyene çakışma GÖSTERİLMEZ.
    expect(await readFieldConflicts(COMPANY, USER_ID)).toHaveLength(0);
  });

  it('multipart gönderir ve dosyayı ancak gönderim anında okur', async () => {
    api.post.mockResolvedValue({data: {id: 8}});
    await queueFieldPhoto(COMPANY, USER_ID, 55, foto(), 'saha-3.jpg', 'signature');

    await flushFieldOutbox(COMPANY, USER_ID);

    const [yol, govde] = api.post.mock.calls[0];
    expect(yol).toBe('/field/work-orders/55/attachments');
    expect(govde).toBeInstanceOf(FormData);
    expect((govde as FormData).get('kind')).toBe('signature');
    expect((govde as FormData).get('operation_id')).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
  });

  it('şebeke hatasında dosya KORUNUR, kayıt kuyrukta kalır', async () => {
    api.post.mockRejectedValue(new Error('Network Error'));
    await queueFieldPhoto(COMPANY, USER_ID, 55, foto(), 'saha-4.jpg');
    const [entry] = await readFieldOutbox(COMPANY, USER_ID);

    await flushFieldOutbox(COMPANY, USER_ID);

    expect(await readFieldOutbox(COMPANY, USER_ID)).toHaveLength(1);
    // Dosya silinseydi yeniden deneme boşa çıkardı: kayıt var, içerik yok.
    expect(await readFieldAttachmentBlob(entry.operationId)).not.toBeNull();
  });
});

describe('saha işçilik kuyruğu', () => {
  it('saati METİN olarak taşır; float yuvarlaması araya girmez', async () => {
    // Bu değer saat ücretiyle çarpılıp faturaya giriyor. Sayıya çevirmek
    // 0.1+0.2 sınıfı bir yuvarlamayı devreye sokar ve kuruş kaydırır.
    await queueFieldLabor(COMPANY, USER_ID, 55, '2.50');
    const [entry] = await readFieldOutbox(COMPANY, USER_ID);
    expect(entry.kind).toBe('labor');
    if (entry.kind !== 'labor') throw new Error('işçilik kaydı bekleniyordu');
    expect(entry.hours).toBe('2.50');
    expect(typeof entry.hours).toBe('string');
  });

  it('gönderirken ÜCRET GÖNDERMEZ — ücret sunucuda iş emrinden donar', async () => {
    api.post.mockResolvedValue({data: {id: 3, line_status: 'DRAFT'}});
    await queueFieldLabor(COMPANY, USER_ID, 55, '1.25', 'Yağ değişimi');

    await flushFieldOutbox(COMPANY, USER_ID);

    const [yol, govde] = api.post.mock.calls[0];
    expect(yol).toBe('/field/work-orders/55/labor');
    expect(govde).toMatchObject({hours: '1.25', note: 'Yağ değişimi'});
    // Saat ücretinin istemciden gitmemesi işin can alıcı yeri: teknisyenin
    // telefonundan gelen bir tutar faturaya dönüşmemeli.
    expect(Object.keys(govde as object)).not.toContain('hourly_rate');
    expect(await readFieldOutbox(COMPANY, USER_ID)).toHaveLength(0);
  });

  it('çakışmada «ne denendi» metni işçiliği anlatır', async () => {
    api.post.mockRejectedValue({response: {status: 409, data: {detail: 'İş emri kapalı'}}});
    await queueFieldLabor(COMPANY, USER_ID, 55, '3');

    await flushFieldOutbox(COMPANY, USER_ID);

    const [conflict] = await readFieldConflicts(COMPANY, USER_ID);
    expect(conflict.attemptedStatus).toBe('3 SAAT İŞÇİLİK');
    expect(conflict.serverStatus).toBe('İş emri kapalı');
  });
});
