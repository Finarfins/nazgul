import {useEffect,useState} from 'react';
import {useNavigate,useParams} from 'react-router-dom';
import {
 Alert,Box,Button,Chip,CircularProgress,Grid,Paper,Stack,Tooltip,Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import SendIcon from '@mui/icons-material/Send';
import {api,errorDetail,money} from '../api';
import ResponsiveTable from '../components/ResponsiveTable';

// Fatura kalemleri: 4 sütun. 390px'te ResponsiveTable kart düzenine geçer.
const itemColumns:GridColDef[]=[
 {field:'description',headerName:'Açıklama',flex:1,minWidth:180},
 {field:'quantity',headerName:'Miktar',width:110,align:'right',headerAlign:'right'},
 {field:'unit_price',headerName:'Birim Fiyat',width:130,align:'right',headerAlign:'right',
  valueGetter:(_v,row:any)=>money(row.unit_price)},
 {field:'line_total',headerName:'Toplam',width:130,align:'right',headerAlign:'right',
  valueGetter:(_v,row:any)=>money(row.line_total)},
];

export type EInvoiceStatus='NONE'|'PENDING'|'SENT'|'ACCEPTED'|'REJECTED'|'ERROR';
export type EInvoiceState={
 einvoice_channel:string|null;
 einvoice_status:EInvoiceStatus;
 einvoice_uuid:string|null;
 einvoice_external_id:string|null;
 einvoice_last_error:string|null;
 einvoice_submitted_at:string|null;
 einvoice_updated_at:string|null;
};

const STATUS_VIEW:Record<EInvoiceStatus,{label:string;color:'default'|'info'|'success'|'error'}>={
 NONE:{label:'e-Fatura: yapılandırılmamış',color:'default'},
 PENDING:{label:'e-Fatura: bekliyor',color:'info'},
 SENT:{label:'e-Fatura: gönderildi',color:'info'},
 ACCEPTED:{label:'e-Fatura: kabul edildi',color:'success'},
 REJECTED:{label:'e-Fatura: reddedildi',color:'error'},
 ERROR:{label:'e-Fatura: hata',color:'error'},
};
const NOT_CONFIGURED='e-Fatura sağlayıcısı yapılandırılmamış';

export function EInvoiceStatusPanel({invoiceId}:{invoiceId:number}){
 const [state,setState]=useState<EInvoiceState|null>(null);
 const [loading,setLoading]=useState(true);const [submitting,setSubmitting]=useState(false);
 const [error,setError]=useState('');const [notice,setNotice]=useState('');const [providerMissing,setProviderMissing]=useState(false);
 const load=()=>{setLoading(true);setError('');api.get(`/invoices/${invoiceId}/einvoice/status`).then(response=>setState(response.data)).catch(err=>setError(errorDetail(err,'e-Fatura durumu yüklenemedi.'))).finally(()=>setLoading(false))};
 useEffect(load,[invoiceId]);
 const submit=async()=>{setSubmitting(true);setError('');setNotice('');
  try{
   const response=await api.post(`/invoices/${invoiceId}/einvoice/submit`);
   setState(current=>({...current,...response.data} as EInvoiceState));
   const message=String(response.data?.message||'e-Fatura gönderim isteği alındı.');
   setNotice(message);
   if(message.toLocaleLowerCase('tr-TR').includes('yapılandırılmamış'))setProviderMissing(true);
  }catch(err){setError(errorDetail(err,'e-Fatura gönderilemedi.'))}
  finally{setSubmitting(false)}
 };
 if(loading)return <Paper variant="outlined" sx={{p:2}}><Stack direction="row" spacing={1.5} alignItems="center"><CircularProgress size={22}/><Typography color="text.secondary">e-Fatura durumu yükleniyor…</Typography></Stack></Paper>;
 const status=state?.einvoice_status||'NONE';const view=STATUS_VIEW[status]||STATUS_VIEW.NONE;
 const locked=providerMissing||status==='PENDING'||status==='SENT'||status==='ACCEPTED';
 const buttonLabel=providerMissing?'Sağlayıcı yok':submitting?'Gönderiliyor…':'e-Fatura Gönder';
 const chip=<Chip label={view.label} color={view.color} variant={status==='NONE'?'outlined':'filled'}/>;
 return <Paper variant="outlined" sx={{p:2}}>
  <Stack spacing={1.5}>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1}>
    <Box><Typography variant="h6" fontWeight={800}>e-Fatura</Typography><Typography variant="body2" color="text.secondary">Dijital gönderim kanalı ve sağlayıcı durumu</Typography></Box>
    {state?.einvoice_last_error?<Tooltip title={state.einvoice_last_error} arrow>{chip}</Tooltip>:chip}
   </Stack>
   {notice&&<Alert severity="info" onClose={()=>setNotice('')}>{notice}</Alert>}
   {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
   {(status==='REJECTED'||status==='ERROR')&&state?.einvoice_last_error&&<Alert severity="error">{state.einvoice_last_error}</Alert>}
   {providerMissing&&<Typography variant="body2" color="text.secondary">Altyapı hazır; gönderim için bir e-Fatura sağlayıcısının bağlanması gerekiyor.</Typography>}
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1}>
    <Typography variant="caption" color="text.secondary">
     {state?.einvoice_external_id?`Harici kayıt: ${state.einvoice_external_id}`:state?.einvoice_uuid?`UUID: ${state.einvoice_uuid}`:'Henüz harici kayıt oluşmadı.'}
    </Typography>
    <Button variant="contained" startIcon={<SendIcon/>} disabled={locked||submitting} onClick={submit}
      color={providerMissing?'inherit':'primary'} sx={{minHeight:{xs:44,md:40}}}>{buttonLabel}</Button>
   </Stack>
  </Stack>
 </Paper>;
}

export default function InvoiceDetail(){
 const {id}=useParams();const navigate=useNavigate();const invoiceId=Number(id);
 const [invoice,setInvoice]=useState<any|null>(null);const [loading,setLoading]=useState(true);const [error,setError]=useState('');
 useEffect(()=>{if(!Number.isInteger(invoiceId)||invoiceId<=0){setError('Geçersiz fatura numarası.');setLoading(false);return}
  api.get(`/invoices/${invoiceId}`).then(response=>setInvoice(response.data)).catch(err=>setError(errorDetail(err,'Fatura yüklenemedi.'))).finally(()=>setLoading(false));
 },[invoiceId]);
 if(loading)return <Box minHeight="40dvh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>;
 if(error)return <Stack spacing={2}><Alert severity="error">{error}</Alert><Button startIcon={<ArrowBackIcon/>} onClick={()=>navigate(-1)}>Geri Dön</Button></Stack>;
 if(!invoice)return null;
 return <Stack spacing={2}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1}>
   <Box><Button startIcon={<ArrowBackIcon/>} onClick={()=>navigate(-1)} sx={{minHeight:{xs:44,md:40}}}>Geri</Button><Typography variant="h4" fontWeight={900}>{invoice.invoice_number}</Typography></Box>
   <Chip label={invoice.status} color={invoice.status==='ISSUED'?'success':invoice.status==='CANCELLED'?'error':'default'}/>
  </Stack>
  <Paper sx={{p:2}}><Typography variant="h6" fontWeight={800} mb={1}>Fatura Bilgileri</Typography><Grid container spacing={2}>
   <Grid size={{xs:12,sm:6,md:3}}><Typography variant="caption" color="text.secondary">Müşteri</Typography><Typography fontWeight={700}>{invoice.customer?.name||'-'}</Typography></Grid>
   <Grid size={{xs:12,sm:6,md:3}}><Typography variant="caption" color="text.secondary">Para Birimi</Typography><Typography fontWeight={700}>{invoice.currency}</Typography></Grid>
   <Grid size={{xs:12,sm:6,md:3}}><Typography variant="caption" color="text.secondary">Genel Toplam</Typography><Typography fontWeight={700}>{money(invoice.totals?.grand_total)}</Typography></Grid>
   <Grid size={{xs:12,sm:6,md:3}}><Typography variant="caption" color="text.secondary">Oluşturulma</Typography><Typography fontWeight={700}>{invoice.created_at?String(invoice.created_at).slice(0,10):'-'}</Typography></Grid>
  </Grid></Paper>
  <EInvoiceStatusPanel invoiceId={invoiceId}/>
  <Paper sx={{p:2}}><Typography variant="h6" fontWeight={800} mb={1}>Kalemler</Typography>
   <Box data-testid="invoice-detail-data-surface">
    <ResponsiveTable
      rows={invoice.items||[]}
      columns={itemColumns}
      hideFooter
      cardTitle={(item:any)=>item.description}
      cardSubtitle={(item:any)=>`${item.quantity} × ${money(item.unit_price)}`}
      cardFields={[
       {label:'Miktar',value:(item:any)=>item.quantity},
       {label:'Birim Fiyat',value:(item:any)=>money(item.unit_price)},
       {label:'Toplam',value:(item:any)=><b>{money(item.line_total)}</b>},
      ]}
    />
   </Box>
  </Paper>
 </Stack>;
}
