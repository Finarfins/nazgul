/**
 * Platform yönetim paneli ekranlarının ORTAK parçaları (PP3, salt-okunur).
 *
 * Altı ekran da aynı üç durumu aynı biçimde çizmek zorunda: yükleniyor,
 * 403 (operatör değil) ve ağ/sunucu hatası. 403 BOŞ SAYFA DEĞİLDİR — rota
 * koruması `can('platform')` ile zaten süzer, ama bayrak oturum içinde
 * düşerse (operatör listesinden çıkarılma) sayfa sessizce boş kalmamalı.
 *
 * Yanıt tipleri `api/types.gen.ts`ten okunur; burada yalnız çağrı ve durum
 * yönetimi var.
 */
import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Alert,Box,Button,Paper,Stack,TablePagination,Tooltip,Typography} from '@mui/material';
import LockIcon from '@mui/icons-material/Lock';

import {api,errorDetail} from '../../api';

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
 */
export function usePlatformVerisi<T>(
 yol:string,
 parametreler?:Record<string,string|number|boolean|undefined>,
 yenilemeMs?:number,
):PlatformVerisi<T>{
 const [veri,setVeri]=useState<T|null>(null);
 const [hata,setHata]=useState<YuklemeHatasi|null>(null);
 const [yukleniyor,setYukleniyor]=useState(true);
 const [tur,setTur]=useState(0);
 const sonIstek=useRef(0);
 const anahtar=JSON.stringify(parametreler??{});

 useEffect(()=>{
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
 },[yol,anahtar,tur]);

 useEffect(()=>{
  if(!yenilemeMs)return undefined;
  const zamanlayici=window.setInterval(()=>setTur(deger=>deger+1),yenilemeMs);
  return()=>window.clearInterval(zamanlayici);
 },[yenilemeMs]);

 const yenile=useCallback(()=>setTur(deger=>deger+1),[]);
 return {veri,hata,yukleniyor,yenile};
}

/** 403: operatör olmayan kullanıcıya düz ve açık bir panel. */
export function YetkiYok(){
 return <Paper sx={{p:4,textAlign:'center'}} data-testid="platform-yetki-yok">
  <LockIcon color="disabled" sx={{fontSize:40}}/>
  <Typography variant="h6" fontWeight={800}>Bu ekrana yetkiniz yok</Typography>
  <Typography color="text.secondary">Platform yönetim paneli yalnız platform operatörlerine açıktır.</Typography>
 </Paper>;
}

/** Yükleme hatasını çizer; 403 için `YetkiYok`, diğerleri için yeniden dene. */
export function HataPaneli({hata,yenile}:{hata:YuklemeHatasi;yenile:()=>void}){
 if(hata.tur==='yetki')return <YetkiYok/>;
 return <Alert severity="error" action={<Button color="inherit" size="small" onClick={yenile}>Yeniden dene</Button>}>{hata.mesaj}</Alert>;
}

/** Sayfa başlığı — Backups.tsx ile aynı düzen. */
export function PlatformBaslik({baslik,aciklama,sag}:{baslik:string;aciklama:string;sag?:React.ReactNode}){
 return <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2}>
  <Box><Typography variant="h5" fontWeight={900}>{baslik}</Typography><Typography color="text.secondary">{aciklama}</Typography></Box>
  {sag}
 </Stack>;
}

/**
 * PP2 yer tutucusu. TODO(PP2): yazma uçları ana makinede yapılıyor; uç yokken
 * düğme HİÇBİR çağrı yapmaz ve devre dışı kalır.
 */
export function PP2Dugmesi({etiket,ikon}:{etiket:string;ikon?:React.ReactNode}){
 return <Tooltip title="PP2 ile gelecek"><span><Button size="small" disabled startIcon={ikon}>{etiket}</Button></span></Tooltip>;
}

export const tarihSaat=(deger:string|null|undefined)=>deger?new Date(deger).toLocaleString('tr-TR'):'—';

/** Saniye cinsinden yaşı insan diliyle yazar: 45 sn, 12 dk, 3 sa 5 dk, 2 gün. */
export function yasMetni(saniye:number|null|undefined):string{
 if(saniye===null||saniye===undefined)return '—';
 if(saniye<60)return `${Math.round(saniye)} sn`;
 if(saniye<3600)return `${Math.floor(saniye/60)} dk`;
 if(saniye<86400){const saat=Math.floor(saniye/3600);const dk=Math.floor((saniye%3600)/60);return dk?`${saat} sa ${dk} dk`:`${saat} sa`}
 return `${Math.floor(saniye/86400)} gün`;
}

export const SAYFA_BOYUTLARI=[25,50,100];

/** Sunucu tarafı sayfalama (limit/offset) — ActivityLog.tsx ile aynı düzen. */
export function Sayfalama({toplam,sayfa,boyut,sayfaDegisti,boyutDegisti}:{
 toplam:number;sayfa:number;boyut:number;sayfaDegisti:(sayfa:number)=>void;boyutDegisti:(boyut:number)=>void;
}){
 return <TablePagination sx={{
   '& .MuiIconButton-root':{minWidth:{xs:44,md:38},minHeight:{xs:44,md:38}},
   '& .MuiTablePagination-select':{minHeight:{xs:44,md:32},display:'flex',alignItems:'center'},
  }}
  component="div" count={toplam} page={sayfa} rowsPerPage={boyut} rowsPerPageOptions={SAYFA_BOYUTLARI}
  labelRowsPerPage="Sayfa başına" labelDisplayedRows={({from,to,count})=>`${from}–${to} / ${count}`}
  onPageChange={(_,yeni)=>sayfaDegisti(yeni)}
  onRowsPerPageChange={olay=>boyutDegisti(Number(olay.target.value))}/>;
}

/** Arama kutusu için gecikmeli değer: her tuşta sunucuya gitmesin. */
export function useGecikmeliDeger<T>(deger:T,ms=300):T{
 const [gecikmeli,setGecikmeli]=useState(deger);
 useEffect(()=>{const zamanlayici=window.setTimeout(()=>setGecikmeli(deger),ms);return()=>window.clearTimeout(zamanlayici)},[deger,ms]);
 return gecikmeli;
}
