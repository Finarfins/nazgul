import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Box,Button,Card,CardContent,Chip,CircularProgress,Divider,Stack,Typography} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import DownloadIcon from '@mui/icons-material/Download';
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined';
import PrintIcon from '@mui/icons-material/Print';
import TimelineIcon from '@mui/icons-material/Timeline';
import {useNavigate,useParams} from 'react-router-dom';
import {GridColDef} from '@mui/x-data-grid';
import ResponsiveTable from '../components/ResponsiveTable';
import {api,errorDetail} from '../api';

const labelSx={fontSize:13.5,lineHeight:1.4,color:'text.secondary'} as const;
const csvCell=(value:unknown)=>{
 const text=String(value??'');
 const safe=typeof value==='string'&&/^[\t\r\n ]*[=+\-@]/.test(text)?`'${text}`:text;
 return `"${safe.replace(/"/g,'""')}"`;
};

export function inventoryCountDetailCsv(count:any,items:any[]):string{
 const metadata=[
  ['Fiziksel Stok Sayımı',`#${count?.id??''}`],
  ['Depo',count?.warehouse_name??''],
  ['Sayım Tarihi',count?.count_date??''],
  ['Açıklama',count?.note??''],
  ['Sayılan Ürün',count?.item_count??items.length],
  ['Değişen Ürün',count?.changed_count??0],
  ['Toplam Mutlak Fark',count?.total_absolute_variance??0],
 ];
 const lines=[
  ...metadata,
  [],
  ['Ürün ID','Ürün','Kod','Birim','Sistem','Sayılan','Fark','Sayım Sonrası Güncel','Kritik Stok'],
  ...items.map(item=>[
   item.product_id,item.product_name,item.product_code,item.unit,item.system_quantity,
   item.counted_quantity,item.variance,item.current_quantity,item.critical_stock,
  ]),
 ];
 return `\uFEFF${lines.map(line=>line.map(csvCell).join(';')).join('\r\n')}`;
}

export default function InventoryCountDetail(){
 const {id}=useParams();
 const countId=Number(id);
 const nav=useNavigate();
 const [data,setData]=useState<any|null>(null);
 const [error,setError]=useState('');

 useEffect(()=>{
  setData(null);setError('');
  api.get(`/warehouses/counts/${countId}`)
   .then(r=>setData(r.data))
   .catch(e=>setError(errorDetail(e,'Stok sayımı yüklenemedi.')));
 },[countId]);

 const columns=useMemo<GridColDef[]>(()=>[
  {field:'product_id',headerName:'ID',width:70},
  {field:'product_name',headerName:'Ürün',flex:1,minWidth:220},
  {field:'product_code',headerName:'Kod',width:120,valueFormatter:v=>v||'-'},
  {field:'system_quantity',headerName:'Sistem',width:110},
  {field:'counted_quantity',headerName:'Sayılan',width:110},
  {field:'variance',headerName:'Fark',width:100,renderCell:p=>{const value=Number(p.value);return <Typography fontWeight={900} color={value<0?'error.main':value>0?'success.main':'text.primary'}>{value>0?'+':''}{value.toLocaleString('tr-TR')}</Typography>}},
  {field:'current_quantity',headerName:'Güncel',width:110},
  {field:'critical_stock',headerName:'Kritik',width:100},
  {field:'unit',headerName:'Birim',width:80,valueFormatter:v=>v||'-'},
 ],[]);

 if(error)return <Stack spacing={2}>
  <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/stok-sayimlari')} sx={{alignSelf:'flex-start'}}>Tüm Sayımlara Dön</Button>
  <Alert severity="error">{error}</Alert>
 </Stack>;
 if(!data)return <Box textAlign="center" p={5}><CircularProgress aria-label="Stok sayımı yükleniyor"/></Box>;

 const count=data.count||{};
 const items:any[]=data.items||[];
 const exportCsv=()=>{
  const blob=new Blob([inventoryCountDetailCsv(count,items)],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  link.href=url;
  link.download=`stok-sayimi-${count.id||countId}-${count.count_date||'belge'}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
 };

 return <Stack spacing={2}>
  <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/stok-sayimlari',{state:{warehouseId:count.warehouse_id}})} sx={{alignSelf:'flex-start',display:{print:'none'}}}>Tüm Sayımlara Dön</Button>
  <Card><CardContent>
   <Stack direction={{xs:'column',lg:'row'}} justifyContent="space-between" gap={2}>
    <Box>
     <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <FactCheckOutlinedIcon color="primary"/>
      <Typography variant="h4" fontWeight={900} sx={{fontSize:{xs:24,sm:30}}}>Fiziksel Stok Sayımı #{count.id}</Typography>
      <Chip label={`${count.changed_count||0} stok değişikliği`} color={Number(count.changed_count)>0?'warning':'success'} size="small"/>
     </Stack>
     <Typography fontWeight={850} sx={{mt:1,fontSize:{xs:17,sm:20}}}>{count.warehouse_name||'Depo'}</Typography>
     <Typography sx={{...labelSx,mt:.75}}>Sayım tarihi: {count.count_date||'-'}</Typography>
    </Box>
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{display:{print:'none'}}}>
     <Button variant="outlined" startIcon={<DownloadIcon/>} onClick={exportCsv}>CSV İndir</Button>
     <Button variant="outlined" startIcon={<PrintIcon/>} onClick={()=>window.print()}>Yazdır</Button>
     <Button variant="outlined" onClick={()=>nav(`/depolar/${count.warehouse_id}`)}>Depoya Git</Button>
     <Button variant="outlined" startIcon={<TimelineIcon/>} onClick={()=>nav('/stok-hareketleri',{state:{warehouseId:count.warehouse_id}})}>Stok Hareketleri</Button>
    </Stack>
   </Stack>
   <Divider sx={{my:2}}/>
   <Stack direction="row" spacing={3} flexWrap="wrap" useFlexGap>
    <Box><Typography sx={labelSx} fontWeight={700}>SAYILAN ÜRÜN</Typography><Typography variant="h6" fontWeight={900}>{Number(count.item_count||0)}</Typography></Box>
    <Box><Typography sx={labelSx} fontWeight={700}>DEĞİŞEN ÜRÜN</Typography><Typography variant="h6" fontWeight={900}>{Number(count.changed_count||0)}</Typography></Box>
    <Box><Typography sx={labelSx} fontWeight={700}>TOPLAM MUTLAK FARK</Typography><Typography variant="h6" fontWeight={900}>{Number(count.total_absolute_variance||0).toLocaleString('tr-TR')}</Typography></Box>
    <Box sx={{minWidth:220,flex:1}}><Typography sx={labelSx} fontWeight={700}>AÇIKLAMA</Typography><Typography sx={{fontSize:14,mt:.35}}>{count.note||'Açıklama girilmemiş.'}</Typography></Box>
   </Stack>
  </CardContent></Card>

  <Card><CardContent>
   <Typography variant="h6" fontWeight={900} mb={1.5}>Sayım Satırları</Typography>
   {items.length===0?<Alert severity="info">Bu sayımda ürün satırı bulunmuyor.</Alert>:
    <ResponsiveTable rows={items} columns={columns} onRowClick={row=>nav(`/urunler/${row.product_id}`)}
     cardTitle={row=>row.product_name}
     cardSubtitle={row=>`${row.product_code||'Kodsuz'} · ${row.unit||''}`}
     cardFields={[
      {label:'Sistem',value:row=>row.system_quantity},
      {label:'Sayılan',value:row=>row.counted_quantity},
      {label:'Fark',value:row=>`${Number(row.variance)>0?'+':''}${row.variance}`},
      {label:'Güncel',value:row=>row.current_quantity},
      {label:'Kritik',value:row=>row.critical_stock},
     ]}
     cardActions={[{label:'Ürün Detayı',color:'primary',onClick:row=>nav(`/urunler/${row.product_id}`)}]}/>} 
  </CardContent></Card>
 </Stack>;
}
