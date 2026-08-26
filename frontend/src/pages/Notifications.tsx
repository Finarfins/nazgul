import {useCallback,useEffect,useMemo,useState} from 'react';
import {
 Alert,Box,Button,Chip,Dialog,DialogActions,DialogContent,
 DialogContentText,DialogTitle,Paper,Stack,Tab,Tabs,Tooltip,Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import BlockIcon from '@mui/icons-material/Block';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import SendIcon from '@mui/icons-material/Send';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';

type OutboxRow={
 id:number;company_id:number;type:string;channel:string;recipient:string;template:string;
 status:string;message_class:string|null;content_hash:string|null;dispatch_armed:boolean;
 disarmed_at:string|null;rule_id:number|null;approved_by:number|null;approved_at:string|null;
 created_by:number|null;attempt_count:number;next_attempt_at:string|null;
 external_id:string|null;last_error:string|null;consent_decision:string|null;
 consent_decision_reason:string|null;created_at:string;updated_at:string;display_state:string;
};
type OutboxPage={items:OutboxRow[];skip:number;limit:number};
type Counters={pending:number;disarmed:number;awaiting_approval:number;sent:number;simulated:number;failed:number;stopped:number};
type Preview={id:number;channel:string;recipient:string;template:string;status:string;
 message_class:string|null;content_hash:string|null;body:string|null;subject:string|null;can_approve:boolean};

/**
 * Sekmeler tasarım §2.9'un görünüm ayrımını birebir yansıtır: "Bekleyenler"
 * YALNIZ gönderilecek (silahlı) satırları içerir; durdurulanlar ayrı sekmededir.
 * İkisini tek listede göstermek, hiçbiri gönderilmeyecek satırları "kuyrukta
 * bekliyor" diye sunmak olurdu.
 */
const TABS=[
 {value:'pending',label:'Bekleyenler'},
 {value:'awaiting',label:'Onay Bekleyenler'},
 {value:'disarmed',label:'Durdurulanlar'},
 {value:'sent',label:'Gönderilenler'},
 {value:'simulated',label:'Simüle'},
 {value:'problem',label:'Sorunlular'},
] as const;
type TabValue=typeof TABS[number]['value'];

const formatMoment=(value:string|null)=>{
 if(!value)return '—';
 const parsed=new Date(value);
 return Number.isNaN(parsed.getTime())?value:parsed.toLocaleString('tr-TR');
};

const CLASS_LABEL:Record<string,string>={
 SERVICE_TRANSACTIONAL:'İşlemsel',COMMERCIAL:'Ticari',
};

export default function Notifications(){
 const {can}=useAuth();
 const canApprove=can('notifications_approve');
 const canDispatch=can('notifications_dispatch');

 const [tab,setTab]=useState<TabValue>('pending');
 const [rows,setRows]=useState<OutboxRow[]>([]);
 const [counters,setCounters]=useState<Counters|null>(null);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');
 const [preview,setPreview]=useState<Preview|null>(null);
 const [busy,setBusy]=useState(false);

 const load=useCallback(async()=>{
  setLoading(true);setError('');
  try{
   const [page,count]=await Promise.all([
    api.get<OutboxPage>('/notifications/outbox',{params:{tab,limit:50}}),
    api.get<Counters>('/notifications/counters'),
   ]);
   setRows(page.data.items||[]);setCounters(count.data);
  }catch(err){setError(errorDetail(err,'Bildirim kuyruğu yüklenemedi'));}
  finally{setLoading(false);}
 },[tab]);

 useEffect(()=>{void load();},[load]);

 const openPreview=async(row:OutboxRow)=>{
  setError('');
  try{
   const {data}=await api.get<Preview>(`/notifications/${row.id}/preview`);
   setPreview(data);
  }catch(err){setError(errorDetail(err,'Önizleme alınamadı'));}
 };

 /**
  * Onay, kullanıcının GÖRDÜĞÜ içerik özetiyle gönderilir. İçerik önizleme ile
  * tıklama arasında değiştiyse sunucu 409 döner ve onay reddedilir — sessizce
  * yeni içerik onaylanmaz.
  */
 const approve=async()=>{
  if(!preview?.content_hash)return;
  setBusy(true);setError('');
  try{
   await api.post(`/notifications/${preview.id}/approve`,{seen_hash:preview.content_hash});
   setNotice('Gönderim onaylandı. Gönderimi ayrıca başlatmanız gerekir.');
   setPreview(null);await load();
  }catch(err){setError(errorDetail(err,'Onay reddedildi'));}
  finally{setBusy(false);}
 };

 const dispatch=async(row:OutboxRow)=>{
  setBusy(true);setError('');setNotice('');
  try{
   const {data}=await api.post<{status:string;message:string|null}>(`/notifications/${row.id}/dispatch`,{});
   setNotice(`Gönderim sonucu: ${data.status}${data.message?` — ${data.message}`:''}`);
   await load();
  }catch(err){setError(errorDetail(err,'Gönderim başlatılamadı'));}
  finally{setBusy(false);}
 };

 const cancel=async(row:OutboxRow)=>{
  setBusy(true);setError('');
  try{
   await api.post(`/notifications/${row.id}/cancel`,{});
   setNotice('Bildirim iptal edildi.');await load();
  }catch(err){setError(errorDetail(err,'İptal edilemedi'));}
  finally{setBusy(false);}
 };

 const summary=useMemo(()=>{
  if(!counters)return null;
  return [
   {label:'Gönderilecek',value:counters.pending},
   {label:'Onay bekleyen',value:counters.awaiting_approval},
   {label:'Durdurulan',value:counters.disarmed},
   {label:'Gönderilen',value:counters.sent},
   {label:'Simüle',value:counters.simulated},
   {label:'Sorunlu',value:counters.failed+counters.stopped},
  ];
 },[counters]);
 const columns:GridColDef[]=[
  {field:'id',headerName:'#',width:90},
  {field:'type',headerName:'Tür',minWidth:160,flex:1},
  {field:'channel',headerName:'Kanal',width:100},
  {field:'recipient',headerName:'Alıcı',width:145},
  {field:'message_class',headerName:'Sınıf',width:125,valueFormatter:value=>value?CLASS_LABEL[value]??value:'—'},
  {field:'display_state',headerName:'Durum',width:220,renderCell:({row})=><Stack justifyContent="center" height="100%"><Stack direction="row" spacing={1} alignItems="center"><Chip size="small" color={row.status==='REJECTED'||row.status==='CANCELLED'?'default':row.status==='FAILED'?'error':row.status==='SENT'||row.status==='DELIVERED'?'success':'default'} label={row.display_state}/>{row.disarmed_at&&<Tooltip title={`Durduruldu: ${formatMoment(row.disarmed_at)}`}><BlockIcon fontSize="small" color="disabled"/></Tooltip>}</Stack>{row.last_error&&<Typography variant="caption" color="text.secondary">{row.last_error}</Typography>}</Stack>},
  {field:'updated_at',headerName:'Güncelleme',width:175,valueFormatter:value=>formatMoment(value)},
 ];

 return <Box sx={{p:2}}>
  <Typography variant="h5" sx={{mb:1}}>Bildirimler</Typography>
  <Alert severity="info" sx={{mb:2}}>
   Hiçbir bildirim kendiliğinden gönderilmez. Her gönderim ayrı bir kullanıcının
   açık onayını gerektirir; onaylayan ile oluşturan aynı kişi olamaz.
  </Alert>

  {summary&&<Stack direction="row" spacing={1} sx={{mb:2,flexWrap:'wrap',gap:1}}>
   {summary.map(item=><Chip key={item.label} label={`${item.label}: ${item.value}`}/>)}
  </Stack>}

  {error&&<Alert severity="error" sx={{mb:2}} onClose={()=>setError('')}>{error}</Alert>}
  {notice&&<Alert severity="success" sx={{mb:2}} onClose={()=>setNotice('')}>{notice}</Alert>}

  <Paper>
   <Tabs value={tab} onChange={(_,value)=>setTab(value as TabValue)} variant="scrollable">
    {TABS.map(item=><Tab key={item.value} value={item.value} label={item.label}/>)}
   </Tabs>
    <Box data-testid="notifications-data-surface" sx={{p:1.5}}>
     <ResponsiveTable
      rows={rows}
      columns={columns}
      loading={loading}
      cardTitle={row=>String(row.id)}
      cardSubtitle={row=>`${row.type} · ${row.channel}`}
      cardFields={[
       {label:'Alıcı',value:row=>row.recipient},
       {label:'Sınıf',value:row=>row.message_class?CLASS_LABEL[row.message_class]??row.message_class:'—'},
       {label:'Durum',value:row=>row.display_state},
       {label:'Güncelleme',value:row=>formatMoment(row.updated_at)},
       {label:'Hata',value:row=>row.last_error||'—'},
      ]}
      cardActions={[
       {label:'İncele / Onayla',icon:<CheckCircleOutlineIcon/>,hidden:row=>row.status!=='AWAITING_APPROVAL'||!canApprove,onClick:row=>void openPreview(row)},
       {label:'İptal',color:'inherit',hidden:row=>row.status!=='AWAITING_APPROVAL'||!canApprove,disabled:()=>busy,onClick:row=>void cancel(row)},
       {label:'Gönder',icon:<SendIcon/>,hidden:row=>row.status==='AWAITING_APPROVAL'||!row.dispatch_armed||!canDispatch||!['PENDING','RETRY_SCHEDULED'].includes(row.status),disabled:()=>busy,onClick:row=>void dispatch(row)},
      ]}
      cardActionsOnDesktop
      getRowId={row=>row.id}
      desktopRowHeight={68}
     />
    </Box>
   </Paper>

  <Dialog open={Boolean(preview)} onClose={()=>setPreview(null)} maxWidth="sm" fullWidth>
   <DialogTitle>Gönderim onayı</DialogTitle>
   <DialogContent>
    <DialogContentText sx={{mb:2}}>
     Aşağıdaki metin müşteriye <strong>olduğu gibi</strong> gönderilecektir.
     Onayınız yalnız bu içerik için geçerlidir; içerik değişirse onay geçersiz olur.
    </DialogContentText>
    <Paper variant="outlined" sx={{p:2,mb:2,whiteSpace:'pre-wrap'}}>
     {preview?.body||'—'}
    </Paper>
    <Stack spacing={0.5}>
     <Typography variant="body2">Alıcı: {preview?.recipient}</Typography>
     <Typography variant="body2">Kanal: {preview?.channel}</Typography>
     <Typography variant="body2">
      Sınıf: {preview?.message_class?CLASS_LABEL[preview.message_class]??preview.message_class:'—'}
     </Typography>
    </Stack>
    {preview&&!preview.can_approve&&<Alert severity="warning" sx={{mt:2}}>
     Bu gönderimi siz oluşturdunuz. Kendi oluşturduğunuz gönderimi
     onaylayamazsınız; başka bir yetkili onaylamalıdır.
    </Alert>}
   </DialogContent>
   <DialogActions>
    <Button onClick={()=>setPreview(null)}>Vazgeç</Button>
    <Button variant="contained" disabled={busy||!preview?.can_approve}
     onClick={()=>void approve()}>Onayla</Button>
   </DialogActions>
  </Dialog>
 </Box>;
}
