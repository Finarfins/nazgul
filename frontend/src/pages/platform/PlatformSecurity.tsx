import React,{useState} from 'react';
import {
 Alert,Box,Button,Chip,LinearProgress,MenuItem,Paper,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Typography,
} from '@mui/material';
import CleaningServicesIcon from '@mui/icons-material/CleaningServices';

import {api} from '../../api';
import type {components} from '../../api/types.gen';

import {
 BOS_SUZGECLER,DENETIM_LIMITLERI,PENCERELER,denetimParametreleri,ipGruplari,suzgecHatalari,
 type DenetimSatiri,type DenetimSuzgecleri,
} from './filtreler';
import {EylemDugmesi,HataPaneli,PlatformBaslik,tarihSaat,useEylemBildirimi,useGecikmeliDeger,usePlatformVerisi} from './ortak';

type HizSiniriOzeti=components['schemas']['HizSiniriOzeti'];
type HizSiniriTemizligi=components['schemas']['HizSiniriTemizligi'];

export default function PlatformSecurity(){
 const [pencere,setPencere]=useState<number>(24);
 const [limit,setLimit]=useState<number>(250);
 const [suzgec,setSuzgec]=useState<DenetimSuzgecleri>(BOS_SUZGECLER);
 const gecikmeliSuzgec=useGecikmeliDeger(suzgec);
 const hatalar=suzgecHatalari(suzgec);
 const gecerli=Object.keys(suzgecHatalari(gecikmeliSuzgec)).length===0;
 const {bildir,bildirimAlani}=useEylemBildirimi();
 const hiz=usePlatformVerisi<HizSiniriOzeti>('/platform/rate-limits',{window_hours:pencere});
 // Süzgeç geçersizken istek ATILMAZ: uç 422 döner, tablo hata paneline dönerdi.
 const denetim=usePlatformVerisi<DenetimSatiri[]>('/platform/audit',
  denetimParametreleri(limit,gecikmeliSuzgec) as Record<string,string|number|undefined>,undefined,{etkin:gecerli});
 const alan=(anahtar:keyof DenetimSuzgecleri)=>({
  value:suzgec[anahtar],
  onChange:(olay:React.ChangeEvent<HTMLInputElement>)=>setSuzgec(onceki=>({...onceki,[anahtar]:olay.target.value})),
 });
 const sayiAlani=(anahtar:'actor_id'|'status_code')=>({
  ...alan(anahtar),
  error:!!hatalar[anahtar],
  helperText:hatalar[anahtar],
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
      {/* Temizlik IP başınadır: düğme grubun İLK satırında, grup boyunca uzanır. */}
      {ipGruplari(hiz.veri.items).map(grup=>grup.satirlar.map((satir,sira)=><TableRow key={`${satir.action}-${grup.ip}`}>
       <TableCell>{satir.action}</TableCell><TableCell>{grup.ip}</TableCell>
       <TableCell align="right">{satir.attempts}</TableCell><TableCell>{tarihSaat(satir.last_at)}</TableCell>
       {sira===0&&<TableCell align="right" rowSpan={grup.satirlar.length}>
        <EylemDugmesi<HizSiniriTemizligi> etiket="Kilidi temizle" renk="warning" ikon={<CleaningServicesIcon/>} testId={`temizle-${grup.ip}`}
         onay={{baslik:'Hız sınırını temizle',icerik:<>
          <b>{grup.ip}</b> adresinin bütün hız sınırı sayaçları ve bu adresten gelen bütün kullanıcı adlarının giriş kilitleri silinecek.
          {' '}Bu tablodaki <b>{grup.satirlar.length}</b> satır ({grup.satirlar.map(kayit=>kayit.action).join(', ')}) etkilenir.
         </>}}
         istek={()=>api.delete('/platform/rate-limits',{params:{ip:grup.ip}})}
         basariMetni={sonuc=>`${sonuc.ip_address}: ${sonuc.rate_limit_rows} hız sınırı kaydı, ${sonuc.login_attempt_rows} giriş kilidi kaydı silindi`}
         yenile={hiz.yenile} bildir={bildir}/>
       </TableCell>}
      </TableRow>))}
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
    <TextField size="small" label="Aktör kimliği" inputProps={{inputMode:'numeric',pattern:'[0-9]*'}} {...sayiAlani('actor_id')}/>
    <TextField size="small" label="Durum kodu" inputProps={{inputMode:'numeric',pattern:'[0-9]*'}} {...sayiAlani('status_code')}/>
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
