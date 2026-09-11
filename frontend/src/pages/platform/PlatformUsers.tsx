import React,{useState} from 'react';
import {
 Box,Chip,LinearProgress,MenuItem,Paper,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Typography,
} from '@mui/material';
import LockResetIcon from '@mui/icons-material/LockReset';
import MarkEmailReadIcon from '@mui/icons-material/MarkEmailRead';

import type {components} from '../../api/types.gen';

import {HataPaneli,PP2Dugmesi,PlatformBaslik,Sayfalama,tarihSaat,useGecikmeliDeger,usePlatformVerisi} from './ortak';

type KullaniciListesi=components['schemas']['PlatformKullaniciListesi'];
type DogrulamaListesi=components['schemas']['BekleyenDogrulamaListesi'];
type DogrulamaSuzgeci='tum'|'evet'|'hayir';

/** Bekleyen doğrulamaların ilk sayfası; tam liste PP2'nin işi değil, burada yalnız görünürlük. */
const DOGRULAMA_LIMITI=20;

function BekleyenDogrulamalar(){
 const {veri,hata,yenile}=usePlatformVerisi<DogrulamaListesi>('/platform/verifications',{limit:DOGRULAMA_LIMITI,offset:0});
 if(hata)return hata.tur==='yetki'?null:<HataPaneli hata={hata} yenile={yenile}/>;
 if(!veri)return null;
 return <Paper sx={{p:2}}>
  <Typography fontWeight={800} mb={1}>Bekleyen e-posta doğrulamaları ({veri.total})</Typography>
  {veri.items.length===0?<Typography variant="body2" color="text.secondary">Bekleyen doğrulama yok.</Typography>:
   <TableContainer><Table size="small">
    <TableHead><TableRow><TableCell>Kullanıcı</TableCell><TableCell>Gönderim</TableCell><TableCell>Son geçerlilik</TableCell></TableRow></TableHead>
    <TableBody>{veri.items.map(satir=><TableRow key={`${satir.user_id}-${satir.created_at}`}>
     <TableCell>{satir.username}</TableCell><TableCell>{tarihSaat(satir.created_at)}</TableCell><TableCell>{tarihSaat(satir.expires_at)}</TableCell>
    </TableRow>)}</TableBody>
   </Table></TableContainer>}
  {veri.total>veri.items.length&&<Typography variant="caption" color="text.secondary">İlk {veri.items.length} kayıt gösteriliyor.</Typography>}
 </Paper>;
}

export default function PlatformUsers(){
 const [arama,setArama]=useState('');
 const [dogrulama,setDogrulama]=useState<DogrulamaSuzgeci>('tum');
 const [sayfa,setSayfa]=useState(0);
 const [boyut,setBoyut]=useState(50);
 const q=useGecikmeliDeger(arama.trim());
 const {veri,hata,yukleniyor,yenile}=usePlatformVerisi<KullaniciListesi>('/platform/users',{
  q:q||undefined,
  verified:dogrulama==='tum'?undefined:dogrulama==='evet',
  limit:boyut,
  offset:sayfa*boyut,
 });
 const baslik=<PlatformBaslik baslik="Platform Kullanıcıları" aciklama="Bütün hesaplar ve firma üyelikleri · rol hesap düzeyindedir"/>;
 if(hata?.tur==='yetki')return <Stack spacing={2.5}>{baslik}<HataPaneli hata={hata} yenile={yenile}/></Stack>;
 return <Stack spacing={2.5}>
  {baslik}
  <Stack direction={{xs:'column',sm:'row'}} gap={1.5}>
   <TextField size="small" label="Ara (kullanıcı adı / ad)" value={arama} onChange={e=>{setArama(e.target.value);setSayfa(0)}} sx={{minWidth:260}}/>
   <TextField size="small" select label="E-posta doğrulaması" value={dogrulama} onChange={e=>{setDogrulama(e.target.value as DogrulamaSuzgeci);setSayfa(0)}} sx={{minWidth:200}}>
    <MenuItem value="tum">Tümü</MenuItem><MenuItem value="evet">Doğrulanmış</MenuItem><MenuItem value="hayir">Doğrulanmamış</MenuItem>
   </TextField>
  </Stack>
  {yukleniyor&&<LinearProgress/>}
  {hata&&<HataPaneli hata={hata} yenile={yenile}/>}
  <Paper>
   <TableContainer><Table size="small">
    <TableHead><TableRow>
     <TableCell>Kullanıcı</TableCell><TableCell>Rol</TableCell><TableCell>Durum</TableCell>
     <TableCell>Üyelikler</TableCell><TableCell>Son giriş</TableCell><TableCell align="right">Eylemler</TableCell>
    </TableRow></TableHead>
    <TableBody>
     {veri&&veri.items.length===0&&<TableRow><TableCell colSpan={6} align="center">Kayıt bulunamadı.</TableCell></TableRow>}
     {veri?.items.map(kullanici=><TableRow key={kullanici.id} data-testid={`kullanici-${kullanici.id}`}>
      <TableCell><Typography fontWeight={700} variant="body2">{kullanici.username}</Typography><Typography variant="caption" color="text.secondary">{kullanici.display_name}</Typography></TableCell>
      <TableCell>{kullanici.role}</TableCell>
      <TableCell><Box display="flex" flexWrap="wrap" gap={0.5}>
       <Chip size="small" color={kullanici.is_active?'success':'default'} label={kullanici.is_active?'Aktif':'Pasif'}/>
       <Chip size="small" variant="outlined" color={kullanici.email_verified?'success':'warning'} label={kullanici.email_verified?'Doğrulanmış':'Doğrulanmamış'}/>
       {kullanici.must_change_password&&<Chip size="small" variant="outlined" label="Şifre değişimi bekliyor"/>}
      </Box></TableCell>
      <TableCell><Box display="flex" flexWrap="wrap" gap={0.5}>
       {kullanici.memberships.length===0?<Typography variant="caption" color="text.secondary">Üyelik yok</Typography>:
        kullanici.memberships.map(uyelik=><Chip key={uyelik.company_id} size="small"
         variant={uyelik.is_default?'filled':'outlined'} color={uyelik.company_is_active?'primary':'default'}
         label={`${uyelik.company_name}${uyelik.is_default?' ★':''}${uyelik.company_is_active?'':' (pasif)'}`}/>)}
      </Box></TableCell>
      <TableCell>{tarihSaat(kullanici.last_login_at)}</TableCell>
      {/* TODO(PP2): şifre sıfırlama ve doğrulamayı yeniden gönderme uçları PP2'de. */}
      <TableCell align="right"><Stack direction="row" justifyContent="flex-end" gap={0.5}>
       <PP2Dugmesi etiket="Şifre sıfırla" ikon={<LockResetIcon/>}/>
       <PP2Dugmesi etiket="Doğrulama gönder" ikon={<MarkEmailReadIcon/>}/>
      </Stack></TableCell>
     </TableRow>)}
    </TableBody>
   </Table></TableContainer>
   <Sayfalama toplam={veri?.total??0} sayfa={sayfa} boyut={boyut} sayfaDegisti={setSayfa} boyutDegisti={yeni=>{setBoyut(yeni);setSayfa(0)}}/>
  </Paper>
  <BekleyenDogrulamalar/>
 </Stack>;
}
