import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Box,Button,Chip,Dialog,DialogActions,DialogContent,DialogTitle,FormControlLabel,MenuItem,Stack,Switch,Tab,Tabs,TextField,Typography} from '@mui/material';
import AddBusinessIcon from '@mui/icons-material/AddBusiness';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import TuneIcon from '@mui/icons-material/Tune';
import {useNavigate} from 'react-router-dom';
import {GridColDef} from '@mui/x-data-grid';
import ResponsiveTable from '../components/ResponsiveTable';
import TransferDialog from '../components/TransferDialog';
import {api,errorDetail,money,policyOverrideHeaders,policyOverrideRequired} from '../api';
import {useAuth} from '../AuthContext';
import {stockCondition} from '../utils/stock';

export default function Warehouses(){
 const {user}=useAuth();
 const nav=useNavigate();
 const manager=user?.role==='admin'||user?.role==='yonetici';
 const [tab,setTab]=useState(0);
 const [warehouses,setWarehouses]=useState<any[]>([]);
 const [stocks,setStocks]=useState<any[]>([]);
 const [transfers,setTransfers]=useState<any[]>([]);
 const [replenishment,setReplenishment]=useState<any[]>([]);
 const [replenishmentError,setReplenishmentError]=useState('');
 const [selected,setSelected]=useState<number|''>('');
 const [q,setQ]=useState('');
 const [criticalOnly,setCriticalOnly]=useState(false);
 const [newOpen,setNewOpen]=useState(false);
 const [transferOpen,setTransferOpen]=useState(false);
 const [transferProduct,setTransferProduct]=useState<any|null>(null);
 const [transferSource,setTransferSource]=useState<number|null>(null);
 const [name,setName]=useState('');
 const [code,setCode]=useState('');
 const [newError,setNewError]=useState('');
 const [adjustOpen,setAdjustOpen]=useState(false);
 const [adjustRow,setAdjustRow]=useState<any|null>(null);
 const [adjustMode,setAdjustMode]=useState<'set'|'add'|'remove'>('set');
 const [adjustQuantity,setAdjustQuantity]=useState<number>(0);
 const [adjustCritical,setAdjustCritical]=useState<number>(0);
 const [adjustDate,setAdjustDate]=useState(new Date().toISOString().slice(0,10));
 const [adjustNote,setAdjustNote]=useState('Depo sayımı');
 const [adjustError,setAdjustError]=useState('');
 const [adjustBusy,setAdjustBusy]=useState(false);
 const [adjustNeedsOverride,setAdjustNeedsOverride]=useState(false);
 const [adjustOverrideReason,setAdjustOverrideReason]=useState('');

 const loadWarehouses=()=>api.get('/warehouses').then(r=>{
  setWarehouses(r.data);
  if(!selected&&r.data[0])setSelected(r.data[0].id);
  return r.data;
 });
 const loadStocks=()=>selected?api.get('/warehouses/stock',{params:{warehouse_id:selected,q,critical_only:criticalOnly||undefined}}).then(r=>setStocks(r.data)):Promise.resolve();
 const loadTransfers=()=>api.get('/warehouses/transfers').then(r=>setTransfers(r.data));
 const loadReplenishment=()=>{setReplenishmentError('');
  api.get('/warehouses/replenishment').then(r=>setReplenishment(r.data)).catch(e=>setReplenishmentError(errorDetail(e,'İkmal listesi yüklenemedi.')));};

 useEffect(()=>{
  loadWarehouses();
  loadTransfers();
  loadReplenishment();
 },[]);

 useEffect(()=>{
  const t=setTimeout(loadStocks,150);
  return()=>clearTimeout(t);
 },[selected,q,criticalOnly]);

 const whCols=useMemo<GridColDef[]>(()=>[
  {field:'id',headerName:'ID',width:70},
  {field:'name',headerName:'Depo',flex:1,minWidth:220},
  {field:'code',headerName:'Kod',width:110},
  {field:'branch_name',headerName:'Şube',width:160},
  {field:'product_count',headerName:'Ürün',width:90},
  {field:'total_quantity',headerName:'Toplam Miktar',width:130},
  {field:'is_default',headerName:'Varsayılan',width:110,valueFormatter:v=>v?'Evet':'Hayır'},
 ],[]);
 const stockCols=useMemo<GridColDef[]>(()=>[
  {field:'product_id',headerName:'ID',width:70},
  {field:'name',headerName:'Ürün',flex:1,minWidth:220},
  {field:'product_code',headerName:'Kod',width:120},
  {field:'oem_number',headerName:'OEM',width:130,valueFormatter:v=>v||'-'},
  {field:'location',headerName:'Raf',width:100,valueFormatter:v=>v||'-'},
  {field:'quantity',headerName:'Stok',width:100},
  {field:'unit',headerName:'Birim',width:75},
  {field:'critical_stock',headerName:'Kritik',width:95},
  {field:'condition',headerName:'Durum',width:120,sortable:false,renderCell:p=>{
   const c=stockCondition(p.row.quantity,p.row.critical_stock);
   return <Chip size="small" color={c.color} label={c.label}/>;
  }},
  {field:'actions',headerName:'İşlem',width:120,sortable:false,filterable:false,renderCell:p=><Button size="small" startIcon={<TuneIcon/>} onClick={e=>{e.stopPropagation();openAdjustment(p.row);}}>Sayım</Button>},
 ],[]);
 const transferCols=useMemo<GridColDef[]>(()=>[
  {field:'id',headerName:'No',width:70},
  {field:'transfer_date',headerName:'Tarih',width:120},
  {field:'source_name',headerName:'Kaynak',flex:1,minWidth:170},
  {field:'target_name',headerName:'Hedef',flex:1,minWidth:170},
  {field:'item_count',headerName:'Kalem',width:80},
  {field:'total_quantity',headerName:'Miktar',width:100},
  {field:'note',headerName:'Not',width:220},
 ],[]);
 const replenishmentCols=useMemo<GridColDef[]>(()=>[
  {field:'product_name',headerName:'Ürün',flex:1,minWidth:200},
  {field:'warehouse_name',headerName:'Depo',width:150},
  {field:'quantity',headerName:'Stok',width:90},
  {field:'critical_stock',headerName:'Kritik',width:90},
  {field:'shortage',headerName:'Eksik',width:90,valueGetter:(_v,row)=>Number(Math.max(0,Number(row.critical_stock||0)-Number(row.quantity||0)).toFixed(4))},
  {field:'condition',headerName:'Durum',width:120,sortable:false,renderCell:p=>{
   const c=stockCondition(p.row.quantity,p.row.critical_stock);
   return <Chip size="small" color={c.color} label={c.label}/>;
  }},
  {field:'last_supplier_name',headerName:'Son Tedarikçi',width:160,valueFormatter:v=>v||'-'},
  {field:'last_purchase_date',headerName:'Son Alış',width:120,valueFormatter:v=>v||'-'},
  {field:'last_purchase_price',headerName:'Son Alış Fiyatı',width:140,valueFormatter:v=>v==null?'-':money(v)},
 ],[]);

 const createWarehouse=async()=>{
  try{
   setNewError('');
   await api.post('/warehouses',{name,code:code||null,is_default:warehouses.length===0});
   setNewOpen(false);
   setName('');
   setCode('');
   await loadWarehouses();
  }catch(e:any){
   setNewError(errorDetail(e,'Depo oluşturulamadı.'));
  }
 };

 const openAdjustment=(row:any)=>{
  setAdjustRow(row);
  setAdjustMode('set');
  setAdjustQuantity(Number(row.quantity||0));
  setAdjustCritical(Number(row.critical_stock||0));
  setAdjustDate(new Date().toISOString().slice(0,10));
  setAdjustNote('Depo sayımı');
  setAdjustError('');
  setAdjustNeedsOverride(false);
  setAdjustOverrideReason('');
  setAdjustOpen(true);
 };

 const saveAdjustment=async()=>{
  if(!selected||!adjustRow)return;
  try{
   setAdjustBusy(true);
   setAdjustError('');
   const value=Number(adjustQuantity);
   if(!Number.isFinite(value)||value<0)throw new Error('Miktar sıfır veya daha büyük olmalıdır.');
   const headers=adjustOverrideReason.trim()?policyOverrideHeaders(adjustOverrideReason):undefined;
   await api.post(`/products/${adjustRow.product_id}/stock`,{
    mode:adjustMode,
    warehouse_id:selected,
    quantity:value,
    movement_date:adjustDate,
    note:adjustNote||null,
   },{headers});
   await api.post('/warehouses/critical-stock',{
    product_id:adjustRow.product_id,
    warehouse_id:selected,
    critical_stock:Number(adjustCritical)||0,
   });
   setAdjustOpen(false);
   await Promise.all([loadStocks(),loadWarehouses(),Promise.resolve(loadReplenishment())]);
  }catch(e:any){
   const detail=e.message&&!e.response?e.message:errorDetail(e,'Stok düzeltmesi kaydedilemedi.');
   setAdjustError(detail);
   if(policyOverrideRequired(e))setAdjustNeedsOverride(true);
  }finally{setAdjustBusy(false);}
 };

 const openTransferFrom=(source:number|null,product:any|null)=>{
  setTransferSource(source);
  setTransferProduct(product);
  setTransferOpen(true);
 };

 return <Stack spacing={2}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1}>
   <Typography variant="h4" fontWeight={900}>Depolar</Typography>
   <Stack direction="row" spacing={1}>
    <Button startIcon={<SwapHorizIcon/>} onClick={()=>openTransferFrom(typeof selected==='number'?selected:null,null)}>Transfer</Button>
    <Button variant="contained" startIcon={<AddBusinessIcon/>} onClick={()=>{setNewError('');setNewOpen(true);}}>Yeni Depo</Button>
   </Stack>
  </Stack>

  <Tabs value={tab} onChange={(_,v)=>setTab(v)} variant="scrollable" scrollButtons="auto">
   <Tab label="Depolar"/>
   <Tab label="Depo Stokları"/>
   <Tab label={`İkmal Listesi${replenishment.length?` (${replenishment.length})`:''}`}/>
   <Tab label="Transferler"/>
  </Tabs>

  {tab===0&&<ResponsiveTable rows={warehouses} columns={whCols} onRowClick={r=>nav(`/depolar/${r.id}`)} cardTitle={r=>r.name} cardSubtitle={r=>r.branch_name||'Şube yok'} cardFields={[
   {label:'Ürün',value:r=>r.product_count},
   {label:'Toplam',value:r=>r.total_quantity},
   {label:'Varsayılan',value:r=>r.is_default?'Evet':'Hayır'},
  ]} cardActions={[{label:'Depo Detayı',color:'primary',onClick:r=>nav(`/depolar/${r.id}`)}]}/>}

  {tab===1&&<Stack spacing={1.5}>
   <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
    <TextField select size="small" label="Depo" value={selected} onChange={e=>setSelected(Number(e.target.value))} sx={{minWidth:220}}>
     {warehouses.map(w=><MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
    </TextField>
    <TextField size="small" fullWidth label="Ürün, kod, barkod, OEM veya raf ara" value={q} onChange={e=>setQ(e.target.value)}/>
    <FormControlLabel control={<Switch checked={criticalOnly} onChange={e=>setCriticalOnly(e.target.checked)}/>} label="Sadece kritikler" sx={{whiteSpace:'nowrap'}}/>
   </Stack>
   <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
    <Chip label={`Toplam ürün: ${stocks.length}`} />
    <Chip color="error" label={`Kritik: ${stocks.filter(x=>x.is_critical).length}`} />
    <Chip color="primary" label={`Toplam miktar: ${stocks.reduce((sum,x)=>sum+Number(x.quantity||0),0).toLocaleString('tr-TR')}`} />
   </Stack>
   <ResponsiveTable rows={stocks} getRowId={r=>r.product_id} columns={stockCols} onRowClick={r=>nav(`/urunler/${r.product_id}`)} cardTitle={r=>r.name} cardSubtitle={r=>r.product_code||'Kod yok'} cardFields={[
    {label:'Stok',value:r=>`${r.quantity} ${r.unit}`},
    {label:'OEM',value:r=>r.oem_number||'-'},
    {label:'Raf',value:r=>r.location||'-'},
    {label:'Kritik',value:r=>r.critical_stock},
    {label:'Durum',value:r=>stockCondition(r.quantity,r.critical_stock).label},
   ]} cardActions={[
    {label:'Ürün Detayı',color:'primary',onClick:r=>nav(`/urunler/${r.product_id}`)},
    {label:'Sayım',onClick:r=>openAdjustment(r)},
   ]}/>
  </Stack>}

  {tab===2&&<Stack spacing={1.5}>
   <Typography variant="body2" color="text.secondary">Kritik, sıfır ve negatif stoktaki ürünler. Satır bir ürüne aittir; işlem için sağdaki butonları kullanın.</Typography>
   {replenishmentError&&<Alert severity="error">{replenishmentError}</Alert>}
   {!replenishmentError&&replenishment.length===0&&<Alert severity="success">İkmal gerektiren ürün bulunmuyor.</Alert>}
   {replenishment.length>0&&<ResponsiveTable rows={replenishment} columns={replenishmentCols} getRowId={r=>`${r.warehouse_id}-${r.product_id}`} onRowClick={r=>nav(`/urunler/${r.product_id}`)} cardTitle={r=>r.product_name} cardSubtitle={r=>`${r.warehouse_name} · ${stockCondition(r.quantity,r.critical_stock).label}`} cardFields={[
    {label:'Stok',value:r=>`${r.quantity} ${r.unit||''}`},
    {label:'Kritik',value:r=>r.critical_stock},
    {label:'Eksik',value:r=>Number(Math.max(0,Number(r.critical_stock||0)-Number(r.quantity||0)).toFixed(4))},
    {label:'Son Tedarikçi',value:r=>r.last_supplier_name||'-'},
    {label:'Son Alış',value:r=>r.last_purchase_date||'-'},
   ]} cardActions={[
    {label:'Alış Oluştur',color:'primary',onClick:r=>nav('/alislar',{state:{newForProductId:r.product_id}})},
    {label:'Transfer Et',onClick:r=>openTransferFrom(null,{id:r.product_id,name:r.product_name,product_code:r.product_code,unit:r.unit})},
    {label:'Ürün Detayı',onClick:r=>nav(`/urunler/${r.product_id}`)},
   ]}/>}
  </Stack>}

  {tab===3&&<ResponsiveTable rows={transfers} columns={transferCols} cardTitle={r=>`${r.source_name} → ${r.target_name}`} cardSubtitle={r=>r.transfer_date} cardFields={[
   {label:'Kalem',value:r=>r.item_count},
   {label:'Miktar',value:r=>r.total_quantity},
   {label:'Not',value:r=>r.note||'-'},
  ]}/>}

  <Dialog open={newOpen} onClose={()=>setNewOpen(false)} fullWidth maxWidth="xs">
   <DialogTitle>Yeni Depo</DialogTitle>
   <DialogContent>
    <Stack spacing={2} mt={1}>
     {newError&&<Alert severity="error">{newError}</Alert>}
     <TextField label="Depo adı" value={name} onChange={e=>setName(e.target.value)} autoFocus/>
     <TextField label="Kod" value={code} onChange={e=>setCode(e.target.value)}/>
    </Stack>
   </DialogContent>
   <DialogActions>
    <Button onClick={()=>setNewOpen(false)}>Vazgeç</Button>
    <Button variant="contained" onClick={createWarehouse}>Kaydet</Button>
   </DialogActions>
  </Dialog>

  <Dialog open={adjustOpen} onClose={()=>!adjustBusy&&setAdjustOpen(false)} fullWidth maxWidth="sm">
   <DialogTitle>Stok Sayımı / Düzeltme</DialogTitle>
   <DialogContent>
    <Stack spacing={2} mt={1}>
     {adjustError&&<Alert severity="error">{adjustError}</Alert>}
     {adjustRow&&<Alert severity={adjustRow.is_critical?'warning':'info'}>
      <strong>{adjustRow.name}</strong> · Mevcut stok: {adjustRow.quantity} {adjustRow.unit} · Raf: {adjustRow.location||'-'}
     </Alert>}
     <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
      <TextField select fullWidth label="İşlem" value={adjustMode} onChange={e=>{setAdjustMode(e.target.value as any);setAdjustNeedsOverride(false);}}>
       <MenuItem value="set">Sayım sonucu olarak ayarla</MenuItem>
       <MenuItem value="add">Stok ekle</MenuItem>
       <MenuItem value="remove">Stok düş</MenuItem>
      </TextField>
      <TextField fullWidth type="number" label={adjustMode==='set'?'Yeni stok':'Miktar'} value={adjustQuantity} onChange={e=>{setAdjustQuantity(Number(e.target.value));setAdjustNeedsOverride(false);}} inputProps={{min:0,step:'0.0001'}}/>
     </Stack>
     <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
      <TextField fullWidth type="number" label="Kritik stok seviyesi" value={adjustCritical} onChange={e=>setAdjustCritical(Number(e.target.value))} inputProps={{min:0,step:'0.0001'}}/>
      <TextField fullWidth type="date" label="Tarih" value={adjustDate} onChange={e=>setAdjustDate(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
     </Stack>
     <TextField label="Açıklama" value={adjustNote} onChange={e=>setAdjustNote(e.target.value)} multiline minRows={2}/>
     {adjustNeedsOverride&&manager&&<TextField label="Yönetici onay gerekçesi" value={adjustOverrideReason} onChange={e=>setAdjustOverrideReason(e.target.value)} helperText="En az 5 karakter. Eksi stok istisnası denetim kaydına yazılır." multiline minRows={2} required/>}
    </Stack>
   </DialogContent>
   <DialogActions>
    <Button onClick={()=>setAdjustOpen(false)} disabled={adjustBusy}>Vazgeç</Button>
    <Button variant="contained" onClick={saveAdjustment} disabled={adjustBusy||(adjustNeedsOverride&&manager&&adjustOverrideReason.trim().length<5)}>{adjustBusy?'Kaydediliyor...':'Stoku Kaydet'}</Button>
   </DialogActions>
  </Dialog>

  <TransferDialog open={transferOpen} warehouses={warehouses} initialSourceId={transferSource} initialProduct={transferProduct}
   onClose={()=>setTransferOpen(false)}
   onDone={()=>{setTransferOpen(false);Promise.all([loadWarehouses(),loadStocks(),loadTransfers(),Promise.resolve(loadReplenishment())]);}}/>
 </Stack>;
}
