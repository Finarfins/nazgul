import React from 'react';
import {useEffect,useState} from 'react';
import {Alert,Box,Button,Chip,IconButton,Paper,Stack,Tab,Tabs,Typography} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import TransformIcon from '@mui/icons-material/Transform';
import {api,money,policyOverrideHeaders,policyOverrideRequired} from '../api';
import {useAuth} from '../AuthContext';
import WorkflowDialog from '../components/WorkflowDialog';
import ResponsiveTable from '../components/ResponsiveTable';
import PolicyOverrideDialog from '../components/PolicyOverrideDialog';

type Kind='quote'|'sales_order'|'delivery'|'sale_return'|'purchase_return';
type PolicyAction={detail:string;run:(headers:Record<string,string>)=>Promise<void>};
const names:Record<Kind,string>={quote:'Teklifler',sales_order:'Siparişler',delivery:'İrsaliyeler',sale_return:'Satış İadeleri',purchase_return:'Alış İadeleri'};

export default function WorkflowDocuments(){
 const {user}=useAuth();
 const [kind,setKind]=useState<Kind>('quote');
 const [rows,setRows]=useState<any[]>([]);
 const [open,setOpen]=useState(false);
 const [edit,setEdit]=useState<number|null>(null);
 const [error,setError]=useState('');
 const [policyAction,setPolicyAction]=useState<PolicyAction|null>(null);
 const [overrideReason,setOverrideReason]=useState('');
 const [overrideError,setOverrideError]=useState('');
 const [overrideBusy,setOverrideBusy]=useState(false);
 const manager=user?.role==='admin'||user?.role==='yonetici';

 const load=()=>api.get(`/workflow/${kind}`).then(response=>setRows(response.data)).catch(err=>setError(err.response?.data?.detail||err.message));
 useEffect(()=>{void load()},[kind]);

 const openPolicyApproval=(err:any,run:(headers:Record<string,string>)=>Promise<void>)=>{
  const message=err.response?.data?.detail||err.message;
  if(manager&&policyOverrideRequired(err)){
   setOverrideReason('');setOverrideError('');setPolicyAction({detail:message,run});
  }else setError(message);
 };

 const del=async(id:number)=>{
  const action=async(headers?:Record<string,string>)=>{await api.delete(`/workflow/${kind}/${id}`,headers?{headers}:undefined)};
  try{setError('');await action();load()}
  catch(err:any){openPolicyApproval(err,async headers=>{await action(headers);load()})}
 };

 const convert=async(row:any,target:'order'|'sale'|'delivery')=>{
  const path=kind==='quote'?`/workflow/quote/${row.id}/to-order`:`/workflow/sales_order/${row.id}/to-${target}`;
  const action=async(headers?:Record<string,string>)=>{await api.post(path,undefined,headers?{headers}:undefined)};
  try{setError('');await action();load()}
  catch(err:any){openPolicyApproval(err,async headers=>{await action(headers);load()})}
 };

 const confirmOverride=async()=>{
  if(!policyAction||overrideReason.trim().length<5)return;
  setOverrideBusy(true);setOverrideError('');
  try{await policyAction.run(policyOverrideHeaders(overrideReason));setPolicyAction(null);setOverrideReason('')}
  catch(err:any){setOverrideError(err.response?.data?.detail||'Yönetici onayı uygulanamadı.')}
  finally{setOverrideBusy(false)}
 };

 const total=(row:any)=>Number(row.grand_total??row.total??0);
 const date=(row:any)=>row.quote_date||row.order_date||row.delivery_date||row.return_date;
 const columns=[
  {field:'document_no',headerName:'Belge No',flex:1,minWidth:130},
  {field:'date',headerName:'Tarih',flex:1,minWidth:120,valueGetter:(_:any,row:any)=>date(row)},
  {field:'entity',headerName:'Cari',flex:2,minWidth:220,valueGetter:(_:any,row:any)=>row.customer_name||row.supplier_name},
  {field:'total',headerName:'Tutar',flex:1,minWidth:130,valueGetter:(_:any,row:any)=>money(total(row))},
  {field:'status',headerName:'Durum',flex:1,minWidth:120,renderCell:(params:any)=><Chip size="small" label={params.value||'Taslak'} color={params.value==='completed'||params.value==='approved'?'success':params.value==='cancelled'?'error':'default'}/>},
  {field:'actions',headerName:'İşlem',sortable:false,minWidth:240,renderCell:(params:any)=><Stack direction="row" spacing={.5}>
   <IconButton size="small" onClick={()=>{setEdit(params.row.id);setOpen(true)}}><EditOutlinedIcon fontSize="small"/></IconButton>
   {kind==='quote'&&<Button size="small" startIcon={<TransformIcon/>} onClick={()=>void convert(params.row,'order')}>Siparişe</Button>}
   {kind==='sales_order'&&<><Button size="small" onClick={()=>void convert(params.row,'sale')}>Satışa</Button><Button size="small" onClick={()=>void convert(params.row,'delivery')}>İrsaliyeye</Button></>}
   <IconButton size="small" color="error" onClick={()=>void del(params.row.id)}><DeleteOutlineIcon fontSize="small"/></IconButton>
  </Stack>},
 ];

 return <Stack spacing={2}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between">
   <Box><Typography variant="h4" fontWeight={900}>Belge Akışları</Typography><Typography color="text.secondary">Tekliften siparişe, siparişten satış veya irsaliyeye; iadelerde stok etkisi otomatik.</Typography></Box>
   <Button variant="contained" startIcon={<AddIcon/>} onClick={()=>{setEdit(null);setOpen(true)}}>Yeni {names[kind].slice(0,-3)}</Button>
  </Stack>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  <Paper><Tabs value={kind} onChange={(_,value)=>setKind(value)} variant="scrollable" scrollButtons="auto"><Tab value="quote" label="Teklifler"/><Tab value="sales_order" label="Siparişler"/><Tab value="delivery" label="İrsaliyeler"/><Tab value="sale_return" label="Satış İadeleri"/><Tab value="purchase_return" label="Alış İadeleri"/></Tabs></Paper>
  <ResponsiveTable rows={rows} columns={columns} cardTitle={(row:any)=>row.customer_name||row.supplier_name||row.document_no||'Belge'} cardSubtitle={(row:any)=>`${date(row)||''} · ${row.status||''}`} cardFields={[{label:'Belge No',value:(row:any)=>row.document_no||'-'},{label:'Tutar',value:(row:any)=>money(total(row))}]}/>
  <WorkflowDialog open={open} kind={kind} id={edit} onClose={()=>setOpen(false)} onSaved={load}/>
  <PolicyOverrideDialog open={!!policyAction} detail={policyAction?.detail||''} reason={overrideReason} busy={overrideBusy} error={overrideError} onReasonChange={setOverrideReason} onClose={()=>{setPolicyAction(null);setOverrideReason('');setOverrideError('')}} onConfirm={confirmOverride}/>
 </Stack>;
}
