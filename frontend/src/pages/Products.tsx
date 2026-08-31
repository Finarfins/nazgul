import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {useNavigate,useSearchParams} from 'react-router-dom';
import {Alert,Box,Button,Card,CardContent,Chip,Dialog,DialogActions,DialogContent,DialogTitle,IconButton,InputAdornment,MenuItem,Paper,Stack,TextField,Typography} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import DownloadIcon from '@mui/icons-material/Download';
import QrCode2Icon from '@mui/icons-material/QrCode2';
import LocalPrintshopIcon from '@mui/icons-material/LocalPrintshop';
import TuneIcon from '@mui/icons-material/Tune';
import SearchIcon from '@mui/icons-material/Search';
import Inventory2OutlinedIcon from '@mui/icons-material/Inventory2Outlined';
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircleOutline';
import PaidOutlinedIcon from '@mui/icons-material/PaidOutlined';
import {GridColDef} from '@mui/x-data-grid';
import ExcelImportDialog from '../components/ExcelImportDialog';
import ResponsiveTable from '../components/ResponsiveTable';
import ProductDialog from '../components/ProductDialog';
import ProductLabelDialog from '../components/ProductLabelDialog';
import {api,money,openAuthenticated,policyOverrideHeaders,policyOverrideRequired} from '../api';
import {useAuth} from '../AuthContext';
import {headerDarkActionVariant} from '../theme';

// Dashboard 'Kritik / Negatif Stok' karti ile ayni esik: stok <= kritik esik (negatif ve sifir dahil).
const isCriticalStock=(row:any)=>Number(row.stock)<=Number(row.critical_stock||row.min_stock||5);

export default function Products(){
 const navigate=useNavigate();
 const [searchParams,setSearchParams]=useSearchParams();
 const {user}=useAuth();
 const manager=user?.role==='admin'||user?.role==='yonetici';
 const [rows,setRows]=useState<any[]>([]);
 const [q,setQ]=useState('');
 const [sort,setSort]=useState('name_asc');
 const [warehouses,setWarehouses]=useState<any[]>([]);
 const [warehouseId,setWarehouseId]=useState<number|''>('');
 const [loading,setLoading]=useState(false);
 const [importOpen,setImportOpen]=useState(false);
 const [open,setOpen]=useState(false);
 const [edit,setEdit]=useState<number|null>(null);
 const [bulk,setBulk]=useState<'price'|'stock'|null>(null);
 // Etiket diyaloğu: tek satırdan da, mevcut filtre sonucunun tamamından da açılır.
 const [labelIds,setLabelIds]=useState<number[]|null>(null);
 const [labelName,setLabelName]=useState<string|undefined>();
 const [value,setValue]=useState(0);
 const [method,setMethod]=useState('percent');
 const [field,setField]=useState('sale_price');
 const [bulkBusy,setBulkBusy]=useState(false);
 const [bulkError,setBulkError]=useState('');
 const [needsOverride,setNeedsOverride]=useState(false);
 const [overrideReason,setOverrideReason]=useState('');
 // Dashboard 'Kritik / Negatif Stok' kisayolu: /urunler?critical=1
 const [criticalOnly,setCriticalOnly]=useState(false);
 const criticalParam=searchParams.get('critical')==='1';

 const load=()=>{
  setLoading(true);
  const query=q.trim();
  api.get(query?'/search/parts':'/products',{params:query?{q:query,sort,limit:300}:{q,sort}})
   .then(r=>setRows(query?r.data.items:r.data))
   .finally(()=>setLoading(false));
 };

 useEffect(()=>{
  api.get('/warehouses').then(r=>{
   setWarehouses(r.data);
   if(r.data[0])setWarehouseId(r.data[0].id);
  });
 },[]);

 useEffect(()=>{
  const t=setTimeout(load,200);
  return()=>clearTimeout(t);
 },[q,sort]);
 useEffect(()=>{
  if(!criticalParam)return;
  setCriticalOnly(true);
  const next=new URLSearchParams(searchParams);next.delete('critical');
  setSearchParams(next,{replace:true});
 },[criticalParam,searchParams,setSearchParams]);

 const cols=useMemo<GridColDef[]>(()=>[
  {field:'id',headerName:'ID',width:70},
  {field:'name',headerName:'Ürün / Hizmet Adı',flex:1,minWidth:300,renderCell:p=><Stack direction="row" spacing={1} alignItems="center" width="100%">
   <Box minWidth={0} flex={1}><Typography fontWeight={800} lineHeight={1.3} noWrap title={String(p.value||'')}>{p.value}</Typography><Typography variant="caption" color="text.secondary" display="block" lineHeight={1.35} mt={.35} noWrap>{p.row.product_code||p.row.oem_number||'Kod bilgisi yok'}</Typography></Box>
   {String(p.row.match_type||'').endsWith('_ocr')&&<Chip label="Benzer / OCR eşleşmesi" size="small" color="warning"/>}
  </Stack>},
  {field:'product_code',headerName:'Kod',width:130},
  {field:'oem_number',headerName:'OEM',width:150},
  {field:'location',headerName:'Raf',width:100},
  {field:'purchase_price',headerName:'Alış',width:125,valueFormatter:v=>money(v)},
  {field:'sale_price',headerName:'Satış',width:125,valueFormatter:v=>money(v)},
  {field:'stock',headerName:'Stok',width:90},
  {field:'unit',headerName:'Birim',width:80},
  {field:'outputs',headerName:'',width:135,sortable:false,renderCell:p=><>
   <IconButton size="small" title="Düzenle" onClick={e=>{e.stopPropagation();setEdit(p.row.id);setOpen(true);}}><EditIcon fontSize="small"/></IconButton>
   <IconButton size="small" title="QR" onClick={e=>{e.stopPropagation();openAuthenticated(`/products/${p.row.id}/qr.png`);}}><QrCode2Icon fontSize="small"/></IconButton>
   <IconButton size="small" title="Etiket" onClick={e=>{e.stopPropagation();setLabelName(p.row.name);setLabelIds([p.row.id]);}}><LocalPrintshopIcon fontSize="small"/></IconButton>
  </>},
 ],[]);

 const resetBulkPolicy=()=>{
  setBulkError('');
  setNeedsOverride(false);
  setOverrideReason('');
 };

 const openBulk=(kind:'price'|'stock')=>{
  resetBulkPolicy();
  setValue(0);
  setBulk(kind);
  setMethod(kind==='price'?'percent':'add');
 };

 const closeBulk=()=>{
  if(bulkBusy)return;
  setBulk(null);
  resetBulkPolicy();
 };

 const changeBulkInput=(setter:(value:any)=>void,next:any)=>{
  setter(next);
  setNeedsOverride(false);
  setOverrideReason('');
  setBulkError('');
 };

 const applyBulk=async()=>{
  try{
   setBulkBusy(true);
   setBulkError('');
   const headers=overrideReason.trim()?policyOverrideHeaders(overrideReason):undefined;
   if(bulk==='price'){
    await api.post('/products/bulk-price',{field,method,value},{headers});
   }else{
    if(!warehouseId)throw new Error('Depo seçin.');
    await api.post('/products/bulk-stock',{
     method:method==='set'?'set':'add',
     value,
     movement_date:new Date().toISOString().slice(0,10),
     note:'Toplu işlem',
     warehouse_id:warehouseId,
    },{headers});
   }
   setBulk(null);
   resetBulkPolicy();
   load();
  }catch(e:any){
   const detail=e.response?.data?.detail||e.message||'Toplu işlem uygulanamadı.';
   setBulkError(detail);
   if(policyOverrideRequired(e))setNeedsOverride(true);
  }finally{
   setBulkBusy(false);
  }
 };

 const visibleRows=useMemo(()=>criticalOnly?rows.filter(isCriticalStock):rows,[rows,criticalOnly]);

 const stockSummary=useMemo(()=>({
  critical:rows.filter(row=>Number(row.stock)>0&&Number(row.stock)<=Number(row.critical_stock||row.min_stock||5)).length,
  negative:rows.filter(row=>Number(row.stock)<0).length,
  value:rows.reduce((sum,row)=>sum+Math.max(0,Number(row.stock||0))*Number(row.sale_price||0),0),
 }),[rows]);

 return <Stack spacing={2}>
  <Paper sx={{position:'relative',overflow:'hidden',p:{xs:2.25,sm:3},color:'#fff',border:0,borderRadius:3.5,background:'linear-gradient(125deg, #071e41 0%, #0b3567 62%, #164a8a 100%)',boxShadow:'0 18px 48px rgba(7,30,65,.18)','&:after':{content:'""',position:'absolute',width:280,height:280,right:-75,top:-145,borderRadius:'50%',background:'rgba(255,255,255,.07)'}}}>
   <Stack direction={{xs:'column',md:'row'}} justifyContent="space-between" alignItems={{md:'center'}} gap={2} position="relative" zIndex={1}>
    <Box><Typography variant="caption" fontWeight={800} letterSpacing=".12em" sx={{color:'rgba(255,255,255,.65)'}}>YEDEK PARÇA VE ENVANTER</Typography><Typography variant="h4" fontWeight={900} sx={{fontSize:{xs:26,sm:32}}}>Ürünler / Stok</Typography><Typography sx={{color:'rgba(255,255,255,.72)',mt:.5}}>OEM, barkod, raf ve depo stoklarını tek merkezden yönetin.</Typography></Box>
    <Stack direction="row" spacing={1} flexWrap="wrap">
      <Button variant={headerDarkActionVariant} startIcon={<UploadFileIcon/>} onClick={()=>setImportOpen(true)}>Excel&apos;den Aktar</Button>
      <Button variant="contained" startIcon={<AddIcon/>} onClick={()=>{setEdit(null);setOpen(true)}} sx={{bgcolor:'#fff',color:'#0b3567','&:hover':{bgcolor:'#eaf2fb'}}}>Yeni Ürün</Button>
    </Stack>
  </Stack>
 </Paper>

  <Stack direction="row" gap={1.4} flexWrap="wrap">
   {[
    ['Toplam Ürün',rows.length,'Görüntülenen kayıt',<Inventory2OutlinedIcon key="products"/>,'primary.main'],
    ['Stok Değeri',money(stockSummary.value),'Satış fiyatı üzerinden',<PaidOutlinedIcon key="value"/>,'info.main'],
    ['Kritik Stok',stockSummary.critical,'Kontrol edilmeli',<WarningAmberOutlinedIcon key="critical"/>,'warning.main'],
    ['Negatif Stok',stockSummary.negative,'Acil düzeltme',<RemoveCircleOutlineIcon key="negative"/>,'error.main'],
   ].map(([label,value,detail,icon,color]:any)=><Card key={label} variant="outlined" sx={{flex:'1 1 190px',borderTop:'3px solid',borderTopColor:color,boxShadow:'0 8px 24px rgba(28,38,32,.045)'}}><CardContent><Stack direction="row" justifyContent="space-between" gap={1}><Box><Typography variant="caption" fontWeight={800} color="text.secondary">{String(label).toUpperCase()}</Typography><Typography fontSize={21} fontWeight={900} mt={.25}>{value}</Typography><Typography variant="caption" color="text.secondary">{detail}</Typography></Box><Box color={color}>{icon}</Box></Stack></CardContent></Card>)}
  </Stack>

  <Paper variant="outlined" sx={{p:{xs:1.5,sm:2},borderRadius:3}}>
  <Stack direction={{xs:'column',md:'row'}} spacing={1.2}>
   <TextField fullWidth size="small" label="Ürün, kod veya barkod ara" value={q} onChange={e=>setQ(e.target.value)} InputProps={{startAdornment:<InputAdornment position="start"><SearchIcon color="action"/></InputAdornment>}}/>
   <TextField select size="small" label="Sıralama" value={sort} onChange={e=>setSort(e.target.value)} sx={{minWidth:{sm:220}}}>
    <MenuItem value="name_asc">İsim A → Z</MenuItem>
    <MenuItem value="stock_asc">Stok düşük → yüksek</MenuItem>
    <MenuItem value="stock_desc">Stok yüksek → düşük</MenuItem>
    <MenuItem value="price_asc">Satış fiyatı düşük → yüksek</MenuItem>
    <MenuItem value="price_desc">Satış fiyatı yüksek → düşük</MenuItem>
    <MenuItem value="purchase_asc">Alış fiyatı düşük → yüksek</MenuItem>
    <MenuItem value="purchase_desc">Alış fiyatı yüksek → düşük</MenuItem>
   </TextField>
  </Stack>
  <Stack direction="row" spacing={.5} flexWrap="wrap" mt={1.5} pt={1.5} borderTop="1px solid" borderColor="divider">
   <Button size="small" startIcon={<DownloadIcon/>} onClick={()=>openAuthenticated('/exports/products.xlsx','urunler.xlsx')}>Excel</Button>
   <Button size="small" startIcon={<LocalPrintshopIcon/>} disabled={!visibleRows.length} onClick={()=>{setLabelName(undefined);setLabelIds(visibleRows.map(r=>Number(r.id)))}}>Etiket ({visibleRows.length})</Button>
   <Button size="small" startIcon={<TuneIcon/>} onClick={()=>openBulk('price')}>Toplu Fiyat</Button>
   <Button size="small" startIcon={<TuneIcon/>} onClick={()=>openBulk('stock')}>Toplu Stok</Button>
   {criticalOnly&&<Chip size="small" color="warning" icon={<WarningAmberOutlinedIcon/>} label={`Kritik / negatif stok (${visibleRows.length})`} onDelete={()=>setCriticalOnly(false)}/>}
  </Stack>
  </Paper>

  <ResponsiveTable
   rows={visibleRows}
   columns={cols}
   loading={loading}
   desktopRowHeight={70}
   onRowClick={r=>navigate(`/urunler/${r.id}`)}
   cardTitle={r=>r.name}
   cardSubtitle={r=>`${r.product_code||r.barcode||'Kod yok'}${String(r.match_type||'').endsWith('_ocr')?' · Benzer / OCR eşleşmesi':''}`}
   cardFields={[
    {label:'Stok',value:r=>`${r.stock} ${r.unit}`},
    {label:'Alış',value:r=>money(r.purchase_price)},
    {label:'Satış',value:r=>money(r.sale_price)},
   ]}
  />

  <ExcelImportDialog open={importOpen} kind="products" onClose={()=>setImportOpen(false)} onDone={load}/>
  <ProductDialog open={open} id={edit} onClose={()=>setOpen(false)} onSaved={load}/>
  <ProductLabelDialog open={labelIds!==null} productIds={labelIds||[]} productName={labelName}
   onClose={()=>setLabelIds(null)}/>

  <Dialog open={!!bulk} onClose={closeBulk} fullWidth maxWidth="xs">
   <DialogTitle>{bulk==='price'?'Toplu Fiyat':'Toplu Stok'}</DialogTitle>
   <DialogContent>
    <Stack spacing={2} mt={1}>
     {bulkError&&<Alert severity="error">{bulkError}</Alert>}
     {needsOverride&&manager&&bulk==='stock'&&<TextField
      label="Yönetici onay gerekçesi"
      value={overrideReason}
      onChange={e=>setOverrideReason(e.target.value)}
      helperText="En az 5 karakter. Eksi stok istisnası denetim kaydına yazılır."
      inputProps={{maxLength:500}}
      multiline
      minRows={2}
      required
     />}
     {bulk==='stock'&&<TextField select label="Depo" value={warehouseId} onChange={e=>changeBulkInput(setWarehouseId,Number(e.target.value))}>
      {warehouses.map(w=><MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
     </TextField>}
     {bulk==='price'&&<TextField select label="Fiyat alanı" value={field} onChange={e=>changeBulkInput(setField,e.target.value)}>
      <MenuItem value="sale_price">Satış fiyatı</MenuItem>
      <MenuItem value="purchase_price">Alış fiyatı</MenuItem>
     </TextField>}
     <TextField select label="Yöntem" value={method} onChange={e=>changeBulkInput(setMethod,e.target.value)}>
      {bulk==='price'&&<MenuItem value="percent">Yüzde artır / azalt</MenuItem>}
      <MenuItem value={bulk==='stock'?'add':'fixed'}>Sabit tutar ekle / çıkar</MenuItem>
      <MenuItem value="set">Doğrudan belirle</MenuItem>
     </TextField>
     <TextField type="number" label="Değer" value={value} onChange={e=>changeBulkInput(setValue,Number(e.target.value))}/>
    </Stack>
   </DialogContent>
   <DialogActions>
    <Button onClick={closeBulk} disabled={bulkBusy}>Vazgeç</Button>
    <Button variant="contained" onClick={applyBulk} disabled={bulkBusy||(needsOverride&&manager&&overrideReason.trim().length<5)}>
     {bulkBusy?'Uygulanıyor...':'Uygula'}
    </Button>
   </DialogActions>
  </Dialog>
 </Stack>;
}
