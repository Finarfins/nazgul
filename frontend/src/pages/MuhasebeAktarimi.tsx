import React from 'react';
import {useCallback,useEffect,useState} from 'react';
import {Alert,AlertTitle,Box,Button,Card,CardContent,Chip,CircularProgress,Grid,IconButton,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Typography} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';

import {api,errorDetail} from '../api';
import type {components} from '../api/types.gen';
import {moneyDecimal} from '../utils/documentMoney';

/**
 * Muhasebe Aktarımı — F9-5c-2 (keşif §7.3). YALNIZ ön yüz: uçlar F9-5a/5b'de.
 *
 *   GET /api/accounting/vat-summary?period=  → KDV özeti + uyarılar
 *   GET /api/accounting/vouchers?period=&limit=&offset= → fiş önizlemesi
 *   GET /api/accounting/export?period=&target=&format= → zip
 *
 * KURAL SUNUCUDADIR: oran grupları, toplamlar, uyarı metinleri ve dengesiz fiş
 * kararı sunucudan hazır gelir; ekran yalnız biçimlendirir. Uyarının `mesaj`ı
 * OLDUĞU GİBİ gösterilir — yeniden yazmak müşavirin mutabakat yaptığı metni
 * ikinci bir kopyaya çevirirdi.
 *
 * İNDİRME 409'U BİR HATA LİSTESİDİR, TOST DEĞİL: dengesiz fiş (`DONEM_DENGESIZ`,
 * `ilk_hatalar`) ve tekrarlı fiş no (`FIS_NO_TEKRARLI`) kullanıcının düzeltmesi
 * gereken belgeleri adıyla söyler. İstek `blob` olarak yapıldığı için hata
 * gövdesi de Blob gelir ve burada JSON'a geri okunur; `errorDetail` onu göremez.
 */

type KdvOzeti=components['schemas']['KdvOzeti'];
type FisListesi=components['schemas']['FisListesi'];
type FisGorunumu=components['schemas']['FisGorunumu'];
type Uyari=components['schemas']['MuhasebeUyarisi'];
type AktarimHatasi=components['schemas']['DisaAktarimHatasiDetayi'];

export const FIS_SAYFA_BOYU=100;
// Akan zip büyük dönemde 15 sn'lik varsayılan istemci süresini aşabilir.
const INDIRME_SURESI_MS=120_000;

/**
 * Sunucunun kabul ettiği hedefler (`routers/accounting.py` SERILESTIRICILER,
 * `types.gen.ts` `target` açıklaması: luca | mikro | canonical). Logo YOK:
 * sunucu `target=logo`yu 422 HEDEF_DESTEKLENMIYOR ile reddeder (K2).
 * `format` hedefin kendi biçimidir; farklısı 422 BICIM_DESTEKLENMIYOR döner.
 */
export const HEDEFLER:readonly {target:string;format:'xlsx'|'csv';etiket:string}[]=[
 {target:'luca',format:'xlsx',etiket:'Luca (xlsx)'},
 {target:'mikro',format:'csv',etiket:'Mikro (csv)'},
 {target:'canonical',format:'csv',etiket:'Kanonik (csv + json)'},
];

const TUR_ETIKETI:Record<string,string>={
 SATIS:'Satış',
 SERVIS_FATURA:'Servis faturası',
 ALIS_IADE:'Alış iadesi',
 ALIS:'Alış',
 SATIS_IADE:'Satış iadesi',
};
const YON_ETIKETI:Record<string,string>={HESAPLANAN:'Hesaplanan',INDIRILECEK:'İndirilecek'};
const HATA_BASLIGI:Record<string,string>={
 DONEM_DENGESIZ:'Dönemde dengesiz fiş var',
 FIS_NO_TEKRARLI:'Tekrarlı fiş no',
};

/** Europe/Istanbul'daki bir önceki ay, `YYYY-AA`. Tarayıcı dilimi kullanılmaz. */
export function oncekiDonem(simdi:Date=new Date()):string{
 const parcalar=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Istanbul',year:'numeric',month:'2-digit'}).formatToParts(simdi);
 const yil=Number(parcalar.find(p=>p.type==='year')?.value);
 const ay=Number(parcalar.find(p=>p.type==='month')?.value);
 return ay===1?`${yil-1}-12`:`${yil}-${String(ay-1).padStart(2,'0')}`;
}

const DONEM_DESENI=/^\d{4}-(0[1-9]|1[0-2])$/;

/** Decimal metin → `1.234,56`. `Number()`dan geçmez. */
export function tutar(deger:string){
 const [tam,kurus]=moneyDecimal(deger).toFixed(2).split('.');
 const eksi=tam.startsWith('-');
 const gruplu=(eksi?tam.slice(1):tam).replace(/\B(?=(\d{3})+(?!\d))/g,'.');
 return `${eksi?'-':''}${gruplu},${kurus}`;
}
const oranMetni=(oran:string)=>`%${moneyDecimal(oran).toFixed()}`;

async function blobMetni(veri:Blob):Promise<string>{
 if(typeof veri.text==='function')return veri.text();
 return new Promise((coz,reddet)=>{
  const okuyucu=new FileReader();
  okuyucu.onload=()=>coz(String(okuyucu.result));
  okuyucu.onerror=()=>reddet(okuyucu.error);
  okuyucu.readAsText(veri);
 });
}

/** Blob ya da nesne olarak gelen `{detail:{code,message,...}}` gövdesini okur. */
export async function aktarimHatasiOku(hata:unknown):Promise<AktarimHatasi|null>{
 let veri=(hata as {response?:{data?:unknown}})?.response?.data;
 if(typeof Blob!=='undefined'&&veri instanceof Blob){
  try{veri=JSON.parse(await blobMetni(veri))}catch{return null}
 }
 const detay=(veri as {detail?:unknown}|undefined)?.detail;
 if(detay&&typeof detay==='object'&&!Array.isArray(detay)&&typeof (detay as AktarimHatasi).message==='string'){
  return detay as AktarimHatasi;
 }
 return null;
}

function dosyaAdi(basliklar:unknown,yedek:string){
 const deger=String((basliklar as Record<string,unknown>|undefined)?.['content-disposition']||'');
 return deger.match(/filename="([^"]+)"/)?.[1]||yedek;
}

function dosyayiSun(veri:Blob,ad:string){
 const href=URL.createObjectURL(veri);
 const a=document.createElement('a');
 a.href=href;a.download=ad;
 document.body.appendChild(a);a.click();a.remove();
 setTimeout(()=>URL.revokeObjectURL(href),30000);
}

function UyariSatiri({uyari}:{uyari:Uyari}){
 return <Box component="li" data-testid="muhasebe-uyarisi" sx={{py:0.75}}>
  <Stack direction="row" gap={0.75} flexWrap="wrap" alignItems="center" mb={0.25}>
   <Chip size="small" color="warning" variant="outlined" label={uyari.kod}/>
   <Typography variant="caption" color="text.secondary">{uyari.kaynak}{uyari.belge_no?` · ${uyari.belge_no}`:''}</Typography>
   {uyari.fark&&<Typography variant="caption" color="text.secondary">fark {tutar(uyari.fark)}</Typography>}
   {uyari.kdv_dahil===true&&<Chip size="small" color="info" data-testid="kdv-dahil" label="KDV özetine dahil"/>}
  </Stack>
  <Typography variant="body2">{uyari.mesaj}</Typography>
 </Box>;
}

function HataListesi({hata}:{hata:AktarimHatasi}){
 const ilk=hata.ilk_hatalar||[];
 return <Alert severity="error" data-testid="aktarim-hatasi">
  <AlertTitle>{HATA_BASLIGI[hata.code]||hata.code}</AlertTitle>
  <Typography variant="body2">{hata.message}</Typography>
  {ilk.length>0&&<Box component="ul" sx={{m:0,mt:1,pl:2.5}}>
   {ilk.map((satir,i)=><li key={i}><Typography variant="body2" component="span">{satir}</Typography></li>)}
  </Box>}
  {typeof hata.dengesiz_sayisi==='number'&&hata.dengesiz_sayisi>ilk.length&&
   <Typography variant="caption" display="block" mt={0.5}>{hata.dengesiz_sayisi} dengesiz fişin ilk {ilk.length} tanesi gösteriliyor.</Typography>}
 </Alert>;
}

function FisSatiri({fis}:{fis:FisGorunumu}){
 const [acik,setAcik]=useState(false);
 return <>
  <TableRow data-testid={`fis-${fis.fis_no}`}>
   <TableCell padding="checkbox">
    <IconButton size="small" aria-label={acik?'Satırları gizle':'Satırları göster'} onClick={()=>setAcik(v=>!v)}>
     {acik?<KeyboardArrowUpIcon fontSize="small"/>:<KeyboardArrowDownIcon fontSize="small"/>}
    </IconButton>
   </TableCell>
   <TableCell sx={{fontWeight:700}}>{fis.fis_no}</TableCell>
   <TableCell>{fis.fis_tarihi}</TableCell>
   <TableCell>{fis.belge_tipi} · {fis.belge_no}</TableCell>
   <TableCell>{fis.cari?.ad||'—'}</TableCell>
   <TableCell align="right">{tutar(fis.borc_toplami)}</TableCell>
   <TableCell align="right">{tutar(fis.alacak_toplami)}</TableCell>
  </TableRow>
  {/* Detay satırı YALNIZ açıkken çizilir: 100 fişlik sayfada 100 gizli
      tablo taşımanın bedeli ekranda da testte de ödenmez. */}
  {acik&&<TableRow>
   <TableCell colSpan={7} sx={{py:0}}>
     <Table size="small" sx={{my:1}}>
      <TableHead><TableRow>
       <TableCell>Hesap</TableCell><TableCell>Açıklama</TableCell><TableCell>KDV</TableCell>
       <TableCell align="right">Borç</TableCell><TableCell align="right">Alacak</TableCell>
      </TableRow></TableHead>
      <TableBody>{fis.satirlar.map((s,i)=><TableRow key={i}>
       <TableCell>{s.hesap_kodu}</TableCell>
       <TableCell>{s.aciklama}</TableCell>
       <TableCell>{s.kdv_orani?oranMetni(s.kdv_orani):'—'}</TableCell>
       <TableCell align="right">{tutar(s.borc)}</TableCell>
       <TableCell align="right">{tutar(s.alacak)}</TableCell>
      </TableRow>)}</TableBody>
     </Table>
   </TableCell>
  </TableRow>}
 </>;
}

export default function MuhasebeAktarimi(){
 const [donem,setDonem]=useState(()=>oncekiDonem());
 const [ozet,setOzet]=useState<KdvOzeti|null>(null);
 const [ozetHata,setOzetHata]=useState('');
 const [fisler,setFisler]=useState<FisListesi|null>(null);
 const [fisHata,setFisHata]=useState('');
 const [offset,setOffset]=useState(0);
 const [yukleniyor,setYukleniyor]=useState(false);
 const [indirilen,setIndirilen]=useState<string|null>(null);
 const [aktarimHatasi,setAktarimHatasi]=useState<AktarimHatasi|null>(null);
 const [indirmeHata,setIndirmeHata]=useState('');
 const gecerli=DONEM_DESENI.test(donem);

 useEffect(()=>{
  setOffset(0);setAktarimHatasi(null);setIndirmeHata('');
  if(!gecerli){setOzet(null);return}
  let aktif=true;
  setYukleniyor(true);setOzetHata('');
  api.get<KdvOzeti>('/accounting/vat-summary',{params:{period:donem}})
   .then(({data})=>{if(aktif)setOzet(data)})
   .catch((e:unknown)=>{if(aktif){setOzet(null);setOzetHata(errorDetail(e,'KDV özeti yüklenemedi.'))}})
   .finally(()=>{if(aktif)setYukleniyor(false)});
  return()=>{aktif=false};
 },[donem,gecerli]);

 useEffect(()=>{
  if(!gecerli){setFisler(null);return}
  let aktif=true;
  setFisHata('');
  api.get<FisListesi>('/accounting/vouchers',{params:{period:donem,limit:FIS_SAYFA_BOYU,offset}})
   .then(({data})=>{if(aktif)setFisler(data)})
   .catch((e:unknown)=>{if(aktif){setFisler(null);setFisHata(errorDetail(e,'Fişler yüklenemedi.'))}});
  return()=>{aktif=false};
 },[donem,gecerli,offset]);

 const indir=useCallback(async(hedef:(typeof HEDEFLER)[number])=>{
  setIndirilen(hedef.target);setAktarimHatasi(null);setIndirmeHata('');
  try{
   const cevap=await api.get<Blob>('/accounting/export',{
    params:{period:donem,target:hedef.target,format:hedef.format},
    responseType:'blob',timeout:INDIRME_SURESI_MS,
   });
   dosyayiSun(cevap.data,dosyaAdi(cevap.headers,`muhasebe-${donem}-${hedef.target}.zip`));
  }catch(e){
   const govde=await aktarimHatasiOku(e);
   if(govde)setAktarimHatasi(govde);
   else setIndirmeHata(errorDetail(e,'Dışa aktarım indirilemedi.'));
  }finally{
   setIndirilen(null);
  }
 },[donem]);

 const hesaplanan=ozet?.satirlar.filter(s=>s.yon==='HESAPLANAN')||[];
 const indirilecek=ozet?.satirlar.filter(s=>s.yon==='INDIRILECEK')||[];

 return <Stack spacing={2}>
  <Typography variant="h4" fontWeight={900}>Muhasebe Aktarımı</Typography>
  <Typography variant="body2" color="text.secondary">
   Dönemin muhasebe fişleri, KDV özeti ve uyarıları. Tutarlar ve uyarılar sunucudan hazır gelir; bu ekran yalnız gösterir ve indirir.
  </Typography>

  <Card><CardContent>
   <Stack direction={{xs:'column',sm:'row'}} gap={2} alignItems={{sm:'center'}}>
    <TextField
     type="month" label="Dönem" size="small" value={donem}
     onChange={e=>setDonem(e.target.value)}
     error={!gecerli} helperText={gecerli?'Tek ay (YYYY-AA)':'Geçerli bir ay seçin'}
     slotProps={{inputLabel:{shrink:true},htmlInput:{'data-testid':'donem'}}}
    />
    <Stack direction="row" gap={1} flexWrap="wrap">
     {HEDEFLER.map(h=><Button key={h.target} variant="outlined" startIcon={indirilen===h.target?<CircularProgress size={16}/>:<DownloadIcon/>}
      disabled={!gecerli||indirilen!==null} onClick={()=>void indir(h)}>
      {h.etiket}
     </Button>)}
    </Stack>
   </Stack>
   <Typography variant="caption" color="text.secondary" display="block" mt={1}>
    Her zip hedef dosyalarının yanında kanonik fisler.json / fisler.csv ve manifest.json taşır. Dengesiz ya da tekrarlı fiş varsa dosya üretilmez.
   </Typography>
   {aktarimHatasi&&<Box mt={1.5}><HataListesi hata={aktarimHatasi}/></Box>}
   {indirmeHata&&<Alert severity="error" sx={{mt:1.5}}>{indirmeHata}</Alert>}
  </CardContent></Card>

  {ozetHata&&<Alert severity="error">{ozetHata}</Alert>}
  {yukleniyor&&!ozet&&<Box py={4} display="grid" sx={{placeItems:'center'}}><CircularProgress aria-label="KDV özeti yükleniyor"/></Box>}

  {ozet&&<>
   <Grid container spacing={1.5}>
    {[
     ['Hesaplanan KDV',ozet.hesaplanan_kdv,'hesaplanan'],
     ['İndirilecek KDV',ozet.indirilecek_kdv,'indirilecek'],
     ['Fark',ozet.fark,'fark'],
    ].map(([etiket,deger,kimlik])=><Grid size={{xs:12,md:4}} key={kimlik}>
     <Card data-testid={`toplam-${kimlik}`}><CardContent>
      <Typography variant="caption" color="text.secondary">{etiket}</Typography>
      <Typography fontWeight={900} fontSize={22}>{tutar(deger)}</Typography>
     </CardContent></Card>
    </Grid>)}
   </Grid>

   <Card><CardContent>
    <Typography variant="h6" fontWeight={800} mb={1}>KDV özeti — {ozet.period}</Typography>
    {ozet.satirlar.length===0
     ?<Alert severity="info">{"Bu dönemde KDV'li belge yok."}</Alert>
     :<TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small" data-testid="kdv-tablosu">
      <TableHead><TableRow>
       <TableCell sx={{fontWeight:800}}>Yön</TableCell>
       <TableCell sx={{fontWeight:800}}>Tür</TableCell>
       <TableCell sx={{fontWeight:800}}>Oran</TableCell>
       <TableCell align="right" sx={{fontWeight:800}}>Matrah</TableCell>
       <TableCell align="right" sx={{fontWeight:800}}>KDV</TableCell>
       <TableCell align="right" sx={{fontWeight:800}}>Belge</TableCell>
      </TableRow></TableHead>
      <TableBody>{[...hesaplanan,...indirilecek].map(s=><TableRow key={`${s.yon}-${s.tur}-${s.oran}`} data-testid={`kdv-${s.yon}-${s.tur}-${s.oran}`}>
       <TableCell>{YON_ETIKETI[s.yon]||s.yon}</TableCell>
       <TableCell>{TUR_ETIKETI[s.tur]||s.tur}</TableCell>
       <TableCell>
        {oranMetni(s.oran)}
        {!s.bilinen_oran&&<Chip size="small" color="warning" variant="outlined" sx={{ml:0.75}} label="yasal oran değil"/>}
       </TableCell>
       <TableCell align="right">{tutar(s.matrah)}</TableCell>
       <TableCell align="right">{tutar(s.kdv)}</TableCell>
       <TableCell align="right">{s.belge_sayisi}</TableCell>
      </TableRow>)}</TableBody>
     </Table></TableContainer>}
   </CardContent></Card>

   <Card data-testid="mustahsil"><CardContent>
    <Typography variant="h6" fontWeight={800} mb={1}>Müstahsil makbuzları</Typography>
    <Stack direction={{xs:'column',sm:'row'}} gap={{xs:0.5,sm:3}}>
     <Typography variant="body2">Belge: <b>{ozet.mustahsil.belge_sayisi}</b></Typography>
     <Typography variant="body2">Brüt: <b>{tutar(ozet.mustahsil.brut)}</b></Typography>
     <Typography variant="body2">Stopaj: <b>{tutar(ozet.mustahsil.stopaj)}</b></Typography>
     <Typography variant="body2">Bağ-Kur: <b>{tutar(ozet.mustahsil.bagkur)}</b></Typography>
     <Typography variant="body2">Net: <b>{tutar(ozet.mustahsil.net)}</b></Typography>
    </Stack>
   </CardContent></Card>

   <Card><CardContent>
    <Typography variant="h6" fontWeight={800} mb={1}>Uyarılar ({ozet.uyarilar.length})</Typography>
    {ozet.uyarilar.length===0
     ?<Typography variant="body2" color="text.secondary">Bu dönem için uyarı yok.</Typography>
     :<Box component="ul" sx={{m:0,pl:2.5}}>{ozet.uyarilar.map((u,i)=><UyariSatiri key={`${u.kod}-${u.belge_no??''}-${i}`} uyari={u}/>)}</Box>}
    {ozet.kapsam_notlari.length>0&&<>
     <Typography variant="subtitle2" fontWeight={800} mt={2}>Kapsam notları</Typography>
     <Box component="ul" sx={{m:0,pl:2.5}}>{ozet.kapsam_notlari.map((n,i)=><li key={i}><Typography variant="body2" color="text.secondary">{n}</Typography></li>)}</Box>
    </>}
   </CardContent></Card>
  </>}

  {gecerli&&<Card><CardContent>
   <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1} gap={1} flexWrap="wrap">
    <Typography variant="h6" fontWeight={800}>Fişler</Typography>
    {fisler&&fisler.dengesiz_sayisi>0&&<Chip color="error" label={`${fisler.dengesiz_sayisi} dengesiz fiş listede yok`}/>}
   </Stack>
   {fisHata&&<Alert severity="error">{fisHata}</Alert>}
   {fisler&&(fisler.total===0
    ?<Alert severity="info">Bu dönemde fiş yok.</Alert>
    :<>
     <TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small">
      <TableHead><TableRow>
       <TableCell padding="checkbox"/>
       <TableCell sx={{fontWeight:800}}>Fiş no</TableCell>
       <TableCell sx={{fontWeight:800}}>Tarih</TableCell>
       <TableCell sx={{fontWeight:800}}>Belge</TableCell>
       <TableCell sx={{fontWeight:800}}>Cari</TableCell>
       <TableCell align="right" sx={{fontWeight:800}}>Borç</TableCell>
       <TableCell align="right" sx={{fontWeight:800}}>Alacak</TableCell>
      </TableRow></TableHead>
      <TableBody>{fisler.items.map(f=><FisSatiri key={f.fis_no} fis={f}/>)}</TableBody>
     </Table></TableContainer>
     <Stack direction="row" alignItems="center" justifyContent="space-between" mt={1.5} gap={1}>
      <Typography variant="caption" color="text.secondary" data-testid="fis-araligi">
       {fisler.offset+1}–{fisler.offset+fisler.items.length} / {fisler.total} fiş
      </Typography>
      <Stack direction="row" gap={1}>
       <Button variant="outlined" disabled={offset===0} onClick={()=>setOffset(o=>Math.max(0,o-FIS_SAYFA_BOYU))}>Önceki</Button>
       <Button variant="outlined" disabled={offset+FIS_SAYFA_BOYU>=fisler.total} onClick={()=>setOffset(o=>o+FIS_SAYFA_BOYU)}>Sonraki</Button>
      </Stack>
     </Stack>
    </>)}
  </CardContent></Card>}
 </Stack>;
}
