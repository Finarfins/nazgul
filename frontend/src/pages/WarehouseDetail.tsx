import React from 'react';
import {useEffect,useMemo,useState,type ReactNode} from 'react';
import {Alert,Box,Button,Card,CardContent,Chip,CircularProgress,Divider,Stack,Typography} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import TimelineIcon from '@mui/icons-material/Timeline';
import {useNavigate,useParams} from 'react-router-dom';
import {GridColDef} from '@mui/x-data-grid';
import InventoryCountDialog from '../components/InventoryCountDialog';
import ResponsiveTable from '../components/ResponsiveTable';
import TransferDialog from '../components/TransferDialog';
import {api,errorDetail} from '../api';
import {stockCondition} from '../utils/stock';

const labelSx={fontSize:13.5,lineHeight:1.4,color:'text.secondary'} as const;

function Stat({label,value,color}:{label:string;value:ReactNode;color?:string}){
 return <Card variant="outlined" sx={{flex:'1 1 150px',minWidth:130}}><CardContent sx={{p:1.75,'&:last-child':{pb:1.75}}}>
  <Typography sx={labelSx} fontWeight={700}>{label}</Typography>
  <Typography fontWeight={900} color={color} sx={{fontSize:{xs:20,sm:24},mt:.25}}>{value}</Typography>
 </CardContent></Card>;
}

export default function WarehouseDetail(){
 const {id}=useParams();
 const warehouseId=Number(id);
 const nav=useNavigate();
 const [data,setData]=useState<any|null>(null);
 const [error,setError]=useState('');
 const [warehouses,setWarehouses]=useState<any[]>([]);
 const [counts,setCounts]=useState<any[]>([]);
 const [countHistoryError,setCountHistoryError]=useState('');
 const [transferOpen,setTransferOpen]=useState(false);
 const [countOpen,setCountOpen]=useState(false);
 const [countSuccess,setCountSuccess]=useState('');

 const load=()=>{setData(null);setError('');
  api.get(`/warehouses/${warehouseId}`).then(r=>setData(r.data)).catch(e=>setError(errorDetail(e,'Depo bilgileri yüklenemedi.')));};
 const loadCounts=()=>{
  setCountHistoryError('');
  api.get('/warehouses/counts',{params:{warehouse_id:warehouseId,limit:10}})
   .then(r=>setCounts(r.data||[]))
   .catch(e=>{setCounts([]);setCountHistoryError(errorDetail(e,'Sayım geçmişi yüklenemedi.'));});
 };
 useEffect(load,[warehouseId]);
 useEffect(loadCounts,[warehouseId]);
 // Warehouse list is a secondary request; its failure must not hide the page.
 useEffect(()=>{api.get('/warehouses').then(r=>setWarehouses(r.data)).catch(()=>setWarehouses([]));},[]);

 const cols=useMemo<GridColDef[]>(()=>[
  {field:'product_id',headerName:'ID',width:70},
  {field:'name',headerName:'Ürün',flex:1,minWidth:220},
  {field:'product_code',headerName:'Kod',width:120,valueFormatter:v=>v||'-'},
  {field:'location',headerName:'Raf',width:100,valueFormatter:v=>v||'-'},
  {field:'quantity',headerName:'Stok',width:90},
  {field:'unit',headerName:'Birim',width:75},
  {field:'critical_stock',headerName:'Kritik',width:90},
  {field:'condition',headerName:'Durum',width:120,sortable:false,renderCell:p=>{
   const c=stockCondition(p.row.quantity,p.row.critical_stock);
   return <Chip size="small" color={c.color} label={c.label}/>;
  }},
  {field:'last_movement_date',headerName:'Son Hareket',width:130,valueFormatter:v=>v||'-'},
 ],[]);
 const countCols=useMemo<GridColDef[]>(()=>[
  {field:'id',headerName:'Sayım No',width:105},
  {field:'count_date',headerName:'Tarih',width:130},
  {field:'item_count',headerName:'Ürün',width:100},
  {field:'changed_count',headerName:'Değişen',width:110},
  {field:'total_absolute_variance',headerName:'Toplam Fark',width:130},
  {field:'action',headerName:'Belge',width:130,sortable:false,filterable:false,renderCell:p=><Button size="small" onClick={event=>{event.stopPropagation();nav(`/stok-sayimlari/${p.row.id}`);}}>Sayımı Aç</Button>},
 ],[nav]);

 if(error)return <Stack spacing={2}>
  <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/depolar')} sx={{alignSelf:'flex-start'}}>Depolara Dön</Button>
  <Alert severity="error">{error}</Alert>
 </Stack>;
 if(!data)return <Box textAlign="center" p={5}><CircularProgress aria-label="Depo yükleniyor"/></Box>;

 const w=data.warehouse;
 const s=data.stats||{};
 const products:any[]=data.products||[];
 const unitOf=(row:any)=>row.unit||'';

 return <Stack spacing={2}>
  <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/depolar')} sx={{alignSelf:'flex-start'}}>Depolara Dön</Button>
  {countSuccess&&<Alert severity="success" onClose={()=>setCountSuccess('')}>{countSuccess}</Alert>}

  <Card><CardContent>
   <Stack direction={{xs:'column',lg:'row'}} justifyContent="space-between" gap={2}>
    <Box>
     <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Typography variant="h4" fontWeight={900} sx={{fontSize:{xs:24,sm:30}}}>{w.name}</Typography>
      {w.is_default&&<Chip label="Varsayılan Depo" color="primary" size="small"/>}
      <Chip label={w.is_active?'Aktif':'Pasif'} color={w.is_active?'success':'default'} size="small"/>
     </Stack>
     <Typography sx={{...labelSx,mt:.75,fontSize:13.5}}>
      Kod: {w.code||'-'} · Şube: {w.branch_name||'-'}
     </Typography>
    </Box>
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{alignItems:'flex-start'}}>
     <Button variant="contained" startIcon={<FactCheckOutlinedIcon/>} onClick={()=>{setCountSuccess('');setCountOpen(true);}}>Fiziksel Sayım</Button>
     <Button variant="outlined" startIcon={<SwapHorizIcon/>} onClick={()=>setTransferOpen(true)}
      disabled={warehouses.length<2}>Buradan Transfer Et</Button>
     <Button variant="outlined" startIcon={<TimelineIcon/>}
      onClick={()=>nav('/stok-hareketleri',{state:{warehouseId}})}>Stok Hareketleri</Button>
    </Stack>
   </Stack>

   <Divider sx={{my:2}}/>
   <Stack direction="row" spacing={1.2} flexWrap="wrap" useFlexGap>
    <Stat label="ÜRÜN SAYISI" value={Number(s.product_count||0)}/>
    <Stat label="TOPLAM MİKTAR" value={Number(s.total_quantity||0).toLocaleString('tr-TR')}/>
    <Stat label="KRİTİK" value={Number(s.critical_count||0)} color={Number(s.critical_count)>0?'warning.main':'text.primary'}/>
    <Stat label="STOK YOK" value={Number(s.zero_count||0)} color={Number(s.zero_count)>0?'error.main':'text.primary'}/>
    <Stat label="NEGATİF" value={Number(s.negative_count||0)} color={Number(s.negative_count)>0?'error.main':'text.primary'}/>
   </Stack>
  </CardContent></Card>

  <Card><CardContent>
   <Typography variant="h6" fontWeight={900} mb={1.5}>Bu Depodaki Ürünler</Typography>
   {products.length===0
    ?<Alert severity="info">Bu depoda ürün stoğu bulunmuyor.</Alert>
    :<ResponsiveTable rows={products} columns={cols} getRowId={r=>r.product_id}
      onRowClick={r=>nav(`/urunler/${r.product_id}`)}
      cardTitle={r=>r.name}
      cardSubtitle={r=>`${r.product_code||'Kodsuz'} · Raf: ${r.location||'-'}`}
      cardFields={[
       {label:'Stok',value:r=>`${r.quantity} ${unitOf(r)}`},
       {label:'Kritik',value:r=>r.critical_stock},
       {label:'Durum',value:r=>stockCondition(r.quantity,r.critical_stock).label},
       {label:'Son Hareket',value:r=>r.last_movement_date||'-'},
      ]}
      cardActions={[{label:'Ürün Detayı',color:'primary',onClick:r=>nav(`/urunler/${r.product_id}`)}]}/>}
  </CardContent></Card>

  <Card><CardContent>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1} mb={.5}>
    <Typography variant="h6" fontWeight={900}>Son Fiziksel Sayımlar</Typography>
    <Button size="small" startIcon={<FactCheckOutlinedIcon/>} onClick={()=>nav('/stok-sayimlari',{state:{warehouseId}})}>Tüm Sayımlar</Button>
   </Stack>
   <Typography sx={{...labelSx,mb:1.5}}>Bu depoda tamamlanan son 10 sayım ve toplam stok farkları.</Typography>
   {countHistoryError&&<Alert severity="error">{countHistoryError}</Alert>}
   {!countHistoryError&&counts.length===0&&<Alert severity="info">Bu depoda henüz fiziksel sayım yapılmamış.</Alert>}
   {!countHistoryError&&counts.length>0&&<ResponsiveTable rows={counts} columns={countCols}
    onRowClick={row=>nav(`/stok-sayimlari/${row.id}`)}
    cardTitle={row=>`Sayım #${row.id}`}
    cardSubtitle={row=>row.count_date}
    cardFields={[
     {label:'Ürün',value:row=>row.item_count},
     {label:'Değişen',value:row=>row.changed_count},
     {label:'Toplam Fark',value:row=>row.total_absolute_variance},
    ]}
    cardActions={[{label:'Sayımı Aç',color:'primary',onClick:row=>nav(`/stok-sayimlari/${row.id}`)}]}/>} 
  </CardContent></Card>

  <InventoryCountDialog open={countOpen} warehouses={warehouses} initialWarehouseId={warehouseId}
   onClose={()=>setCountOpen(false)}
   onDone={result=>{
    setCountOpen(false);
    setCountSuccess(`Sayım #${result.id} kaydedildi: ${result.item_count} ürün, ${result.changed_count} stok değişikliği.`);
    load();loadCounts();
   }}/>
  <TransferDialog open={transferOpen} warehouses={warehouses} initialSourceId={warehouseId}
   onClose={()=>setTransferOpen(false)} onDone={()=>{setTransferOpen(false);load();}}/>
 </Stack>;
}
