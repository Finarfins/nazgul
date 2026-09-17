import React,{useState} from 'react';
import {
 Chip,LinearProgress,MenuItem,Paper,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,
} from '@mui/material';
import BlockIcon from '@mui/icons-material/Block';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';

import {api} from '../../api';
import type {components} from '../../api/types.gen';

import {EylemDugmesi,HataPaneli,PlatformBaslik,Sayfalama,tarihSaat,useGecikmeliDeger,useEylemBildirimi,usePlatformVerisi} from './ortak';

type SirketListesi=components['schemas']['PlatformSirketListesi'];
type SirketDurumu=components['schemas']['SirketDurumu'];
type DurumSuzgeci='tum'|'aktif'|'pasif';

export default function PlatformCompanies(){
 const [arama,setArama]=useState('');
 const [durum,setDurum]=useState<DurumSuzgeci>('tum');
 const [sayfa,setSayfa]=useState(0);
 const [boyut,setBoyut]=useState(50);
 const q=useGecikmeliDeger(arama.trim());
 const {veri,hata,yukleniyor,yenile}=usePlatformVerisi<SirketListesi>('/platform/companies',{
  q:q||undefined,
  active:durum==='tum'?undefined:durum==='aktif',
  limit:boyut,
  offset:sayfa*boyut,
 });
 const {bildir,bildirimAlani}=useEylemBildirimi();
 const baslik=<PlatformBaslik baslik="Şirketler" aciklama="Platformdaki bütün firmalar · üye sayısı ve son hareket"/>;
 if(hata?.tur==='yetki')return <Stack spacing={2.5}>{baslik}<HataPaneli hata={hata} yenile={yenile}/></Stack>;
 return <Stack spacing={2.5}>
  {baslik}
  <Stack direction={{xs:'column',sm:'row'}} gap={1.5}>
   <TextField size="small" label="Ara (firma adı)" value={arama} onChange={e=>{setArama(e.target.value);setSayfa(0)}} sx={{minWidth:260}}/>
   <TextField size="small" select label="Durum" value={durum} onChange={e=>{setDurum(e.target.value as DurumSuzgeci);setSayfa(0)}} sx={{minWidth:160}}>
    <MenuItem value="tum">Tümü</MenuItem><MenuItem value="aktif">Aktif</MenuItem><MenuItem value="pasif">Pasif</MenuItem>
   </TextField>
  </Stack>
  {yukleniyor&&<LinearProgress/>}
  {hata&&<HataPaneli hata={hata} yenile={yenile}/>}
  <Paper>
   <TableContainer><Table size="small">
    <TableHead><TableRow>
     <TableCell>ID</TableCell><TableCell>Firma</TableCell><TableCell>Durum</TableCell>
     <TableCell align="right">Üye</TableCell><TableCell>Son hareket</TableCell><TableCell>Oluşturma</TableCell>
     <TableCell align="right">Eylemler</TableCell>
    </TableRow></TableHead>
    <TableBody>
     {veri&&veri.items.length===0&&<TableRow><TableCell colSpan={7} align="center">Kayıt bulunamadı.</TableCell></TableRow>}
     {veri?.items.map(sirket=><TableRow key={sirket.id} data-testid={`sirket-${sirket.id}`}>
      <TableCell>{sirket.id}</TableCell>
      <TableCell>{sirket.name}</TableCell>
      <TableCell><Chip size="small" color={sirket.is_active?'success':'default'} label={sirket.is_active?'Aktif':'Pasif'}/></TableCell>
      <TableCell align="right">{sirket.member_count}</TableCell>
      <TableCell>{tarihSaat(sirket.last_activity_at)}</TableCell>
      <TableCell>{tarihSaat(sirket.created_at)}</TableCell>
      <TableCell align="right">{sirket.is_active?
       <EylemDugmesi<SirketDurumu> etiket="Askıya al" renk="error" ikon={<BlockIcon/>} testId={`askiya-al-${sirket.id}`}
        onay={{baslik:'Firmayı askıya al',icerik:<><b>{sirket.name}</b> askıya alınacak. Firmanın bütün üyeleri bu firmada oturum açamaz ve işlem yapamaz; veriler silinmez.</>}}
        istek={()=>api.post(`/platform/companies/${sirket.id}/deactivate`)} basariMetni={()=>`${sirket.name} askıya alındı`} yenile={yenile} bildir={bildir}/>:
       <EylemDugmesi<SirketDurumu> etiket="Aç" ikon={<CheckCircleIcon/>} testId={`ac-${sirket.id}`}
        istek={()=>api.post(`/platform/companies/${sirket.id}/activate`)} basariMetni={()=>`${sirket.name} yeniden açıldı`} yenile={yenile} bildir={bildir}/>}
      </TableCell>
     </TableRow>)}
    </TableBody>
   </Table></TableContainer>
   <Sayfalama toplam={veri?.total??0} sayfa={sayfa} boyut={boyut} sayfaDegisti={setSayfa} boyutDegisti={yeni=>{setBoyut(yeni);setSayfa(0)}}/>
  </Paper>
  {bildirimAlani}
 </Stack>;
}
