import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Alert,Box,Chip,MenuItem,Pagination,Paper,Stack,TextField,Typography} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import {api,errorDetail,money} from '../api';
import ResponsiveTable from '../components/ResponsiveTable';

type EInvoiceStatus='NONE'|'PENDING'|'SENT'|'ACCEPTED'|'REJECTED'|'ERROR';
type InvoiceRow={
 id:number;
 invoice_number:string;
 status:string;
 currency:string;
 customer?:{name?:string};
 totals?:{grand_total?:string};
 created_at?:string;
 einvoice_status?:EInvoiceStatus|null;
};

const INVOICE_STATUS:Record<string,{label:string;color:'success'|'error'|'default'}>={
 ISSUED:{label:'Kesildi',color:'success'},
 CANCELLED:{label:'İptal',color:'error'},
};
const EINVOICE_STATUS:Record<EInvoiceStatus,{label:string;color:'default'|'info'|'success'|'error'}>={
 NONE:{label:'Hazır değil',color:'default'},
 PENDING:{label:'Bekliyor',color:'info'},
 SENT:{label:'Gönderildi',color:'info'},
 ACCEPTED:{label:'Kabul edildi',color:'success'},
 REJECTED:{label:'Reddedildi',color:'error'},
 ERROR:{label:'Hata',color:'error'},
};

const date=(value?:string)=>value?String(value).slice(0,10):'-';
const invoiceStatusChip=(value:string)=>{
 const view=INVOICE_STATUS[value]||{label:value||'-',color:'default' as const};
 return <Chip size="small" label={view.label} color={view.color}/>;
};
const einvoiceStatusChip=(value?:EInvoiceStatus|null)=>{
 if(value===undefined)return <Chip size="small" label="Yükleniyor…" variant="outlined"/>;
 if(value===null)return <Chip size="small" label="Durum alınamadı" color="error" variant="outlined"/>;
 const view=EINVOICE_STATUS[value]||EINVOICE_STATUS.NONE;
 return <Chip size="small" label={view.label} color={view.color} variant={value==='NONE'?'outlined':'filled'}/>;
};

export default function Invoices(){
 const navigate=useNavigate();const requestSequence=useRef(0);
 const [rows,setRows]=useState<InvoiceRow[]>([]);const [loading,setLoading]=useState(true);const [error,setError]=useState('');
 const [query,setQuery]=useState('');const [status,setStatus]=useState('');const [page,setPage]=useState(1);const [pages,setPages]=useState(1);const [total,setTotal]=useState(0);
 const load=useCallback(async()=>{
  const sequence=++requestSequence.current;setLoading(true);setError('');
  try{
   const response=await api.get('/invoices',{params:{q:query,status:status||undefined,page,page_size:25,sort:'created_at',direction:'desc'}});
   if(sequence!==requestSequence.current)return;
   const items:InvoiceRow[]=response.data?.items||[];
   setRows(items);setPages(Math.max(1,response.data?.pages||1));setTotal(response.data?.total||0);
   const enriched=await Promise.all(items.map(async item=>{
    try{
     const state=await api.get(`/invoices/${item.id}/einvoice/status`);
     return {...item,einvoice_status:state.data?.einvoice_status||'NONE'} as InvoiceRow;
    }catch{return {...item,einvoice_status:null} as InvoiceRow}
   }));
   if(sequence===requestSequence.current)setRows(enriched);
  }catch(err){
   if(sequence===requestSequence.current){setRows([]);setError(errorDetail(err,'Faturalar yüklenemedi.'))}
  }finally{if(sequence===requestSequence.current)setLoading(false)}
 },[page,query,status]);
 useEffect(()=>{void load()},[load]);
 const columns=useMemo<GridColDef<InvoiceRow>[]>(()=>[
  {field:'invoice_number',headerName:'Fatura No',minWidth:180,flex:1},
  {field:'created_at',headerName:'Tarih',width:120,valueGetter:value=>date(value)},
  {field:'customer',headerName:'Müşteri',minWidth:200,flex:1,renderCell:params=>params.row.customer?.name||'-'},
  {field:'total',headerName:'Tutar',width:150,align:'right',headerAlign:'right',valueGetter:(_value,row)=>row.totals?.grand_total,renderCell:params=><b>{money(params.value)}</b>},
  {field:'status',headerName:'Durum',width:120,renderCell:params=>invoiceStatusChip(params.value)},
  {field:'einvoice_status',headerName:'e-Fatura',width:145,renderCell:params=>einvoiceStatusChip(params.value)},
 ],[]);
 return <Stack spacing={2}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={2}>
   <Box><Typography variant="h3">Faturalar</Typography><Typography color="text.secondary" mt={.5}>Kesilen faturaları ve dijital gönderim durumlarını takip edin.</Typography></Box>
   <Paper variant="outlined" sx={{px:2,py:1,borderRadius:2}}><Typography variant="caption" color="text.secondary">TOPLAM FATURA</Typography><Typography variant="h5" fontWeight={900}>{total}</Typography></Paper>
  </Stack>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  <Paper variant="outlined" sx={{p:1.5,borderRadius:3}}>
   <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
    <TextField size="small" label="Fatura no veya müşteri ara" value={query} onChange={event=>{setQuery(event.target.value);setPage(1)}} sx={{flex:1,minWidth:220}}/>
    <TextField select size="small" label="Durum" value={status} onChange={event=>{setStatus(event.target.value);setPage(1)}} sx={{minWidth:160}}>
     <MenuItem value="">Tümü</MenuItem><MenuItem value="ISSUED">Kesildi</MenuItem><MenuItem value="CANCELLED">İptal</MenuItem>
    </TextField>
   </Stack>
  </Paper>
  {!loading&&!error&&rows.length===0&&<Box><Typography fontWeight={800}>Henüz fatura bulunmuyor.</Typography><Typography color="text.secondary">Tamamlanan bir iş emrini faturalandırdığınızda burada görünecek.</Typography></Box>}
  <ResponsiveTable rows={rows} columns={columns} loading={loading} onRowClick={row=>navigate(`/faturalar/${row.id}`)}
   cardTitle={row=>row.invoice_number} cardSubtitle={row=>`${date(row.created_at)} · ${row.customer?.name||'-'}`}
   cardFields={[{label:'Tutar',value:row=>money(row.totals?.grand_total)},{label:'Durum',value:row=>invoiceStatusChip(row.status)},{label:'e-Fatura',value:row=>einvoiceStatusChip(row.einvoice_status)}]}/>
  {pages>1&&<Stack alignItems="center"><Pagination count={pages} page={page} onChange={(_event,value)=>setPage(value)} color="primary"/></Stack>}
 </Stack>;
}
