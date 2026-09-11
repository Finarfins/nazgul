import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {
 Alert,Autocomplete,Box,Button,Chip,Menu,MenuItem,Paper,Stack,TablePagination,TextField,Tooltip,Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import AddIcon from '@mui/icons-material/Add';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd';

import {api,money} from '../../api';
import {useAuth} from '../../AuthContext';
import ResponsiveTable from '../../components/ResponsiveTable';

import {
 type CekSenet,type CekSenetListesi,DURUM_ETIKETI,DURUM_RENGI,DURUMLAR,type Durum,type Eylem,EYLEMLER,
 ACIK_DURUMLAR,eylemEtkinMi,eylemGorunurMu,hataMesaji,kurusToplami,TUR_ETIKETI,YON_ETIKETI,yerelTarih,
} from './cekSenet';
import {BordroDialog,type Cari,DurumDialog,type Hesap,YeniEvrakDialog} from './CekSenetDialoglari';
import VadeTakvimi,{type VadeAraligi} from './VadeTakvimi';

const SAYFA_BOYUTLARI=[25,50,100];
type CariSecimi=Cari&{tip:'customer'|'supplier'};

/**
 * ÇEK / SENET PORTFÖYÜ (CS3). Veri `GET /api/cek-senetler`dan sunucu tarafı
 * sayfalama ile gelir (vade artan). İzin `payments`: admin/yönetici/muhasebe/
 * satış; depo/rapor rota korumasında durur, doğrudan 403 alırsa sayfa bunu
 * açıkça yazar.
 *
 * TOPLAM ÇUBUĞU: evrak SAYISI sunucunun `total`ıdır (süzgecin tamamı). TUTAR
 * toplamı için sunucuda bir uç YOK ve uydurulmadı: tutar İSTEMCİDE, yalnız
 * ekrandaki sayfadan hesaplanır ve etiket bunu söyler. Süzgecin tamamı tek
 * sayfaya sığıyorsa etiket "süzgeç toplamı" olur.
 *
 * `hesap_no` maskeli rollerde son 4 haneyle gelebilir: OLDUĞU GİBİ çizilir.
 */
export default function CekSenetPortfoyu(){
 const {can}=useAuth();
 // Tedarikçi listesi (`GET /api/suppliers`) `purchases` ister; `satis` taşımaz
 // (Payments.tsx ile aynı görünürlük kararı). Bu rolde tedarikçi seçicileri
 // hiç çizilmez, ciro devre dışıdır; asıl kapı backend'dedir.
 const tedarikciGorunur=can('purchases');

 const [evraklar,setEvraklar]=useState<CekSenet[]>([]);
 const [toplam,setToplam]=useState(0);
 const [sayfa,setSayfa]=useState(0);
 const [sayfaBoyutu,setSayfaBoyutu]=useState(25);
 const [yukleniyor,setYukleniyor]=useState(true);
 const [hata,setHata]=useState('');
 const [yetkisiz,setYetkisiz]=useState(false);
 const [bilgi,setBilgi]=useState('');

 const [tur,setTur]=useState('');
 const [yon,setYon]=useState('');
 const [durum,setDurum]=useState<Durum|''>('');
 const [cari,setCari]=useState<CariSecimi|null>(null);
 const [vadeFrom,setVadeFrom]=useState('');
 const [vadeTo,setVadeTo]=useState('');
 const [aramaGirdisi,setAramaGirdisi]=useState('');
 const [arama,setArama]=useState('');

 const [musteriler,setMusteriler]=useState<Cari[]>([]);
 const [tedarikciler,setTedarikciler]=useState<Cari[]>([]);
 const [hesaplar,setHesaplar]=useState<Hesap[]>([]);

 const [yeniAcik,setYeniAcik]=useState(false);
 const [bordroAcik,setBordroAcik]=useState(false);
 const [eylemHedefi,setEylemHedefi]=useState<{eylem:Eylem;evrak:CekSenet}|null>(null);
 const [menu,setMenu]=useState<{el:HTMLElement;evrak:CekSenet}|null>(null);
 const [takvimSurumu,setTakvimSurumu]=useState(0);
 const sira=useRef(0);

 // Arama kutusu her tuşta istek atmaz.
 useEffect(()=>{const t=setTimeout(()=>{setArama(aramaGirdisi.trim());setSayfa(0)},250);return()=>clearTimeout(t)},[aramaGirdisi]);

 const yukle=useCallback(async()=>{
  const bu=++sira.current;
  setYukleniyor(true);setHata('');
  const params:Record<string,string|number>={limit:sayfaBoyutu,offset:sayfa*sayfaBoyutu};
  if(tur)params.tur=tur;
  if(yon)params.yon=yon;
  if(durum)params.portfoy_durumu=durum;
  if(cari?.tip==='customer')params.customer_id=cari.id;
  if(cari?.tip==='supplier')params.supplier_id=cari.id;
  if(vadeFrom)params.vade_from=vadeFrom;
  if(vadeTo)params.vade_to=vadeTo;
  if(arama)params.q=arama;
  try{
   const yanit=await api.get<CekSenetListesi>('/cek-senetler',{params});
   if(bu!==sira.current)return;
   setEvraklar(yanit.data.items);setToplam(yanit.data.total);setYetkisiz(false);
  }catch(exception){
   if(bu!==sira.current)return;
   setEvraklar([]);setToplam(0);
   const durumKodu=(exception as {response?:{status?:number}})?.response?.status;
   setYetkisiz(durumKodu===403);
   setHata(durumKodu===403?'Çek/senet portföyünü görüntüleme yetkiniz yok.':hataMesaji(exception,'Çek/senet listesi yüklenemedi'));
  }finally{
   if(bu===sira.current)setYukleniyor(false);
  }
 },[arama,cari,durum,sayfa,sayfaBoyutu,tur,vadeFrom,vadeTo,yon]);

 useEffect(()=>{void yukle()},[yukle]);

 useEffect(()=>{
  api.get<Cari[]>('/customers').then(r=>setMusteriler(r.data)).catch(()=>setMusteriler([]));
  if(tedarikciGorunur)api.get<Cari[]>('/suppliers').then(r=>setTedarikciler(r.data)).catch(()=>setTedarikciler([]));
  api.get<Hesap[]>('/payments/accounts',{params:{active_only:true}}).then(r=>setHesaplar(r.data)).catch(()=>setHesaplar([]));
 },[tedarikciGorunur]);

 const musteriAdi=useMemo(()=>new Map(musteriler.map(m=>[m.id,m.name])),[musteriler]);
 const tedarikciAdi=useMemo(()=>new Map(tedarikciler.map(t=>[t.id,t.name])),[tedarikciler]);
 const cariAdi=useCallback((evrak:CekSenet)=>{
  if(evrak.customer_id!==null)return musteriAdi.get(evrak.customer_id)||`Müşteri #${evrak.customer_id}`;
  if(evrak.supplier_id!==null)return tedarikciAdi.get(evrak.supplier_id)||`Tedarikçi #${evrak.supplier_id}`;
  return '—';
 },[musteriAdi,tedarikciAdi]);
 const cariSecenekleri=useMemo<CariSecimi[]>(()=>[
  ...musteriler.map(m=>({...m,tip:'customer' as const})),
  ...tedarikciler.map(t=>({...t,tip:'supplier' as const})),
 ],[musteriler,tedarikciler]);

 // Süzgeç değişince ilk sayfaya dön: aksi hâlde boş bir offset'te kalınır.
 const suz=<T,>(ayarla:(deger:T)=>void)=>(deger:T)=>{ayarla(deger);setSayfa(0)};
 const vadeAraligiSec=(aralik:VadeAraligi)=>{setVadeFrom(aralik.vadeFrom);setVadeTo(aralik.vadeTo);setSayfa(0)};
 const temizle=()=>{setTur('');setYon('');setDurum('');setCari(null);setVadeFrom('');setVadeTo('');setAramaGirdisi('');setArama('');setSayfa(0)};

 const bugun=yerelTarih();
 const vadesiGecmis=(evrak:CekSenet)=>evrak.vade<bugun&&(ACIK_DURUMLAR as readonly string[]).includes(evrak.portfoy_durumu);
 const sayfaToplami=kurusToplami(evraklar.map(e=>e.tutar));
 const tekSayfa=toplam<=evraklar.length;

 const ciroKapali=(eylem:Eylem)=>eylem.hedef==='ciro_edildi'&&!tedarikciGorunur;
 const eylemAc=(eylem:Eylem,evrak:CekSenet)=>{setMenu(null);setBilgi('');setEylemHedefi({eylem,evrak})};
 const degisti=(mesaj:string)=>{setBilgi(mesaj);setTakvimSurumu(s=>s+1);void yukle()};

 const sutunlar:GridColDef<CekSenet>[]=[
  {field:'vade',headerName:'Vade',width:120,renderCell:p=><Typography variant="body2"
    color={vadesiGecmis(p.row)?'error.main':undefined} fontWeight={vadesiGecmis(p.row)?800:undefined}>{p.row.vade}</Typography>},
  {field:'tur',headerName:'Tür',width:80,valueFormatter:v=>TUR_ETIKETI[String(v)]||v},
  {field:'yon',headerName:'Yön',width:90,valueFormatter:v=>YON_ETIKETI[String(v)]||v},
  {field:'seri_no',headerName:'Seri No',width:130},
  {field:'customer_id',headerName:'Cari',flex:1,minWidth:180,renderCell:p=>cariAdi(p.row)},
  {field:'banka_adi',headerName:'Banka',width:140,valueFormatter:v=>v||'—'},
  // Maskeli rolde "****1234" gelir; OLDUĞU GİBİ yazılır, yeniden kurulmaz.
  {field:'hesap_no',headerName:'Hesap No',width:130,valueFormatter:v=>v||'—'},
  {field:'tutar',headerName:'Tutar',width:140,valueFormatter:v=>money(v)},
  {field:'portfoy_durumu',headerName:'Durum',width:140,renderCell:p=>{
   const d=p.row.portfoy_durumu as Durum;
   return <Chip size="small" color={DURUM_RENGI[d]||'default'} label={DURUM_ETIKETI[d]||d}/>;
  }},
  {field:'__eylemler',headerName:'İşlem',width:120,sortable:false,filterable:false,renderCell:p=><Button size="small"
    endIcon={<MoreVertIcon fontSize="small"/>} aria-label={`${p.row.seri_no} — İşlemler`}
    onClick={event=>{event.stopPropagation();setMenu({el:event.currentTarget,evrak:p.row})}}>İşlemler</Button>},
 ];

 const kartEylemleri=EYLEMLER.map(eylem=>({
  label:eylem.etiket,
  hidden:(evrak:CekSenet)=>!eylemGorunurMu(eylem,evrak),
  disabled:(evrak:CekSenet)=>!eylemEtkinMi(eylem,evrak)||ciroKapali(eylem),
  onClick:(evrak:CekSenet)=>eylemAc(eylem,evrak),
 }));

 return <Stack spacing={2}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1}>
   <Box>
    <Typography variant="h4" fontWeight={900}>Çek / Senet Portföyü</Typography>
    <Typography variant="body2" color="text.secondary">Alınan ve verilen evrakların durumu, vadesi ve hareketleri.</Typography>
   </Box>
   {!yetkisiz&&<Stack direction="row" gap={1} flexWrap="wrap" alignItems="flex-start">
    <Button variant="outlined" startIcon={<PlaylistAddIcon/>} onClick={()=>setBordroAcik(true)}>Bordro Girişi</Button>
    <Button variant="contained" startIcon={<AddIcon/>} onClick={()=>setYeniAcik(true)}>Yeni Evrak</Button>
   </Stack>}
  </Stack>

  {hata&&<Alert severity={yetkisiz?'warning':'error'}>{hata}</Alert>}
  {bilgi&&<Alert severity="success" onClose={()=>setBilgi('')}>{bilgi}</Alert>}

  {!yetkisiz&&<>
   <VadeTakvimi yenile={takvimSurumu} onSec={vadeAraligiSec}/>

   <Paper sx={{p:2}}>
    <Stack spacing={1.5}>
     <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
      <Typography variant="body2" color="text.secondary" mr={.5}>Durum:</Typography>
      <Chip label="Tümü" color={durum===''?'primary':'default'} variant={durum===''?'filled':'outlined'} onClick={()=>suz(setDurum)('')}/>
      {DURUMLAR.map(d=><Chip key={d} label={DURUM_ETIKETI[d]} color={durum===d?DURUM_RENGI[d]:'default'}
        variant={durum===d?'filled':'outlined'} onClick={()=>suz(setDurum)(d)} aria-pressed={durum===d}/>)}
     </Stack>
     <Stack direction={{xs:'column',md:'row'}} spacing={1.5} flexWrap="wrap" useFlexGap>
      <TextField size="small" label="Ara (seri no, keşideci, banka)" value={aramaGirdisi} onChange={e=>setAramaGirdisi(e.target.value)} sx={{minWidth:240,flex:1}}/>
      <TextField select size="small" label="Tür" value={tur} onChange={e=>suz(setTur)(e.target.value)} sx={{minWidth:120}}>
       <MenuItem value="">Tümü</MenuItem><MenuItem value="cek">Çek</MenuItem><MenuItem value="senet">Senet</MenuItem>
      </TextField>
      <TextField select size="small" label="Yön" value={yon} onChange={e=>suz(setYon)(e.target.value)} sx={{minWidth:130}}>
       <MenuItem value="">Tümü</MenuItem><MenuItem value="alinan">Alınan</MenuItem><MenuItem value="verilen">Verilen</MenuItem>
      </TextField>
      <Autocomplete size="small" sx={{minWidth:240}} options={cariSecenekleri} value={cari}
        groupBy={o=>o.tip==='customer'?'Müşteriler':'Tedarikçiler'}
        isOptionEqualToValue={(a,b)=>a.id===b.id&&a.tip===b.tip} getOptionLabel={o=>o?.name||''}
        onChange={(_,v)=>suz(setCari)(v)} renderInput={p=><TextField {...p} label="Cari"/>}/>
      <TextField size="small" type="date" label="Vade Başlangıç" value={vadeFrom} onChange={e=>suz(setVadeFrom)(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
      <TextField size="small" type="date" label="Vade Bitiş" value={vadeTo} onChange={e=>suz(setVadeTo)(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
      <Button onClick={temizle}>Temizle</Button>
     </Stack>
    </Stack>
   </Paper>

   <Paper variant="outlined" sx={{px:2,py:1}} data-testid="cek-toplam-cubugu">
    <Typography variant="body2">
     <b>{toplam}</b> evrak · {tekSayfa?'Süzgeç toplamı':`Bu sayfadaki ${evraklar.length} evrakın toplamı`}: <b>{money(sayfaToplami)}</b>
    </Typography>
   </Paper>

   {!yukleniyor&&evraklar.length===0&&!hata?<Paper sx={{py:4,textAlign:'center'}}>
    <Typography variant="body2" color="text.secondary">Seçilen süzgeçlerde çek/senet yok.</Typography>
   </Paper>:<ResponsiveTable
    rows={evraklar}
    columns={sutunlar}
    loading={yukleniyor}
    hideFooter
    cardTitle={(e:CekSenet)=>`${TUR_ETIKETI[e.tur]||e.tur} · ${e.seri_no}`}
    cardSubtitle={(e:CekSenet)=>`${e.vade} · ${DURUM_ETIKETI[e.portfoy_durumu as Durum]||e.portfoy_durumu}`}
    cardFields={[
     {label:'Cari',value:(e:CekSenet)=>cariAdi(e)},
     {label:'Yön',value:(e:CekSenet)=>YON_ETIKETI[e.yon]||e.yon},
     {label:'Tutar',value:(e:CekSenet)=>money(e.tutar)},
     {label:'Hesap No',value:(e:CekSenet)=>e.hesap_no||'—'},
    ]}
    cardActions={kartEylemleri}
   />}

   <Paper>
    <TablePagination component="div" count={toplam} page={sayfa} rowsPerPage={sayfaBoyutu}
     rowsPerPageOptions={SAYFA_BOYUTLARI} labelRowsPerPage="Sayfa başına"
     labelDisplayedRows={({from,to,count})=>`${from}–${to} / ${count}`}
     onPageChange={(_,sonraki)=>setSayfa(sonraki)}
     onRowsPerPageChange={e=>{setSayfaBoyutu(Number(e.target.value));setSayfa(0)}}/>
   </Paper>
  </>}

  <Menu anchorEl={menu?.el} open={Boolean(menu)} onClose={()=>setMenu(null)}>
   {menu&&EYLEMLER.filter(eylem=>eylemGorunurMu(eylem,menu.evrak)).map(eylem=>{
    const kapali=!eylemEtkinMi(eylem,menu.evrak)||ciroKapali(eylem);
    const oge=<MenuItem key={eylem.anahtar} disabled={kapali} onClick={()=>eylemAc(eylem,menu.evrak)}>{eylem.etiket}</MenuItem>;
    return ciroKapali(eylem)
     ?<Tooltip key={eylem.anahtar} title="Tedarikçi listesi için satın alma yetkisi gerekir"><span>{oge}</span></Tooltip>
     :oge;
   })}
  </Menu>

  <YeniEvrakDialog open={yeniAcik} onClose={()=>setYeniAcik(false)}
   onSaved={()=>{setYeniAcik(false);degisti('Evrak portföye alındı.')}}
   musteriler={musteriler} tedarikciler={tedarikciler} tedarikciGorunur={tedarikciGorunur}/>
  <BordroDialog open={bordroAcik} onClose={()=>setBordroAcik(false)}
   onSaved={adet=>{setBordroAcik(false);degisti(`Bordro kaydedildi: ${adet} evrak portföye alındı.`)}}
   musteriler={musteriler} tedarikciler={tedarikciler} tedarikciGorunur={tedarikciGorunur}/>
  <DurumDialog eylem={eylemHedefi?.eylem??null} evrak={eylemHedefi?.evrak??null}
   onClose={()=>setEylemHedefi(null)}
   onDone={()=>{const hedef=eylemHedefi;setEylemHedefi(null);degisti(`${hedef?.evrak.seri_no}: ${hedef?DURUM_ETIKETI[hedef.eylem.hedef]:''}`)}}
   hesaplar={hesaplar} tedarikciler={tedarikciler}/>
 </Stack>;
}
