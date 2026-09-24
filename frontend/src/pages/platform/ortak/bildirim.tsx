/** Platform eylemlerinin sayfa düzeyindeki bildirim alanı (PP4). */
import {useState} from 'react';
import {Alert,Snackbar} from '@mui/material';

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
