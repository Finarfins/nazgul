/**
 * Platform **Güvenlik** ekranının SAF yardımcıları: denetim süzgeçlerinin uç
 * parametrelerine eşlenmesi, istemci tarafı doğrulama ve hız sınırı
 * satırlarının IP'ye göre gruplanması.
 *
 * Bileşenden ayrı bir dosyada durur: `PlatformSecurity.tsx` yalnız bileşen
 * dışa aktarsın (`react-refresh/only-export-components`). Burada React yok —
 * hepsi saf işlev, doğrudan sınanır.
 */
import type {components,operations} from '../../api/types.gen';

type DenetimParametreleri=NonNullable<operations['list_untenanted_audit_api_platform_audit_get']['parameters']['query']>;
type HizSiniriSatiri=components['schemas']['HizSiniriSatiri'];

/**
 * `/api/platform/audit` yanıtı types.gen.ts'te `unknown` (uçta response_model
 * yok). Satır, `security_audit_logs` tablosunun kolonlarıdır
 * (backend/app/auth.py `audit_logs`); burada yalnız ekranda kullanılan alanlar
 * daraltılır.
 *
 * TODO(H38): uç hâlâ response_model taşımıyor (PP2 yalnız süzgeç ekledi;
 * types.gen.ts'te yanıt `unknown`). Uca model eklenip types.gen.ts yeniden
 * üretilince bu elle yazılmış tip silinip `components['schemas']` tipine geçilmeli.
 */
export type DenetimSatiri={
 id:number;username:string|null;action:string;path:string;status_code:number;ip_address:string|null;
 created_at:string;outcome:string;failure_reason:string|null;
};

/** Uç `window_hours` için 1..168 kabul eder (platform_management.py `le=168`). */
export const PENCERELER=[1,6,24,72,168] as const;
export const DENETIM_LIMITLERI=[50,250,1000] as const;

/** Denetim süzgeçlerinin ekrandaki (ham, yazıldığı gibi) hâli. */
export type DenetimSuzgecleri={action:string;ip_address:string;username:string;status_code:string;actor_id:string;date_from:string;date_to:string};
export const BOS_SUZGECLER:DenetimSuzgecleri={action:'',ip_address:'',username:'',status_code:'',actor_id:'',date_from:'',date_to:''};

/**
 * Sayısal süzgeçlerin uçtaki kabul aralıkları — AYNEN uçtan kopyalanmıştır:
 * `backend/app/routers/platform_audit.py:75` `actor_id: int | None = Query(None, ge=1)`
 * `backend/app/routers/platform_audit.py:76` `status_code: int | None = Query(None, ge=100, le=599)`
 * Uç değişirse burası da değişmeli.
 */
export const AKTOR_EN_AZ=1;
export const DURUM_EN_AZ=100;
export const DURUM_EN_COK=599;

const tamSayi=(deger:string)=>/^\d+$/.test(deger.trim())?Number(deger.trim()):undefined;
/** Tam sayı VE aralıkta değilse `undefined` (uç 422 dönerdi; öyle bir süzgeç gönderilmez). */
const araliktaTamSayi=(deger:string,enAz:number,enCok:number)=>{
 const sayi=tamSayi(deger);
 return sayi!==undefined&&sayi>=enAz&&sayi<=enCok?sayi:undefined;
};

export type SuzgecHatalari=Partial<Record<'actor_id'|'status_code',string>>;

/**
 * Uçun kabul aralığını İSTEMCİDE yineler. Doğrulama olmadan `actor_id=0` ya da
 * `status_code=4` yazmak uçtan 422 getiriyor, `usePlatformVerisi` onu ağ hatası
 * sayıyor ve tablo bir hata paneline dönüşüyordu — kullanıcı yazarken tablosunu
 * kaybetmemeli. Alan boşsa hata YOK: boş süzgeç zaten gönderilmez.
 */
export function suzgecHatalari(suzgec:DenetimSuzgecleri):SuzgecHatalari{
 const hatalar:SuzgecHatalari={};
 if(suzgec.actor_id.trim()&&araliktaTamSayi(suzgec.actor_id,AKTOR_EN_AZ,Number.MAX_SAFE_INTEGER)===undefined)
  hatalar.actor_id=`Aktör kimliği ${AKTOR_EN_AZ} veya daha büyük bir tam sayı olmalı.`;
 if(suzgec.status_code.trim()&&araliktaTamSayi(suzgec.status_code,DURUM_EN_AZ,DURUM_EN_COK)===undefined)
  hatalar.status_code=`Durum kodu ${DURUM_EN_AZ}–${DURUM_EN_COK} arası bir tam sayı olmalı.`;
 return hatalar;
}

/** `YYYY-MM-DD` → yerel gece yarısının ISO anı; `gunEkle` bitiş için bir sonraki gün. */
export const gunBasi=(gun:string,gunEkle=0)=>{
 if(!/^\d{4}-\d{2}-\d{2}$/.test(gun))return undefined;
 const an=new Date(`${gun}T00:00:00`);
 an.setDate(an.getDate()+gunEkle);
 return an.toISOString();
};

/**
 * Ekrandaki süzgeçleri uç parametrelerine çevirir. Boş alan GÖNDERİLMEZ
 * (`usePlatformVerisi` `null` kabul etmez; uç için yokluk = süzgeç yok).
 * `date_to` uçta HARİÇTİR (platform_audit.py): seçilen bitiş GÜNÜNÜ kapsamak
 * için ertesi günün gece yarısı gönderilir.
 */
export function denetimParametreleri(limit:number,suzgec:DenetimSuzgecleri):DenetimParametreleri{
 const metin=(deger:string)=>deger.trim()||undefined;
 const parametreler:DenetimParametreleri={
  limit,
  action:metin(suzgec.action),
  ip_address:metin(suzgec.ip_address),
  username:metin(suzgec.username),
  status_code:araliktaTamSayi(suzgec.status_code,DURUM_EN_AZ,DURUM_EN_COK),
  actor_id:araliktaTamSayi(suzgec.actor_id,AKTOR_EN_AZ,Number.MAX_SAFE_INTEGER),
  date_from:gunBasi(suzgec.date_from),
  date_to:gunBasi(suzgec.date_to,1),
 };
 return Object.fromEntries(Object.entries(parametreler).filter(([,deger])=>deger!==undefined)) as DenetimParametreleri;
}

export type IpGrubu={ip:string;satirlar:HizSiniriSatiri[]};

/**
 * Hız sınırı satırlarını IP'ye göre gruplar (ilk görülme sırası korunur).
 *
 * Temizlik ucu EYLEM değil IP başınadır
 * (`DELETE /platform/rate-limits?ip=` — o IP'nin bütün sayaçlarını siler), bu
 * yüzden eylem satırı başına düğme aynı isteği tekrarlar ve ikinci düğme hep
 * "0 kayıt silindi" derdi. Gruplama düğmeyi isteğin gerçek birimine bağlar.
 */
export function ipGruplari(satirlar:HizSiniriSatiri[]):IpGrubu[]{
 const gruplar=new Map<string,IpGrubu>();
 for(const satir of satirlar){
  const grup=gruplar.get(satir.ip_address)??{ip:satir.ip_address,satirlar:[]};
  grup.satirlar.push(satir);
  gruplar.set(satir.ip_address,grup);
 }
 return [...gruplar.values()];
}
