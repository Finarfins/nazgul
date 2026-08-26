import React from 'react';
import {useEffect,useMemo,useRef,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Alert,Button,Chip,MenuItem,Paper,Stack,TextField} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import {GridColDef} from '@mui/x-data-grid';
import ResponsiveTable from '../components/ResponsiveTable';
import WorkOrderDialog from '../components/WorkOrderDialog';
import {api,money} from '../api';
import {useAuth} from '../AuthContext';
import {WO_STATUS_LABELS,WO_STATUS_COLORS,WO_PRIORITY_LABELS,WO_PRIORITY_COLORS} from '../workOrderConstants';
import ServiceWorkspaceHeader from '../components/ServiceWorkspaceHeader';

export default function WorkOrders(){
 const nav=useNavigate();const {can}=useAuth();const canWrite=can('sales');
 const [rows,setRows]=useState<any[]>([]);const [q,setQ]=useState('');const [status,setStatus]=useState('');const [priority,setPriority]=useState('');const [dateFrom,setDateFrom]=useState('');const [dateTo,setDateTo]=useState('');
 const [loading,setLoading]=useState(false);const [error,setError]=useState('');const [open,setOpen]=useState(false);const [editId,setEditId]=useState<number|null>(null);const requestSeq=useRef(0);
 const load=()=>{const seq=++requestSeq.current;setLoading(true);setError('');const params:any={q,page_size:200};if(status)params.status=status;if(dateFrom)params.date_from=dateFrom;if(dateTo)params.date_to=dateTo;
  api.get('/work-orders',{params}).then(r=>{if(seq!==requestSeq.current)return;let items=r.data?.items||[];if(priority)items=items.filter((w:any)=>w.priority===priority);setRows(items)}).catch(e=>{if(seq===requestSeq.current)setError(e.response?.data?.detail||'İş emri listesi yüklenemedi.')}).finally(()=>{if(seq===requestSeq.current)setLoading(false)})};
 useEffect(()=>{const timer=setTimeout(load,250);return()=>clearTimeout(timer)},[q,status,priority,dateFrom,dateTo]);
 const columns=useMemo<GridColDef[]>(()=>[
  {field:'work_order_no',headerName:'İş Emri No',width:150,renderCell:p=><Chip label={p.value} size="small" sx={{bgcolor:'#0b3567',color:'white',fontWeight:800,borderRadius:1}}/>},
  {field:'machine',headerName:'Makine',flex:1,minWidth:170,valueGetter:(_v,row)=>`${row.machine_brand||''} ${row.machine_model||''}`.trim()||'-'},
  {field:'customer_name',headerName:'Müşteri',flex:1,minWidth:150,valueGetter:value=>value||'-'},
  {field:'technician_name',headerName:'Teknisyen',width:150,valueGetter:(_v,row)=>row.technician_name||row.technician_username||'-'},
  {field:'status',headerName:'Durum',width:135,renderCell:p=><Chip size="small" color={WO_STATUS_COLORS[p.value]||'default'} label={WO_STATUS_LABELS[p.value]||p.value}/>},
  {field:'priority',headerName:'Öncelik',width:110,renderCell:p=><Chip size="small" variant="outlined" color={WO_PRIORITY_COLORS[p.value]||'default'} label={WO_PRIORITY_LABELS[p.value]||p.value}/>},
  {field:'opened_at',headerName:'Açılış',width:110,valueGetter:value=>value?String(value).slice(0,10):'-'},
  {field:'grand_total',headerName:'Genel Toplam',width:140,align:'right',headerAlign:'right',renderCell:p=><b>{money(p.value)}</b>},
 ],[]);
 return <Stack spacing={2}>
  <ServiceWorkspaceHeader title="İş Emirleri" description="Atölye akışını, teknisyen işlerini ve servis önceliklerini anlık takip edin." stats={[{label:'Toplam iş emri',value:rows.length},{label:'Devam ediyor',value:rows.filter(r=>r.status==='IN_PROGRESS').length,tone:'warning'}]} action={canWrite?<Button variant="contained" startIcon={<AddIcon/>} onClick={()=>{setEditId(null);setOpen(true)}} sx={{bgcolor:'#f5c400',color:'#071e41',fontWeight:850,'&:hover':{bgcolor:'#ffd329'}}}>Yeni İş Emri</Button>:undefined}/>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  <Paper variant="outlined" sx={{p:1.5,borderRadius:3}}><Stack direction={{xs:'column',md:'row'}} spacing={1} flexWrap="wrap" useFlexGap>
   <TextField sx={{flex:1,minWidth:220}} size="small" label="İş emri no, şikayet, müşteri veya makine ara" value={q} onChange={e=>setQ(e.target.value)}/>
   <TextField select size="small" label="Durum" value={status} onChange={e=>setStatus(e.target.value)} sx={{minWidth:160}}><MenuItem value="">Tümü</MenuItem>{Object.entries(WO_STATUS_LABELS).map(([val,label])=><MenuItem key={val} value={val}>{label}</MenuItem>)}</TextField>
   <TextField select size="small" label="Öncelik" value={priority} onChange={e=>setPriority(e.target.value)} sx={{minWidth:140}}><MenuItem value="">Tümü</MenuItem>{Object.entries(WO_PRIORITY_LABELS).map(([val,label])=><MenuItem key={val} value={val}>{label}</MenuItem>)}</TextField>
   <TextField size="small" type="date" label="Başlangıç" value={dateFrom} onChange={e=>setDateFrom(e.target.value)} InputLabelProps={{shrink:true}} sx={{minWidth:150}}/>
   <TextField size="small" type="date" label="Bitiş" value={dateTo} onChange={e=>setDateTo(e.target.value)} InputLabelProps={{shrink:true}} sx={{minWidth:150}}/>
  </Stack></Paper>
  <ResponsiveTable rows={rows} columns={columns} loading={loading} onRowClick={(row:any)=>nav(`/is-emirleri/${row.id}`)} cardTitle={r=>r.work_order_no} cardSubtitle={r=>`${r.machine_brand||''} ${r.machine_model||''}`.trim()||'-'} cardFields={[{label:'Müşteri',value:r=>r.customer_name||'-'},{label:'Durum',value:r=>WO_STATUS_LABELS[r.status]||r.status},{label:'Öncelik',value:r=>WO_PRIORITY_LABELS[r.priority]||r.priority},{label:'Genel Toplam',value:r=>money(r.grand_total)}]}/>
  <WorkOrderDialog open={open} id={editId} onClose={()=>setOpen(false)} onSaved={load}/>
 </Stack>;
}
