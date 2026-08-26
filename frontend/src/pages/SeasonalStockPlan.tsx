import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {
 Alert,Box,Card,CardContent,Chip,CircularProgress,MenuItem,Stack,Table,TableBody,
 TableCell,TableContainer,TableHead,TableRow,TextField,ToggleButton,
 ToggleButtonGroup,Typography,
} from '@mui/material';
import EventRepeatIcon from '@mui/icons-material/EventRepeat';

import {api,errorDetail} from '../api';

type PlanItem={
 product_id:number;product_name:string;product_code:string|null;
 historical_demand:string|number;current_stock:string|number;suggested_quantity:string|number;
};
type PlanResponse={
 historical_year:number;months:number[];formula:string;
 total_historical_demand:string|number;total_suggested_quantity:string|number;items:PlanItem[];
};
type SortKey='demand_desc'|'suggestion_desc'|'stock_asc'|'name_asc';

const MONTHS=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const SEASONS=[
 {id:'planting',label:'Ekim / dikim',months:[2,3,4]},
 {id:'pre_harvest',label:'Hasat öncesi',months:[6,7,8]},
 {id:'autumn',label:'Sonbahar',months:[9,10,11]},
] as const;
const qty=(value:string|number)=>new Intl.NumberFormat('tr-TR',{
 minimumFractionDigits:0,maximumFractionDigits:4,
}).format(Number(value||0));

export default function SeasonalStockPlan(){
 const [months,setMonths]=useState<number[]>([6,7,8]);
 const [season,setSeason]=useState('pre_harvest');
 const [sort,setSort]=useState<SortKey>('demand_desc');
 const [data,setData]=useState<PlanResponse|null>(null);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');

 useEffect(()=>{
  let active=true;
  setLoading(true);setError('');
  api.get('/analytics/seasonal-plan',{params:{months:months.join(',')}})
   .then(response=>{if(active)setData(response.data)})
   .catch(err=>{if(active){setData(null);setError(errorDetail(err,'Sezonsal stok planı yüklenemedi.'))}})
   .finally(()=>{if(active)setLoading(false)});
  return()=>{active=false};
 },[months]);

 const chooseSeason=(id:string)=>{
  setSeason(id);
  const selected=SEASONS.find(item=>item.id===id);
  if(selected)setMonths([...selected.months]);
 };
 const toggleMonths=(_event:React.MouseEvent<HTMLElement>,next:number[])=>{
  if(next.length===0)return;
  setSeason('custom');setMonths([...next].sort((a,b)=>a-b));
 };
 const items=useMemo(()=>{
  const next=[...(data?.items||[])];
  next.sort((a,b)=>{
   if(sort==='name_asc')return a.product_name.localeCompare(b.product_name,'tr');
   if(sort==='stock_asc')return Number(a.current_stock)-Number(b.current_stock);
   if(sort==='suggestion_desc')return Number(b.suggested_quantity)-Number(a.suggested_quantity);
   return Number(b.historical_demand)-Number(a.historical_demand);
  });
  return next;
 },[data,sort]);

 return <Stack spacing={2}>
  <Stack direction={{xs:'column',md:'row'}} justifyContent="space-between" gap={1.5}>
   <Box>
    <Stack direction="row" spacing={1} alignItems="center"><EventRepeatIcon color="primary"/><Typography variant="h4">Sezonsal Stok Planı</Typography></Stack>
    <Typography color="text.secondary">Geçen yılın seçili aylarındaki satış talebini bugünkü stokla karşılaştırın.</Typography>
   </Box>
   <Chip color="primary" variant="outlined" label={data?`Referans yıl: ${data.historical_year}`:'Referans yıl yükleniyor'}/>
  </Stack>

  <Card><CardContent><Stack spacing={2}>
   <Stack direction={{xs:'column',md:'row'}} spacing={1.5}>
    <TextField select label="Sezon" value={season} onChange={event=>chooseSeason(event.target.value)} sx={{minWidth:200}}>
     {SEASONS.map(item=><MenuItem key={item.id} value={item.id}>{item.label}</MenuItem>)}
     <MenuItem value="custom">Özel ay seçimi</MenuItem>
    </TextField>
    <TextField select label="Sıralama" value={sort} onChange={event=>setSort(event.target.value as SortKey)} sx={{minWidth:220}}>
     <MenuItem value="demand_desc">Talep: yüksekten düşüğe</MenuItem><MenuItem value="suggestion_desc">Öneri: yüksekten düşüğe</MenuItem>
     <MenuItem value="stock_asc">Stok: düşükten yükseğe</MenuItem><MenuItem value="name_asc">Ürün adı: A-Z</MenuItem>
    </TextField>
   </Stack>
   <ToggleButtonGroup value={months} onChange={toggleMonths} size="small" sx={{display:'flex',flexWrap:'wrap',gap:1}}>
    {MONTHS.map((label,index)=><ToggleButton key={label} value={index+1} sx={{m:'0!important',border:'1px solid!important',borderColor:'divider!important',borderRadius:'8px!important'}}>{label}</ToggleButton>)}
   </ToggleButtonGroup>
   <Typography variant="caption" color="text.secondary">Formül: geçen yıl talebi − güncel stok; sonuç sıfırın altındaysa öneri 0 olur.</Typography>
  </Stack></CardContent></Card>

  {error&&<Alert severity="error">{error}</Alert>}
  {data&&<Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
   <Chip label={`Ürün: ${data.items.length}`}/><Chip color="info" label={`Geçmiş talep: ${qty(data.total_historical_demand)}`}/>
   <Chip color="success" label={`Önerilen tamamla: ${qty(data.total_suggested_quantity)}`}/>
  </Stack>}

  <Card>
   {loading&&!data?<Box py={7} display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>
    :!error&&items.length===0?<Alert severity="info" sx={{m:2}}>Seçilen aylarda geçen yıldan satış kaydı bulunamadı.</Alert>
    :<TableContainer><Table stickyHeader>
     <TableHead><TableRow><TableCell>Ürün</TableCell><TableCell align="right">Geçen yıl talebi</TableCell><TableCell align="right">Güncel stok</TableCell><TableCell align="right">Önerilen tamamlama</TableCell></TableRow></TableHead>
     <TableBody>{items.map(item=><TableRow key={item.product_id} hover>
      <TableCell><Typography fontWeight={650}>{item.product_name}</Typography><Typography variant="caption" color="text.secondary">{item.product_code||'Ürün kodu yok'}</Typography></TableCell>
      <TableCell align="right">{qty(item.historical_demand)}</TableCell><TableCell align="right">{qty(item.current_stock)}</TableCell>
      <TableCell align="right"><Chip size="small" color={Number(item.suggested_quantity)>0?'warning':'success'} label={qty(item.suggested_quantity)}/></TableCell>
     </TableRow>)}</TableBody>
    </Table></TableContainer>}
  </Card>
 </Stack>;
}
