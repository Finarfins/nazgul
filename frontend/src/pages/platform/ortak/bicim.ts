/** Platform ekranlarının tarih ve süre biçimleyicileri. */

/**
 * Anı Türkiye duvar saatiyle yazar (H86). Platform panelini Türk operatörler
 * kullanır; tarayıcının saat dilimine bırakılsaydı UTC+12'deki bir makinede
 * 15.06 12:00Z "16.06.2026 00:00:00" olurdu. Bölge sabit: Europe/Istanbul.
 */
export const tarihSaat=(deger:string|null|undefined)=>deger?new Date(deger).toLocaleString('tr-TR',{timeZone:'Europe/Istanbul'}):'—';

/** Saniye cinsinden yaşı insan diliyle yazar: 45 sn, 12 dk, 3 sa 5 dk, 2 gün. */
export function yasMetni(saniye:number|null|undefined):string{
 if(saniye===null||saniye===undefined)return '—';
 if(saniye<60)return `${Math.round(saniye)} sn`;
 if(saniye<3600)return `${Math.floor(saniye/60)} dk`;
 if(saniye<86400){const saat=Math.floor(saniye/3600);const dk=Math.floor((saniye%3600)/60);return dk?`${saat} sa ${dk} dk`:`${saat} sa`}
 return `${Math.floor(saniye/86400)} gün`;
}
