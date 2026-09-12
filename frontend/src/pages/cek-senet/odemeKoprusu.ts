/**
 * CS4 — ödeme formu ile çek/senet portföyü arasındaki köprü.
 *
 * CS2'den sonra `POST /api/payments` yöntemi `check`/`promissory_note` ise
 * evrak alanlarını (`cek_senet`) ZORUNLU kılar (422) ve başka yöntemde
 * YASAKLAR (422). Şef kararı: ödeme formları o yükü kendileri kurmaz —
 * yöntem çek/senet seçildiğinde CS3'ün "Yeni Evrak" penceresi ön dolgulu
 * açılır ve evrak `payment_olustur:true` ile yazılır (ters köprü, ödeme
 * satırını sunucu doğurur). Böylece evrak alanlarının TEK bir formu kalır.
 *
 * Yöntem -> tür eşlemesi backend `cek_senet_cari.YONTEM_TUR` ile birebirdir.
 */
import type {Cari,YeniEvrakBaslangici} from './CekSenetDialoglari';

/** Backend `CEK_YONTEMLERI`. */
export const CEK_YONTEMLERI:readonly string[]=['check','promissory_note'];

/** Backend `YONTEM_TUR`. */
export const YONTEM_TUR:Record<string,'cek'|'senet'>={check:'cek',promissory_note:'senet'};

export const cekYontemiMi=(yontem:string|null|undefined):boolean=>
 CEK_YONTEMLERI.includes(String(yontem??''));

/**
 * Ödeme formundaki alanları pencerenin ön dolgusuna çevirir.
 *
 * `not` EVRAKIN `notlar` alanına gider, ödemenin notuna DEĞİL: ters köprü
 * ödeme notunu kendisi `"Çek <seri_no>"` olarak yazar (backend
 * `_cek_odemeyle_olustur`), formdaki metni kullanmaz.
 */
export const evrakBaslangici=(girdi:{
 entityType:'customer'|'supplier';
 cari:Cari;
 yontem:string;
 tutar:number|string;
 tarih:string;
 not?:string|null;
}):YeniEvrakBaslangici=>({
 tur:YONTEM_TUR[girdi.yontem]||'cek',
 yon:girdi.entityType==='customer'?'alinan':'verilen',
 cari:girdi.cari,
 tutar:String(girdi.tutar??''),
 vade:girdi.tarih,
 notlar:girdi.not||'',
 odemeOlustur:true,
 odemeTarihi:girdi.tarih,
 tarafKilitli:true,
});
