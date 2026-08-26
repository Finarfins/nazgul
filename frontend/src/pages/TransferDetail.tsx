import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Box,Button,Card,CardContent,Chip,CircularProgress,Divider,Stack,Typography} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import Inventory2OutlinedIcon from '@mui/icons-material/Inventory2Outlined';
import TimelineIcon from '@mui/icons-material/Timeline';
import {useNavigate,useParams} from 'react-router-dom';
import {GridColDef} from '@mui/x-data-grid';
import ResponsiveTable from '../components/ResponsiveTable';
import {api,errorDetail} from '../api';
import {movementLabel} from '../utils/stock';

const labelSx={fontSize:13.5,lineHeight:1.4,color:'text.secondary'} as const;

function formatDateTime(value:unknown){
 const raw=String(value||'');
 if(!raw)return '-';
 const parsed=new Date(raw);
 return Number.isNaN(parsed.getTime())?raw:parsed.toLocaleString('tr-TR');
}

export default function TransferDetail(){
 const {id}=useParams();
 const transferId=Number(id);
 const nav=useNavigate();
 const [data,setData]=useState<any|null>(null);
 const [error,setError]=useState('');

 useEffect(()=>{
  setData(null);setError('');
  api.get(`/warehouse-transfers/${transferId}`)
   .then(r=>setData(r.data))
   .catch(e=>setError(errorDetail(e,'Depo transferi yüklenemedi.')));
 },[transferId]);

 const itemCols=useMemo<GridColDef[]>(()=>[
  {field:'product_id',headerName:'ID',width:70},
  {field:'product_name',headerName:'Ürün',flex:1,minWidth:230},
  {field:'product_code',headerName:'Kod',width:130,valueFormatter:v=>v||'-'},
  {field:'quantity',headerName:'Miktar',width:120,renderCell:p=><Typography fontWeight={800}>{Number(p.value).toLocaleString('tr-TR')}</Typography>},
  {field:'unit',headerName:'Birim',width:90,valueFormatter:v=>v||'-'},
 ],[]);

 const movementCols=useMemo<GridColDef[]>(()=>[
  {field:'id',headerName:'No',width:70},
  {field:'product_name',headerName:'Ürün',flex:1,minWidth:220},
  {field:'movement_type',headerName:'Hareket',width:160,renderCell:p=><Chip size="small" variant="outlined" label={movementLabel(p.value)}/>},
  {field:'warehouse_name',headerName:'Depo',width:160,valueFormatter:v=>v||'-'},
  {field:'quantity',headerName:'Miktar',width:110,renderCell:p=>{const qty=Number(p.value);return <Typography fontWeight={800} color={qty<0?'error.main':'success.main'}>{qty>0?'+':''}{qty.toLocaleString('tr-TR')}</Typography>}},
  {field:'movement_date',headerName:'Tarih',width:130,valueFormatter:v=>v||'-'},
 ],[]);

 if(error)return <Stack spacing={2}>
  <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/stok-hareketleri')} sx={{alignSelf:'flex-start'}}>Stok Hareketlerine Dön</Button>
  <Alert severity="error">{error}</Alert>
 </Stack>;
 if(!data)return <Box textAlign="center" p={5}><CircularProgress aria-label="Depo transferi yükleniyor"/></Box>;

 const transfer=data.transfer||{};
 const items:any[]=data.items||[];
 const movements:any[]=data.movements||[];
 const statusLabel=transfer.status==='completed'?'Tamamlandı':String(transfer.status||'Bilinmiyor');

 return <Stack spacing={2}>
  <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/stok-hareketleri')} sx={{alignSelf:'flex-start'}}>Stok Hareketlerine Dön</Button>

  <Card><CardContent>
   <Stack direction={{xs:'column',lg:'row'}} justifyContent="space-between" gap={2}>
    <Box>
     <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Inventory2OutlinedIcon color="primary"/>
      <Typography variant="h4" fontWeight={900} sx={{fontSize:{xs:24,sm:30}}}>Depo Transferi #{transfer.id}</Typography>
      <Chip label={statusLabel} color={transfer.status==='completed'?'success':'default'} size="small"/>
     </Stack>
     <Typography fontWeight={850} sx={{mt:1,fontSize:{xs:17,sm:20}}}>{transfer.source_name} → {transfer.target_name}</Typography>
     <Typography sx={{...labelSx,mt:.75}}>Transfer tarihi: {transfer.transfer_date||'-'} · Oluşturulma: {formatDateTime(transfer.created_at)}</Typography>
    </Box>
    <Button variant="outlined" startIcon={<TimelineIcon/>} onClick={()=>nav('/stok-hareketleri')}>Tüm Stok Hareketleri</Button>
   </Stack>
   <Divider sx={{my:2}}/>
   <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
    <Box><Typography sx={labelSx} fontWeight={700}>ÜRÜN SATIRI</Typography><Typography variant="h6" fontWeight={900}>{Number(transfer.item_count||0)}</Typography></Box>
    <Box><Typography sx={labelSx} fontWeight={700}>TOPLAM MİKTAR</Typography><Typography variant="h6" fontWeight={900}>{Number(transfer.total_quantity||0).toLocaleString('tr-TR')}</Typography></Box>
    <Box sx={{minWidth:220,flex:1}}><Typography sx={labelSx} fontWeight={700}>NOT</Typography><Typography sx={{fontSize:14,mt:.35}}>{transfer.note||'Not girilmemiş.'}</Typography></Box>
   </Stack>
  </CardContent></Card>

  <Card><CardContent>
   <Typography variant="h6" fontWeight={900} mb={1.5}>Transfer Edilen Ürünler</Typography>
   {items.length===0?<Alert severity="info">Bu transferde ürün satırı bulunmuyor.</Alert>:
    <ResponsiveTable rows={items} columns={itemCols} onRowClick={r=>nav(`/urunler/${r.product_id}`)}
     cardTitle={r=>r.product_name}
     cardSubtitle={r=>r.product_code||'Kodsuz ürün'}
     cardFields={[{label:'Miktar',value:r=>`${Number(r.quantity).toLocaleString('tr-TR')} ${r.unit||''}`}]}
     cardActions={[{label:'Ürün Detayı',color:'primary',onClick:r=>nav(`/urunler/${r.product_id}`)}]}/>} 
  </CardContent></Card>

  <Card><CardContent>
   <Typography variant="h6" fontWeight={900} mb={1.5}>Muhasebe ve Stok İzleri</Typography>
   {movements.length===0?<Alert severity="warning">Bu transfer için stok hareketi kaydı bulunamadı.</Alert>:
    <ResponsiveTable rows={movements} columns={movementCols} onRowClick={r=>nav(`/urunler/${r.product_id}`)}
     cardTitle={r=>r.product_name}
     cardSubtitle={r=>`${movementLabel(r.movement_type)} · ${r.warehouse_name||'Depo belirtilmemiş'}`}
     cardFields={[
      {label:'Miktar',value:r=>`${Number(r.quantity)>0?'+':''}${Number(r.quantity).toLocaleString('tr-TR')}`},
      {label:'Tarih',value:r=>r.movement_date||'-'},
      {label:'Not',value:r=>r.note||'-'},
     ]}
     cardActions={[{label:'Ürün Detayı',color:'primary',onClick:r=>nav(`/urunler/${r.product_id}`)}]}/>} 
  </CardContent></Card>
 </Stack>;
}
