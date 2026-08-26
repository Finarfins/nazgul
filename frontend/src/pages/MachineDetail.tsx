/**
 * Makine 360 — sekmeli detay kartı (docs/ux-yenileme-f0-tasarim.md §4, U3).
 *
 * Önceki hali sekmesiz, ~4000px kaydırmalı tek sayfaydı: 4 `Paper` bloğu alt
 * alta ve 3 tablo elle `<Table>` ile kurulmuştu. Burada aynı veri, aynı uçlar
 * ve aynı yazma eylemleriyle sekmeli kabuğa alındı; tablolar `ResponsiveTable`
 * üzerinden çizildiği için mobil/tablette karta dönüşüyorlar.
 *
 * YENİ UÇ YOK: dört sekmenin de verisi F1'de eklenmiş mevcut uçlardan gelir
 * (§4.2).
 *
 * EKLER SEKMESİ BİLİNÇLİ OLARAK YOK. Makine bazlı ek ucu yok (tasarımda "M-2",
 * §4.3); ekler yalnız iş emrine bağlı. İş emri başına istek atıp kısmî bir
 * liste göstermek kullanıcıya TAM liste gibi görünürdü — eksikliği fark
 * edilmeyen veri, olmayan veriden kötüdür. Ekler U3'ün değil M-2'nin işidir ve
 * oraya tek uçla (`GET /machines/{id}/attachments`) tam ve tek istekle gelecek.
 * Yer tutucu / "yakında" kartı da konmadı: bkz. §6 U3 — "U3'te yer tutucu bile
 * konmaz".
 *
 * Yer imi sözleşmesi korunur: `/makineler/:id` TEK rota olarak kalır. Aktif
 * sekme `?sekme=` sorgu parametresinde tutulur — sorgu dizesi yeni bir rota
 * değildir (`navigation.tsx#normalizePath` sorguyu zaten ayıklar), bu yüzden
 * `ROUTE_PERMISSIONS` ve menü sözleşmesi değişmez. Yazım `{replace:true}` ile
 * yapılır: bağlantı paylaşılabilir ve yenilemede sekme korunur, ama her sekme
 * tıklaması geçmişe itilmediği için geri tuşu makine listesine döner.
 */
import React from 'react';
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {useNavigate,useParams,useSearchParams,Link as RouterLink} from 'react-router-dom';
import {Alert,Autocomplete,Box,Button,Card,CardContent,Chip,CircularProgress,Dialog,DialogActions,DialogContent,DialogTitle,Divider,Grid,Link,MenuItem,Paper,Stack,Tab,Tabs,TextField,Typography} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';import EditIcon from '@mui/icons-material/Edit';import BlockIcon from '@mui/icons-material/Block';import CheckCircleIcon from '@mui/icons-material/CheckCircle';import AddIcon from '@mui/icons-material/Add';import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import BadgeIcon from '@mui/icons-material/Badge';import SpeedIcon from '@mui/icons-material/Speed';import BuildIcon from '@mui/icons-material/Build';
import ResponsiveTable from '../components/ResponsiveTable';
import MachineDialog,{MACHINE_STATUS_LABELS} from '../components/MachineDialog';import {api,errorDetail,money} from '../api';import {useAuth} from '../AuthContext';
import {WO_STATUS_LABELS,WO_STATUS_COLORS} from '../workOrderConstants';

const Meta=({label,value}:{label:string;value:React.ReactNode})=><Grid size={{xs:12,sm:6,md:4}}><Typography variant="caption" color="text.secondary">{label}</Typography><Typography fontWeight={600}>{value??'-'}</Typography></Grid>;

// Para/miktar alanları Number() ile yuvarlanmaz; string olarak doğrulanıp
// olduğu gibi gönderilir (backend Decimal ile doğrular).
const isNonNegativeDecimal=(value:string)=>/^\+?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value.trim());

const READING_TYPE_LABELS:Record<string,string>={
 normal:'Normal',anomaly:'Anomali',meter_replacement:'Sayaç Değişimi',correction:'Düzeltme',
};
const READING_SOURCE_LABELS:Record<string,string>={manual:'Manuel',work_order:'İş Emri'};

/**
 * Sekme etiketleri — `NAV_LABELS` düzeninde tek blok, tek kaynak.
 * Testler bu sabitten okur; ekrandaki metni değiştirmek tek satırlık iştir.
 */
export const MACHINE_TAB_LABELS={
 identity:'Kimlik',
 readings:'Sayaç Geçmişi',
 ownership:'Sahiplik Tarihçesi',
 service:'Servis / İş Emirleri',
} as const;

/** Sekme sırası; `tab` durumu bu dizinin indeksidir. */
export const MACHINE_TABS=[
 MACHINE_TAB_LABELS.identity,
 MACHINE_TAB_LABELS.readings,
 MACHINE_TAB_LABELS.ownership,
 MACHINE_TAB_LABELS.service,
] as const;

/** `?sekme=` değerleri; sıra `MACHINE_TABS` ile birebir aynı olmalıdır. */
export const MACHINE_TAB_SLUGS=['kimlik','sayac','sahiplik','servis'] as const;
export const TAB_QUERY_KEY='sekme';

/** Geçersiz/eksik slug hata değil, ilk sekmedir. */
export const tabIndexForSlug=(slug:string|null):number=>{
 const index=MACHINE_TAB_SLUGS.indexOf((slug||'') as (typeof MACHINE_TAB_SLUGS)[number]);
 return index<0?0:index;
};

/**
 * Sayfa boyutları. İş emri ucu gerçek sayfalama sunar (`page`/`page_size`,
 * yanıtta `total`+`pages`); sayaç ucu `limit`/`offset` sunar ama toplam
 * DÖNDÜRMEZ — bu yüzden orada "sonraki var mı" bilgisi dolu sayfa gelmesinden
 * türetilir ve toplam yalnız tek sayfaya sığdığında yazılır. Sahiplik ucunda
 * hiç sınır yoktur, tüm kayıtlar döner (bkz. machine_ownership.py).
 */
const WORK_ORDER_PAGE_SIZE=25;
const READING_PAGE_SIZE=25;

/**
 * Sayı değilse `null`. Metadata'yı 0/1 gibi varsayılanlara düşürmek, eksik
 * bilgiyi "eksik" değil "sıfır" diye göstermek olurdu.
 */
const numberOrNull=(value:unknown):number|null=>
 typeof value==='number'&&Number.isFinite(value)?value:null;

const dateOnly=(value:unknown)=>value?String(value).slice(0,10):'-';
const dateTime=(value:unknown)=>value?String(value).slice(0,16).replace('T',' '):'-';
const ellipsis={maxWidth:260,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'} as const;
/** Dokunmatik hedef alt sınırı (POS/tablet servis kullanımı). */
const touch={minHeight:44} as const;

// ---- Sayaç okuma girişi -----------------------------------------------------
function HourReadingDialog({open,machineId,onClose,onSaved}:{open:boolean;machineId:number;onClose:()=>void;onSaved:()=>void}){
 const blank={reading_hours:'',reading_type:'normal',reason:''};
 const [v,setV]=useState<any>(blank);const [error,setError]=useState('');const [saving,setSaving]=useState(false);
 useEffect(()=>{if(open){setV(blank);setError('')}},[open]);
 const needsReason=v.reading_type!=='normal';
 const canSave=isNonNegativeDecimal(v.reading_hours)&&(!needsReason||Boolean(v.reason.trim()));
 const save=async()=>{try{setSaving(true);setError('');
   const payload:any={reading_hours:v.reading_hours.trim(),reading_type:v.reading_type};
   if(v.reason.trim())payload.reason=v.reason.trim();
   await api.post(`/machines/${machineId}/hour-readings`,payload);
   onSaved();onClose();
  }catch(e:any){setError(e.response?.data?.detail||e.message)}finally{setSaving(false)}};
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs"><DialogTitle>Yeni Sayaç Okuması</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}>
  {error&&<Alert severity="error">{error}</Alert>}
  <TextField label="Çalışma Saati" value={v.reading_hours} onChange={e=>setV({...v,reading_hours:e.target.value})} required error={Boolean(v.reading_hours)&&!isNonNegativeDecimal(v.reading_hours)} helperText="Örn. 1250.50"/>
  <TextField select label="Okuma Türü" value={v.reading_type} onChange={e=>setV({...v,reading_type:e.target.value})}>
   {Object.entries(READING_TYPE_LABELS).map(([val,label])=><MenuItem key={val} value={val}>{label}</MenuItem>)}
  </TextField>
  {v.reading_type==='normal'
   ?<Typography variant="caption" color="text.secondary">Sayaç düşüşü yalnız &quot;Sayaç Değişimi&quot; veya &quot;Düzeltme&quot; türüyle ve açıklamayla kaydedilebilir.</Typography>
   :<TextField label="Açıklama" value={v.reason} onChange={e=>setV({...v,reason:e.target.value})} required multiline minRows={2} error={needsReason&&!v.reason.trim()} helperText="Bu okuma türü için zorunlu"/>}
 </Stack></DialogContent><DialogActions><Button onClick={onClose}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={saving||!canSave}>{saving?'Kaydediliyor...':'Kaydet'}</Button></DialogActions></Dialog>;
}

// ---- Sahiplik devri ---------------------------------------------------------
function OwnershipTransferDialog({open,machineId,currentCustomerId,onClose,onSaved}:{open:boolean;machineId:number;currentCustomerId:number|null;onClose:()=>void;onSaved:()=>void}){
 const [customers,setCustomers]=useState<any[]>([]);const [customerId,setCustomerId]=useState<number|null>(null);
 const [reason,setReason]=useState('');const [error,setError]=useState('');const [saving,setSaving]=useState(false);
 useEffect(()=>{if(!open)return;setCustomerId(null);setReason('');setError('');
  api.get('/customers',{params:{active:'all'}}).then(r=>setCustomers(r.data||[])).catch(()=>setCustomers([]));
 },[open]);
 const options=customers.filter(c=>c.id!==currentCustomerId);
 const save=async()=>{try{setSaving(true);setError('');
   const payload:any={to_customer_id:customerId};
   if(reason.trim())payload.reason=reason.trim();
   await api.post(`/machines/${machineId}/transfer-ownership`,payload);
   onSaved();onClose();
  }catch(e:any){setError(e.response?.data?.detail||e.message)}finally{setSaving(false)}};
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs"><DialogTitle>Sahiplik Devri</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}>
  {error&&<Alert severity="error">{error}</Alert>}
  <Autocomplete options={options} getOptionLabel={o=>o?.name||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={options.find(c=>c.id===customerId)||null} onChange={(_,val)=>setCustomerId(val?val.id:null)} renderInput={params=><TextField {...params} required label="Yeni Müşteri"/>}/>
  <TextField label="Açıklama (ör. satış)" value={reason} onChange={e=>setReason(e.target.value)} multiline minRows={2}/>
  <Typography variant="caption" color="text.secondary">Açık iş emri varken sahiplik devredilemez; devir geçmişi makine kartında saklanır.</Typography>
 </Stack></DialogContent><DialogActions><Button onClick={onClose}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={saving||!customerId}>{saving?'Devrediliyor...':'Devret'}</Button></DialogActions></Dialog>;
}

// ---- Sekme başına tek durum makinesi ---------------------------------------
//
// `loading → (error | empty | data)`. Üç bitiş durumu AYRIK BİRLEŞİM ile
// modellenir; "hata" ile "kayıt yok" aynı anda çizilemez — derleyici izin
// vermez. Servis/para geçmişinde bu fark kritiktir: yüklenemeyen listeye
// bakan kullanıcı "bu makinenin servis kaydı yok" sonucuna varmamalıdır.

/** Sayfalama bilgisi. `total`/`pages` yalnız uç veriyorsa doludur. */
type PageInfo={page:number;hasNext:boolean;total:number|null;pages:number|null};
type Loaded<T>={rows:T[];pageInfo:PageInfo|null};

type ListState<T>=
 |{status:'loading'}
 |{status:'error';message:string}
 |{status:'empty';pageInfo:PageInfo|null}
 |{status:'data';rows:T[];pageInfo:PageInfo|null};

/**
 * Tek bir listenin durum makinesi. `load` çağıran tarafta `useCallback` ile
 * sarılır; kimliği değişince (makine değişti, sayfa değişti) otomatik yeniden
 * yükler ve durum `loading`'e döner. Yarış koşulu sıra numarasıyla elenir:
 * geç dönen eski yanıt yeni durumu ezmez.
 */
function useListResource<T>(load:()=>Promise<Loaded<T>>,errorText:string){
 const [state,setState]=useState<ListState<T>>({status:'loading'});
 const seq=useRef(0);
 const reload=useCallback(()=>{
  const ticket=++seq.current;
  setState({status:'loading'});
  load().then(({rows,pageInfo})=>{
   if(ticket!==seq.current)return;
   setState(rows.length?{status:'data',rows,pageInfo}:{status:'empty',pageInfo});
  }).catch(e=>{
   if(ticket!==seq.current)return;
   setState({status:'error',message:errorDetail(e,errorText)});
  });
 },[load,errorText]);
 useEffect(()=>{reload()},[reload]);
 return [state,reload] as const;
}

/**
 * Sayfalama şeridi. Toplam ASLA uydurulmaz:
 *   - uç toplamı verdiyse yazılır;
 *   - vermediyse ama liste tek sayfaya sığdıysa yüklenen satır sayısı zaten
 *     toplamdır (türetme, uydurma değil);
 *   - vermediyse ve birden fazla sayfa varsa açıkça "toplam bilinmiyor" denir.
 */
function Pager({pageInfo,loaded,onPage}:{pageInfo:PageInfo|null;loaded:number;onPage:(page:number)=>void}){
 if(!pageInfo)return null;
 const {page,hasNext,total,pages}=pageInfo;
 // Tek sayfa: ilk sayfadayız ve sonrası yok → toplam kesin bilinir.
 if(page===1&&!hasNext){
  const exact=total??loaded;
  return <Typography variant="caption" color="text.secondary">Toplam {exact} kayıt</Typography>;
 }
 const summary=total!==null
  ?(pages!==null?`Toplam ${total} kayıt · Sayfa ${page}/${pages}`:`Toplam ${total} kayıt · Sayfa ${page}`)
  :`Sayfa ${page} · toplam bilinmiyor`;
 return <Stack direction="row" spacing={1} alignItems="center" justifyContent="flex-end">
  <Typography variant="caption" color="text.secondary" flex={1}>{summary}</Typography>
  <Button size="small" variant="outlined" disabled={page<=1} onClick={()=>onPage(page-1)} sx={touch}>Önceki</Button>
  <Button size="small" variant="outlined" disabled={!hasNext} onClick={()=>onPage(page+1)} sx={touch}>Sonraki</Button>
 </Stack>;
}

/**
 * Sekme gövdesi. Durumun ÜÇ bitişi de birbirini dışlar; `&&` zinciri yok,
 * erken dönüş var. Hata varsa boş-durum metni hiçbir koşulda çizilmez.
 */
function TabPanel<T>({state,emptyText,onPage,children}:{state:ListState<T>;emptyText:string;onPage?:(page:number)=>void;children:(rows:T[])=>React.ReactNode}){
 if(state.status==='loading')return <Box py={4} display="grid" sx={{placeItems:'center'}}><CircularProgress size={28} aria-label="Yükleniyor"/></Box>;
 if(state.status==='error')return <Alert severity="error">{state.message}</Alert>;
 if(state.status==='empty')return <Stack spacing={1.5}>
  <Typography color="text.secondary" py={2}>{emptyText}</Typography>
  <Pager pageInfo={state.pageInfo} loaded={0} onPage={onPage??(()=>{})}/>
 </Stack>;
 return <Stack spacing={1.5}>
  {children(state.rows)}
  <Pager pageInfo={state.pageInfo} loaded={state.rows.length} onPage={onPage??(()=>{})}/>
 </Stack>;
}

export default function MachineDetail(){
 const {id}=useParams();const nav=useNavigate();const {can}=useAuth();const canWrite=can('machines');
 const [machine,setMachine]=useState<any|null>(null);const [loading,setLoading]=useState(true);const [error,setError]=useState('');const [open,setOpen]=useState(false);const [toggling,setToggling]=useState(false);
 const [readingOpen,setReadingOpen]=useState(false);const [transferOpen,setTransferOpen]=useState(false);
 const [orderPage,setOrderPage]=useState(1);const [readingPage,setReadingPage]=useState(1);

 // Aktif sekme URL'den türetilir; yerel kopya tutulmaz ki derin bağlantı,
 // yenileme ve geri/ileri tek kaynaktan beslensin.
 const [searchParams,setSearchParams]=useSearchParams();
 const tab=tabIndexForSlug(searchParams.get(TAB_QUERY_KEY));
 const setTab=useCallback((next:number)=>{
  const params=new URLSearchParams(searchParams);
  params.set(TAB_QUERY_KEY,MACHINE_TAB_SLUGS[next]);
  // replace: her sekme tıklaması geçmişe itilseydi kullanıcı listeye dönmek
  // için dört kez geri basardı.
  setSearchParams(params,{replace:true});
 },[searchParams,setSearchParams]);

 const load=useCallback(()=>{setLoading(true);setError('');api.get(`/machines/${id}`).then(r=>setMachine(r.data)).catch(e=>setError(errorDetail(e,'Makine yüklenemedi.'))).finally(()=>setLoading(false))},[id]);
 useEffect(()=>{load()},[load]);
 // Makine değişince sayfa imleçleri başa sarılır.
 useEffect(()=>{setOrderPage(1);setReadingPage(1)},[id]);

 // İş emirleri backend'de machine_id ile sunucu tarafında filtrelenir.
 //
 // Metadata UYDURULMAZ. `Number(x??0)` deseni, uç `total`/`pages` göndermezse
 // ekranda satırlar dururken "Toplam 0 kayıt" yazdırıyordu — eksik veriyi
 // eksik değil YANLIŞ göstermek, sessiz kesmeyle aynı sınıf. `null` olduğu
 // gibi taşınır; Pager onu "toplam bilinmiyor" diye yazar.
 const loadOrders=useCallback(async():Promise<Loaded<any>>=>{
  const r=await api.get('/work-orders',{params:{machine_id:id,page:orderPage,page_size:WORK_ORDER_PAGE_SIZE}});
  const rows=r.data?.items||[];
  const total=numberOrNull(r.data?.total);
  const pages=numberOrNull(r.data?.pages);
  // Sonraki sayfa bilgisi metadata'dan türetilir. Uç hiçbir metadata
  // göndermezse elimizdeki tek sinyal dolu sayfa gelmesidir; bu uç `page`/
  // `page_size` alıyor, `offset` almıyor, dolayısıyla sayaçtaki +1 deseni
  // burada uygulanamaz (page_size'ı 1 artırmak sunucu offset'ini kaydırıp
  // her sayfa sınırında bir kaydı GÖRÜNMEZ yapardı).
  const hasNext=pages!==null?orderPage<pages
   :total!==null?orderPage*WORK_ORDER_PAGE_SIZE<total
   :rows.length===WORK_ORDER_PAGE_SIZE;
  return {rows,pageInfo:{page:orderPage,hasNext,total,pages}};
 },[id,orderPage]);
 const [orderState,reloadOrders]=useListResource<any>(loadOrders,'Servis geçmişi yüklenemedi.');

 // Sayaç ucu limit/offset veriyor ama TOPLAM DÖNDÜRMÜYOR.
 //
 // "+1 deseni": bir sayfalık değil, bir fazla kayıt istenir. Fazlalık gelirse
 // sonraki sayfa VARDIR, gelmezse yoktur — ve fazlalık ekrana çizilmez.
 // Eski `rows.length===PAGE_SIZE` testi tam kat sayılarda yanılıyordu: tam 25
 // okuması olan makinede "Sonraki" çıkıp boş sayfaya götürüyordu.
 const loadReadings=useCallback(async():Promise<Loaded<any>>=>{
  const r=await api.get(`/machines/${id}/hour-readings`,{params:{limit:READING_PAGE_SIZE+1,offset:(readingPage-1)*READING_PAGE_SIZE}});
  const fetched=r.data||[];
  const rows=fetched.slice(0,READING_PAGE_SIZE);
  return {rows,pageInfo:{page:readingPage,hasNext:fetched.length>READING_PAGE_SIZE,total:null,pages:null}};
 },[id,readingPage]);
 const [readingState,reloadReadings]=useListResource<any>(loadReadings,'Sayaç geçmişi yüklenemedi.');

 // Sahiplik ucunda sınır yok: tüm kayıtlar tek istekte döner, sayfalama şeridi
 // de bu yüzden çizilmez (pageInfo null).
 const loadOwnership=useCallback(async():Promise<Loaded<any>>=>{
  const r=await api.get(`/machines/${id}/ownership-history`);
  return {rows:r.data||[],pageInfo:null};
 },[id]);
 const [ownershipState,reloadOwnership]=useListResource<any>(loadOwnership,'Sahiplik tarihçesi yüklenemedi.');

 const orderTotal=orderState.status==='data'?orderState.pageInfo?.total??null:orderState.status==='empty'?orderState.pageInfo?.total??null:null;
 const ownershipCount=ownershipState.status==='data'?ownershipState.rows.length:ownershipState.status==='empty'?0:null;
 const countLabel=(base:string,count:number|null)=>count===null?base:`${base} (${count})`;

 const toggleActive=async()=>{if(!machine)return;try{setToggling(true);const r=await api.patch(`/machines/${machine.id}/active`,{is_active:!machine.is_active});setMachine(r.data)}catch(e:any){setError(errorDetail(e,e.message))}finally{setToggling(false)}};

 const readingColumns=useMemo<GridColDef[]>(()=>[
  {field:'reading_date',headerName:'Tarih',width:150,valueGetter:dateTime},
  {field:'reading_hours',headerName:'Saat',width:110,align:'right',headerAlign:'right',renderCell:p=><b>{p.value}</b>},
  {field:'reading_type',headerName:'Tür',width:140,renderCell:p=><Chip size="small" variant="outlined" color={p.value==='normal'?'default':'warning'} label={READING_TYPE_LABELS[p.value]||p.value}/>},
  {field:'source',headerName:'Kaynak',width:150,renderCell:p=>p.row.source==='work_order'&&p.row.work_order_id
   ?<Link component={RouterLink} to={`/is-emirleri/${p.row.work_order_id}`}>{p.row.work_order_no||`İş Emri #${p.row.work_order_id}`}</Link>
   :<>{READING_SOURCE_LABELS[p.value]||p.value}</>},
  {field:'reason',headerName:'Açıklama',flex:1,minWidth:160,valueGetter:v=>v||'-'},
  {field:'created_by_name',headerName:'Kaydeden',width:150,valueGetter:(_v,row)=>row.created_by_name||row.created_by_username||'-'},
 ],[]);

 const ownershipColumns=useMemo<GridColDef[]>(()=>[
  {field:'transfer_date',headerName:'Tarih',width:130,valueGetter:dateOnly},
  {field:'from_customer_id',headerName:'Eski Sahip',width:180,renderCell:p=>p.value?<Link component={RouterLink} to={`/musteriler/${p.value}`}>{p.row.from_customer_name||`#${p.value}`}</Link>:<>—</>},
  {field:'to_customer_id',headerName:'Yeni Sahip',width:180,renderCell:p=><Link component={RouterLink} to={`/musteriler/${p.value}`}>{p.row.to_customer_name||`#${p.value}`}</Link>},
  {field:'reason',headerName:'Açıklama',flex:1,minWidth:160,valueGetter:v=>v||'-'},
  {field:'created_by_name',headerName:'Kaydeden',width:150,valueGetter:(_v,row)=>row.created_by_name||row.created_by_username||'Sistem'},
 ],[]);

 const orderColumns=useMemo<GridColDef[]>(()=>[
  {field:'work_order_no',headerName:'İş Emri No',width:150,renderCell:p=><Link component={RouterLink} to={`/is-emirleri/${p.row.id}`} onClick={e=>e.stopPropagation()}>{p.value}</Link>},
  {field:'status',headerName:'Durum',width:140,renderCell:p=><Chip size="small" color={WO_STATUS_COLORS[p.value]||'default'} label={WO_STATUS_LABELS[p.value]||p.value}/>},
  {field:'priority',headerName:'Öncelik',width:110,valueGetter:v=>v||'-'},
  {field:'opened_at',headerName:'Açılış',width:120,valueGetter:dateOnly},
  {field:'complaint',headerName:'Şikayet',flex:1,minWidth:180,valueGetter:v=>v||'-'},
  {field:'labor_total',headerName:'İşçilik',width:120,align:'right',headerAlign:'right',valueGetter:v=>money(v)},
  {field:'parts_total',headerName:'Parça',width:120,align:'right',headerAlign:'right',valueGetter:v=>money(v)},
  {field:'grand_total',headerName:'Genel Toplam',width:140,align:'right',headerAlign:'right',renderCell:p=><b>{money(p.value)}</b>},
 ],[]);

 if(loading)return <Box minHeight="40dvh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>;
 if(error&&!machine)return <Stack spacing={2}><Alert severity="error">{error}</Alert><Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/makineler')} sx={touch}>Makinelere Dön</Button></Stack>;
 if(!machine)return null;

 return <Stack spacing={2}>
  {/* Başlık kartı — Müşteri 360 ile aynı kalıp (EntityDetail).
      BİLİNÇLİ OLARAK yapışkan DEĞİL: `position:sticky` başlık, altındaki sekme
      şeridinin üzerine binip tıklamayı yutuyor (e2e'de yakalandı). Sekmeler
      zaten uzun kaydırmayı ortadan kaldırdığı için yapışkanlığa gerek yok. */}
  <Card sx={{border:'1px solid',borderColor:'divider'}}><CardContent>
   <Stack direction={{xs:'column',md:'row'}} justifyContent="space-between" alignItems={{md:'center'}} gap={1.5}>
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
     <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/makineler')} sx={touch}>Makineler</Button>
     <Typography variant="h4" fontWeight={900}>{machine.brand} {machine.model}</Typography>
     <Chip size="small" color={machine.is_active?'success':'default'} label={machine.is_active?'Aktif':'Pasif'}/>
     <Chip size="small" variant="outlined" label={MACHINE_STATUS_LABELS[machine.status]||machine.status}/>
    </Stack>
    {canWrite&&<Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
     <Button variant="outlined" startIcon={<EditIcon/>} onClick={()=>setOpen(true)} sx={touch}>Düzenle</Button>
     <Button variant="outlined" color={machine.is_active?'warning':'success'} startIcon={machine.is_active?<BlockIcon/>:<CheckCircleIcon/>} disabled={toggling} onClick={toggleActive} sx={touch}>{machine.is_active?'Pasife Al':'Aktif Et'}</Button>
    </Stack>}
   </Stack>
   <Typography variant="body2" color="text.secondary" mt={1}>
    {machine.serial_number?`Seri ${machine.serial_number}`:'Seri no yok'} · {machine.working_hours??'-'} saat · {machine.customer_id?<Link component={RouterLink} to={`/musteriler/${machine.customer_id}`}>{machine.customer_name||`#${machine.customer_id}`}</Link>:'Sahip atanmadı'}
   </Typography>
  </CardContent></Card>

  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}

  <Card>
   {/* Sekme sayacı YALNIZ kesin bilindiğinde yazılır. Sayaç ucu toplam
       döndürmediği için orada sayı yok — yüklenen sayfanın uzunluğunu toplam
       gibi göstermek yanıltıcı olurdu. */}
   <Tabs value={tab} onChange={(_,v)=>setTab(v)} variant="scrollable" scrollButtons="auto"
    aria-label="Makine 360 sekmeleri"
    sx={{'& .MuiTab-root':{...touch,'&.Mui-focusVisible':{outline:'2px solid',outlineColor:'primary.main',outlineOffset:-2,borderRadius:1}}}}>
    <Tab icon={<BadgeIcon/>} iconPosition="start" label={MACHINE_TAB_LABELS.identity}/>
    <Tab icon={<SpeedIcon/>} iconPosition="start" label={MACHINE_TAB_LABELS.readings}/>
    <Tab icon={<SwapHorizIcon/>} iconPosition="start" label={countLabel(MACHINE_TAB_LABELS.ownership,ownershipCount)}/>
    <Tab icon={<BuildIcon/>} iconPosition="start" label={countLabel(MACHINE_TAB_LABELS.service,orderTotal)}/>
   </Tabs>
   <Divider/>
   <CardContent>

    {tab===0&&<Stack spacing={2}>
     <Grid container spacing={2}>
      <Meta label="Marka" value={machine.brand}/>
      <Meta label="Model" value={machine.model}/>
      <Meta label="Üretici" value={machine.manufacturer}/>
      <Meta label="Varyant" value={machine.variant}/>
      <Meta label="Seri No" value={machine.serial_number}/>
      <Meta label="Şasi No" value={machine.chassis_number}/>
      <Meta label="Plaka" value={machine.registration_number}/>
      <Meta label="Motor No" value={machine.engine_number}/>
      <Meta label="Model Yılı" value={machine.model_year}/>
      <Meta label="Çalışma Saati" value={machine.working_hours}/>
      <Meta label="Müşteri" value={machine.customer_id?<Link component={RouterLink} to={`/musteriler/${machine.customer_id}`}>{machine.customer_name||`#${machine.customer_id}`}</Link>:'Atanmadı'}/>
      <Meta label="Kayıt Tarihi" value={dateOnly(machine.created_at)}/>
     </Grid>
     {machine.notes&&<Paper variant="outlined" sx={{p:2}}><Typography variant="caption" color="text.secondary">Notlar</Typography><Typography>{machine.notes}</Typography></Paper>}
    </Stack>}

    {/* Sayaç okumaları — makinenin Çalışma Saati alanı son okumanın yansımasıdır.
        Kart alanları masaüstü kolonlarının ÜST kümesidir: 6 kolonun 6'sı da
        kartta var ve iş emri bağlantısı kartta da tıklanabilir. */}
    {tab===1&&<Stack spacing={1.5}>
     {canWrite&&<Button variant="contained" startIcon={<AddIcon/>} onClick={()=>setReadingOpen(true)} sx={{...touch,alignSelf:'flex-start'}}>Yeni Okuma</Button>}
     <TabPanel state={readingState} emptyText="Henüz sayaç okuması kaydedilmedi." onPage={setReadingPage}>
      {rows=><ResponsiveTable rows={rows} columns={readingColumns}
       cardTitle={r=>`${r.reading_hours} saat`} cardSubtitle={r=>dateTime(r.reading_date)}
       cardFields={[
        {label:'Tür',value:r=><Chip size="small" variant="outlined" color={r.reading_type==='normal'?'default':'warning'} label={READING_TYPE_LABELS[r.reading_type]||r.reading_type}/>},
        {label:'Kaynak',value:r=>r.source==='work_order'&&r.work_order_id
         ?<Link component={RouterLink} to={`/is-emirleri/${r.work_order_id}`}>{r.work_order_no||`İş Emri #${r.work_order_id}`}</Link>
         :<>{READING_SOURCE_LABELS[r.source]||r.source}</>},
        {label:'Açıklama',value:r=><Box component="span" sx={ellipsis}>{r.reason||'-'}</Box>},
        {label:'Kaydeden',value:r=><>{r.created_by_name||r.created_by_username||'-'}</>},
       ]}/>}
     </TabPanel>
    </Stack>}

    {/* Sahiplik tarihçesi (salt-okuma) + devir. Kart başlığı düz metin olmak
        zorunda olduğu için kimlik alanı TARİH; iki müşteri bağlantısı da
        alanlara taşındı, böylece ikisi de kartta tıklanabilir kalıyor. */}
    {tab===2&&<Stack spacing={1.5}>
     {canWrite&&<Button variant="outlined" startIcon={<SwapHorizIcon/>} onClick={()=>setTransferOpen(true)} sx={{...touch,alignSelf:'flex-start'}}>Sahiplik Devret</Button>}
     <TabPanel state={ownershipState} emptyText="Sahiplik kaydı yok.">
      {rows=><ResponsiveTable rows={rows} columns={ownershipColumns}
       cardTitle={h=>dateOnly(h.transfer_date)} cardSubtitle={h=>h.reason||'Açıklama yok'}
       cardFields={[
        {label:'Eski Sahip',value:h=>h.from_customer_id
         ?<Link component={RouterLink} to={`/musteriler/${h.from_customer_id}`}>{h.from_customer_name||`#${h.from_customer_id}`}</Link>
         :<>—</>},
        {label:'Yeni Sahip',value:h=><Link component={RouterLink} to={`/musteriler/${h.to_customer_id}`}>{h.to_customer_name||`#${h.to_customer_id}`}</Link>},
        {label:'Açıklama',value:h=><Box component="span" sx={ellipsis}>{h.reason||'-'}</Box>},
        {label:'Kaydeden',value:h=><>{h.created_by_name||h.created_by_username||'Sistem'}</>},
       ]}/>}
     </TabPanel>
    </Stack>}

    {/* Servis kartı, masaüstü tablosunun 8 kolonunun tamamını taşır: İş Emri No
        başlıkta, Durum alt başlıkta, kalan 6'sı alanlarda. Satır zaten iş
        emrini açtığı için kartta ayrıca bağlantı yok (iç içe buton olurdu). */}
    {tab===3&&<TabPanel state={orderState} emptyText="Bu makine için henüz iş emri kaydı yok." onPage={setOrderPage}>
     {rows=><ResponsiveTable rows={rows} columns={orderColumns} onRowClick={(row:any)=>nav(`/is-emirleri/${row.id}`)}
      cardTitle={w=>w.work_order_no} cardSubtitle={w=>WO_STATUS_LABELS[w.status]||w.status}
      cardFields={[
       {label:'Öncelik',value:w=><>{w.priority||'-'}</>},
       {label:'Açılış',value:w=><>{dateOnly(w.opened_at)}</>},
       {label:'Şikayet',value:w=><Box component="span" sx={ellipsis}>{w.complaint||'-'}</Box>},
       {label:'İşçilik',value:w=><>{money(w.labor_total)}</>},
       {label:'Parça',value:w=><>{money(w.parts_total)}</>},
       {label:'Genel Toplam',value:w=><>{money(w.grand_total)}</>},
      ]}/>}
    </TabPanel>}

   </CardContent>
  </Card>

  <MachineDialog open={open} id={machine.id} onClose={()=>setOpen(false)} onSaved={()=>{load();reloadOwnership()}}/>
  <HourReadingDialog open={readingOpen} machineId={machine.id} onClose={()=>setReadingOpen(false)} onSaved={()=>{load();setReadingPage(1);reloadReadings()}}/>
  <OwnershipTransferDialog open={transferOpen} machineId={machine.id} currentCustomerId={machine.customer_id??null} onClose={()=>setTransferOpen(false)} onSaved={()=>{load();reloadOwnership();reloadOrders()}}/>
 </Stack>
}
