import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Button,Card,CardContent,Chip,MenuItem,Stack,TextField,Typography} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined';
import RefreshIcon from '@mui/icons-material/Refresh';
import {GridColDef} from '@mui/x-data-grid';
import {useLocation,useNavigate,useSearchParams} from 'react-router-dom';
import InventoryCountDialog from '../components/InventoryCountDialog';
import ResponsiveTable from '../components/ResponsiveTable';
import {api,errorDetail} from '../api';

type VarianceFilter='all'|'changed'|'unchanged';

const csvCell=(value:unknown)=>{
 const text=String(value??'');
 const safe=typeof value==='string'&&/^[\t\r\n ]*[=+\-@]/.test(text)?`'${text}`:text;
 return `"${safe.replace(/"/g,'""')}"`;
};
export function inventoryCountsCsv(rows:any[]):string{
 const table=[
  ['Sayım No','Tarih','Depo','Sayılan Satır','Değişen','Değişmeyen','Artan','Azalan','Toplam Mutlak Fark'],
  ...rows.map(row=>[
   row.id,row.count_date,row.warehouse_name,row.item_count,row.changed_count,
   row.unchanged_count,row.increase_count,row.decrease_count,row.total_absolute_variance,
  ]),
 ];
 return `\uFEFF${table.map(line=>line.map(csvCell).join(';')).join('\r\n')}`;
}

export default function InventoryCounts(){
 const nav=useNavigate();
 const location=useLocation();
 const [searchParams,setSearchParams]=useSearchParams();
 const initialWarehouse=Number((location.state as any)?.warehouseId||0);
 const [warehouses,setWarehouses]=useState<any[]>([]);
 const [rows,setRows]=useState<any[]>([]);
 const [warehouseId,setWarehouseId]=useState<number|''>(initialWarehouse>0?initialWarehouse:'');
 const [dateFrom,setDateFrom]=useState('');
 const [dateTo,setDateTo]=useState('');
 const [varianceFilter,setVarianceFilter]=useState<VarianceFilter>('all');
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const [dialogOpen,setDialogOpen]=useState(false);
 const quickStart=searchParams.get('new')==='1';

 useEffect(()=>{
  api.get('/warehouses').then(r=>setWarehouses(r.data||[])).catch(()=>setWarehouses([]));
 },[]);

 useEffect(()=>{
  if(!quickStart)return;
  setDialogOpen(true);
  const next=new URLSearchParams(searchParams);
  next.delete('new');
  setSearchParams(next,{replace:true});
 },[quickStart,searchParams,setSearchParams]);

 const load=()=>{
  setLoading(true);setError('');
  api.get('/warehouses/counts',{params:{
   warehouse_id:warehouseId||undefined,
   date_from:dateFrom||undefined,
   date_to:dateTo||undefined,
   changed_only:varianceFilter==='changed'||undefined,
  }})
   .then(r=>setRows(r.data||[]))
   .catch(e=>{setRows([]);setError(errorDetail(e,'Stok sayımları yüklenemedi.'));})
   .finally(()=>setLoading(false));
 };

 useEffect(()=>{
  const timer=setTimeout(load,120);
  return()=>clearTimeout(timer);
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[warehouseId,dateFrom,dateTo,varianceFilter]);

 const visibleRows=useMemo(()=>varianceFilter==='unchanged'
  ?rows.filter(row=>Number(row.changed_count||0)===0)
  :rows,[rows,varianceFilter]);

 const summary=useMemo(()=>visibleRows.reduce((acc,row)=>({
  documents:acc.documents+1,
  items:acc.items+Number(row.item_count||0),
  changed:acc.changed+Number(row.changed_count||0),
  variance:acc.variance+Number(row.total_absolute_variance||0),
 }),{documents:0,items:0,changed:0,variance:0}),[visibleRows]);

 const cols=useMemo<GridColDef[]>(()=>[
  {field:'id',headerName:'Sayım No',width:105,valueFormatter:v=>`#${v}`},
  {field:'count_date',headerName:'Tarih',width:125},
  {field:'warehouse_name',headerName:'Depo',flex:1,minWidth:190},
  {field:'item_count',headerName:'Satır',width:85},
  {field:'changed_count',headerName:'Değişen',width:100},
  {field:'increase_count',headerName:'Artan',width:85},
  {field:'decrease_count',headerName:'Azalan',width:85},
  {field:'total_absolute_variance',headerName:'Mutlak Fark',width:120,valueFormatter:v=>Number(v||0).toLocaleString('tr-TR')},
  {field:'status',headerName:'Durum',width:115,sortable:false,renderCell:p=>Number(p.row.changed_count||0)>0
   ?<Chip size="small" color="warning" label="Fark Var"/>
   :<Chip size="small" color="success" label="Fark Yok"/>},
 ],[]);

 const resetFilters=()=>{setWarehouseId('');setDateFrom('');setDateTo('');setVarianceFilter('all');};
 const exportCsv=()=>{
  if(visibleRows.length===0)return;
  const blob=new Blob([inventoryCountsCsv(visibleRows)],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  link.href=url;
  link.download=`stok-sayimlari-${new Date().toISOString().slice(0,10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
 };

 return <Stack spacing={2}>
  <Stack direction={{xs:'column',md:'row'}} justifyContent="space-between" gap={1.5}>
   <Stack spacing={.35}>
    <Typography variant="h4" fontWeight={900}>Stok Sayımları</Typography>
    <Typography color="text.secondary">Fiziksel sayım belgelerini, depo farklarını ve düzeltme etkilerini tek ekrandan izleyin.</Typography>
   </Stack>
   <Stack direction={{xs:'column',sm:'row'}} spacing={1} sx={{alignSelf:{xs:'stretch',md:'flex-start'}}}>
    <Button variant="outlined" startIcon={<DownloadIcon/>} onClick={exportCsv} disabled={visibleRows.length===0}>CSV Dışa Aktar</Button>
    <Button variant="contained" startIcon={<FactCheckOutlinedIcon/>} onClick={()=>setDialogOpen(true)}>Yeni Fiziksel Sayım</Button>
   </Stack>
  </Stack>

  <Card><CardContent>
   <Stack direction={{xs:'column',md:'row'}} spacing={1}>
    <TextField select size="small" label="Depo" value={warehouseId} onChange={e=>setWarehouseId(e.target.value===''?'':Number(e.target.value))} sx={{minWidth:{md:220}}}>
     <MenuItem value="">Tüm depolar</MenuItem>
     {warehouses.map(w=><MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
    </TextField>
    <TextField size="small" type="date" label="Başlangıç" value={dateFrom} onChange={e=>setDateFrom(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
    <TextField size="small" type="date" label="Bitiş" value={dateTo} onChange={e=>setDateTo(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
    <TextField select size="small" label="Fark durumu" value={varianceFilter} onChange={e=>setVarianceFilter(e.target.value as VarianceFilter)} sx={{minWidth:{md:160}}}>
     <MenuItem value="all">Tümü</MenuItem>
     <MenuItem value="changed">Fark bulunanlar</MenuItem>
     <MenuItem value="unchanged">Fark bulunmayanlar</MenuItem>
    </TextField>
    <Button startIcon={<RefreshIcon/>} onClick={resetFilters}>Filtreleri Temizle</Button>
   </Stack>
  </CardContent></Card>

  <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
   <Chip label={`Sayım belgesi: ${summary.documents}`}/>
   <Chip label={`Sayılan satır: ${summary.items}`}/>
   <Chip color={summary.changed?'warning':'default'} label={`Değişen satır: ${summary.changed}`}/>
   <Chip color={summary.variance?'error':'success'} label={`Toplam mutlak fark: ${Number(summary.variance.toFixed(4)).toLocaleString('tr-TR')}`}/>
  </Stack>

  {error&&<Alert severity="error">{error}</Alert>}
  {!error&&!loading&&visibleRows.length===0&&<Alert severity="info">Seçilen filtrelerde stok sayımı bulunmuyor.</Alert>}
  {!error&&visibleRows.length>0&&<ResponsiveTable rows={visibleRows} columns={cols}
   onRowClick={row=>nav(`/stok-sayimlari/${row.id}`)}
   cardTitle={row=>`Sayım #${row.id}`}
   cardSubtitle={row=>`${row.count_date} · ${row.warehouse_name}`}
   cardFields={[
    {label:'Satır',value:row=>row.item_count},
    {label:'Değişen',value:row=>row.changed_count},
    {label:'Artan / Azalan',value:row=>`${row.increase_count||0} / ${row.decrease_count||0}`},
    {label:'Mutlak fark',value:row=>Number(row.total_absolute_variance||0).toLocaleString('tr-TR')},
    {label:'Durum',value:row=>Number(row.changed_count||0)>0?'Fark Var':'Fark Yok'},
   ]}
   cardActions={[{label:'Sayımı Aç',color:'primary',onClick:row=>nav(`/stok-sayimlari/${row.id}`)}]}/>} 

  <InventoryCountDialog open={dialogOpen} warehouses={warehouses}
   initialWarehouseId={typeof warehouseId==='number'?warehouseId:null}
   onClose={()=>setDialogOpen(false)}
   onDone={result=>{setDialogOpen(false);nav(`/stok-sayimlari/${result.id}`);}}/>
 </Stack>;
}
