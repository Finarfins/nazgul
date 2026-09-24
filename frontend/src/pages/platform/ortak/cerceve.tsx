/**
 * Platform ekranlarının SAYFA ÇERÇEVESİ: başlık ve yükleme hatası panelleri.
 *
 * 403 BOŞ SAYFA DEĞİLDİR — rota koruması `can('platform')` ile zaten süzer,
 * ama bayrak oturum içinde düşerse (operatör listesinden çıkarılma) sayfa
 * sessizce boş kalmamalı.
 */
import React from 'react';
import {Alert,Box,Button,Paper,Stack,Typography} from '@mui/material';
import LockIcon from '@mui/icons-material/Lock';

import type {YuklemeHatasi} from './veri';

/** 403: operatör olmayan kullanıcıya düz ve açık bir panel. */
export function YetkiYok(){
 return <Paper sx={{p:4,textAlign:'center'}} data-testid="platform-yetki-yok">
  <LockIcon color="disabled" sx={{fontSize:40}}/>
  <Typography variant="h6" fontWeight={800}>Bu ekrana yetkiniz yok</Typography>
  <Typography color="text.secondary">Platform yönetim paneli yalnız platform operatörlerine açıktır.</Typography>
 </Paper>;
}

/** Yükleme hatasını çizer; 403 için `YetkiYok`, diğerleri için yeniden dene. */
export function HataPaneli({hata,yenile}:{hata:YuklemeHatasi;yenile:()=>void}){
 if(hata.tur==='yetki')return <YetkiYok/>;
 return <Alert severity="error" action={<Button color="inherit" size="small" onClick={yenile}>Yeniden dene</Button>}>{hata.mesaj}</Alert>;
}

/** Sayfa başlığı — Backups.tsx ile aynı düzen. */
export function PlatformBaslik({baslik,aciklama,sag}:{baslik:string;aciklama:string;sag?:React.ReactNode}){
 return <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2}>
  <Box><Typography variant="h5" fontWeight={900}>{baslik}</Typography><Typography color="text.secondary">{aciklama}</Typography></Box>
  {sag}
 </Stack>;
}
