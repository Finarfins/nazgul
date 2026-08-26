import {api} from '../api';

import {
  conflictFieldOperation, discardFieldOperation, dropFieldOperation,
  queueFieldAttachment, queueFieldLaborEntry, queueFieldStatusChange, readFieldAttachmentBlob,
  readFieldOutbox, recordFieldOperationAttempt, settleFieldOperation,
  type FieldAttachmentOutbox, type FieldLaborOutbox, type FieldOutboxEntry, type FieldStatusOutbox,
  type FieldWorkOrder,
} from './fieldDatabase';
import {createFieldSyncController} from './sessionCoordinator';

/**
 * Saha yazmalarının kuyruğa alınması ve gönderilmesi.
 *
 * Kuyruk YENİDEN GÖNDERMEYE açıktır: istek sunucuya ulaşıp cevabı yolda
 * kaybolursa istemci başarısız sayar ve aynı işlemi tekrar yollar. Bu yüzden
 * her kayıt kendi `operationId`'sini taşır ve sunucu aynı kimliği ikinci kez
 * gördüğünde işlemi tekrar uygulamaz. Kimliğin ÜRETİLDİĞİ an kuyruğa alınma
 * anıdır, gönderim anı değil — gönderimde üretilseydi her yeniden deneme yeni
 * bir kimlik alır ve tekrar koruması hiç çalışmazdı.
 */

// Sunucudaki doğrulama ile aynı: ^[A-Za-z0-9_-]{8,64}$
const newOperationId = () =>
  (globalThis.crypto?.randomUUID?.() ?? `op-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`)
    .replaceAll(/[^A-Za-z0-9_-]/g, '')
    .slice(0, 64);

export async function queueFieldStatus(
  companyId: number, userId: number, workOrder: FieldWorkOrder, status: string,
): Promise<void> {
  await queueFieldStatusChange({
    operationId: newOperationId(),
    companyId, userId,
    workOrderId: workOrder.id,
    status,
    // Teknisyenin GÖRDÜĞÜ sürüm. Gönderim anında tazelenmez; gerekçesi
    // FieldOutboxEntry tanımında.
    expectedVersion: workOrder.version,
  });
}


export async function queueFieldPhoto(
  companyId: number, userId: number, workOrderId: number,
  blob: Blob, fileName: string, attachmentKind: 'photo' | 'signature' = 'photo',
): Promise<void> {
  await queueFieldAttachment(
    {operationId: newOperationId(), companyId, userId, workOrderId, attachmentKind, fileName},
    blob,
  );
}

export async function queueFieldLabor(
  companyId: number, userId: number, workOrderId: number, hours: string, note?: string,
): Promise<void> {
  await queueFieldLaborEntry({
    operationId: newOperationId(), companyId, userId, workOrderId, hours, note,
  });
}

export type FieldFlushResult = {sent: number; conflicted: number; pending: number};

/**
 * Kuyruğu boşaltmayı dener. Çevrimdışıysa hiç uğraşmaz.
 *
 * Sıra en eskiden yeniye ve TEK TEK: aynı iş emrine ard arda yapılan iki
 * değişiklik paralel gönderilirse ikincisi birincinin ürettiği sürümü
 * göremez ve gereksiz yere çakışma sayılır.
 */
export async function flushFieldOutbox(companyId: number, userId: number): Promise<FieldFlushResult> {
  const result: FieldFlushResult = {sent: 0, conflicted: 0, pending: 0};
  if (typeof navigator !== 'undefined' && !navigator.onLine) {
    result.pending = (await readFieldOutbox(companyId, userId)).length;
    return result;
  }
  for (const entry of await readFieldOutbox(companyId, userId)) {
    const outcome = await sendOne(entry);
    if (outcome === 'sent') result.sent += 1;
    else if (outcome === 'conflict') result.conflicted += 1;
    else {
      result.pending += 1;
      // İlk kalıcı olmayan hatada duruyoruz: şebeke gittiyse kalan kayıtlar
      // için de gidecektir ve her biri için ayrı ayrı zaman aşımı beklemek
      // arayüzü dakikalarca kilitler.
      break;
    }
  }
  return result;
}


/** Durum değişikliği. Sunucu güncel iş emrini döndürür. */
async function sendStatus(entry: FieldStatusOutbox, signal: AbortSignal): Promise<FieldWorkOrder | null> {
  const {data} = await api.post<FieldWorkOrder>(
    `/field/work-orders/${entry.workOrderId}/status`,
    {operation_id: entry.operationId, expected_version: entry.expectedVersion, status: entry.status},
    {signal},
  );
  // Tekrar gönderimde sunucu iş emri yerine {status:'already_applied'} döner.
  return (data as unknown as {status?: string}).status === 'already_applied' ? null : data;
}

/**
 * Fotoğraf/imza. multipart gönderiliyor ve dosya ancak BURADA belleğe
 * okunuyor — kuyruğu listeleyen ekranlar megabaytları taşımasın diye.
 *
 * Sunucu ek kaydını döndürüyor, iş emrini değil; bu yüzden önbelleğe yazacak
 * bir şey yok ve kayıt kuyruktan sessizce düşüyor. Ek listesi bir sonraki
 * snapshot ile geliyor.
 */
async function sendAttachment(entry: FieldAttachmentOutbox, signal: AbortSignal): Promise<FieldWorkOrder | null> {
  const blob = await readFieldAttachmentBlob(entry.operationId);
  if (!blob) {
    // Kayıt var ama dosyası yok: normalde imkânsız (ikisi tek işlemde
    // yazılıyor). Yine de sonsuza kadar denememek için kuyruktan düşürülüyor.
    throw Object.assign(new Error('Dosya bulunamadı'), {response: {status: 410, data: {detail: 'Fotoğraf cihazda bulunamadı'}}});
  }
  const form = new FormData();
  form.append('operation_id', entry.operationId);
  form.append('kind', entry.attachmentKind);
  form.append('file', blob, entry.fileName);
  await api.post(`/field/work-orders/${entry.workOrderId}/attachments`, form, {signal});
  return null;
}

/** İşçilik. Sunucu satırı döndürür; iş emri değil, önbelleğe yazacak şey yok. */
async function sendLabor(entry: FieldLaborOutbox, signal: AbortSignal): Promise<FieldWorkOrder | null> {
  await api.post(`/field/work-orders/${entry.workOrderId}/labor`,
    {operation_id: entry.operationId, hours: entry.hours, note: entry.note ?? null},
    {signal});
  return null;
}

async function sendOne(entry: FieldOutboxEntry): Promise<'sent' | 'conflict' | 'retry'> {
  const {controller, release} = createFieldSyncController();
  try {
    const data = entry.kind === 'status' ? await sendStatus(entry, controller.signal)
      : entry.kind === 'labor' ? await sendLabor(entry, controller.signal)
      : await sendAttachment(entry, controller.signal);
    if (data === null) {
      // Sunucu "zaten uygulanmış" dedi (tekrar gönderim). İş yapılmış
      // durumda; kuyruktan düşmeli ama önbelleğe yazacak taze bir kayıt yok.
      // Doğrusu bir sonraki snapshot ile gelir.
      await dropFieldOperation(entry.operationId);
      return 'sent';
    }
    await settleFieldOperation(entry.operationId, entry.companyId, entry.userId, data);
    return 'sent';
  } catch (error: unknown) {
    const response = (error as {response?: {status?: number; data?: unknown}}).response;
    const status = response?.status;

    // 409 + `current`: iyimser sürüm çakışması. Sunucu güncel kaydı da
    // gönderiyor, önbelleği onunla tazeliyoruz.
    const current = (response?.data as {current?: FieldWorkOrder} | undefined)?.current;
    if (status === 409 && current) {
      await conflictFieldOperation(entry, current);
      return 'conflict';
    }

    // Diğer 4xx'ler: istek olduğu gibi bir daha kabul edilmeyecek (geçersiz
    // geçiş, 404, doğrulama hatası). Sonsuza kadar denemek kuyruğu tıkar, bu
    // yüzden kapatılıp teknisyene bildiriliyor. Önbelleğe DOKUNULMUYOR —
    // sunucunun gerçeği elimizde yok, uydurulmuş bir kayıt yazmak teknisyenin
    // elindeki doğru veriyi bozardı. Doğrusu bir sonraki snapshot ile gelir.
    if (typeof status === 'number' && status >= 400 && status < 500) {
      const detail = (response?.data as {detail?: string} | undefined)?.detail;
      await discardFieldOperation(entry, detail || `Sunucu isteği reddetti (${status})`);
      return 'conflict';
    }

    // Şebeke hatası veya 5xx: geçici kabul edilir, kayıt kuyrukta kalır.
    await recordFieldOperationAttempt(
      entry.operationId,
      status ? `Sunucu ${status}` : 'Sunucuya ulaşılamadı',
    );
    return 'retry';
  } finally {
    release();
  }
}
