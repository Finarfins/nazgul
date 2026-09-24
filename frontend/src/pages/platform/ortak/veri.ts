/**
 * Platform yönetim paneli ekranlarının VERİ ÇEKME parçaları (PP3 okuma).
 *
 * Altı ekran da aynı üç durumu aynı biçimde çizmek zorunda: yükleniyor,
 * 403 (operatör değil) ve ağ/sunucu hatası. Durumun çizimi `cerceve.tsx`te;
 * burada yalnız çağrı ve durum yönetimi var.
 *
 * Yanıt tipleri `api/types.gen.ts`ten okunur.
 */
import {useCallback,useEffect,useRef,useState} from 'react';

import {api,errorDetail} from '../../../api';

export type YuklemeHatasi={tur:'yetki'}|{tur:'hata';mesaj:string};

export type PlatformVerisi<T>={
 veri:T|null;
 hata:YuklemeHatasi|null;
 yukleniyor:boolean;
 yenile:()=>void;
};

/**
 * `GET /api<yol>` çağrısı. `parametreler` değiştiğinde yeniden çağırır;
 * `yenilemeMs` verilirse o aralıkla tazeler. Geç gelen eski yanıt yenisinin
 * üzerine YAZILMAZ (sayfa hızlı değiştirildiğinde tablo geri atlamasın).
 *
 * `secenekler.etkin` `false` iken İSTEK ATILMAZ ve eldeki veri OLDUĞU GİBİ
 * kalır: ekran, uçtan kesin 422 dönecek bir süzgeç yazılırken tablosunu
 * kaybetmemeli (PP4b/H67b).
 */
export function usePlatformVerisi<T>(
 yol:string,
 parametreler?:Record<string,string|number|boolean|undefined>,
 yenilemeMs?:number,
 secenekler?:{etkin?:boolean},
):PlatformVerisi<T>{
 const [veri,setVeri]=useState<T|null>(null);
 const [hata,setHata]=useState<YuklemeHatasi|null>(null);
 const [yukleniyor,setYukleniyor]=useState(true);
 const [tur,setTur]=useState(0);
 const sonIstek=useRef(0);
 const anahtar=JSON.stringify(parametreler??{});
 const etkin=secenekler?.etkin!==false;

 useEffect(()=>{
  if(!etkin){setYukleniyor(false);return}
  const istek=++sonIstek.current;
  setYukleniyor(true);
  const params=JSON.parse(anahtar) as Record<string,unknown>;
  api.get(yol,{params}).then(({data})=>{
   if(istek!==sonIstek.current)return;
   setVeri(data as T);setHata(null);
  }).catch((error:unknown)=>{
   if(istek!==sonIstek.current)return;
   const status=(error as {response?:{status?:number}})?.response?.status;
   setHata(status===403?{tur:'yetki'}:{tur:'hata',mesaj:errorDetail(error,'Veri yüklenemedi; bağlantıyı kontrol edip yeniden deneyin.')});
  }).finally(()=>{if(istek===sonIstek.current)setYukleniyor(false)});
 },[yol,anahtar,tur,etkin]);

 useEffect(()=>{
  if(!yenilemeMs)return undefined;
  const zamanlayici=window.setInterval(()=>setTur(deger=>deger+1),yenilemeMs);
  return()=>window.clearInterval(zamanlayici);
 },[yenilemeMs]);

 const yenile=useCallback(()=>setTur(deger=>deger+1),[]);
 return {veri,hata,yukleniyor,yenile};
}

/** Arama kutusu için gecikmeli değer: her tuşta sunucuya gitmesin. */
export function useGecikmeliDeger<T>(deger:T,ms=300):T{
 const [gecikmeli,setGecikmeli]=useState(deger);
 useEffect(()=>{const zamanlayici=window.setTimeout(()=>setGecikmeli(deger),ms);return()=>window.clearTimeout(zamanlayici)},[deger,ms]);
 return gecikmeli;
}
