import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {useNavigate,useParams,Link as RouterLink} from 'react-router-dom';
import {Alert,Autocomplete,Box,Button,Chip,CircularProgress,Dialog,DialogActions,DialogContent,DialogTitle,Divider,Grid,Link,Paper,Stack,Step,StepLabel,Stepper,TextField,Typography} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';import EditIcon from '@mui/icons-material/Edit';import AddIcon from '@mui/icons-material/Add';import DeleteIcon from '@mui/icons-material/Delete';
import PhotoCameraIcon from '@mui/icons-material/PhotoCamera';import DrawIcon from '@mui/icons-material/Draw';import DownloadIcon from '@mui/icons-material/Download';
import type {GridColDef} from '@mui/x-data-grid';
import WorkOrderDialog from '../components/WorkOrderDialog';
import {api,errorDetail,money,openAuthenticated} from '../api';
import {decimalPayload,partLineTotalDecimal} from '../utils/documentMoney';
import {useAuth} from '../AuthContext';
import {WO_STATUS_LABELS,WO_STATUS_COLORS,WO_PRIORITY_LABELS,WARRANTY_TYPE_LABELS,WO_ALLOWED_TRANSITIONS,WO_TERMINAL_STATES,WO_MAIN_FLOW} from '../workOrderConstants';
import ResponsiveTable from '../components/ResponsiveTable';

const Meta=({label,value}:{label:string;value:React.ReactNode})=><Grid size={{xs:12,sm:6,md:4}}><Typography variant="caption" color="text.secondary">{label}</Typography><Typography fontWeight={600}>{value??'-'}</Typography></Grid>;
const TOUCH_TARGET_STACK_SX = {
  '& button': {minHeight: 44, minWidth: 44},
  '& [role="button"]': {minHeight: 44, minWidth: 44},
  '& a[href]': {display: 'inline-flex', alignItems: 'center', minHeight: 44, minWidth: 44},
  '& input': {minHeight: 44},
  '& .MuiInputBase-root': {minHeight: 44},
  '& .MuiInputBase-input': {minHeight: 44},
  '& .MuiInputBase-inputMultiline': {minHeight: 44},
};
const TOUCH_TARGET_DIALOG_SX = TOUCH_TARGET_STACK_SX;

async function findInvoiceForWorkOrder(workOrderId:number){
 const first=await api.get('/invoices',{params:{page:1,page_size:200}});
 const pages=Math.max(1,Number(first.data?.pages||1));
 const pageItems=[...(first.data?.items||[])];
 for(let page=2;page<=pages;page++){
  const response=await api.get('/invoices',{params:{page,page_size:200}});
  pageItems.push(...(response.data?.items||[]));
 }
 const details=await Promise.all(pageItems.map((item:any)=>api.get(`/invoices/${item.id}`).then(response=>response.data)));
 return details.find((invoice:any)=>Number(invoice.work_order_id??invoice.work_order?.id)===workOrderId)||null;
}

// ---- Parça ekle/düzenle dialog'u -------------------------------------------
function PartDialog({open,workOrderId,part,onClose,onSaved}:{open:boolean;workOrderId:number;part:any|null;onClose:()=>void;onSaved:()=>void}){
 const blank={warehouse_id:null as number|null,product_id:null as number|null,quantity:'1',unit_price:'0',discount:'0',tax_rate:'0'};
 const [v,setV]=useState<any>(blank);const [warehouses,setWarehouses]=useState<any[]>([]);const [products,setProducts]=useState<any[]>([]);const [error,setError]=useState('');const [saving,setSaving]=useState(false);
 useEffect(()=>{if(!open)return;setError('');
  api.get('/warehouses').then(r=>setWarehouses(r.data||[])).catch(()=>setWarehouses([]));
  if(part)setV({warehouse_id:part.warehouse_id,product_id:part.product_id,quantity:String(part.quantity),unit_price:String(part.unit_price),discount:String(part.discount??'0'),tax_rate:String(part.tax_rate??'0')});
  else setV(blank);
 },[open,part]);
 // Ürünler seçili depoya göre stok bilgisiyle yüklenir (canlı stok görünürlüğü).
 useEffect(()=>{if(!open||!v.warehouse_id){setProducts([]);return}api.get('/products',{params:{limit:2000,warehouse_id:v.warehouse_id}}).then(r=>setProducts(r.data||[])).catch(()=>setProducts([]))},[open,v.warehouse_id]);
 const selectedProduct=products.find(p=>p.id===v.product_id)||null;
 const stockOf=(p:any)=>p?.stock??p?.available_stock??p?.quantity??null;
 // Satır toplamı WorkOrderDialog ile AYNI ortak yardımcıyla, backend
 // money.compute_line paritesinde (float değil, bileşen-bileşen Decimal).
 const total=useMemo(()=>partLineTotalDecimal({quantity:v.quantity,unit_price:v.unit_price,discount:v.discount,tax_rate:v.tax_rate}),[v]);
 const save=async()=>{try{setSaving(true);setError('');
   // Para/miktar alanları backend'e Decimal-STRING gider (float sapması yok).
   const payload={product_id:v.product_id,warehouse_id:v.warehouse_id,quantity:decimalPayload(v.quantity||0),unit_price:decimalPayload(v.unit_price||0),discount:decimalPayload(v.discount||0),tax_rate:decimalPayload(v.tax_rate||0)};
   if(part)await api.put(`/work-orders/${workOrderId}/parts/${part.id}`,payload);
   else await api.post(`/work-orders/${workOrderId}/parts`,payload);
   onSaved();onClose();
  }catch(e:any){setError(e.response?.data?.detail||e.message)}finally{setSaving(false)}};
 // Birim fiyatı 0/negatif olan parça kaydedilemez (gelir kaybı riski).
 const canSave=Boolean(v.warehouse_id&&v.product_id&&Number(v.quantity)>0&&Number(v.unit_price)>0);
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" PaperProps={{sx: TOUCH_TARGET_DIALOG_SX}}><DialogTitle>{part?'Parça Düzenle':'Parça Ekle'}</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}>{error&&<Alert severity="error">{error}</Alert>}
  <Autocomplete options={warehouses} getOptionLabel={o=>o?.name||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={warehouses.find(w=>w.id===v.warehouse_id)||null} onChange={(_,val)=>setV({...v,warehouse_id:val?val.id:null,product_id:null})} renderInput={params=><TextField {...params} required label="Depo"/>}/>
  {/* Parça müşteriye SATIŞ fiyatından yansır (alış değil); satış fiyatı yoksa 0. */}
  <Autocomplete options={products} disabled={!v.warehouse_id} getOptionLabel={o=>{const s=stockOf(o);return o?`${o.name||o.sku||o.code||('#'+o.id)}${s!=null?` (stok: ${s})`:''}`:''}} isOptionEqualToValue={(o,val)=>o.id===val.id} value={selectedProduct} onChange={(_,val)=>setV({...v,product_id:val?val.id:null,unit_price:val&&!part?String(val.sale_price??'0'):v.unit_price})} renderInput={params=><TextField {...params} required label={v.warehouse_id?'Ürün / Parça':'Önce depo seçin'}/>}/>
  {selectedProduct&&stockOf(selectedProduct)!=null&&<Typography variant="caption" color={Number(stockOf(selectedProduct))<Number(v.quantity||0)?'error':'text.secondary'}>Seçili depoda stok: <b>{stockOf(selectedProduct)}</b>{Number(stockOf(selectedProduct))<Number(v.quantity||0)&&' — yetersiz stok'}</Typography>}
  {v.product_id&&!(Number(v.unit_price)>0)&&<Alert severity="error">Birim fiyatı 0 olan parça kaydedilemez; satış fiyatını girin.</Alert>}
  <Stack direction="row" spacing={1}>
   <TextField fullWidth type="number" label="Miktar" value={v.quantity} onChange={e=>setV({...v,quantity:e.target.value})} inputProps={{min:0,step:.01}}/>
   <TextField fullWidth type="number" label="Birim Fiyat (₺)" value={v.unit_price} error={Boolean(v.product_id)&&!(Number(v.unit_price)>0)} onChange={e=>setV({...v,unit_price:e.target.value})} inputProps={{min:0,step:.01}}/>
  </Stack>
  <Stack direction="row" spacing={1}>
   <TextField fullWidth type="number" label="İskonto (%)" value={v.discount} onChange={e=>setV({...v,discount:e.target.value})} inputProps={{min:0,max:100,step:.5}}/>
   <TextField fullWidth type="number" label="KDV (%)" value={v.tax_rate} onChange={e=>setV({...v,tax_rate:e.target.value})} inputProps={{min:0,max:100,step:.5}}/>
  </Stack>
  <Typography align="right" color="text.secondary">Satır Toplamı: <b>{money(total.toFixed(2))}</b></Typography>
 </Stack></DialogContent><DialogActions><Button onClick={onClose}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={saving||!canSave}>{saving?'Kaydediliyor...':'Kaydet'}</Button></DialogActions></Dialog>;
}

// ---- Ekler (foto / müşteri imzası) -----------------------------------------
const ATTACHMENT_KIND_LABELS:Record<string,string>={photo:'Fotoğraf',signature:'İmza',other:'Diğer'};
function AttachmentsCard({workOrderId,canWrite,onError}:{workOrderId:number;canWrite:boolean;onError:(message:string)=>void}){
 const [items,setItems]=useState<any[]>([]);const [uploading,setUploading]=useState(false);
 const photoInput=React.useRef<HTMLInputElement|null>(null);const signatureInput=React.useRef<HTMLInputElement|null>(null);
 const load=()=>{api.get(`/work-order-attachments/${workOrderId}`).then(r=>setItems(r.data||[])).catch(()=>setItems([]))};
 useEffect(()=>{load()},[workOrderId]);
 const upload=async(kind:string,file:File|null)=>{if(!file)return;
  try{setUploading(true);
   const form=new FormData();form.append('file',file);form.append('kind',kind);
   await api.post(`/work-order-attachments/${workOrderId}`,form);load();
  }catch(e:any){onError(e.response?.data?.detail||e.message)}finally{setUploading(false);
   if(photoInput.current)photoInput.current.value='';if(signatureInput.current)signatureInput.current.value=''}};
 const remove=async(a:any)=>{try{await api.delete(`/work-order-attachments/${workOrderId}/${a.id}`);load()}catch(e:any){onError(e.response?.data?.detail||e.message)}};
 const download=(a:any)=>{openAuthenticated(`/work-order-attachments/${workOrderId}/${a.id}/download`,a.file_name).catch((e:any)=>onError(e.response?.data?.detail||e.message))};
 const sizeLabel=(bytes:number)=>bytes>=1024*1024?`${(bytes/1024/1024).toFixed(1)} MB`:`${Math.max(1,Math.round(bytes/1024))} KB`;
 const attachmentColumns:GridColDef[]=[
  {field:'file_name',headerName:'Dosya',flex:1,minWidth:190},
  {field:'kind',headerName:'Tür',width:130,valueGetter:(_v,row:any)=>ATTACHMENT_KIND_LABELS[row.kind]||row.kind},
  {field:'file_size',headerName:'Boyut',width:120,align:'right',headerAlign:'right',valueGetter:(_v,row:any)=>sizeLabel(Number(row.file_size||0))},
  {field:'uploaded_by_name',headerName:'Yükleyen',flex:1,minWidth:170,valueGetter:(_v,row:any)=>row.uploaded_by_name||row.uploaded_by_username||'-'},
  {field:'created_at',headerName:'Tarih',width:120,valueGetter:(_v,row:any)=>String(row.created_at||'').slice(0,10)},
 ];
 return <Paper sx={{p:2}}>
  <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1} flexWrap="wrap" gap={1}>
   <Typography variant="h6" fontWeight={800}>Ekler (Foto / İmza)</Typography>
   {canWrite&&<Stack direction="row" spacing={1}>
    <input ref={photoInput} hidden type="file" accept="image/*,video/*,.pdf" onChange={e=>upload('photo',e.target.files?.[0]||null)}/>
    <input ref={signatureInput} hidden type="file" accept="image/*" onChange={e=>upload('signature',e.target.files?.[0]||null)}/>
    <Button size="small" variant="outlined" startIcon={<PhotoCameraIcon/>} disabled={uploading} onClick={()=>photoInput.current?.click()} sx={{minHeight:{xs:44}}}>Foto Ekle</Button>
    <Button size="small" variant="outlined" startIcon={<DrawIcon/>} disabled={uploading} onClick={()=>signatureInput.current?.click()} sx={{minHeight:{xs:44}}}>İmza Ekle</Button>
   </Stack>}
  </Stack>
  {items.length===0?<Typography color="text.secondary" py={1}>Henüz ek yüklenmedi.</Typography>
   :<Box data-testid="work-order-detail-data-surface"><ResponsiveTable
      rows={items}
      columns={attachmentColumns}
      cardTitle={row=>row.file_name}
      cardSubtitle={row=>`${ATTACHMENT_KIND_LABELS[row.kind]||row.kind} · ${row.created_at?String(row.created_at).slice(0,10):'-'}`}
      cardFields={[
        {label:'Tür',value:row=><Chip size="small" variant="outlined" label={ATTACHMENT_KIND_LABELS[row.kind]||row.kind}/>},
        {label:'Boyut',value:row=>sizeLabel(Number(row.file_size||0))},
        {label:'Yükleyen',value:row=><>{row.uploaded_by_name||row.uploaded_by_username||'-'}</>},
      ]}
      cardActions={[
      {label:'İndir',icon:<DownloadIcon/>,onClick:download},
        ...(canWrite?[{label:'Sil',icon:<DeleteIcon/>,color:'error' as const,onClick:remove}]:[]),
      ]}
      cardActionsOnDesktop
    /></Box>}
 </Paper>;
}

// ---- Parça satırı durumları (FAZ-3 rezervasyon akışı) ----------------------
// Eski satırlarda line_status boş gelebilir; o durumda "Çıkış" (ISSUED) kabul
// edilir — migration da legacy satırları ISSUED olarak taşır.
const PART_STATUS_LABELS:Record<string,string>={RESERVED:'Rezerve',ISSUED:'Çıkış',RETURNED:'İade'};
const PART_STATUS_COLORS:Record<string,'default'|'info'|'success'|'warning'>={RESERVED:'info',ISSUED:'success',RETURNED:'default'};

// ---- İşçilik satırları ------------------------------------------------------
const LABOR_STATUS_LABELS:Record<string,string>={DRAFT:'Taslak',APPROVED:'Onaylı',VOID:'İptal'};
const LABOR_STATUS_COLORS:Record<string,'default'|'info'|'success'|'error'>={DRAFT:'info',APPROVED:'success',VOID:'default'};
// Para/saat alanları Number() ile karara bağlanmaz; string doğrulanıp olduğu gibi
// gönderilir (backend Decimal ile hesaplar).
const isPositiveDecimalValue=(value:string)=>{const text=value.trim();if(!/^\+?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text))return false;return /[1-9]/.test(text)};
const isNonNegativeDecimalValue=(value:string)=>/^\+?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value.trim());

function LaborLineDialog({open,workOrderId,line,defaultRate,onClose,onSaved}:{open:boolean;workOrderId:number;line:any|null;defaultRate:string;onClose:()=>void;onSaved:()=>void}){
 const [technicians,setTechnicians]=useState<any[]>([]);
 const [v,setV]=useState<any>({technician_user_id:null,hours:'',hourly_rate:'',note:''});
 const [error,setError]=useState('');const [saving,setSaving]=useState(false);
 useEffect(()=>{if(!open)return;setError('');
  api.get('/work-orders/technicians').then(r=>setTechnicians(r.data||[])).catch(()=>setTechnicians([]));
  if(line)setV({technician_user_id:line.technician_user_id,hours:String(line.hours),hourly_rate:String(line.hourly_rate),note:line.note||''});
  else setV({technician_user_id:null,hours:'',hourly_rate:defaultRate,note:''});
 },[open,line,defaultRate]);
 const hoursValid=isPositiveDecimalValue(v.hours);
 const rateValid=!String(v.hourly_rate).trim()||isNonNegativeDecimalValue(v.hourly_rate);
 const save=async()=>{try{setSaving(true);setError('');
   const payload:any={technician_user_id:v.technician_user_id,hours:v.hours.trim()};
   if(String(v.hourly_rate).trim())payload.hourly_rate=String(v.hourly_rate).trim();
   if(v.note.trim())payload.note=v.note.trim();
   if(line)await api.put(`/work-orders/${workOrderId}/labor-lines/${line.id}`,payload);
   else await api.post(`/work-orders/${workOrderId}/labor-lines`,payload);
   onSaved();onClose();
  }catch(e:any){setError(e.response?.data?.detail||e.message)}finally{setSaving(false)}};
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" PaperProps={{sx: TOUCH_TARGET_DIALOG_SX}}><DialogTitle>{line?'İşçilik Satırı Düzenle':'İşçilik Satırı Ekle'}</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}>
  {error&&<Alert severity="error">{error}</Alert>}
  <Autocomplete options={technicians} getOptionLabel={o=>o?.display_name||o?.username||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={technicians.find(t=>t.id===v.technician_user_id)||null} onChange={(_,val)=>setV({...v,technician_user_id:val?val.id:null})} renderInput={params=><TextField {...params} required label="Teknisyen"/>}/>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
   <TextField fullWidth required label="Saat" value={v.hours} onChange={e=>setV({...v,hours:e.target.value})} error={Boolean(v.hours)&&!hoursValid} helperText="0'dan büyük olmalı (ör. 1.50)"/>
   <TextField fullWidth label="Saat Ücreti (₺)" value={v.hourly_rate} onChange={e=>setV({...v,hourly_rate:e.target.value})} error={!rateValid} helperText={line?'Satır oranı sabittir; değiştirmezseniz korunur':'Boş bırakılırsa iş emri saat ücreti dondurulur'}/>
  </Stack>
  <TextField label="Not" value={v.note} onChange={e=>setV({...v,note:e.target.value})} multiline minRows={2}/>
 </Stack></DialogContent><DialogActions><Button onClick={onClose}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={saving||!v.technician_user_id||!hoursValid||!rateValid}>{saving?'Kaydediliyor...':'Kaydet'}</Button></DialogActions></Dialog>;
}

function LaborCard({workOrderId,canWrite,canApprove,defaultRate,onError,onChanged}:{workOrderId:number;canWrite:boolean;canApprove:boolean;defaultRate:string;onError:(m:string)=>void;onChanged:()=>void}){
 const [data,setData]=useState<any>({items:[],approved_total:'0',labor_source:'header'});
 const [open,setOpen]=useState(false);const [editing,setEditing]=useState<any|null>(null);const [busy,setBusy]=useState(false);
 const load=()=>{api.get(`/work-orders/${workOrderId}/labor-lines`).then(r=>setData(r.data||{items:[]})).catch(()=>setData({items:[],approved_total:'0',labor_source:'header'}))};
 useEffect(()=>{load()},[workOrderId]);
  const refresh=()=>{load();onChanged()};
 const approve=async(row:any)=>{setBusy(true);try{await api.post(`/work-orders/${workOrderId}/labor-lines/${row.id}/approve`);refresh()}catch(e:any){onError(e.response?.data?.detail||e.message)}finally{setBusy(false)}};
 const voidLine=async(row:any)=>{setBusy(true);try{await api.delete(`/work-orders/${workOrderId}/labor-lines/${row.id}`);refresh()}catch(e:any){onError(e.response?.data?.detail||e.message)}finally{setBusy(false)}};
 const usesLines=data.labor_source==='lines';
 const laborColumns:GridColDef[]=[
  {field:'technician_name',headerName:'Teknisyen',flex:1,minWidth:170,valueGetter:(_v,row:any)=>row.technician_name||row.technician_username},
  {field:'work_date',headerName:'Tarih',width:140,valueGetter:(_v,row:any)=>row.work_date?String(row.work_date).slice(0,10):'-'},
  {field:'hours',headerName:'Saat',width:90,align:'right',headerAlign:'right'},
  {field:'hourly_rate',headerName:'Saat Ücreti',width:140,align:'right',headerAlign:'right',valueGetter:(_v,row:any)=>money(row.hourly_rate)},
  {field:'line_total',headerName:'Tutar',width:130,align:'right',headerAlign:'right',valueGetter:(_v,row:any)=>money(row.line_total)},
  {field:'line_status',headerName:'Durum',width:120,renderCell:params=><Chip size="small" color={LABOR_STATUS_COLORS[params.row.line_status]||'default'} label={LABOR_STATUS_LABELS[params.row.line_status]||params.row.line_status}/>},
  {field:'note',headerName:'Not',flex:1,minWidth:180,valueGetter:(_v,row:any)=>row.note||'-'},
 ];
 const laborCardActions = [
  ...(canWrite ? [{label:'Düzenle',icon:<EditIcon/>,disabled:(row:any)=>row.line_status!=='DRAFT',onClick:(row:any)=>{setEditing(row);setOpen(true);}}] : []),
  ...(canApprove ? [{label:'Onayla',color:'success' as const,disabled:(row:any)=>row.line_status!=='DRAFT'||busy,onClick:(row:any)=>void approve(row)}] : []),
  ...(canWrite ? [{label:'İptal',icon:<DeleteIcon/>,color:'error' as const,disabled:(row:any)=>row.line_status==='VOID'||(row.line_status!=='DRAFT'&&!canApprove)||busy,onClick:(row:any)=>void voidLine(row)}] : []),
 ];
 return <Paper sx={{p:2}}>
  <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1} flexWrap="wrap" gap={1}>
   <Typography variant="h6" fontWeight={800}>İşçilik</Typography>
   {canWrite&&<Button size="small" variant="contained" startIcon={<AddIcon/>} onClick={()=>{setEditing(null);setOpen(true)}} sx={{minHeight:{xs:44}}}>Satır Ekle</Button>}
  </Stack>
  <Alert severity={usesLines?'success':'info'} sx={{mb:1}}>
   {usesLines
    ?<>Faturada <b>onaylı işçilik satırları</b> kullanılacak (toplam {money(data.approved_total)}). İş emri başlığındaki saat/ücret bu faturaya eklenmez.</>
    :<>Faturada <b>iş emri başlığındaki saat × ücret</b> kullanılacak. Onaylanmış satır yoksa taslak satırlar hesaba girmez.</>}
  </Alert>
  {data.items.length===0?<Typography color="text.secondary" py={1}>Henüz işçilik satırı yok.</Typography>
   :<Box data-testid="work-order-detail-data-surface"><ResponsiveTable
     rows={data.items}
     columns={laborColumns}
     cardTitle={row=>row.technician_name||row.technician_username}
     cardSubtitle={row=>`${row.work_date?String(row.work_date).slice(0,10):'-'} · ${LABOR_STATUS_LABELS[row.line_status]||row.line_status}`}
     cardFields={[
       {label:'Saat',value:row=>row.hours},
       {label:'Saat Ücreti',value:row=>money(row.hourly_rate)},
       {label:'Tutar',value:row=><b>{money(row.line_total)}</b>},
       {label:'Not',value:row=><>{row.note||'-'}</>},
     ]}
     cardActions={laborCardActions}
     cardActionsOnDesktop
   /></Box>}
  <LaborLineDialog open={open} workOrderId={workOrderId} line={editing} defaultRate={defaultRate} onClose={()=>setOpen(false)} onSaved={refresh}/>
 </Paper>;
}

// ---- İş emri detay sayfası --------------------------------------------------
export default function WorkOrderDetail(){
 const {id}=useParams();const nav=useNavigate();const {can,user}=useAuth();const canWrite=can('sales');
 // Onay backend'de admin/yonetici ile sınırlı; buton da aynı kapıdan geçer.
 const canApproveLabor=['admin','yonetici'].includes(String(user?.role||''));
 const [wo,setWo]=useState<any|null>(null);const [parts,setParts]=useState<any[]>([]);const [billing,setBilling]=useState<any|null>(null);const [billingState,setBillingState]=useState<'loading'|'ready'|'not-ready'|'error'>('loading');
 const [loading,setLoading]=useState(true);const [error,setError]=useState('');const [actionError,setActionError]=useState('');
 const [editOpen,setEditOpen]=useState(false);const [partOpen,setPartOpen]=useState(false);const [editPart,setEditPart]=useState<any|null>(null);const [invoiceOpen,setInvoiceOpen]=useState(false);const [busy,setBusy]=useState(false);
 const load=()=>{setLoading(true);setError('');api.get(`/work-orders/${id}`).then(r=>setWo(r.data)).catch(e=>setError(e.response?.data?.detail||'İş emri yüklenemedi.')).finally(()=>setLoading(false))};
 const loadParts=()=>{api.get(`/work-orders/${id}/parts`).then(r=>setParts(r.data?.items||[])).catch(()=>setParts([]))};
 const loadBilling=()=>{setBillingState('loading');api.get(`/work-orders/${id}/invoice`).then(r=>{setBilling(r.data);setBillingState('ready')}).catch(error=>{setBilling(null);setBillingState(error?.response?.status===409?'not-ready':'error')})};
 const refresh=()=>{load();loadParts();loadBilling()};
 useEffect(()=>{refresh()},[id]);
 const changeStatus=async(next:string)=>{setActionError('');setBusy(true);try{await api.patch(`/work-orders/${id}/status`,{status:next});refresh()}catch(e:any){setActionError(e.response?.data?.detail||e.message)}finally{setBusy(false)}};
 const deletePart=async(part:any)=>{setActionError('');try{await api.delete(`/work-orders/${id}/parts/${part.id}`);loadParts();loadBilling()}catch(e:any){setActionError(e.response?.data?.detail||e.message)}};
 // Rezervasyon akışı (FAZ-3): RESERVED -> ISSUED -> RETURNED. Durum geçişleri
 // backend'de compare-and-set ile korunur; UI yalnız uygun butonu gösterir.
 const issuePart=async(part:any)=>{setActionError('');setBusy(true);try{await api.post(`/work-orders/${id}/parts/${part.id}/issue`);loadParts();loadBilling()}catch(e:any){setActionError(e.response?.data?.detail||e.message)}finally{setBusy(false)}};
 const returnPart=async(part:any)=>{setActionError('');setBusy(true);try{await api.post(`/work-orders/${id}/parts/${part.id}/return`);loadParts();loadBilling()}catch(e:any){setActionError(e.response?.data?.detail||e.message)}finally{setBusy(false)}};
 const generateInvoice=async()=>{setBusy(true);setActionError('');
  try{
   const response=await api.post('/invoices/generate',{work_order_id:wo.id});
   setInvoiceOpen(false);nav(`/faturalar/${response.data.id}`);
  }catch(error:any){
   if(error?.response?.status===409&&String(error?.response?.data?.detail||'').includes('zaten')){
    try{
     const existing=await findInvoiceForWorkOrder(wo.id);
     if(existing){setInvoiceOpen(false);nav(`/faturalar/${existing.id}`);return}
    }catch{/* original duplicate error is more useful */}
   }
   setActionError(errorDetail(error,'Fatura oluşturulamadı.'));
  }finally{setBusy(false)}
 };
 const status=wo?.status as string|undefined;
 const isTerminal=status?WO_TERMINAL_STATES.has(status):false;
 const nextStates=status?WO_ALLOWED_TRANSITIONS[status]||[]:[];
 const activeStep=useMemo(()=>{if(!status)return 0;const i=WO_MAIN_FLOW.indexOf(status as any);return i>=0?i:0},[status]);
 const partColumns:GridColDef[]=[
  {field:'product_name',headerName:'Parça',flex:1,minWidth:170},
  {field:'warehouse_name',headerName:'Depo',width:170},
  {field:'line_status',headerName:'Durum',width:120,renderCell:params=><Chip size="small" color={PART_STATUS_COLORS[params.value]||'default'} label={PART_STATUS_LABELS[params.value]||params.value||'Çıkış'}/>},
  {field:'quantity',headerName:'Miktar',width:100,align:'right',headerAlign:'right'},
  {field:'unit_price',headerName:'Birim Fiyat',width:140,align:'right',headerAlign:'right',valueGetter:(_v,row:any)=>money(row.unit_price)},
  {field:'discount',headerName:'İskonto',width:110,align:'right',headerAlign:'right',valueGetter:(_v,row:any)=>`%${row.discount}`},
  {field:'tax_rate',headerName:'KDV',width:100,align:'right',headerAlign:'right',valueGetter:(_v,row:any)=>`%${row.tax_rate}`},
  {field:'total_price',headerName:'Toplam',width:140,align:'right',headerAlign:'right',valueGetter:(_v,row:any)=>money(row.total_price)},
 ];
 const partCardActions = canWrite&&!isTerminal ? [
  {label:'Çıkış',disabled:(row:any)=>row.line_status!=='RESERVED'||busy,onClick:(row:any)=>issuePart(row)},
  {label:'İade',color:'warning' as const,disabled:(row:any)=>row.line_status==='RETURNED'||busy,onClick:(row:any)=>returnPart(row)},
  {label:'Düzenle',icon:<EditIcon/>,disabled:(row:any)=>row.line_status==='RETURNED',onClick:(row:any)=>{setEditPart(row);setPartOpen(true);}},
  {label:'Sil',icon:<DeleteIcon/>,color:'error' as const,onClick:(row:any)=>deletePart(row)},
 ] : [];
 if(loading)return <Box minHeight="40dvh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>;
  if(error&&!wo)return <Stack spacing={2}><Alert severity="error">{error}</Alert><Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/is-emirleri')} sx={{minHeight:{xs:44}}}>İş Emirlerine Dön</Button></Stack>;
  if(!wo)return null;
  return <Stack spacing={2} sx={TOUCH_TARGET_STACK_SX}>
  <Stack direction={{xs:'column',md:'row'}} justifyContent="space-between" alignItems={{md:'center'}} gap={1}>
   <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
    <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav('/is-emirleri')} sx={{minHeight:{xs:44}}}>İş Emirleri</Button>
    <Typography variant="h4" fontWeight={900}>{wo.work_order_no}</Typography>
    <Chip size="small" color={WO_STATUS_COLORS[wo.status]||'default'} label={WO_STATUS_LABELS[wo.status]||wo.status}/>
    <Chip size="small" variant="outlined" label={WO_PRIORITY_LABELS[wo.priority]||wo.priority}/>
   </Stack>
   {canWrite&&!isTerminal&&<Button variant="outlined" startIcon={<EditIcon/>} onClick={()=>setEditOpen(true)} sx={{minHeight:{xs:44}}}>Düzenle</Button>}
  </Stack>
  {actionError&&<Alert severity="error" onClose={()=>setActionError('')}>{actionError}</Alert>}

  {/* Durum akışı + geçiş aksiyonları */}
  <Paper sx={{p:2}}>
   <Stepper activeStep={activeStep} alternativeLabel sx={{mb:2}}>{WO_MAIN_FLOW.map(s=><Step key={s} completed={activeStep>WO_MAIN_FLOW.indexOf(s)}><StepLabel error={wo.status==='CANCELLED'}>{WO_STATUS_LABELS[s]}</StepLabel></Step>)}</Stepper>
   {canWrite&&<Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
   {nextStates.length===0?<Typography color="text.secondary">Bu iş emri {WO_STATUS_LABELS[wo.status]} durumunda ve artık durum değiştirilemez.</Typography>
     :nextStates.map(s=><Button key={s} size="small" variant={s==='CANCELLED'?'outlined':'contained'} color={s==='CANCELLED'?'error':s==='COMPLETED'||s==='DELIVERED'?'success':'primary'} disabled={busy} onClick={()=>changeStatus(s)} sx={{minHeight:{xs:44}}}>{WO_STATUS_LABELS[s]} durumuna geçir</Button>)}
   </Stack>}
  </Paper>

  {/* İş emri bilgileri */}
  <Paper sx={{p:2}}><Typography variant="h6" fontWeight={800} mb={1}>İş Emri Bilgileri</Typography>
   <Grid container spacing={2}>
   <Meta label="Makine" value={<Link component={RouterLink} to={`/makineler/${wo.machine_id}`} sx={{display: 'inline-flex', alignItems: 'center', minHeight: 44}}>{`${wo.machine_brand||''} ${wo.machine_model||''}`.trim()||`#${wo.machine_id}`}</Link>}/>
    <Meta label="Seri No" value={wo.machine_serial_number}/>
   <Meta label="Müşteri" value={<Link component={RouterLink} to={`/musteriler/${wo.customer_id}`} sx={{display: 'inline-flex', alignItems: 'center', minHeight: 44}}>{wo.customer_name||`#${wo.customer_id}`}</Link>}/>
    <Meta label="Teknisyen" value={wo.technician_name||wo.technician_username}/>
    <Meta label="Açılış" value={wo.opened_at?String(wo.opened_at).slice(0,10):'-'}/>
    {wo.scheduled_date&&<Meta label="Planlanan Tarih" value={String(wo.scheduled_date).slice(0,16).replace('T',' ')}/>}
    <Meta label="Tamamlanma" value={wo.completed_at?String(wo.completed_at).slice(0,10):'-'}/>
    <Meta label="Tahmini Saat" value={wo.estimated_hours}/>
    <Meta label="Gerçekleşen Saat" value={wo.actual_hours}/>
    <Meta label="Saat Ücreti" value={money(wo.labor_rate)}/>
    <Meta label="Garanti" value={WARRANTY_TYPE_LABELS[wo.warranty_type]||wo.warranty_type}/>
    {wo.warranty_type==='PARTIAL'&&<Meta label="Garanti Oranı" value={`%${wo.warranty_percent}`}/>}
    <Meta label="Oluşturan" value={wo.created_by_name||wo.created_by_username}/>
   </Grid>
   {(wo.complaint||wo.diagnosis||wo.repair_summary||wo.technician_notes)&&<><Divider sx={{my:2}}/><Grid container spacing={2}>
    {wo.complaint&&<Grid size={{xs:12,md:6}}><Typography variant="caption" color="text.secondary">Şikayet</Typography><Typography>{wo.complaint}</Typography></Grid>}
    {wo.diagnosis&&<Grid size={{xs:12,md:6}}><Typography variant="caption" color="text.secondary">Teşhis</Typography><Typography>{wo.diagnosis}</Typography></Grid>}
    {wo.repair_summary&&<Grid size={{xs:12,md:6}}><Typography variant="caption" color="text.secondary">Yapılan İşlem</Typography><Typography>{wo.repair_summary}</Typography></Grid>}
    {wo.technician_notes&&<Grid size={{xs:12,md:6}}><Typography variant="caption" color="text.secondary">Teknisyen Notları</Typography><Typography>{wo.technician_notes}</Typography></Grid>}
   </Grid></>}
  </Paper>

  {/* Kullanılan parçalar */}
  <Paper sx={{p:2}}>
   <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
    <Typography variant="h6" fontWeight={800}>Kullanılan Parçalar</Typography>
   {canWrite&&!isTerminal&&<Button size="small" variant="contained" startIcon={<AddIcon/>} onClick={()=>{setEditPart(null);setPartOpen(true)}} sx={{minHeight:{xs:44}}}>Parça Ekle</Button>}
   </Stack>
  {parts.length===0?<Typography color="text.secondary" py={1}>Henüz parça eklenmedi.</Typography>
    :<Box data-testid="work-order-detail-data-surface"><ResponsiveTable
      rows={parts}
      columns={partColumns}
      loading={loading}
      cardTitle={row=>row.product_name}
      cardSubtitle={row=>`${row.warehouse_name} · ${PART_STATUS_LABELS[row.line_status]||row.line_status||'Çıkış'}`}
      cardFields={[
        {label:'Miktar',value:row=>row.quantity},
        {label:'Birim Fiyat',value:row=>money(row.unit_price)},
        {label:'İskonto',value:row=>`%${row.discount}`},
        {label:'KDV',value:row=>`%${row.tax_rate}`},
        {label:'Toplam',value:row=><b>{money(row.total_price)}</b>},
      ]}
      cardActions={partCardActions}
      cardActionsOnDesktop
    /></Box>}
  </Paper>

  {/* İşçilik satırları */}
  <LaborCard workOrderId={wo.id} canWrite={canWrite&&!isTerminal} canApprove={canApproveLabor&&!isTerminal}
             defaultRate={String(wo.labor_rate??'0')} onError={setActionError} onChanged={loadBilling}/>

  {/* Ekler */}
  <AttachmentsCard workOrderId={wo.id} canWrite={canWrite&&!isTerminal} onError={setActionError}/>

  {/* Faturalandırma özeti */}
   <Paper sx={{p:2}}>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1} mb={1}>
     <Typography variant="h6" fontWeight={800}>Faturalandırma Özeti</Typography>
     {canWrite&&(status==='COMPLETED'||status==='DELIVERED')&&<Button variant="contained" color="success" disabled={busy||billingState!=='ready'} onClick={()=>setInvoiceOpen(true)} sx={{minHeight:{xs:44}}}>Faturalandır</Button>}
    </Stack>
    {billingState==='loading'?<Stack direction="row" spacing={1} alignItems="center"><CircularProgress size={18}/><Typography color="text.secondary">Özet hesaplanıyor…</Typography></Stack>
     :billingState==='not-ready'?<Alert severity="info">İş emri faturalandırmaya hazır değil. Faturalandırma özeti tamamlandığında gösterilecek.</Alert>
     :billingState==='error'?<Alert severity="error">Özet hesaplanamadı. Lütfen tekrar deneyin.</Alert>
     :!billing?<Alert severity="error">Özet hesaplanamadı. Lütfen tekrar deneyin.</Alert>
     :<Grid container spacing={2}>
     <Meta label="İşçilik" value={money(billing.totals?.labor)}/>
     <Meta label="Parçalar" value={money(billing.totals?.parts)}/>
     <Meta label="KDV" value={money(billing.totals?.tax)}/>
     <Meta label="İskonto" value={money(billing.totals?.discount)}/>
     <Meta label="Genel Toplam" value={<b>{money(billing.totals?.grand_total)}</b>}/>
     <Meta label="İşçilik Kaynağı" value={billing.totals?.labor_source==='lines'?'Onaylı işçilik satırları':'İş emri başlığı (saat × ücret)'}/>
     <Meta label="Garanti" value={`${WARRANTY_TYPE_LABELS[billing.warranty?.type]||billing.warranty?.type} (%${billing.warranty?.coverage_percent})`}/>
     <Meta label="Müşteriden Tahsil" value={<b>{money(billing.warranty?.customer_amount)}</b>}/>
     <Meta label="Garanti Karşılığı" value={money(billing.warranty?.warranty_amount)}/>
    </Grid>}
   </Paper>

   <Dialog open={invoiceOpen} onClose={()=>{if(!busy)setInvoiceOpen(false)}} fullWidth maxWidth="sm" PaperProps={{sx: TOUCH_TARGET_DIALOG_SX}}>
    <DialogTitle>Fatura oluşturulsun mu?</DialogTitle>
    <DialogContent><Stack spacing={1.5} mt={1}>
     <Typography>Bu iş emri için fatura oluşturulacak.</Typography>
     <Meta label="Müşteri" value={billing?.customer?.name||wo.customer_name}/>
     <Meta label="Üretilecek Tutar" value={<b>{money(billing?.totals?.grand_total)}</b>}/>
    </Stack></DialogContent>
    <DialogActions><Button disabled={busy} onClick={()=>setInvoiceOpen(false)}>Vazgeç</Button><Button variant="contained" color="success" disabled={busy} onClick={generateInvoice}>{busy?'Oluşturuluyor…':'Faturayı Oluştur'}</Button></DialogActions>
   </Dialog>
   <WorkOrderDialog open={editOpen} id={wo.id} onClose={()=>setEditOpen(false)} onSaved={()=>refresh()}/>
  <PartDialog open={partOpen} workOrderId={wo.id} part={editPart} onClose={()=>setPartOpen(false)} onSaved={()=>{loadParts();loadBilling()}}/>
 </Stack>;
}
