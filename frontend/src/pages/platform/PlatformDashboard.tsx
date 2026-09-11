import React from 'react';
import {Box,Button,Chip,Grid,LinearProgress,Paper,Stack,Typography} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import {Link as RouterLink} from 'react-router-dom';

import type {components} from '../../api/types.gen';

import {HataPaneli,PlatformBaslik,usePlatformVerisi,yasMetni} from './ortak';

type PlatformOzeti=components['schemas']['PlatformOzeti'];

/** Pano 60 sn'de bir kendini tazeler; yalnız SAYILAR taşıyan tek uç. */
export const PANO_YENILEME_MS=60_000;

function Sayac({etiket,deger,alt}:{etiket:string;deger:number;alt?:string}){
 return <Paper sx={{p:2,height:'100%'}}>
  <Typography variant="body2" color="text.secondary">{etiket}</Typography>
  <Typography variant="h4" fontWeight={900}>{deger.toLocaleString('tr-TR')}</Typography>
  {alt&&<Typography variant="caption" color="text.secondary">{alt}</Typography>}
 </Paper>;
}

function SaglikKarti({baslik,durum,hedef,children}:{baslik:string;durum:'iyi'|'uyari'|'hata';hedef:string;children:React.ReactNode}){
 const renk=durum==='iyi'?'success':durum==='uyari'?'warning':'error';
 const metin=durum==='iyi'?'Sağlıklı':durum==='uyari'?'Dikkat':'Sorun';
 return <Paper sx={{p:2,height:'100%'}}>
  <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
   <Typography fontWeight={800}>{baslik}</Typography><Chip size="small" color={renk} label={metin}/>
  </Stack>
  <Stack spacing={0.5}>{children}</Stack>
  <Button component={RouterLink} to={hedef} size="small" sx={{mt:1}}>Ayrıntı</Button>
 </Paper>;
}

const zamanlayiciDurumu=(saha:Record<string,unknown>):'iyi'|'uyari'|'hata'=>{
 if(saha.enabled===false)return 'iyi';
 if(saha.alive===false)return 'hata';
 return saha.stale===true?'uyari':'iyi';
};

export default function PlatformDashboard(){
 const {veri,hata,yukleniyor,yenile}=usePlatformVerisi<PlatformOzeti>('/platform/overview',undefined,PANO_YENILEME_MS);
 const baslik=<PlatformBaslik baslik="Platform Özeti" aciklama="Tüm firmalar · yalnız sayılar · 60 sn'de bir tazelenir"
  sag={<Button startIcon={<RefreshIcon/>} onClick={yenile} disabled={yukleniyor}>Yenile</Button>}/>;
 if(hata&&!veri)return <Stack spacing={2.5}>{baslik}<HataPaneli hata={hata} yenile={yenile}/></Stack>;
 const efatura=Object.entries(veri?.einvoice_status_histogram??{}).sort((a,b)=>b[1]-a[1]);
 const efaturaHatali=efatura.filter(([durum])=>/ERROR|FAIL|REJECT/i.test(durum)).reduce((t,[,n])=>t+n,0);
 const saha=veri?.field_stock_scheduler??{};
 return <Stack spacing={2.5}>
  {baslik}
  {yukleniyor&&<LinearProgress/>}
  {hata&&<HataPaneli hata={hata} yenile={yenile}/>}
  {veri&&<>
   <Grid container spacing={2}>
    <Grid size={{xs:6,md:3}}><Sayac etiket="Firmalar" deger={veri.companies.total} alt={`${veri.companies.active} aktif · ${veri.companies.inactive} pasif`}/></Grid>
    <Grid size={{xs:6,md:3}}><Sayac etiket="Kullanıcılar" deger={veri.users.total} alt={`${veri.users.verified} doğrulanmış · ${veri.users.unverified} doğrulanmamış`}/></Grid>
    <Grid size={{xs:6,md:3}}><Sayac etiket="Bekleyen doğrulamalar" deger={veri.pending_verifications}/></Grid>
    <Grid size={{xs:6,md:3}}><Sayac etiket="Hız sınırı blokları (24 sa)" deger={veri.rate_limit_blocks_last_24h}/></Grid>
   </Grid>
   <Grid container spacing={2}>
    <Grid size={{xs:12,md:4}}>
     <SaglikKarti baslik="Bildirim kuyruğu" hedef="/platform/kuyruk"
      durum={veri.outbox.failed>0?'hata':veri.outbox.pending>0&&(veri.outbox.oldest_pending_age_seconds??0)>3600?'uyari':'iyi'}>
      <Typography variant="body2">Bekleyen: <b>{veri.outbox.pending}</b></Typography>
      <Typography variant="body2">Başarısız: <b>{veri.outbox.failed}</b></Typography>
      <Typography variant="body2">En eski bekleyen: <b>{yasMetni(veri.outbox.oldest_pending_age_seconds)}</b></Typography>
     </SaglikKarti>
    </Grid>
    <Grid size={{xs:12,md:4}}>
     <SaglikKarti baslik="Saha stok zamanlayıcısı" hedef="/platform/kuyruk" durum={zamanlayiciDurumu(saha)}>
      <Typography variant="body2">Açık: <b>{saha.enabled===undefined?'—':saha.enabled?'evet':'hayır'}</b></Typography>
      <Typography variant="body2">Canlı: <b>{saha.alive===undefined?'—':saha.alive?'evet':'hayır'}</b></Typography>
      <Typography variant="body2">Bayat: <b>{saha.stale===undefined?'—':saha.stale?'evet':'hayır'}</b></Typography>
     </SaglikKarti>
    </Grid>
    <Grid size={{xs:12,md:4}}>
     <SaglikKarti baslik="E-fatura durumları" hedef="/platform/e-belgeler" durum={efaturaHatali>0?'uyari':'iyi'}>
      {efatura.length===0?<Typography variant="body2" color="text.secondary">Henüz e-fatura yok.</Typography>
       :<Box display="flex" flexWrap="wrap" gap={0.5}>{efatura.map(([durum,sayi])=><Chip key={durum} size="small" label={`${durum}: ${sayi}`}/>)}</Box>}
     </SaglikKarti>
    </Grid>
   </Grid>
  </>}
 </Stack>;
}
