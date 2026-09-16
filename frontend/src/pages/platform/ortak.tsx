/**
 * Platform yönetim paneli ekranlarının ORTAK parçaları (PP3 okuma, PP4 eylemler).
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
import {Alert,Box,Button,Dialog,DialogActions,DialogContent,DialogTitle,Paper,Snackbar,Stack,TablePagination,Typography} from '@mui/material';
import LockIcon from '@mui/icons-material/Lock';

import {api,errorDetail} from '../../api';
import {useAuth} from '../../AuthContext';

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

export type EylemBildirimi={tur:'basari'|'bilgi'|'hata';mesaj:string};

/**
 * Sayfa düzeyinde TEK bildirim alanı. Düğmenin içinde tutulmaz: başarıdan
 * sonra liste yeniden çekilir ve satır (süzgece göre) kaybolabilir; bildirim
 * onunla birlikte kaybolmamalı.
 */
export function useEylemBildirimi(){
 const [bildirim,setBildirim]=useState<EylemBildirimi|null>(null);
 const kapat=()=>setBildirim(null);
 const alan=<Snackbar open={!!bildirim} autoHideDuration={6000} onClose={kapat} anchorOrigin={{vertical:'bottom',horizontal:'center'}}>
  {bildirim?<Alert severity={bildirim.tur==='basari'?'success':bildirim.tur==='bilgi'?'info':'error'} onClose={kapat} data-testid="eylem-bildirimi">{bildirim.mesaj}</Alert>:undefined}
 </Snackbar>;
 return {bildir:setBildirim,bildirimAlani:alan};
}

/** PP2 yazma uçlarının ortak yanıt çekirdeği (`EylemSonucu`). */
type DegisimYaniti={changed:boolean};

/** `changed:false` hata DEĞİLDİR (Şef kararı): bilgi bildirimi. */
export const ZATEN_BU_DURUMDA='Zaten bu durumda';

/**
 * Platform yazma eylemi düğmesi (PP4).
 *
 * - `can('platform')` yoksa HİÇ çizilmez (rota koruması tek savunma değil).
 * - `onay` verilirse çağrıdan önce hedefi adıyla anan bir onay penceresi açar;
 *   yıkıcı eylemlerin hepsi (askıya alma, kilitleme, şifre sıfırlatma, hız
 *   sınırı temizliği, kuyruk yeniden deneme) `onay` ile kullanılır.
 * - Başarı + `changed:true` → başarı bildirimi ve `yenile()`;
 *   `changed:false` → "Zaten bu durumda" bilgi bildirimi ve `yenile()`;
 *   hata (409/429/404…) → sunucunun cümlesi (`errorDetail`), liste yenilenmez.
 */
export function EylemDugmesi<T extends DegisimYaniti>({etiket,ikon,renk,onay,istek,basariMetni,yenile,bildir,testId}:{
 etiket:string;
 ikon?:React.ReactNode;
 renk?:'primary'|'error'|'warning';
 onay?:{baslik:string;icerik:React.ReactNode;onayEtiketi?:string};
 istek:()=>Promise<{data:T}>;
 basariMetni:(veri:T)=>string;
 yenile:()=>void;
 bildir:(bildirim:EylemBildirimi)=>void;
 testId?:string;
}){
 const {can}=useAuth();
 const [acik,setAcik]=useState(false);
 const [calisiyor,setCalisiyor]=useState(false);
 if(!can('platform'))return null;
 const calistir=async()=>{
  setCalisiyor(true);
  try{
   const {data}=await istek();
   bildir(data.changed?{tur:'basari',mesaj:basariMetni(data)}:{tur:'bilgi',mesaj:ZATEN_BU_DURUMDA});
   setAcik(false);
   yenile();
  }catch(error){
   bildir({tur:'hata',mesaj:errorDetail(error,'İşlem tamamlanamadı; yeniden deneyin.')});
   setAcik(false);
  }finally{setCalisiyor(false)}
 };
 return <>
  <Button size="small" color={renk} startIcon={ikon} disabled={calisiyor} data-testid={testId}
   onClick={()=>{if(onay)setAcik(true);else void calistir()}}>{etiket}</Button>
  {onay&&<Dialog open={acik} onClose={()=>{if(!calisiyor)setAcik(false)}} maxWidth="xs" fullWidth>
   <DialogTitle>{onay.baslik}</DialogTitle>
   <DialogContent><Box data-testid="eylem-onay-metni">{onay.icerik}</Box></DialogContent>
   <DialogActions>
    <Button onClick={()=>setAcik(false)} disabled={calisiyor}>Vazgeç</Button>
    <Button variant="contained" color={renk??'primary'} onClick={()=>void calistir()} disabled={calisiyor}>{onay.onayEtiketi??etiket}</Button>
   </DialogActions>
  </Dialog>}
 </>;
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
