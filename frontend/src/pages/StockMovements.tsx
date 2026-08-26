import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Button,Chip,MenuItem,Stack,TextField,Typography} from '@mui/material';
import {useLocation,useNavigate} from 'react-router-dom';
import {GridColDef} from '@mui/x-data-grid';
import ResponsiveTable from '../components/ResponsiveTable';
import {api,errorDetail} from '../api';
import {movementLabel,movementTarget} from '../utils/stock';

export default function StockMovements(){
 const location=useLocation();
 const nav=useNavigate();
 const [rows,setRows]=useState<any[]>([]);
 const [error,setError]=useState('');
 const [loading,setLoading]=useState(false);
 const [q,setQ]=useState('');
 const [warehouses,setWarehouses]=useState<any[]>([]);
 const [warehouseId,setWarehouseId]=useState<number|''>(()=>{
  const state:any=location.state;
  return state?.warehouseId?Number(state.warehouseId):'';
 });

 useEffect(()=>{api.get('/warehouses').then(r=>setWarehouses(r.data)).catch(()=>setWarehouses([]))},[]);
 useEffect(()=>{
  const t=setTimeout(()=>{
   setLoading(true);setError('');
   api.get('/products/stock/movements/all',{params:{q,warehouse_id:warehouseId||undefined}})
    .then(r=>setRows(r.data))
    .catch(e=>setError(errorDetail(e,'Stok hareketleri yüklenemedi.')))
    .finally(()=>setLoading(false));
  },200);
  return()=>clearTimeout(t);
 },[q,warehouseId]);

 const openDocument=(row:any)=>{
  const target=movementTarget(row.reference_type,row.reference_id);
  if(target)nav(target.path,{state:{openId:target.openId}});
 };

 const cols=useMemo<GridColDef[]>(()=>[
  {field:'id',headerName:'No',width:70},
  {field:'product_name',headerName:'Ürün',flex:1,minWidth:220},
  {field:'warehouse_name',headerName:'Depo',width:150,valueFormatter:v=>v||'-'},
  {field:'movement_date',headerName:'Tarih',width:120},
  {field:'movement_type',headerName:'Tür',width:150,renderCell:p=><Chip size="small" variant="outlined" label={movementLabel(p.value)}/>},
  {field:'quantity',headerName:'Miktar',width:110,renderCell:p=>{const q=Number(p.value);return <span style={{fontWeight:700,color:q<0?'#d32f2f':'#2e7d32'}}>{q>0?'+':''}{q}</span>}},
  {field:'note',headerName:'Not',width:220,valueFormatter:v=>v||'-'},
  {field:'actions',headerName:'Belge',width:120,sortable:false,filterable:false,renderCell:p=>{
   const target=movementTarget(p.row.reference_type,p.row.reference_id);
   return target?<Button size="small" onClick={e=>{e.stopPropagation();openDocument(p.row)}} aria-label={`${movementLabel(p.row.movement_type)} belgesini aç`}>Belgeyi Aç</Button>:<span style={{color:'#999'}}>-</span>;
  }},
 ],[]);

 return <Stack spacing={2}>
  <Typography variant="h4" fontWeight={900}>Stok Hareketleri</Typography>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
   <TextField size="small" label="Ürün ara" value={q} onChange={e=>setQ(e.target.value)} sx={{flex:1,maxWidth:500}}/>
   <TextField select size="small" label="Depo" value={warehouseId} onChange={e=>setWarehouseId(e.target.value===''?'':Number(e.target.value))} sx={{minWidth:220}}>
    <MenuItem value="">Tüm depolar</MenuItem>
    {warehouses.map(w=><MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
   </TextField>
  </Stack>
  {error&&<Alert severity="error">{error}</Alert>}
  {!error&&!loading&&rows.length===0&&<Alert severity="info">Bu filtreye uygun stok hareketi bulunmuyor.</Alert>}
  <ResponsiveTable rows={rows} columns={cols} loading={loading} onRowClick={r=>nav(`/urunler/${r.product_id}`)}
   cardTitle={r=>r.product_name}
   cardSubtitle={r=>`${r.movement_date} · ${r.warehouse_name||'Depo belirtilmemiş'}`}
   cardFields={[
    {label:'Tür',value:r=>movementLabel(r.movement_type)},
    {label:'Miktar',value:r=>`${Number(r.quantity)>0?'+':''}${Number(r.quantity)}`},
    {label:'Not',value:r=>r.note||'-'},
   ]}
   cardActions={[
    {label:'Ürün Detayı',color:'primary',onClick:r=>nav(`/urunler/${r.product_id}`)},
    {label:'Belgeyi Aç',disabled:r=>!movementTarget(r.reference_type,r.reference_id),onClick:r=>openDocument(r)},
   ]}/>
 </Stack>;
}
