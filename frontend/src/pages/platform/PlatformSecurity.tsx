import React,{useState} from 'react';
import {
 Alert,Chip,LinearProgress,MenuItem,Paper,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Typography,
} from '@mui/material';

import type {components,operations} from '../../api/types.gen';

import {HataPaneli,PlatformBaslik,tarihSaat,usePlatformVerisi} from './ortak';

type HizSiniriOzeti=components['schemas']['HizSiniriOzeti'];
type DenetimParametreleri=NonNullable<operations['list_untenanted_audit_api_platform_audit_get']['parameters']['query']>;

/**
 * `/api/platform/audit` yanıtı types.gen.ts'te `unknown` (uçta response_model
 * yok). Satır, `security_audit_logs` tablosunun kolonlarıdır
 * (backend/app/auth.py `audit_logs`); burada yalnız ekranda kullanılan alanlar
 * daraltılır.
 *
 * TODO(H38): PP2 uca response_model ekleyecek; types.gen.ts yeniden üretilince
 * bu elle yazılmış tip silinip `components['schemas']` tipine geçilmeli.
 */
type DenetimSatiri={
 id:number;username:string|null;action:string;path:string;status_code:number;ip_address:string|null;
 created_at:string;outcome:string;failure_reason:string|null;
};

/** Uç `window_hours` için 1..168 kabul eder (platform_management.py `le=168`). */
export const PENCERELER=[1,6,24,72,168] as const;
/** Bu ekranın gönderdiği TEK süzgeç `limit`tir (1..1000); PP2 süzgeçleri henüz bağlanmadı. */
export const DENETIM_LIMITLERI=[50,250,1000] as const;

export default function PlatformSecurity(){
 const [pencere,setPencere]=useState<number>(24);
 const [limit,setLimit]=useState<number>(250);
 const hiz=usePlatformVerisi<HizSiniriOzeti>('/platform/rate-limits',{window_hours:pencere});
 // Yalnız `limit` gönderilir: PP2 süzgeçleri `string|null` olarak üretilir ve
 // `usePlatformVerisi`nin null kabul etmeyen parametre tipine sığmaz.
 const denetimParametreleri:Pick<DenetimParametreleri,'limit'>={limit};
 const denetim=usePlatformVerisi<DenetimSatiri[]>('/platform/audit',denetimParametreleri);
 const baslik=<PlatformBaslik baslik="Güvenlik" aciklama="Hız sınırı denemeleri · firmasız güvenlik denetim olayları"/>;
 if(hiz.hata?.tur==='yetki'||denetim.hata?.tur==='yetki')return <Stack spacing={2.5}>{baslik}<HataPaneli hata={{tur:'yetki'}} yenile={hiz.yenile}/></Stack>;
 return <Stack spacing={2.5}>
  {baslik}
  <Paper sx={{p:2}}>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1.5} mb={1.5}>
    <Typography fontWeight={800}>Hız sınırı özeti</Typography>
    <TextField size="small" select label="Pencere" value={pencere} onChange={e=>setPencere(Number(e.target.value))} sx={{minWidth:160}}>
     {PENCERELER.map(saat=><MenuItem key={saat} value={saat}>Son {saat} saat</MenuItem>)}
    </TextField>
   </Stack>
   {hiz.yukleniyor&&<LinearProgress/>}
   {hiz.hata&&<HataPaneli hata={hiz.hata} yenile={hiz.yenile}/>}
   {hiz.veri&&<>
    <Alert severity="info" sx={{mb:1.5}}>Deneme kayıtları {hiz.veri.retention_hours} saat saklanır; daha uzun bir pencere daha fazla veri göstermez.</Alert>
    <TableContainer><Table size="small">
     <TableHead><TableRow><TableCell>Eylem</TableCell><TableCell>IP adresi</TableCell><TableCell align="right">Deneme</TableCell><TableCell>Son deneme</TableCell></TableRow></TableHead>
     <TableBody>
      {hiz.veri.items.length===0&&<TableRow><TableCell colSpan={4} align="center">Bu pencerede deneme yok.</TableCell></TableRow>}
      {hiz.veri.items.map(satir=><TableRow key={`${satir.action}-${satir.ip_address}`}>
       <TableCell>{satir.action}</TableCell><TableCell>{satir.ip_address}</TableCell>
       <TableCell align="right">{satir.attempts}</TableCell><TableCell>{tarihSaat(satir.last_at)}</TableCell>
      </TableRow>)}
     </TableBody>
    </Table></TableContainer>
   </>}
  </Paper>
  <Paper sx={{p:2}}>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1.5} mb={1.5}>
    <Typography fontWeight={800}>Firmasız denetim olayları</Typography>
    <TextField size="small" select label="Son kayıt" value={limit} onChange={e=>setLimit(Number(e.target.value))} sx={{minWidth:160}}>
     {DENETIM_LIMITLERI.map(sayi=><MenuItem key={sayi} value={sayi}>Son {sayi}</MenuItem>)}
    </TextField>
   </Stack>
   {denetim.yukleniyor&&<LinearProgress/>}
   {denetim.hata&&<HataPaneli hata={denetim.hata} yenile={denetim.yenile}/>}
   {denetim.veri&&<TableContainer><Table size="small">
    <TableHead><TableRow><TableCell>Zaman</TableCell><TableCell>Kullanıcı</TableCell><TableCell>İşlem</TableCell><TableCell>Yol</TableCell><TableCell>Sonuç</TableCell><TableCell>IP</TableCell></TableRow></TableHead>
    <TableBody>
     {denetim.veri.length===0&&<TableRow><TableCell colSpan={6} align="center">Kayıt yok.</TableCell></TableRow>}
     {denetim.veri.map(satir=><TableRow key={satir.id} data-testid={`denetim-${satir.id}`}>
      <TableCell>{tarihSaat(satir.created_at)}</TableCell><TableCell>{satir.username??'—'}</TableCell>
      <TableCell>{satir.action}</TableCell><TableCell sx={{wordBreak:'break-all'}}>{satir.path}</TableCell>
      <TableCell><Chip size="small" color={satir.status_code>=400?'error':'default'} label={`${satir.status_code} · ${satir.outcome}`}/>{satir.failure_reason&&<Typography variant="caption" display="block" color="text.secondary">{satir.failure_reason}</Typography>}</TableCell>
      <TableCell>{satir.ip_address??'—'}</TableCell>
     </TableRow>)}
    </TableBody>
   </Table></TableContainer>}
  </Paper>
 </Stack>;
}
