import React from 'react';
import {useEffect,useMemo,useRef,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Alert,Autocomplete,Button,Chip,IconButton,MenuItem,Paper,Stack,TextField,Tooltip,Typography} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';import EditIcon from '@mui/icons-material/Edit';import BlockIcon from '@mui/icons-material/Block';import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import {GridColDef} from '@mui/x-data-grid';import ResponsiveTable from '../components/ResponsiveTable';import MachineDialog,{MACHINE_STATUS_LABELS} from '../components/MachineDialog';import {api} from '../api';import {useAuth} from '../AuthContext';
import ServiceWorkspaceHeader from '../components/ServiceWorkspaceHeader';

const STATUS_COLORS:Record<string,'success'|'warning'|'default'|'info'|'error'>={active:'success',maintenance:'warning',idle:'info',sold:'default',scrapped:'error'};

export default function Machines(){
 const nav=useNavigate();const {can}=useAuth();const canWrite=can('machines');
 const [rows,setRows]=useState<any[]>([]);const [customers,setCustomers]=useState<any[]>([]);const [q,setQ]=useState('');const [active,setActive]=useState('active');const [customerId,setCustomerId]=useState<number|null>(null);const [loading,setLoading]=useState(false);const [error,setError]=useState('');const [open,setOpen]=useState(false);const [editId,setEditId]=useState<number|null>(null);const requestSeq=useRef(0);
 const load=()=>{const seq=++requestSeq.current;setLoading(true);setError('');const params:any={q,active,limit:500};if(customerId)params.customer_id=customerId;
  api.get('/machines',{params}).then(r=>{if(seq===requestSeq.current)setRows(r.data)}).catch(e=>{if(seq===requestSeq.current)setError(e.response?.data?.detail||'Makine listesi yüklenemedi.')}).finally(()=>{if(seq===requestSeq.current)setLoading(false)})};
 useEffect(()=>{const timer=setTimeout(load,250);return()=>clearTimeout(timer)},[q,active,customerId]);
 useEffect(()=>{api.get('/customers',{params:{active:'all'}}).then(r=>setCustomers(r.data||[])).catch(()=>setCustomers([]))},[]);
 const toggleActive=async(row:any)=>{try{await api.patch(`/machines/${row.id}/active`,{is_active:!row.is_active});load()}catch(e:any){setError(e.response?.data?.detail||e.message)}};
 const columns=useMemo<GridColDef[]>(()=>[
  {field:'id',headerName:'ID',width:70},
  {field:'brand',headerName:'Marka',width:140,renderCell:p=><Chip label={p.value} sx={{width:'100%',justifyContent:'flex-start',bgcolor:'#0b3567',color:'white',fontWeight:800,borderRadius:1}}/>},
  {field:'model',headerName:'Model',width:150},
  {field:'serial_number',headerName:'Seri No',width:140,valueGetter:value=>value||'-'},
  {field:'chassis_number',headerName:'Şasi No',width:150,valueGetter:value=>value||'-'},
  {field:'customer_name',headerName:'Müşteri',flex:1,minWidth:170,valueGetter:value=>value||'-'},
  {field:'status',headerName:'Durum',width:115,renderCell:p=><Chip size="small" color={STATUS_COLORS[p.value]||'default'} label={MACHINE_STATUS_LABELS[p.value]||p.value}/>},
  {field:'working_hours',headerName:'Çalışma Saati',width:125},
  {field:'actions',headerName:'',width:110,sortable:false,renderCell:params=>canWrite?<><Tooltip title="Düzenle"><IconButton size="small" onClick={e=>{e.stopPropagation();setEditId(params.row.id);setOpen(true)}}><EditIcon fontSize="small"/></IconButton></Tooltip><Tooltip title={params.row.is_active?'Pasife al':'Aktif et'}><IconButton size="small" color={params.row.is_active?'warning':'success'} onClick={e=>{e.stopPropagation();toggleActive(params.row)}}>{params.row.is_active?<BlockIcon fontSize="small"/>:<CheckCircleIcon fontSize="small"/>}</IconButton></Tooltip></>:null}
 ],[canWrite]);
 return <Stack spacing={2}>
  <ServiceWorkspaceHeader title="Makine Parkı" description="Müşteri makinelerini, çalışma saatlerini ve servis durumlarını tek merkezden yönetin." stats={[{label:'Toplam makine',value:rows.length},{label:'Bakımda',value:rows.filter(r=>r.status==='maintenance').length,tone:'warning'}]} action={canWrite?<Button variant="contained" startIcon={<AddIcon/>} onClick={()=>{setEditId(null);setOpen(true)}} sx={{bgcolor:'#f5c400',color:'#071e41',fontWeight:850,'&:hover':{bgcolor:'#ffd329'}}}>Yeni Makine</Button>:undefined}/>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  <Paper variant="outlined" sx={{p:1.5,borderRadius:3}}><Stack direction={{xs:'column',md:'row'}} spacing={1}>
   <TextField fullWidth size="small" label="Marka, model, seri no, şasi no veya plaka ara" value={q} onChange={e=>setQ(e.target.value)}/>
   <Autocomplete size="small" sx={{minWidth:220}} options={customers} getOptionLabel={o=>o?.name||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={customers.find(c=>c.id===customerId)||null} onChange={(_,val)=>setCustomerId(val?val.id:null)} renderInput={params=><TextField {...params} label="Müşteri"/>}/>
   <TextField select size="small" label="Durum" value={active} onChange={e=>setActive(e.target.value)} sx={{minWidth:150}}><MenuItem value="active">Aktifler</MenuItem><MenuItem value="inactive">Pasifler</MenuItem><MenuItem value="all">Tümü</MenuItem></TextField>
  </Stack></Paper>
  <ResponsiveTable rows={rows} columns={columns} loading={loading} onRowClick={(row:any)=>nav(`/makineler/${row.id}`)} cardTitle={r=>`${r.brand} ${r.model}`} cardSubtitle={r=>r.customer_name||'Müşteri atanmadı'} cardFields={[{label:'Seri No',value:r=>r.serial_number||'-'},{label:'Şasi No',value:r=>r.chassis_number||'-'},{label:'Durum',value:r=>MACHINE_STATUS_LABELS[r.status]||r.status},{label:'Çalışma Saati',value:r=>r.working_hours??'-'}]}/>
  <MachineDialog open={open} id={editId} onClose={()=>setOpen(false)} onSaved={load}/>
 </Stack>
}
