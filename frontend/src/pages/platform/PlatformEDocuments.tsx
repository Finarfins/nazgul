import React from 'react';
import {
 Button,Chip,LinearProgress,Paper,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';

import type {components} from '../../api/types.gen';

import {HataPaneli,PlatformBaslik,usePlatformVerisi} from './ortak';

type EBelgeSagligi=components['schemas']['EBelgeSagligi'];

export default function PlatformEDocuments(){
 const {veri,hata,yukleniyor,yenile}=usePlatformVerisi<EBelgeSagligi>('/platform/edocuments/health');
 const baslik=<PlatformBaslik baslik="E-Belge Sağlığı" aciklama="Firma başına e-fatura durum dağılımı"
  sag={<Stack direction="row" gap={1} alignItems="center">
   {veri&&<Chip color={veri.izibiz_env==='live'?'success':'warning'} label={`Entegratör ortamı: ${veri.izibiz_env}`}/>}
   <Button startIcon={<RefreshIcon/>} onClick={yenile} disabled={yukleniyor}>Yenile</Button>
  </Stack>}/>;
 if(hata?.tur==='yetki')return <Stack spacing={2.5}>{baslik}<HataPaneli hata={hata} yenile={yenile}/></Stack>;
 // Sütunlar sunucunun döndürdüğü durumların BİRLEŞİMİdir: sabit bir liste yeni bir durumu gizlerdi.
 const durumlar=[...new Set((veri?.companies??[]).flatMap(sirket=>Object.keys(sirket.by_status)))].sort();
 return <Stack spacing={2.5}>
  {baslik}
  {yukleniyor&&<LinearProgress/>}
  {hata&&<HataPaneli hata={hata} yenile={yenile}/>}
  {veri&&<Paper>
   <TableContainer><Table size="small">
    <TableHead><TableRow>
     <TableCell>Firma</TableCell>
     {durumlar.map(durum=><TableCell key={durum} align="right">{durum}</TableCell>)}
     <TableCell align="right">Toplam</TableCell>
    </TableRow></TableHead>
    <TableBody>
     {veri.companies.length===0&&<TableRow><TableCell colSpan={durumlar.length+2} align="center">Henüz e-belge yok.</TableCell></TableRow>}
     {veri.companies.map(sirket=><TableRow key={sirket.company_id} data-testid={`ebelge-${sirket.company_id}`}>
      <TableCell>{sirket.company_name}</TableCell>
      {durumlar.map(durum=><TableCell key={durum} align="right">{sirket.by_status[durum]??0}</TableCell>)}
      <TableCell align="right"><b>{sirket.total}</b></TableCell>
     </TableRow>)}
    </TableBody>
   </Table></TableContainer>
  </Paper>}
 </Stack>;
}
