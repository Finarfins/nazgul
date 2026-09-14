import React from 'react';
import {useCallback,useEffect,useMemo,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Alert,Box,Button,Card,CardContent,Chip,CircularProgress,Grid,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,Typography} from '@mui/material';

import {api,errorDetail} from '../api';
import {quantityDecimal} from '../utils/documentMoney';

/**
 * Parti Mutabakatı — `GET /api/products/lots/mutabakat` (1B-G / 1B-H).
 *
 * KURAL SUNUCUDADIR (`backend/app/parti_mutabakat.py`) ve burada YENİDEN
 * YAZILMAZ: kova, fark ve sayımlar sunucudan hazır gelir; ekran yalnız
 * biçimlendirir. Kovanın tanımını tarayıcıda ikinci kez kurmak, raporun kendi
 * sayacıyla çelişebileceği ikinci bir kopya olurdu.
 *
 * SAYIMLAR KİRACININ TAMAMINDANDIR, SAYFANIN DEĞİL. Kartlar bu yüzden `counts`tan
 * okunur, yüklenen satırlardan SAYILMAZ: ikinci sayfadaki tek SAPMA'yı ilk
 * sayfaya bakan operatör aksi hâlde "sapma yok" diye okurdu.
 *
 * KOVA FİLTRESİ YOK ve bilinçli: uçta kova filtresi yok, sayfa sunucuda kesiliyor.
 * Yüklenen sayfanın üstünde istemci filtresi, filtrelenmiş görünümü tenant'ın
 * tamamı sanan bir yalan olurdu. Yerine her sayfa KENDİ İÇİNDE SAPMA → LOTSUZ
 * TASARIM → EŞİT sırasıyla dizilir ve bu altyazıda söylenir.
 *
 * MİKTARLAR Decimal string gelir; eşitlik/işaret `quantityDecimal` ile okunur,
 * `Number()` ile değil.
 */

type Kova='ESIT'|'LOTSUZ_TASARIM'|'SAPMA';
type Satir={product_id:number;warehouse_id:number;stok:string;parti_toplami:string;fark:string;kova:Kova};
type Rapor={items:Satir[];counts:Record<Kova,number>;total:number;has_more:boolean;bos_ciftler:number};
type Urun={id:number;name:string;product_code?:string|null};
type Depo={id:number;name:string};

export const SAYFA_BOYU=200;

// Sıra: incelenmesi gereken önce. Sunucunun `KOVALAR` sırası (iyiden kötüye)
// raporun okunuşudur; ekranda operatörün işi olan satır yukarı çıkar.
const KOVA_SIRASI:Record<Kova,number>={SAPMA:0,LOTSUZ_TASARIM:1,ESIT:2};

// Açıklamalar `parti_mutabakat.py` başlığındaki kova tanımlarından kısaltıldı;
// yeni anlam EKLENMEDİ.
const KOVALAR:{kova:Kova;etiket:string;renk:'success'|'default'|'error';aciklama:string}[]=[
 {kova:'SAPMA',etiket:'Sapma',renk:'error',aciklama:'Parti satırı var ama toplam stoğu tutmuyor ya da parti toplamı stoğu aşıyor; incelenmesi gereken tek kova.'},
 {kova:'LOTSUZ_TASARIM',etiket:'Lotsuz (tasarım)',renk:'default',aciklama:'Bu çift için hiç parti satırı açılmamış; fark beklenendir, kusur değildir.'},
 {kova:'ESIT',etiket:'Eşit',renk:'success',aciklama:'Parti satırı var ve toplamı stoğa tam eşit; defter bu çift için tam.'},
];
const KOVA_BILGI=Object.fromEntries(KOVALAR.map(k=>[k.kova,k])) as Record<Kova,(typeof KOVALAR)[number]>;

const miktar=(value:string)=>quantityDecimal(value).toFixed();
// `fark = stok - parti_toplami` sunucuda türetilir. Negatif: parti stoktan
// fazla — defter elde olmayan malı VAR gösteriyor, tehlikeli yön budur.
const farkRengi=(fark:string)=>{
 const d=quantityDecimal(fark);
 return d.isNegative()&&!d.isZero()?'error.main':d.isPositive()&&!d.isZero()?'warning.main':undefined;
};
const farkMetni=(fark:string)=>{
 const d=quantityDecimal(fark);
 return d.isPositive()&&!d.isZero()?`+${d.toFixed()}`:d.toFixed();
};

export default function PartiMutabakati(){
 const nav=useNavigate();
 const [satirlar,setSatirlar]=useState<Satir[]>([]);
 const [rapor,setRapor]=useState<Omit<Rapor,'items'>|null>(null);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');
 const [urunler,setUrunler]=useState<Map<number,Urun>>(new Map());
 const [depolar,setDepolar]=useState<Map<number,Depo>>(new Map());

 // Ad çözümü: uç yalnız kimlik döndürür. Listeler SAYFA BAŞINA BİR KEZ
 // istenir, satır başına değil; bulunamayan kimlik `#id` ile gösterilir.
 useEffect(()=>{
  let active=true;
  api.get<Urun[]>('/products',{params:{limit:2000}})
   .then(r=>{if(active)setUrunler(new Map((r.data||[]).map(u=>[u.id,u])))})
   .catch(()=>{if(active)setUrunler(new Map())});
  api.get<Depo[]>('/warehouses')
   .then(r=>{if(active)setDepolar(new Map((r.data||[]).map(d=>[d.id,d])))})
   .catch(()=>{if(active)setDepolar(new Map())});
  return()=>{active=false};
 },[]);

 const yukle=useCallback((offset:number)=>{
  setLoading(true);setError('');
  return api.get<Rapor>('/products/lots/mutabakat',{params:{limit:SAYFA_BOYU,offset}})
   .then(({data})=>{
    const sayfa=[...(data.items||[])].sort((a,b)=>KOVA_SIRASI[a.kova]-KOVA_SIRASI[b.kova]);
    setSatirlar(onceki=>offset===0?sayfa:[...onceki,...sayfa]);
    setRapor({counts:data.counts,total:data.total,has_more:data.has_more,bos_ciftler:data.bos_ciftler});
   })
   .catch((e:unknown)=>setError(errorDetail(e,'Parti mutabakat raporu yüklenemedi.')))
   .finally(()=>setLoading(false));
 },[]);
 useEffect(()=>{void yukle(0)},[yukle]);

 const hepsiEsit=useMemo(()=>!!rapor&&rapor.total>0&&rapor.counts.SAPMA===0&&rapor.counts.LOTSUZ_TASARIM===0,[rapor]);

 const baslik=<Typography variant="h4" fontWeight={900}>Parti Mutabakatı</Typography>;
 if(!rapor){
  if(error)return <Stack spacing={2}>{baslik}<Alert severity="error">{error}</Alert></Stack>;
  return <Box py={6} display="grid" sx={{placeItems:'center'}}><CircularProgress aria-label="Parti mutabakatı yükleniyor"/></Box>;
 }

 return <Stack spacing={2}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1}>
   {baslik}
   <Chip variant="outlined" label={`${rapor.total} çift`}/>
  </Stack>
  <Typography variant="body2" color="text.secondary">
   Stok defteri (depo stoğu) ile parti defteri her (ürün, depo) çifti için yan yana. Rapor yalnız okur; farkı kapatmak farkı üreten hareketi partiye bağlamakla olur.
  </Typography>

  <Grid container spacing={1.5}>
   {KOVALAR.map(k=><Grid size={{xs:12,md:4}} key={k.kova}>
    <Card data-testid={`kova-${k.kova}`}><CardContent>
     <Stack direction="row" justifyContent="space-between" alignItems="center">
      <Chip size="small" color={k.renk} label={k.etiket}/>
      <Typography fontWeight={900} fontSize={22}>{rapor.counts[k.kova]??0}</Typography>
     </Stack>
     <Typography variant="caption" color="text.secondary" display="block" mt={1}>{k.aciklama}</Typography>
    </CardContent></Card>
   </Grid>)}
  </Grid>
  <Typography variant="caption" color="text.secondary" data-testid="bos-ciftler">
   {rapor.bos_ciftler} rapordan düşülen boş çift — stoğu 0 olan ve hiç partisi açılmamış çiftler; toplama dahil değildir.
  </Typography>

  {rapor.total===0
   ?<Alert severity="info">Rapor boş: stok ve parti defterinde karşılaştırılacak çift yok.</Alert>
   :hepsiEsit&&<Alert severity="success">Stok ve parti defteri hiçbir çiftte ayrışmıyor: {rapor.total} çiftin tamamı eşit.</Alert>}

  {rapor.total>0&&<Card><CardContent>
   <Typography variant="body2" color="text.secondary" mb={1.5}>
    Kova filtresi yok: sayfa sunucuda kesilir. Her sayfa kendi içinde önce Sapma, sonra Lotsuz (tasarım), sonra Eşit sıralanır. Ürün kartındaki partileri görmek için satıra tıklayın.
   </Typography>
   <TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small">
    <TableHead><TableRow>
     <TableCell sx={{fontWeight:800}}>Ürün</TableCell>
     <TableCell sx={{fontWeight:800}}>Depo</TableCell>
     <TableCell align="right" sx={{fontWeight:800}}>Stok</TableCell>
     <TableCell align="right" sx={{fontWeight:800}}>Parti Toplamı</TableCell>
     <TableCell align="right" sx={{fontWeight:800}}>Fark</TableCell>
     <TableCell sx={{fontWeight:800}}>Kova</TableCell>
    </TableRow></TableHead>
    <TableBody>{satirlar.map(s=>{
     const urun=urunler.get(s.product_id);
     const depo=depolar.get(s.warehouse_id);
     const bilgi=KOVA_BILGI[s.kova];
     return <TableRow key={`${s.product_id}-${s.warehouse_id}`} hover sx={{cursor:'pointer'}}
      data-testid={`satir-${s.product_id}-${s.warehouse_id}`} onClick={()=>nav(`/urunler/${s.product_id}`)}>
      <TableCell>
       <Typography fontWeight={700} fontSize={14}>{urun?.name||`Ürün #${s.product_id}`}</Typography>
       {urun?.product_code&&<Typography variant="caption" color="text.secondary">{urun.product_code}</Typography>}
      </TableCell>
      <TableCell>{depo?.name||`Depo #${s.warehouse_id}`}</TableCell>
      <TableCell align="right">{miktar(s.stok)}</TableCell>
      <TableCell align="right">{miktar(s.parti_toplami)}</TableCell>
      <TableCell align="right"><Typography component="span" fontSize={14} fontWeight={700} color={farkRengi(s.fark)} data-testid="fark">{farkMetni(s.fark)}</Typography></TableCell>
      <TableCell><Chip size="small" color={bilgi?.renk||'default'} label={bilgi?.etiket||s.kova}/></TableCell>
     </TableRow>;
    })}</TableBody>
   </Table></TableContainer>
   {error&&<Alert severity="error" sx={{mt:1.5}}>{error}</Alert>}
   <Stack direction="row" alignItems="center" justifyContent="space-between" mt={1.5} gap={1}>
    <Typography variant="caption" color="text.secondary">{satirlar.length} / {rapor.total} çift gösteriliyor</Typography>
    {rapor.has_more&&<Button variant="outlined" disabled={loading} onClick={()=>void yukle(satirlar.length)}>Daha fazla</Button>}
   </Stack>
  </CardContent></Card>}
 </Stack>;
}
