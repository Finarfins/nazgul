export const FIELD_DB_NAME = 'sungur-field-m1';
export const FIELD_DB_VERSION = 2;
export const OFFLINE_VERIFICATION_MS = 12 * 60 * 60 * 1000;
export const CLOCK_ROLLBACK_TOLERANCE_MS = 60 * 1000;
export const FIELD_BROWSER_STORAGE_PREFIX = 'sungur-field-';
export const FIELD_CACHE_PREFIX = 'sungur-field-pwa-v1:';

export type FieldUser = {
  id: number;
  username: string;
  display_name: string;
  role: string;
  must_change_password: boolean;
};
export type FieldMeta = {
  partition: string;
  companyId: number;
  companyName: string;
  userId: number;
  user: FieldUser;
  permissions: string[];
  lastOnlineVerifiedAt: number;
  lastObservedAt: number;
  lastSeenNow: number;
  lastSyncAt?: number;
  snapshotVersion: number;
  revokedLocallyAt?: number;
};
export type FieldWorkOrder = {
  id: number;
  work_order_no: string;
  status: string;
  version: string;
  updated_at: string;
  priority: string;
  scheduled_date: string | null;
  complaint: string | null;
  diagnosis: string | null;
  repair_summary: string | null;
  technician_notes: string | null;
  machine: {
    id: number; brand: string | null; model: string | null;
    serial_number: string | null; chassis_number: string | null;
    working_hours: string | null;
  };
  customer: {display_name: string; service_address: string | null};
  parts: FieldPart[];
  attachments: FieldAttachment[];
};
export type FieldAttachment = {
  id: number;
  kind: string;
  file_name: string;
  created_at: string;
};
export type FieldPart = {
  id: number;
  product_name: string;
  // Miktar METİN: sunucu da metin gönderiyor. Sayıya çevirmek 0.1+0.2
  // yuvarlamasını devreye sokar ve bu değer doğrudan faturaya giden bir
  // miktarı temsil ediyor.
  quantity: string;
  unit: string | null;
  line_status: string;
};
export type FieldSnapshot = {
  snapshot_version: number;
  generated_at: string;
  user_id: number;
  company_id: number;
  work_orders: FieldWorkOrder[];
};
export type FieldAccess = {meta: FieldMeta; workOrders: FieldWorkOrder[]};

/**
 * Gönderilmeyi bekleyen bir saha yazması.
 *
 * `expectedVersion` KUYRUĞA ALINDIĞI ANDAKİ sunucu sürümüdür ve sonradan
 * güncellenmez. Teknisyen değişikliği o veriyi görerek yaptı; sunucu arada
 * başka bir şey yazdıysa bunun bir çakışma olarak dönmesi İSTENEN davranış.
 * Gönderim anında taze sürümle değiştirmek, görülmemiş bir durumun üstüne
 * körlemesine yazmak olurdu.
 */
type FieldOutboxBase = {
  operationId: string;
  companyId: number;
  userId: number;
  workOrderId: number;
  createdAt: number;
  attempts: number;
  lastError?: string;
};
export type FieldStatusOutbox = FieldOutboxBase & {
  kind: 'status';
  status: string;
  expectedVersion: string;
};
/**
 * Bekleyen fotoğraf/imza. Dosyanın KENDİSİ burada değil, `photo_blobs`
 * deposunda aynı `operationId` ile duruyor.
 *
 * Ayrı tutulmasının sebebi: kuyruğu okuyan her yer (liste ekranı, sayaç,
 * gönderim döngüsü) megabaytlarca ikili veriyi belleğe çekmek zorunda
 * kalmasın. Blob yalnız gönderim anında okunuyor.
 */
export type FieldAttachmentOutbox = FieldOutboxBase & {
  kind: 'attachment';
  attachmentKind: 'photo' | 'signature';
  fileName: string;
  byteSize: number;
};
/**
 * Bekleyen işçilik kaydı.
 *
 * Saat METİN olarak taşınıyor — miktarla aynı gerekçe: bu değer saat ücretiyle
 * çarpılıp faturaya giriyor ve float yuvarlaması kuruş kaydırır.
 */
export type FieldLaborOutbox = FieldOutboxBase & {
  kind: 'labor';
  hours: string;
  note?: string;
};
export type FieldOutboxEntry = FieldStatusOutbox | FieldAttachmentOutbox | FieldLaborOutbox;

/** Sunucunun reddettiği bir yazma; teknisyene gösterilip kapatılana kadar durur. */
export type FieldConflict = {
  operationId: string;
  companyId: number;
  userId: number;
  workOrderId: number;
  workOrderNo: string;
  attemptedStatus: string;
  serverStatus: string;
  detectedAt: number;
};

const partition = (companyId: number, userId: number) => `${companyId}:${userId}`;
export const offlineSessionExpiresAt = (meta: FieldMeta) =>
  meta.lastOnlineVerifiedAt + OFFLINE_VERIFICATION_MS;
export const isOfflineAccessValid = (meta: FieldMeta | null, now = Date.now()) => Boolean(
  meta && !meta.revokedLocallyAt && now >= meta.lastOnlineVerifiedAt
  && now >= meta.lastSeenNow - CLOCK_ROLLBACK_TOLERANCE_MS && now < offlineSessionExpiresAt(meta),
);
const requestResult = <T>(request: IDBRequest<T>) => new Promise<T>((resolve, reject) => {
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});
const transactionDone = (tx: IDBTransaction) => new Promise<void>((resolve, reject) => {
  tx.oncomplete = () => resolve();
  tx.onabort = tx.onerror = () => reject(tx.error || new Error('IndexedDB işlemi tamamlanamadı'));
});

let connection: IDBDatabase | null = null;
let writesLocked = false;
async function openFieldDb(): Promise<IDBDatabase> {
  if (connection) return connection;
  const request = indexedDB.open(FIELD_DB_NAME, FIELD_DB_VERSION);
  request.onupgradeneeded = () => {
    const db = request.result;
    if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta', {keyPath: 'partition'});
    if (!db.objectStoreNames.contains('work_order_snapshots')) {
      db.createObjectStore('work_order_snapshots', {keyPath: ['companyId', 'userId', 'workOrderId']});
    }
    // M1 bu store'lara yazmaz. Şimdiden additive oluşturulmaları, sonraki
    // sürüm yükseltmesinde bekleyen sentetik outbox'ın korunmasını sağlar.
    for (const name of ['outbox', 'photo_blobs', 'conflicts']) {
      if (!db.objectStoreNames.contains(name)) db.createObjectStore(name, {keyPath: 'operationId'});
    }
    if (!db.objectStoreNames.contains('control')) {
      const control=db.createObjectStore('control',{keyPath:'key'});
      control.put({key:'write_epoch',value:0});
    }
  };
  connection = await requestResult(request);
  connection.onversionchange = () => {
    connection?.close();
    connection = null;
    window.dispatchEvent(new Event('field-db-versionchange'));
  };
  return connection;
}
export function closeFieldDatabase(){connection?.close();connection=null}

export class StaleFieldWriteError extends Error {
  constructor(message:string){
    super(message);
    this.name='StaleFieldWriteError';
  }
}
export async function captureWriteEpoch():Promise<number>{
  if(writesLocked)throw new StaleFieldWriteError('Saha yazma yüzeyi kilitli');
  const db=await openFieldDb(),tx=db.transaction('control','readonly');
  const row=await requestResult(tx.objectStore('control').get('write_epoch')) as {value:number}|undefined;
  await transactionDone(tx);return row?.value||0;
}
async function assertWriteEpoch(store:IDBObjectStore,expected:number){
  const row=await requestResult(store.get('write_epoch')) as {value:number}|undefined;
  if(writesLocked||(row?.value||0)!==expected){
    console.error('field-write-rejected',{expected,actual:row?.value||0});
    throw new StaleFieldWriteError('Saha yazması eski oturum kuşağına ait');
  }
}

const assertTextOrNull = (value: unknown, field: string) => {
  if (value !== null && typeof value !== 'string') throw new Error(`Geçersiz saha alanı: ${field}`);
};
export function validateSnapshot(value: unknown, companyId: number, userId: number): FieldSnapshot {
  if (!value || typeof value !== 'object') throw new Error('Geçersiz saha snapshot');
  const snapshot = value as FieldSnapshot;
  if (!Number.isSafeInteger(snapshot.snapshot_version) || snapshot.snapshot_version <= 0
      || snapshot.company_id !== companyId || snapshot.user_id !== userId
      || !Array.isArray(snapshot.work_orders)) throw new Error('Snapshot kapsamı geçersiz');
  const ids = new Set<number>();
  for (const item of snapshot.work_orders) {
    if (!Number.isSafeInteger(item.id) || ids.has(item.id) || typeof item.work_order_no !== 'string'
      || typeof item.status !== 'string' || typeof item.version !== 'string'
      || !item.machine || !item.customer || typeof item.customer.display_name !== 'string') {
      throw new Error('Snapshot bütünlüğü geçersiz');
    }
    ids.add(item.id);
    assertTextOrNull(item.customer.service_address, 'service_address');
    // DTO allow-list dışı bir müşteri alanı kalıcı depoya asla taşınmasın.
    if (Object.keys(item.customer).some(key => !['display_name', 'service_address'].includes(key))) {
      throw new Error('Snapshot müşteri alanı allow-list dışında');
    }
    // Parçalar için de aynı kapı. Sunucuya "fiyatı da gönderiver" demek tek
    // satırlık bir değişiklik; o satır atıldığı gün fiyat teknisyenin
    // cihazında şifresiz durmaya başlar ve kimse fark etmez. Allow-list burada
    // olduğu için fark edilir: snapshot reddedilir.
    //
    // Alanın KENDİSİ isteğe bağlı, içeriği değil. Sebebi dağıtım: yeni istemci
    // henüz güncellenmemiş bir sunucuyla konuşursa `parts` hiç gelmez ve
    // zorunlu tutsaydık snapshot komple reddedilir, teknisyen bayat veriyle
    // kalırdı. Gelirse tam denetimden geçer.
    if (item.parts === undefined) item.parts = [];
    if (!Array.isArray(item.parts)) throw new Error('Snapshot parça listesi geçersiz');
    for (const part of item.parts) {
      if (!Number.isSafeInteger(part.id) || typeof part.product_name !== 'string'
        || typeof part.quantity !== 'string' || typeof part.line_status !== 'string') {
        throw new Error('Snapshot parça bütünlüğü geçersiz');
      }
      assertTextOrNull(part.unit, 'unit');
      if (Object.keys(part).some(key =>
        !['id', 'product_name', 'quantity', 'unit', 'line_status'].includes(key))) {
        throw new Error('Snapshot parça alanı allow-list dışında');
      }
    }
    // Ekler için de aynı kapı. Buradaki asıl risk dosya İÇERİĞİNİN bir gün
    // snapshot'a girmesi: fotoğraflar megabaytlarca yer tutar, senkronu
    // dakikalara çıkarır ve cihaz depolamasını doldurur. Allow-list böyle bir
    // alanı sessizce kabul etmiyor.
    if (item.attachments === undefined) item.attachments = [];
    if (!Array.isArray(item.attachments)) throw new Error('Snapshot ek listesi geçersiz');
    for (const ek of item.attachments) {
      if (!Number.isSafeInteger(ek.id) || typeof ek.kind !== 'string'
        || typeof ek.file_name !== 'string' || typeof ek.created_at !== 'string') {
        throw new Error('Snapshot ek bütünlüğü geçersiz');
      }
      if (Object.keys(ek).some(key => !['id', 'kind', 'file_name', 'created_at'].includes(key))) {
        throw new Error('Snapshot ek alanı allow-list dışında');
      }
    }
  }
  return snapshot;
}

export async function rememberOnlineIdentity(meta: Omit<FieldMeta, 'partition'|'lastOnlineVerifiedAt'|'lastObservedAt'|'lastSeenNow'|'snapshotVersion'>) {
  const expectedEpoch=await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control','meta'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'),expectedEpoch);
  const store = tx.objectStore('meta');
  const key = partition(meta.companyId, meta.userId);
  const previous = await requestResult(store.get(key)) as FieldMeta | undefined;
  const now = Date.now();
  store.put({...previous, ...meta, partition: key, lastOnlineVerifiedAt: now, lastObservedAt: now,
    lastSeenNow: Math.max(previous?.lastSeenNow || 0, now),
    snapshotVersion: previous?.snapshotVersion || 0, revokedLocallyAt: undefined});
  await transactionDone(tx);
}

export async function applySnapshot(raw: unknown, companyId: number, userId: number, expectedEpoch:number): Promise<boolean> {
  const snapshot = validateSnapshot(raw, companyId, userId);
  const db = await openFieldDb();
  const tx = db.transaction(['control','meta', 'work_order_snapshots'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'),expectedEpoch);
  const metaStore = tx.objectStore('meta');
  const snapshotStore = tx.objectStore('work_order_snapshots');
  const key = partition(companyId, userId);
  const meta = await requestResult(metaStore.get(key)) as FieldMeta | undefined;
  if (!meta || meta.revokedLocallyAt || snapshot.snapshot_version <= meta.snapshotVersion) {
    tx.abort();
    return false;
  }
  const range = IDBKeyRange.bound([companyId, userId, 0], [companyId, userId, Number.MAX_SAFE_INTEGER]);
  const existing = await requestResult(snapshotStore.getAllKeys(range)) as IDBValidKey[];
  const incomingIds = new Set(snapshot.work_orders.map(item => item.id));
  for (const item of snapshot.work_orders) {
    snapshotStore.put({companyId, userId, workOrderId: item.id, serverVersion: item.version,
      cachedAt: Date.now(), payload: item});
  }
  for (const existingKey of existing) {
    const id = Number((existingKey as [number, number, number])[2]);
    if (!incomingIds.has(id)) snapshotStore.delete(existingKey);
  }
  metaStore.put({...meta, snapshotVersion: snapshot.snapshot_version, lastSyncAt: Date.now(),
    lastObservedAt: Date.now(), lastSeenNow: Math.max(meta.lastSeenNow, Date.now())});
  await transactionDone(tx);
  return true;
}

/** Çakışma kaydında gösterilecek "ne denendi" metni; iki kuyruk türünü de karşılar. */
const _denenenIslem = (entry: FieldOutboxEntry) =>
  entry.kind === 'status' ? entry.status
    : entry.kind === 'labor' ? `${entry.hours} SAAT İŞÇİLİK`
    : entry.attachmentKind === 'signature' ? 'İMZA' : 'FOTOĞRAF';

const belongsTo = (row: {companyId: number; userId: number}, companyId: number, userId: number) =>
  row.companyId === companyId && row.userId === userId;

/**
 * Bir saha yazmasını kuyruğa alır.
 *
 * `work_order_snapshots` BİLEREK değiştirilmiyor: orası sunucu gerçeğinin
 * önbelleği. Bekleyen değişiklik yalnız outbox'ta durur ve arayüz ikisini
 * okurken birleştirir. Bunun karşılığı, çakışmada geri almanın kuyruk kaydını
 * silmekten ibaret olması — önbelleğe iyimser yazsaydık "hangi alan benimdi,
 * hangisi sunucunun" sorusunu her seferinde çözmek gerekirdi.
 */
export async function queueFieldStatusChange(
  entry: Omit<FieldStatusOutbox, 'createdAt' | 'attempts' | 'kind'>,
): Promise<void> {
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'outbox'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  tx.objectStore('outbox').put({
    ...entry, kind: 'status', createdAt: Date.now(), attempts: 0,
  } satisfies FieldStatusOutbox);
  await transactionDone(tx);
}

/**
 * Fotoğraf/imzayı kuyruğa alır: kayıt ve dosya TEK işlemde yazılır.
 *
 * Tek işlem olması şart. İkisi ayrı yazılsaydı arada kesilen bir çekim ya
 * dosyasız bir kuyruk kaydı (gönderimde çöker) ya da kayıtsız bir dosya
 * (kimsenin silmediği, sessizce yer kaplayan bir blob) bırakırdı.
 */
export async function queueFieldAttachment(
  entry: Omit<FieldAttachmentOutbox, 'createdAt' | 'attempts' | 'kind' | 'byteSize'>,
  blob: Blob,
): Promise<void> {
  // HAM BAYT saklanıyor, Blob DEĞİL — bilinçli.
  //
  // Blob'un IndexedDB'den sağlam geçmesi ortama bağlı. Ölçüldü: bu depodaki
  // test ortamında (jsdom + fake-indexeddb) geri okunan nesnenin `size`ı
  // undefined geliyor ve `FormData.append` onunla patlıyor. Hata da şebeke
  // hatası gibi görünüp kaydı sonsuza kadar kuyrukta bırakıyordu.
  //
  // Aynı kırılganlık bazı WebView sürümlerinde de bildirildi. ArrayBuffer her
  // yerde güvenle saklanıyor; Blob gönderim anında yeniden kuruluyor.
  const bytes = await blob.arrayBuffer();
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'outbox', 'photo_blobs'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  tx.objectStore('outbox').put({
    ...entry, kind: 'attachment', byteSize: bytes.byteLength, createdAt: Date.now(), attempts: 0,
  } satisfies FieldAttachmentOutbox);
  tx.objectStore('photo_blobs').put({
    operationId: entry.operationId, bytes, mimeType: blob.type || 'application/octet-stream',
  });
  await transactionDone(tx);
}

export async function readFieldAttachmentBlob(operationId: string): Promise<Blob | null> {
  const db = await openFieldDb();
  const tx = db.transaction('photo_blobs', 'readonly');
  const row = await requestResult(tx.objectStore('photo_blobs').get(operationId)) as
    {bytes: ArrayBuffer; mimeType: string} | undefined;
  await transactionDone(tx);
  if (!row?.bytes) return null;
  return new Blob([row.bytes], {type: row.mimeType});
}

export async function readFieldOutbox(companyId: number, userId: number): Promise<FieldOutboxEntry[]> {
  const db = await openFieldDb();
  const tx = db.transaction('outbox', 'readonly');
  const rows = await requestResult(tx.objectStore('outbox').getAll()) as FieldOutboxEntry[];
  await transactionDone(tx);
  // En eskiden yeniye: aynı iş emrine ard arda yapılan değişiklikler
  // sunucuya yapıldıkları sırayla gitmeli.
  return rows.filter(row => belongsTo(row, companyId, userId)).sort((a, b) => a.createdAt - b.createdAt);
}

export async function readFieldConflicts(companyId: number, userId: number): Promise<FieldConflict[]> {
  const db = await openFieldDb();
  const tx = db.transaction('conflicts', 'readonly');
  const rows = await requestResult(tx.objectStore('conflicts').getAll()) as FieldConflict[];
  await transactionDone(tx);
  return rows.filter(row => belongsTo(row, companyId, userId)).sort((a, b) => b.detectedAt - a.detectedAt);
}

/** Sunucu yazmayı kabul etti: kuyruk kaydı düşer, önbellek sunucunun cevabıyla tazelenir. */
export async function settleFieldOperation(
  operationId: string, companyId: number, userId: number, server: FieldWorkOrder,
): Promise<void> {
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'outbox', 'photo_blobs', 'work_order_snapshots'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  tx.objectStore('outbox').delete(operationId);
  // Ek kuyruğuysa dosyası da düşer; yoksa kimsenin silmediği megabaytlar birikir.
  tx.objectStore('photo_blobs').delete(operationId);
  tx.objectStore('work_order_snapshots').put({
    companyId, userId, workOrderId: server.id, serverVersion: server.version,
    cachedAt: Date.now(), payload: server,
  });
  await transactionDone(tx);
}

/**
 * Sunucu yazmayı reddetti (409).
 *
 * Kuyruk kaydı SİLİNİR, yeniden denenmez: iş emri teknisyenin gördüğünden
 * farklı bir durumda ve aynı geçişi körlemesine tekrarlamak yanlış olabilir.
 * Yerine bir çakışma kaydı bırakılır ki teknisyen ne olduğunu görsün, ve
 * önbellek sunucunun güncel hâliyle tazelenir.
 */
export async function conflictFieldOperation(
  entry: FieldOutboxEntry, server: FieldWorkOrder,
): Promise<void> {
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'outbox', 'photo_blobs', 'conflicts', 'work_order_snapshots'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  tx.objectStore('outbox').delete(entry.operationId);
  tx.objectStore('photo_blobs').delete(entry.operationId);
  tx.objectStore('conflicts').put({
    operationId: entry.operationId, companyId: entry.companyId, userId: entry.userId,
    workOrderId: entry.workOrderId, workOrderNo: server.work_order_no,
    attemptedStatus: _denenenIslem(entry), serverStatus: server.status, detectedAt: Date.now(),
  } satisfies FieldConflict);
  tx.objectStore('work_order_snapshots').put({
    companyId: entry.companyId, userId: entry.userId, workOrderId: server.id,
    serverVersion: server.version, cachedAt: Date.now(), payload: server,
  });
  await transactionDone(tx);
}

/**
 * Sunucu yazmayı kalıcı olarak reddetti ama güncel kaydı GÖNDERMEDİ.
 *
 * (Geçersiz geçiş, 404, doğrulama hatası… Sürüm çakışmasının aksine burada
 * elimizde sunucunun gerçeği yok.)
 *
 * `work_order_snapshots`'a DOKUNULMAZ. İlk yazdığımda buraya uydurma bir iş
 * emri koyup önbelleğe yazıyordum; o, teknisyenin elindeki gerçek kaydı
 * "BİLİNMİYOR" durumlu boş bir kayıtla ezmek olurdu. Önbellek yalnız sunucudan
 * gelen gerçekle güncellenir; burada tek yapılan kuyruğu boşaltıp durumu
 * teknisyene bildirmek. Doğru veri bir sonraki snapshot ile gelir.
 */
export async function discardFieldOperation(
  entry: FieldOutboxEntry, sebep: string,
): Promise<void> {
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'outbox', 'photo_blobs', 'conflicts', 'work_order_snapshots'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  const cached = await requestResult(
    tx.objectStore('work_order_snapshots').get([entry.companyId, entry.userId, entry.workOrderId]),
  ) as {payload: FieldWorkOrder} | undefined;
  tx.objectStore('outbox').delete(entry.operationId);
  tx.objectStore('photo_blobs').delete(entry.operationId);
  tx.objectStore('conflicts').put({
    operationId: entry.operationId, companyId: entry.companyId, userId: entry.userId,
    workOrderId: entry.workOrderId,
    workOrderNo: cached?.payload.work_order_no ?? `#${entry.workOrderId}`,
    attemptedStatus: _denenenIslem(entry), serverStatus: sebep, detectedAt: Date.now(),
  } satisfies FieldConflict);
  await transactionDone(tx);
}

/**
 * Kuyruktan sessizce düşür — çakışma YAZMADAN.
 *
 * Tek kullanım yeri: sunucunun "bu işlem zaten uygulanmış" dediği tekrar
 * gönderim. İş gerçekten yapılmış durumda; buraya çakışma kaydı yazmak
 * başarılı bir işlem için teknisyene uyarı göstermek olurdu. Önbelleğe
 * yazacak taze bir kayıt da yok, doğrusu bir sonraki snapshot ile gelir.
 */
export async function dropFieldOperation(operationId: string): Promise<void> {
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'outbox', 'photo_blobs'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  tx.objectStore('outbox').delete(operationId);
  tx.objectStore('photo_blobs').delete(operationId);
  await transactionDone(tx);
}

export async function queueFieldLaborEntry(
  entry: Omit<FieldLaborOutbox, 'createdAt' | 'attempts' | 'kind'>,
): Promise<void> {
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'outbox'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  tx.objectStore('outbox').put({
    ...entry, kind: 'labor', createdAt: Date.now(), attempts: 0,
  } satisfies FieldLaborOutbox);
  await transactionDone(tx);
}

/** Geçici hata (şebeke yok, 5xx): kayıt kalır, deneme sayacı ilerler. */
export async function recordFieldOperationAttempt(operationId: string, message: string): Promise<void> {
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'outbox'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  const store = tx.objectStore('outbox');
  const row = await requestResult(store.get(operationId)) as FieldOutboxEntry | undefined;
  if (row) store.put({...row, attempts: row.attempts + 1, lastError: message});
  await transactionDone(tx);
}

export async function dismissFieldConflict(operationId: string): Promise<void> {
  const expectedEpoch = await captureWriteEpoch();
  const db = await openFieldDb();
  const tx = db.transaction(['control', 'conflicts'], 'readwrite');
  await assertWriteEpoch(tx.objectStore('control'), expectedEpoch);
  tx.objectStore('conflicts').delete(operationId);
  await transactionDone(tx);
}

function clearPrefixedWebStorage(storage: Storage) {
  for (let index = storage.length - 1; index >= 0; index -= 1) {
    const key = storage.key(index);
    if (key?.startsWith(FIELD_BROWSER_STORAGE_PREFIX)) storage.removeItem(key);
  }
}

async function clearFieldBrowserSurfaces() {
  clearPrefixedWebStorage(localStorage);
  clearPrefixedWebStorage(sessionStorage);
  if (typeof caches !== 'undefined') {
    for (const name of await caches.keys()) {
      if (name.startsWith(FIELD_CACHE_PREFIX)) await caches.delete(name);
    }
  }
}

async function clearEveryRuntimeStore(db: IDBDatabase) {
  const storeNames = Array.from(db.objectStoreNames);
  if (!storeNames.length) return;
  const tx = db.transaction(storeNames, 'readwrite');
  for (const name of storeNames) tx.objectStore(name).clear();
  await transactionDone(tx);
}

async function purgeExpiredFieldData(db: IDBDatabase) {
  // DİKKAT: bu temizlik GÖNDERİLMEMİŞ KUYRUĞU DA siler.
  //
  // Bilinçli bir tercih, kaza değil: 12 saatlik çevrimdışı doğrulama süresi
  // dolduğunda cihazın kiracı verisini tutmaya devam etmesi güvenlik modelini
  // bozar, kuyruk da o verinin parçası. Ayrıca 12 saatten eski bir yazma
  // sunucudaki iyimser sürüm kontrolüne zaten büyük olasılıkla takılırdı.
  //
  // Yine de bedeli gerçek: teknisyen şebekesiz bir günün sonunda girdiği
  // değişiklikleri kaybedebilir. Bunu görünür kılmak arayüzün işi — kuyrukta
  // bekleyen iş varken oturumun ne zaman düşeceği kullanıcıya söylenmeli.
  writesLocked = true;
  await clearEveryRuntimeStore(db);
  await clearFieldBrowserSurfaces();
}

export async function readFieldAccess(companyId?: number, userId?: number, now = Date.now()): Promise<FieldAccess | null> {
  const db = await openFieldDb();
  const tx = db.transaction(['meta','work_order_snapshots'], 'readwrite');
  const metaStore=tx.objectStore('meta'),snapshotStore=tx.objectStore('work_order_snapshots');
  const metas = await requestResult(metaStore.getAll()) as FieldMeta[];
  const meta = companyId===undefined||userId===undefined
    ? metas.sort((a,b)=>b.lastOnlineVerifiedAt-a.lastOnlineVerifiedAt)[0]
    : metas.find(item=>item.companyId===companyId&&item.userId===userId);
  if(!meta){await transactionDone(tx);return null}
  if(!isOfflineAccessValid(meta,now)){
    await transactionDone(tx);
    await purgeExpiredFieldData(db);
    return null;
  }
  if(now>meta.lastSeenNow)metaStore.put({...meta,lastSeenNow:now,lastObservedAt:now});
  const range = IDBKeyRange.bound([meta.companyId, meta.userId, 0], [meta.companyId, meta.userId, Number.MAX_SAFE_INTEGER]);
  const rows = await requestResult(snapshotStore.getAll(range)) as {payload: FieldWorkOrder}[];
  await transactionDone(tx);
  return {meta:{...meta,lastSeenNow:Math.max(meta.lastSeenNow,now)},workOrders:rows.map(row=>row.payload)};
}

export async function clearAllFieldData() {
  const db = await openFieldDb();
  const stores = ['meta', 'work_order_snapshots', 'outbox', 'photo_blobs', 'conflicts'];
  const clear = db.transaction(stores, 'readwrite');
  for (const name of stores) clear.objectStore(name).clear();
  await transactionDone(clear);
}

export async function lockFieldWrites():Promise<number>{
  writesLocked=true;
  const db=await openFieldDb(),tx=db.transaction('control','readwrite'),store=tx.objectStore('control');
  const row=await requestResult(store.get('write_epoch')) as {value:number}|undefined;
  const epoch=(row?.value||0)+1;store.put({key:'write_epoch',value:epoch});
  await transactionDone(tx);return epoch;
}
export function acceptRemoteFieldLock(){writesLocked=true}
export function unlockFieldWritesAfterLogin(){writesLocked=false}

export async function resetFieldStorageForTests() {
  connection?.close();
  connection = null;
  writesLocked = false;
  await requestResult(indexedDB.deleteDatabase(FIELD_DB_NAME));
}

if (location.hostname === '127.0.0.1' && location.port === '5599') {
  window.__FIELD_E2E__ = {applySnapshot, captureWriteEpoch};
}
