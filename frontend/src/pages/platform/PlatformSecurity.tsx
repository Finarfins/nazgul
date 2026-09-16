import React,{useState} from 'react';
import {
 Alert,Box,Button,Chip,LinearProgress,MenuItem,Paper,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Typography,
} from '@mui/material';
import CleaningServicesIcon from '@mui/icons-material/CleaningServices';

import {api} from '../../api';
import type {components,operations} from '../../api/types.gen';

import {EylemDugmesi,HataPaneli,PlatformBaslik,tarihSaat,useEylemBildirimi,useGecikmeliDeger,usePlatformVerisi} from './ortak';

type HizSiniriOzeti=components['schemas']['HizSiniriOzeti'];
type HizSiniriTemizligi=components['schemas']['HizSiniriTemizligi'];
type DenetimParametreleri=NonNullable<operations['list_untenanted_audit_api_platform_audit_get']['parameters']['query']>;

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
type DenetimSatiri={
 id:number;username:string|null;action:string;path:string;status_code:number;ip_address:string|null;
 created_at:string;outcome:string;failure_reason:string|null;
};

/** Uç `window_hours` için 1..168 kabul eder (platform_management.py `le=168`). */
export const PENCERELER=[1,6,24,72,168] as const;
export const DENETIM_LIMITLERI=[50,250,1000] as const;

/** Denetim süzgeçlerinin ekrandaki (ham, yazıldığı gibi) hâli. */
export type DenetimSuzgecleri={action:string;ip_address:string;username:string;status_code:string;actor_id:string;date_from:string;date_to:string};
export const BOS_SUZGECLER:DenetimSuzgecleri={action:'',ip_address:'',username:'',status_code:'',actor_id:'',date_from:'',date_to:''};

/** Pozitif tam sayı değilse `undefined` (uç 422 dönerdi; boş süzgeç gönderilmez). */
const tamSayi=(deger:string)=>/^\d+$/.test(deger.trim())?Number(deger.trim()):undefined;
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
  status_code:tamSayi(suzgec.status_code),
  actor_id:tamSayi(suzgec.actor_id),
  date_from:gunBasi(suzgec.date_from),
  date_to:gunBasi(suzgec.date_to,1),
 };
 return Object.fromEntries(Object.entries(parametreler).filter(([,deger])=>deger!==undefined)) as DenetimParametreleri;
}

export default function PlatformSecurity(){
 const [pencere,setPencere]=useState<number>(24);
 const [limit,setLimit]=useState<number>(250);
 const [suzgec,setSuzgec]=useState<DenetimSuzgecleri>(BOS_SUZGECLER);
 const gecikmeliSuzgec=useGecikmeliDeger(suzgec);
 const {bildir,bildirimAlani}=useEylemBildirimi();
 const hiz=usePlatformVerisi<HizSiniriOzeti>('/platform/rate-limits',{window_hours:pencere});
 const denetim=usePlatformVerisi<DenetimSatiri[]>('/platform/audit',
  denetimParametreleri(limit,gecikmeliSuzgec) as Record<string,string|number|undefined>);
 const alan=(anahtar:keyof DenetimSuzgecleri)=>({
  value:suzgec[anahtar],
  onChange:(olay:React.ChangeEvent<HTMLInputElement>)=>setSuzgec(onceki=>({...onceki,[anahtar]:olay.target.value})),
 });
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
     <TableHead><TableRow><TableCell>Eylem</TableCell><TableCell>IP adresi</TableCell><TableCell align="right">Deneme</TableCell><TableCell>Son deneme</TableCell><TableCell align="right">Eylemler</TableCell></TableRow></TableHead>
     <TableBody>
      {hiz.veri.items.length===0&&<TableRow><TableCell colSpan={5} align="center">Bu pencerede deneme yok.</TableCell></TableRow>}
      {hiz.veri.items.map(satir=><TableRow key={`${satir.action}-${satir.ip_address}`}>
       <TableCell>{satir.action}</TableCell><TableCell>{satir.ip_address}</TableCell>
       <TableCell align="right">{satir.attempts}</TableCell><TableCell>{tarihSaat(satir.last_at)}</TableCell>
       <TableCell align="right"><EylemDugmesi<HizSiniriTemizligi> etiket="Kilidi temizle" renk="warning" ikon={<CleaningServicesIcon/>} testId={`temizle-${satir.action}-${satir.ip_address}`}
        onay={{baslik:'Hız sınırını temizle',icerik:<><b>{satir.ip_address}</b> adresinin bütün hız sınırı sayaçları ve bu adresten gelen bütün kullanıcı adlarının giriş kilitleri silinecek.</>}}
        istek={()=>api.delete('/platform/rate-limits',{params:{ip:satir.ip_address}})}
        basariMetni={sonuc=>`${sonuc.ip_address}: ${sonuc.rate_limit_rows} hız sınırı kaydı, ${sonuc.login_attempt_rows} giriş kilidi kaydı silindi`}
        yenile={hiz.yenile} bildir={bildir}/></TableCell>
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
   <Box display="grid" gridTemplateColumns={{xs:'1fr',sm:'repeat(2,1fr)',md:'repeat(4,1fr)'}} gap={1.5} mb={1.5} data-testid="denetim-suzgecleri">
    <TextField size="small" label="İşlem" inputProps={{maxLength:20}} {...alan('action')}/>
    <TextField size="small" label="IP adresi" inputProps={{maxLength:80}} {...alan('ip_address')}/>
    <TextField size="small" label="Kullanıcı adı" inputProps={{maxLength:80}} {...alan('username')}/>
    <TextField size="small" label="Aktör kimliği" inputProps={{inputMode:'numeric',pattern:'[0-9]*'}} {...alan('actor_id')}/>
    <TextField size="small" label="Durum kodu" inputProps={{inputMode:'numeric',pattern:'[0-9]*'}} {...alan('status_code')}/>
    <TextField size="small" type="date" label="Başlangıç" InputLabelProps={{shrink:true}} {...alan('date_from')}/>
    <TextField size="small" type="date" label="Bitiş" InputLabelProps={{shrink:true}} {...alan('date_to')}/>
    <Button onClick={()=>setSuzgec(BOS_SUZGECLER)} disabled={Object.values(suzgec).every(deger=>!deger)}>Süzgeçleri temizle</Button>
   </Box>
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
  {bildirimAlani}
 </Stack>;
}
