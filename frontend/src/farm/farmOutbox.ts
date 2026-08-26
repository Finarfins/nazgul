/**
 * Tarla yazmalarının çevrimdışı kuyruğu (mobil-erp#2, FAZ 4).
 *
 * NEDEN AYRI BİR KUYRUK — `field/fieldOutbox.ts` OLDUĞU GİBİ KULLANILAMADI.
 * Oradaki kuyruk her işlemi bir İŞ EMRİNE bağlıyor ve gönderim sonrası cevabı
 * iş emri önbelleğine yazıyor (`settleFieldOperation`). Tarla faaliyetinin iş
 * emri yok ve yazılacak bir önbellek de yok. O yapıyı zorlamak, tarla
 * kayıtlarına sahte bir iş emri kimliği uydurmak demekti.
 *
 * ORTAK OLAN ŞEY BİLEREK ORTAK: işlem kimliği biçimi (`^[A-Za-z0-9_-]{8,64}$`)
 * ve kimliğin KUYRUĞA ALINIRKEN üretilmesi. Gönderim anında üretilseydi her
 * yeniden deneme yeni bir kimlik alır ve sunucudaki tekrar koruması hiç
 * çalışmazdı.
 *
 * KUYRUKTA YALNIZ OLUŞTURMA VAR, GÜNCELLEME YOK. Güncellemeler iyimser kilit
 * (`expected_updated_at`) kullanıyor ve çevrimdışı beklemiş bir güncellemenin
 * sürümü neredeyse kesin bayatlamış olur — kuyruğa almak, kullanıcıya saatler
 * sonra "çakıştı" demekten başka işe yaramazdı. Oluşturmada böyle bir sorun
 * yok: yeni kayıt kimsenin sürümüyle yarışmıyor.
 */
import {api} from '../api';

const DB_ADI = 'harman-tarla';
const DEPO = 'outbox';
const SURUM = 1;

export type FarmOutboxKind = 'activity' | 'activity_input' | 'harvest';

export type FarmOutboxEntry = {
  operationId: string;
  companyId: number;
  kind: FarmOutboxKind;
  /** `activity_input` için bağlı olduğu faaliyetin kimliği; diğerlerinde null. */
  activityId: number | null;
  /** Sunucuya gidecek gövde (operation_id dahil edilerek gönderilir). */
  payload: Record<string, unknown>;
  queuedAt: number;
  attempts: number;
  lastError: string | null;
  /** Sunucu kalıcı olarak reddettiyse dolu olur; kayıt kuyrukta ama ölü. */
  rejectedReason: string | null;
};

// Sunucudaki doğrulama ile aynı: ^[A-Za-z0-9_-]{8,64}$
export const newFarmOperationId = (): string =>
  (globalThis.crypto?.randomUUID?.() ?? `op-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`)
    .replaceAll(/[^A-Za-z0-9_-]/g, '')
    .slice(0, 64);

function acDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const istek = indexedDB.open(DB_ADI, SURUM);
    istek.onupgradeneeded = () => {
      const db = istek.result;
      if (!db.objectStoreNames.contains(DEPO)) {
        const depo = db.createObjectStore(DEPO, {keyPath: 'operationId'});
        // Firma başına sıralı okuma için: kuyruk kiracıya göre ayrılmalı,
        // yoksa firma değiştiren kullanıcı diğerinin kaydını gönderirdi.
        depo.createIndex('companyId', 'companyId', {unique: false});
      }
    };
    istek.onsuccess = () => resolve(istek.result);
    istek.onerror = () => reject(istek.error);
  });
}

async function islem<T>(mod: IDBTransactionMode, is: (depo: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await acDatabase();
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(DEPO, mod);
    const istek = is(tx.objectStore(DEPO));
    istek.onsuccess = () => resolve(istek.result);
    istek.onerror = () => reject(istek.error);
    tx.oncomplete = () => db.close();
  });
}

/** Kuyruğa yeni bir oluşturma isteği koyar ve kimliğini döndürür. */
export async function queueFarmCreate(
  companyId: number, kind: FarmOutboxKind, payload: Record<string, unknown>,
  activityId: number | null = null,
): Promise<string> {
  const operationId = newFarmOperationId();
  await islem('readwrite', depo => depo.put({
    operationId, companyId, kind, activityId,
    payload: {...payload, operation_id: operationId},
    queuedAt: Date.now(), attempts: 0, lastError: null, rejectedReason: null,
  } satisfies FarmOutboxEntry));
  return operationId;
}

export type SaveOrQueueResult =
  | {durum: 'sent'; kayit: unknown}
  | {durum: 'queued'; operationId: string};

/**
 * Önce DOĞRUDAN gönderir; şebeke yüzünden başarısız olursa AYNI işlem
 * kimliğiyle kuyruğa alır.
 *
 * KİMLİĞİN AYNI KALMASI BU FONKSİYONUN BÜTÜN SEBEBİ. İstek sunucuya ulaşmış
 * ama cevabı yolda kaybolmuş olabilir; istemci bunu başarısız sayar. Kuyruğa
 * YENİ bir kimlikle alınsaydı, sonraki gönderim sunucuda ikinci bir kayıt
 * açardı — mükerrer faaliyet, mükerrer maliyet. Aynı kimlikle gidince sunucu
 * "bunu zaten uyguladım" deyip ilk oluşturduğu satırı döndürüyor.
 *
 * 4xx KUYRUĞA ALINMAZ: sunucu isteği anlamış ve reddetmiş; tekrar göndermek
 * aynı cevabı alır. Hata doğrudan çağırana döner ki kullanıcı düzeltebilsin.
 */
export async function saveOrQueueFarmCreate(
  companyId: number, kind: FarmOutboxKind, payload: Record<string, unknown>,
  activityId: number | null = null,
): Promise<SaveOrQueueResult> {
  const operationId = newFarmOperationId();
  const govde = {...payload, operation_id: operationId};
  const cevrimdisi = typeof navigator !== 'undefined' && navigator.onLine === false;

  if (!cevrimdisi) {
    try {
      const {data} = await api.post(UC[kind](activityId), govde);
      return {durum: 'sent', kayit: data};
    } catch (error: unknown) {
      const status = (error as {response?: {status?: number}})?.response?.status;
      // Sunucu anladı ve reddetti: kuyruğa almak anlamsız, hatayı göster.
      if (typeof status === 'number' && status >= 400 && status < 500) throw error;
    }
  }

  await islem('readwrite', depo => depo.put({
    operationId, companyId, kind, activityId, payload: govde,
    queuedAt: Date.now(), attempts: 0, lastError: null, rejectedReason: null,
  } satisfies FarmOutboxEntry));
  return {durum: 'queued', operationId};
}

/** Firmanın kuyruğu, eskiden yeniye. */
export async function readFarmOutbox(companyId: number): Promise<FarmOutboxEntry[]> {
  const hepsi = await islem<FarmOutboxEntry[]>('readonly', depo => depo.getAll() as IDBRequest<FarmOutboxEntry[]>);
  return hepsi
    .filter(e => e.companyId === companyId)
    .sort((a, b) => a.queuedAt - b.queuedAt);
}

export async function removeFarmOperation(operationId: string): Promise<void> {
  await islem('readwrite', depo => depo.delete(operationId) as unknown as IDBRequest<undefined>);
}

async function guncelle(entry: FarmOutboxEntry, degisiklik: Partial<FarmOutboxEntry>): Promise<void> {
  await islem('readwrite', depo => depo.put({...entry, ...degisiklik}));
}

const UC = {
  activity: () => '/field-activities',
  harvest: () => '/field-harvests',
  activity_input: (activityId: number | null) => `/field-activities/${activityId}/inputs`,
};

export type FarmFlushResult = {sent: number; rejected: number; pending: number};

/**
 * Kuyruğu boşaltmayı dener. Çevrimdışıysa hiç uğraşmaz.
 *
 * Sıra en eskiden yeniye ve TEK TEK: bir girdi, kendisinden önce kuyruğa
 * girmiş faaliyete bağlı olabilir ve o faaliyet daha sunucuya ulaşmamışsa
 * girdinin gideceği adres yoktur.
 */
export async function flushFarmOutbox(companyId: number): Promise<FarmFlushResult> {
  const sonuc: FarmFlushResult = {sent: 0, rejected: 0, pending: 0};
  const kuyruk = await readFarmOutbox(companyId);
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    sonuc.pending = kuyruk.filter(e => !e.rejectedReason).length;
    return sonuc;
  }
  for (const entry of kuyruk) {
    // Kalıcı reddedilmiş kayıt tekrar denenmez; kullanıcı görüp silecek.
    if (entry.rejectedReason) {
      sonuc.rejected += 1;
      continue;
    }
    const durum = await gonder(entry);
    if (durum === 'sent') sonuc.sent += 1;
    else if (durum === 'rejected') sonuc.rejected += 1;
    else {
      sonuc.pending += 1;
      // İlk geçici hatada duruyoruz: şebeke gittiyse kalanlar için de
      // gidecektir ve her biri için ayrı zaman aşımı beklemek arayüzü
      // dakikalarca kilitler.
      break;
    }
  }
  return sonuc;
}

async function gonder(entry: FarmOutboxEntry): Promise<'sent' | 'rejected' | 'retry'> {
  try {
    // Sunucu aynı `operation_id`'yi ikinci kez görürse yeni kayıt açmaz,
    // ilk oluşturduğu satırı döndürür. Bu yüzden burada "zaten gönderilmiş
    // mi" diye ayrıca kontrol etmiyoruz — cevap her hâlükârda doğru.
    await api.post(UC[entry.kind](entry.activityId), entry.payload);
    await removeFarmOperation(entry.operationId);
    return 'sent';
  } catch (error: unknown) {
    const status = (error as {response?: {status?: number}})?.response?.status;

    // 4xx: istek olduğu gibi bir daha kabul edilmeyecek (geçersiz veri, silinmiş
    // sezon, yetki). Sonsuza kadar denemek kuyruğu tıkar. Kayıt SİLİNMİYOR —
    // kullanıcı ne kaybettiğini görsün ve gerekirse elle yeniden girsin.
    if (typeof status === 'number' && status >= 400 && status < 500) {
      const detail = (error as {response?: {data?: {detail?: string}}})?.response?.data?.detail;
      await guncelle(entry, {rejectedReason: detail || `Sunucu isteği reddetti (${status})`});
      return 'rejected';
    }

    // Şebeke hatası veya 5xx: geçici. Kayıt kuyrukta kalır.
    await guncelle(entry, {
      attempts: entry.attempts + 1,
      lastError: status ? `Sunucu ${status}` : 'Sunucuya ulaşılamadı',
    });
    return 'retry';
  }
}
