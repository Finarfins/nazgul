// H73: fatura uçları `*_at` alanlarını UTC ISO-8601 yazar
// (`2026-09-24T21:22:20.054785+00:00`). `slice(0,10)` bu dizgenin UTC
// GÜNÜNÜ verir; İstanbul'da 00:00-03:00 arası kesilen bir fatura bir önceki
// günde görünürdü. Bu yardımcı anı `new Date` ile ayrıştırıp YEREL günü
// `YYYY-MM-DD` olarak yazar. Yalnız tarih (`2026-09-24`) olduğu gibi kalır;
// ayrıştırılamayan değer eski davranışa (`slice`) düşer.
const YALNIZ_TARIH=/^\d{4}-\d{2}-\d{2}$/;
const iki=(n:number)=>String(n).padStart(2,'0');

export function yerelGun(value?:string|null):string{
 if(!value)return '-';
 const metin=String(value);
 if(YALNIZ_TARIH.test(metin))return metin;
 const an=new Date(metin);
 if(Number.isNaN(an.getTime()))return metin.slice(0,10);
 return `${an.getFullYear()}-${iki(an.getMonth()+1)}-${iki(an.getDate())}`;
}
