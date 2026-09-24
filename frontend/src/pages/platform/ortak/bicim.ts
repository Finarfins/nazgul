/** Platform ekranlarının tarih ve süre biçimleyicileri. */

export const tarihSaat=(deger:string|null|undefined)=>deger?new Date(deger).toLocaleString('tr-TR'):'—';

/** Saniye cinsinden yaşı insan diliyle yazar: 45 sn, 12 dk, 3 sa 5 dk, 2 gün. */
export function yasMetni(saniye:number|null|undefined):string{
 if(saniye===null||saniye===undefined)return '—';
 if(saniye<60)return `${Math.round(saniye)} sn`;
 if(saniye<3600)return `${Math.floor(saniye/60)} dk`;
 if(saniye<86400){const saat=Math.floor(saniye/3600);const dk=Math.floor((saniye%3600)/60);return dk?`${saat} sa ${dk} dk`:`${saat} sa`}
 return `${Math.floor(saniye/86400)} gün`;
}
