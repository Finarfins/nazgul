import {useEffect,useMemo,useState} from 'react';
import {Alert,Box,Card,CardActionArea,CardContent,Paper,Stack,Typography} from '@mui/material';

import {api,money} from '../../api';

import {ACIK_DURUMLAR,type CekSenet,type CekSenetListesi,gunEkle,hataMesaji,kurusToplami,TUR_ETIKETI,yerelTarih} from './cekSenet';

export type Kova='gecikmis'|'bugun'|'yedi'|'otuz';
export type VadeAraligi={vadeFrom:string;vadeTo:string};

/** Tek istekte okunan evrak tavanı (backend `SAYFA_TAVANI`). */
const TAVAN=200;
const GUN_UFKU=30;

/** Kovalar AYRIKTIR: bir evrak tam olarak bir kovaya düşer. */
const kovaBul=(vade:string,bugun:string):Kova|null=>{
 if(vade<bugun)return 'gecikmis';
 if(vade===bugun)return 'bugun';
 if(vade<=gunEkle(bugun,7))return 'yedi';
 if(vade<=gunEkle(bugun,GUN_UFKU))return 'otuz';
 return null;
};

const kovaAraligi=(kova:Kova,bugun:string):VadeAraligi=>{
 switch(kova){
  case 'gecikmis':return {vadeFrom:'',vadeTo:gunEkle(bugun,-1)};
  case 'bugun':return {vadeFrom:bugun,vadeTo:bugun};
  case 'yedi':return {vadeFrom:gunEkle(bugun,1),vadeTo:gunEkle(bugun,7)};
  case 'otuz':return {vadeFrom:gunEkle(bugun,8),vadeTo:gunEkle(bugun,GUN_UFKU)};
 }
};

const KOVALAR:{anahtar:Kova;etiket:string}[]=[
 {anahtar:'gecikmis',etiket:'Vadesi Geçmiş'},
 {anahtar:'bugun',etiket:'Bugün'},
 {anahtar:'yedi',etiket:'1–7 Gün'},
 {anahtar:'otuz',etiket:'8–30 Gün'},
];

type Props={
 /** Değeri değiştikçe takvim yeniden okunur (ör. bir durum değişikliğinden sonra). */
 yenile:number;
 /** Bir kovaya tıklanınca portföy tablosunun vade süzgeci o aralığa çekilir. */
 onSec:(aralik:VadeAraligi)=>void;
};

/**
 * VADE TAKVİMİ — hâlâ açık (portföyde / tahsilde) evrakların vadeye göre
 * dağılımı. Tamamen istemci tarafında hesaplanır: her açık durum için bir
 * `GET /api/cek-senetler?portfoy_durumu=..&vade_to=bugün+30` çağrısı yapılır
 * (sunucu tek durum süzgeci alır) ve sonuç yerel güne göre kovalara bölünür.
 * Yeni bir uç YOKTUR.
 */
export default function VadeTakvimi({yenile,onSec}:Props){
 const [evraklar,setEvraklar]=useState<CekSenet[]>([]);
 const [eksik,setEksik]=useState(false);
 const [hata,setHata]=useState('');
 const bugun=yerelTarih();

 useEffect(()=>{
  let iptal=false;
  setHata('');
  Promise.all(ACIK_DURUMLAR.map(durum=>api.get<CekSenetListesi>('/cek-senetler',{params:{
   portfoy_durumu:durum,vade_to:gunEkle(bugun,GUN_UFKU),limit:TAVAN,offset:0,
  }}))).then(yanitlar=>{
   if(iptal)return;
   setEvraklar(yanitlar.flatMap(yanit=>yanit.data.items));
   setEksik(yanitlar.some(yanit=>yanit.data.total>yanit.data.items.length));
  }).catch(exception=>{
   if(iptal)return;
   setEvraklar([]);
   setHata(hataMesaji(exception,'Vade takvimi yüklenemedi'));
  });
  return()=>{iptal=true};
 },[yenile,bugun]);

 const kovalar=useMemo(()=>{
  const sonuc:Record<Kova,CekSenet[]>={gecikmis:[],bugun:[],yedi:[],otuz:[]};
  for(const evrak of evraklar){
   const kova=kovaBul(evrak.vade,bugun);
   if(kova)sonuc[kova].push(evrak);
  }
  return sonuc;
 },[evraklar,bugun]);

 const toplam=(liste:CekSenet[],yon:string)=>kurusToplami(liste.filter(e=>e.yon===yon).map(e=>e.tutar));

 return <Paper variant="outlined" sx={{p:2}} data-testid="vade-takvimi">
  <Typography variant="h6" fontWeight={900}>Vade Takvimi</Typography>
  <Typography variant="body2" color="text.secondary" mb={1.5}>
   Portföyde ve tahsildeki evraklar. Bir kutuya tıklayınca tablo o vade aralığına süzülür.
  </Typography>
  {hata&&<Alert severity="error" sx={{mb:1.5}}>{hata}</Alert>}
  <Stack direction={{xs:'column',md:'row'}} spacing={1}>
   {KOVALAR.map(({anahtar,etiket})=>{
    const liste=kovalar[anahtar];
    const gecikmis=anahtar==='gecikmis'&&liste.length>0;
    return <Card key={anahtar} variant="outlined" data-testid={`vade-kova-${anahtar}`}
      sx={{flex:1,borderColor:gecikmis?'error.main':undefined,bgcolor:gecikmis?'rgba(211,47,47,.06)':undefined}}>
     <CardActionArea onClick={()=>onSec(kovaAraligi(anahtar,bugun))} sx={{height:'100%'}}>
      <CardContent>
       <Typography variant="caption" color={gecikmis?'error.main':'text.secondary'} fontWeight={gecikmis?800:400}>{etiket}</Typography>
       <Typography variant="h6" fontWeight={900} color={gecikmis?'error.main':undefined}>{liste.length} evrak</Typography>
       <Typography variant="body2" color="text.secondary">Alınan {money(toplam(liste,'alinan'))}</Typography>
       <Typography variant="body2" color="text.secondary">Verilen {money(toplam(liste,'verilen'))}</Typography>
      </CardContent>
     </CardActionArea>
    </Card>;
   })}
  </Stack>
  {kovalar.gecikmis.length>0&&<Box mt={1.5}>
   <Typography variant="subtitle2" color="error.main" fontWeight={800}>Vadesi geçmiş açık evraklar</Typography>
   {kovalar.gecikmis.slice(0,5).map(evrak=><Typography key={evrak.id} variant="body2" color="error.main">
    {evrak.vade} · {TUR_ETIKETI[evrak.tur]||evrak.tur} {evrak.seri_no} · {money(evrak.tutar)}
   </Typography>)}
   {kovalar.gecikmis.length>5&&<Typography variant="caption" color="text.secondary">ve {kovalar.gecikmis.length-5} evrak daha</Typography>}
  </Box>}
  {eksik&&<Typography variant="caption" color="text.secondary" display="block" mt={1}>
   Durum başına ilk {TAVAN} evrak hesaba katıldı; tam liste için tabloyu vade aralığına süzün.
  </Typography>}
 </Paper>;
}
