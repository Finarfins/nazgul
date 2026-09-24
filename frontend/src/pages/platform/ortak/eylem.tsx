/** Platform yazma eylemi düğmesi ve rol kapısı (PP4). */
import React,{useState} from 'react';
import {Box,Button,Dialog,DialogActions,DialogContent,DialogTitle} from '@mui/material';

import {errorDetail} from '../../../api';
import {useAuth} from '../../../AuthContext';

import type {EylemBildirimi} from './bildirim';

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
 *   `changed:false` → bilgi bildirimi (`degismediMetni` verilmezse
 *   "Zaten bu durumda") ve `yenile()`;
 *   hata (409/429/404…) → sunucunun cümlesi (`errorDetail`), liste yenilenmez.
 * - `changed:false` sonrası da `yenile()` çağrılır: sunucu bizden farklı
 *   düşünüyorsa ekrandaki satır bayattır, tazelenmeli. Şef kabul etti
 *   (mercek #142, 3a).
 */
export function EylemDugmesi<T extends DegisimYaniti>({etiket,ikon,renk,onay,istek,basariMetni,degismediMetni,yenile,bildir,testId}:{
 etiket:string;
 ikon?:React.ReactNode;
 renk?:'primary'|'error'|'warning';
 onay?:{baslik:string;icerik:React.ReactNode;onayEtiketi?:string};
 istek:()=>Promise<{data:T}>;
 basariMetni:(veri:T)=>string;
 /** `changed:false` yanıtını sayılarıyla anlatır; yoksa `ZATEN_BU_DURUMDA`. */
 degismediMetni?:(veri:T)=>string;
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
   bildir(data.changed
    ?{tur:'basari',mesaj:basariMetni(data)}
    :{tur:'bilgi',mesaj:degismediMetni?degismediMetni(data):ZATEN_BU_DURUMDA});
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
