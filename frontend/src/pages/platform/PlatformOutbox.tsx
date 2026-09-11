import React from 'react';
import {
 Box,Button,Chip,LinearProgress,Paper,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import ReplayIcon from '@mui/icons-material/Replay';

import type {components} from '../../api/types.gen';

import {HataPaneli,PP2Dugmesi,PlatformBaslik,usePlatformVerisi,yasMetni} from './ortak';

type KuyrukSagligi=components['schemas']['KuyrukSagligi'];

const evetHayir=(deger:unknown)=>deger===undefined||deger===null?'—':typeof deger==='boolean'?(deger?'evet':'hayır'):String(deger);

export default function PlatformOutbox(){
 const {veri,hata,yukleniyor,yenile}=usePlatformVerisi<KuyrukSagligi>('/platform/outbox/health');
 const baslik=<PlatformBaslik baslik="Kuyruk Sağlığı" aciklama="Bildirim kuyruğu kanal başına · saha stok zamanlayıcısı"
  sag={<Button startIcon={<RefreshIcon/>} onClick={yenile} disabled={yukleniyor}>Yenile</Button>}/>;
 if(hata?.tur==='yetki')return <Stack spacing={2.5}>{baslik}<HataPaneli hata={hata} yenile={yenile}/></Stack>;
 const saha=Object.entries(veri?.field_stock_scheduler??{}).filter(([,deger])=>deger===null||typeof deger!=='object');
 return <Stack spacing={2.5}>
  {baslik}
  {yukleniyor&&<LinearProgress/>}
  {hata&&<HataPaneli hata={hata} yenile={yenile}/>}
  {veri&&<>
   <Paper>
    <TableContainer><Table size="small">
     <TableHead><TableRow>
      <TableCell>Kanal</TableCell><TableCell align="right">Bekleyen</TableCell><TableCell align="right">Başarısız</TableCell>
      <TableCell align="right">Gönderilen (24 sa)</TableCell><TableCell>En eski bekleyen</TableCell><TableCell align="right">Eylemler</TableCell>
     </TableRow></TableHead>
     <TableBody>
      {veri.channels.length===0&&<TableRow><TableCell colSpan={6} align="center">Kuyrukta kanal yok.</TableCell></TableRow>}
      {veri.channels.map(kanal=><TableRow key={kanal.channel} data-testid={`kanal-${kanal.channel}`}>
       <TableCell><Typography fontWeight={700} variant="body2">{kanal.channel}</Typography></TableCell>
       <TableCell align="right">{kanal.pending}</TableCell>
       <TableCell align="right">{kanal.failed>0?<Chip size="small" color="error" label={kanal.failed}/>:0}</TableCell>
       <TableCell align="right">{kanal.sent_last_24h}</TableCell>
       <TableCell>{yasMetni(kanal.oldest_pending_age_seconds)}</TableCell>
       {/* TODO(PP2): başarısızları yeniden deneme ucu PP2'de; şimdilik çağrı yok. */}
       <TableCell align="right"><PP2Dugmesi etiket="Yeniden dene" ikon={<ReplayIcon/>}/></TableCell>
      </TableRow>)}
     </TableBody>
    </Table></TableContainer>
   </Paper>
   <Paper sx={{p:2}}>
    <Typography fontWeight={800} mb={1}>Saha stok zamanlayıcısı</Typography>
    {saha.length===0?<Typography variant="body2" color="text.secondary">Zamanlayıcı bilgisi yok.</Typography>:
     <Box display="grid" gridTemplateColumns={{xs:'1fr',sm:'repeat(2,1fr)',md:'repeat(3,1fr)'}} gap={1}>
      {saha.map(([anahtar,deger])=><Typography key={anahtar} variant="body2"><Box component="span" color="text.secondary">{anahtar}:</Box> <b>{evetHayir(deger)}</b></Typography>)}
     </Box>}
   </Paper>
  </>}
 </Stack>;
}
