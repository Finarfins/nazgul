import React from 'react';
import {useEffect,useState} from 'react';
import {Alert,Box,Chip,CircularProgress,Stack,Typography} from '@mui/material';

import {api,errorDetail} from '../api';

/**
 * Ürünün parti (lot) defteri — `GET /api/products/{id}/lots`.
 *
 * VERİYİ SEKME AÇILINCA İSTER, sayfayla birlikte DEĞİL. Sebep ölçülebilir:
 * `GET /api/products/{id}` zaten beş sekmenin verisini tek gövdede taşıyor ve
 * parti defterini oraya eklemek, partiye HİÇ bakmayan her ziyareti de
 * yavaşlatırdı. Ayrı uç ayrı istektir; istek sekme açılana kadar YAPILMAZ.
 *
 * MİKTARI SIFIR OLAN PARTİ GİZLENMEZ, SOLDURULUR. Tükenmiş parti satırı göç
 * 0067'de bilinçli olarak SİLİNMİYOR çünkü o satır GERİ ÇAĞIRMANIN KANITIDIR:
 * "bu partiden mal çıktı ve bitti" ile "böyle bir parti hiç olmadı" iki farklı
 * cümledir ve ekran ikincisini söyleyemez. Satırı listeden düşürmek, kanıtı
 * veritabanında tutup operatörden SAKLAMAK olurdu.
 *
 * RENK TEK BAŞINA ANLAM TAŞIMAZ: tükenmiş satır hem soluk hem de "Tükendi"
 * etiketiyle işaretlenir (sayfanın geri kalanındaki stok durumu rozetiyle aynı
 * kural).
 *
 * SIRA SUNUCUDAN GELİR ve burada YENİDEN SIRALANMAZ. Uç listeyi depo adı, SKT,
 * kimlik sırasıyla veriyor ve o sıra FEFO seçicisinin sırası DEĞİLDİR (uç bunu
 * kendi belgesinde söylüyor). İstemcide ikinci bir sıralama kurmak, listenin
 * bir gün seçicinin cevabı sanılmasına yol açardı.
 */
export default function ProductLotsPanel({productId}:{productId:number}){
 const [lots,setLots]=useState<any[]|null>(null);
 const [error,setError]=useState('');
 useEffect(()=>{
  let iptal=false;
  setLots(null);setError('');
  api.get(`/products/${productId}/lots`)
   .then(r=>{if(!iptal)setLots(r.data?.lots||[])})
   .catch(e=>{if(!iptal)setError(errorDetail(e,'Parti bilgileri yüklenemedi.'))});
  // Ürün değişirse önceki isteğin geç gelen cevabı yeni ürünün listesini
  // EZMEMELİ: iki istek yarışırsa ekran yanlış ürünün partilerini gösterirdi.
  return ()=>{iptal=true};
 },[productId]);

 if(error)return <Alert severity="error">{error}</Alert>;
 if(lots===null)return <Box py={4} display="grid" sx={{placeItems:'center'}}>
   <CircularProgress aria-label="Partiler yükleniyor"/></Box>;
 if(!lots.length)return <Typography sx={{fontSize:14,color:'text.secondary',py:2.5}}>
   Bu ürün için parti kaydı bulunmuyor.</Typography>;

 return <Stack spacing={1}>
  {lots.map((l:any)=>{
   const miktar=Number(l.quantity||0);
   const tukendi=miktar===0;
   return <Stack key={l.id} direction={{xs:'column',sm:'row'}} justifyContent="space-between"
     alignItems={{sm:'center'}} gap={1}
     sx={{p:1.5,border:'1px solid',borderColor:'divider',borderRadius:2,
      opacity:tukendi?0.55:1}}>
    <Box>
     <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Typography fontWeight={800} sx={{fontSize:15}}>{l.lot_code||'-'}</Typography>
      {tukendi&&<Chip label="Tükendi" size="small" variant="outlined"/>}
     </Stack>
     <Typography sx={{fontSize:13.5,lineHeight:1.5,color:'text.secondary'}}>
      {l.warehouse_name||'Depo belirtilmemiş'}
      {' · '}
      {/* SKT İSTEĞE BAĞLIDIR (sahip kararı 2): tarihi olmayan parti "-" değil,
          ne olduğunu SÖYLEYEN bir metinle geçer. */}
      {l.expiry_date?`SKT: ${l.expiry_date}`:'SKT yok'}
      {l.created_at?` · Açılış: ${String(l.created_at).slice(0,10)}`:''}
     </Typography>
    </Box>
    <Typography fontWeight={900} sx={{fontSize:17}}
      color={tukendi?'text.secondary':'text.primary'}>{miktar}</Typography>
   </Stack>;
  })}
 </Stack>;
}
