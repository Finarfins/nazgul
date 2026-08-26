import React from 'react';
import {useEffect,useMemo,useRef,useState} from 'react';
import {Alert,Autocomplete,Box,Button,Chip,Collapse,Dialog,DialogActions,DialogContent,DialogTitle,Divider,IconButton,MenuItem,Stack,TextField,Typography,useMediaQuery,useTheme} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AddIcon from '@mui/icons-material/Add';
import AddBoxOutlinedIcon from '@mui/icons-material/AddBoxOutlined';
import PointOfSaleIcon from '@mui/icons-material/PointOfSale';
import PersonAddAltIcon from '@mui/icons-material/PersonAddAlt';
import WarehouseOutlinedIcon from '@mui/icons-material/WarehouseOutlined';
import type Decimal from 'decimal.js';
import {api,money,policyOverrideHeaders} from '../api';
import {useAuth} from '../AuthContext';
import {isLineBlank,useAutoTrailingLine} from '../utils/useAutoTrailingLine';
import EntityDialog from './EntityDialog';
import ProductDialog from './ProductDialog';
import {decimal,decimalPayload,documentGrossDecimal,documentVatDecimal,lineGrossDecimal,manualTotalDiscount,percentageDiscountDecimal} from '../utils/documentMoney';

type SaveResult={id:number;document_no?:string|null;final_total?:number;remaining_amount?:number};
type Props={open:boolean;kind:'sale'|'purchase';id?:number|null;initialEntityId?:number|null;initialProductId?:number|null;onClose:()=>void;onSaved:(result:SaveResult)=>void};
type Line={product_id:number|null;product_name:string;quantity:Decimal.Value;unit_price:Decimal.Value;vat_rate:Decimal.Value;discount_percent:Decimal.Value};
type PaymentAllocation={payment_method:string;amount:Decimal.Value;account_id:number|''};
const blankLine=():Line=>({product_id:null,product_name:'',quantity:'1',unit_price:'0',vat_rate:'20',discount_percent:'0'});

export default function TransactionDialog({open,kind,id,initialEntityId,initialProductId,onClose,onSaved}:Props){
 const theme=useTheme();const full=useMediaQuery(theme.breakpoints.down('sm'));const {user}=useAuth();
 const presetProductRef=useRef<number|null>(null);
 const entityInputRef=useRef<HTMLInputElement|null>(null);const firstProductInputRef=useRef<HTMLInputElement|null>(null);const paidInputRef=useRef<HTMLInputElement|null>(null);
 const [entities,setEntities]=useState<any[]>([]);const [products,setProducts]=useState<any[]>([]);const [warehouses,setWarehouses]=useState<any[]>([]);const [accounts,setAccounts]=useState<any[]>([]);
 const [warehouseId,setWarehouseId]=useState<number|''>('');const [entity,setEntity]=useState<any|null>(null);const [date,setDate]=useState(new Date().toISOString().slice(0,10));
 const [doc,setDoc]=useState('');const [dueDate,setDueDate]=useState('');const [note,setNote]=useState('');const [status,setStatus]=useState('completed');const [paymentMethod,setPaymentMethod]=useState('credit');const [paymentTerm,setPaymentTerm]=useState('PESIN');const [paidAmount,setPaidAmount]=useState<Decimal.Value>('0');const [paymentAccountId,setPaymentAccountId]=useState<number|''>('');const [splitPayment,setSplitPayment]=useState(false);const [paymentAllocations,setPaymentAllocations]=useState<PaymentAllocation[]>([]);const [discountPercent,setDiscountPercent]=useState<Decimal.Value>('0');const [manualTotal,setManualTotal]=useState('');
 const [lines,setLines]=useState<Line[]>([blankLine()]);const [loadingInitial,setLoadingInitial]=useState(false);const [loadingProducts,setLoadingProducts]=useState(false);const [entitySummary,setEntitySummary]=useState<any|null>(null);const [lastPrices,setLastPrices]=useState<Record<number,any>>({});const [error,setError]=useState('');const [saving,setSaving]=useState(false);const [needsOverride,setNeedsOverride]=useState(false);const [overrideReason,setOverrideReason]=useState('');const [quickEntityOpen,setQuickEntityOpen]=useState(false);const [quickProductOpen,setQuickProductOpen]=useState(false);const [quickProductLine,setQuickProductLine]=useState(0);const [quickProductInitial,setQuickProductInitial]=useState<Record<string,unknown>>({});
 const [showShortcuts,setShowShortcuts]=useState(false);

 useEffect(()=>{if(!open)return;setError('');setLoadingInitial(true);Promise.all([api.get(kind==='sale'?'/customers':'/suppliers'),api.get('/warehouses'),api.get('/payments/accounts',{params:{active_only:true}})]).then(([e,w,a])=>{setEntities(e.data);setWarehouses(w.data);setAccounts(a.data);if(!id&&w.data[0])setWarehouseId(w.data[0].id);if(!id&&initialEntityId){const preset=e.data.find((x:any)=>x.id===initialEntityId);if(preset)setEntity(preset)}}).catch((e:any)=>setError(e.response?.data?.detail||'Satış ekranı için gerekli başlangıç verileri yüklenemedi.')).finally(()=>setLoadingInitial(false));
  if(id){api.get(`/${kind==='sale'?'orders':'purchases'}/${id}`).then(r=>{const d=r.data.document;setEntity({id:d.entity_id,name:d.customer_name||d.supplier_name});setDate(d.transaction_date||'');setDoc(d.document_no||'');setDueDate(d.due_date||'');setNote(d.note||'');setStatus(d.status||'completed');setPaymentMethod(d.payment_method||'credit');setPaymentTerm(d.payment_term||'PESIN');setPaidAmount(decimalPayload(d.paid_amount||0));setPaymentAccountId(Number(d.payment_account_id||d.account_id)||'');setDiscountPercent(decimalPayload(d.discount_percent||0));const storedAmount=decimal(d.discount_amount||0);const percentAmount=percentageDiscountDecimal(d.grand_total||0,d.discount_percent||0);setManualTotal(storedAmount.greaterThan(0)&&!storedAmount.equals(percentAmount)?decimal(d.final_total).toFixed(2):'');setWarehouseId(d.warehouse_id||'');setLines(r.data.items.map((x:any)=>({product_id:x.product_id,product_name:x.product_name,quantity:decimalPayload(x.quantity),unit_price:decimalPayload(x.unit_price),vat_rate:decimalPayload(x.vat_rate),discount_percent:decimalPayload(x.discount_percent||0)})))})}
  else{setEntity(null);setDate(new Date().toISOString().slice(0,10));setDoc('');setDueDate('');setNote('');setStatus('completed');setPaymentMethod('credit');setPaidAmount(0);setPaymentAccountId('');setSplitPayment(false);setPaymentAllocations([]);setDiscountPercent(0);setManualTotal('');setNeedsOverride(false);setOverrideReason('');setLines([blankLine()])}
 },[open,id,kind,initialEntityId]);

 useEffect(()=>{if(!open||!warehouseId){setProducts([]);return}setLoadingProducts(true);api.get('/products',{params:{limit:2000,warehouse_id:warehouseId}}).then(r=>setProducts(r.data)).catch((e:any)=>{setProducts([]);setError(e.response?.data?.detail||'Seçili deponun ürünleri yüklenemedi.')}).finally(()=>setLoadingProducts(false))},[open,warehouseId]);
 useEffect(()=>{setEntitySummary(null);setLastPrices({});if(!open||!entity?.id)return;api.get(`/${kind==='sale'?'customers':'suppliers'}/${entity.id}`).then(r=>setEntitySummary(r.data.summary)).catch(()=>setEntitySummary(null))},[open,kind,entity?.id]);
 const loadLastPrice=async(productId:number)=>{
  if(!entity?.id||lastPrices[productId])return;
  try{
   const sale=kind==='sale';
   const r=await api.get(
    sale?'/orders/last-sale-price':'/purchases/last-purchase-price',
    {params:sale?{customer_id:entity.id,product_id:productId}:{supplier_id:entity.id,product_id:productId}}
   );
   setLastPrices(v=>({...v,[productId]:r.data}));
  }catch(e:any){
   if(e.response?.status!==404){
    setError(e.response?.data?.detail||(kind==='sale'?'Son satış fiyatı alınamadı.':'Son alış fiyatı alınamadı.'));
   }
  }
 };

 const gross=useMemo(()=>documentGrossDecimal(lines),[lines]);
 const manualAdjustment=useMemo(()=>{if(!manualTotal.trim())return null;try{return manualTotalDiscount(gross,manualTotal)}catch{return null}},[gross,manualTotal]);
 const manualTotalError=useMemo(()=>{if(!manualTotal.trim())return '';try{manualTotalDiscount(gross,manualTotal);return ''}catch(e:any){return e.message}},[gross,manualTotal]);
 const percentageDiscount=useMemo(()=>percentageDiscountDecimal(gross,discountPercent),[gross,discountPercent]);
 const total=manualAdjustment?.target??gross.minus(percentageDiscount);
 const vatTotal=useMemo(()=>documentVatDecimal(lines,total),[lines,total]);
 const allocationTotal=useMemo(()=>paymentAllocations.reduce((sum,item)=>sum.plus(decimal(item.amount||0)),decimal(0)),[paymentAllocations]);
 const effectivePaid=splitPayment?allocationTotal:decimal(paidAmount||0);
 const remaining=decimal(total).minus(effectivePaid).isNegative()?decimal(0):decimal(total).minus(effectivePaid);
 const accountTypeForMethod:Record<string,string>={cash:'cash',card:'pos',bank_transfer:'bank'};
 const compatibleAccounts=useMemo(()=>{const expected=accountTypeForMethod[paymentMethod];return expected?accounts.filter(a=>a.account_type===expected):[]},[accounts,paymentMethod]);
 const compatibleAccountsFor=(method:string)=>{const expected=accountTypeForMethod[method];return expected?accounts.filter(a=>a.account_type===expected):[]};
 const addPaymentAllocation=()=>setPaymentAllocations(v=>[...v,{payment_method:'cash',amount:0,account_id:''}]);
 const patchPaymentAllocation=(index:number,patch:Partial<PaymentAllocation>)=>setPaymentAllocations(v=>v.map((item,i)=>i===index?{...item,...patch}:item));
 const removePaymentAllocation=(index:number)=>setPaymentAllocations(v=>v.filter((_,i)=>i!==index));
 const patch=(i:number,p:Partial<Line>)=>{setNeedsOverride(false);setOverrideReason('');setLines(v=>v.map((x,n)=>n===i?{...x,...p}:x))};
 const selectProduct=(i:number,p:any|null)=>{if(!p){patch(i,{product_id:null,product_name:'',unit_price:'0',vat_rate:'20'});return}void loadLastPrice(Number(p.id));setLines(current=>{const duplicate=current.findIndex((x,n)=>n!==i&&x.product_id===p.id);if(duplicate>=0)return current.filter((_,n)=>n!==i).map((x,n)=>n===duplicate-(i<duplicate?1:0)?{...x,quantity:decimalPayload(decimal(x.quantity||0).plus(1))}:x);return current.map((x,n)=>n===i?{...x,product_id:p.id,product_name:p.name||'',unit_price:decimalPayload(kind==='sale'?p.sale_price:p.purchase_price),vat_rate:decimalPayload(p.vat_rate??20)}:x)});};
 // Ürün detayından gelen ön seçim: ürünler yüklendikten sonra ilk satıra bir kez uygulanır.
 // Mevcut selectProduct kullanılır, böylece fiyat/KDV/tekrar mantığı kopyalanmaz.
 useEffect(()=>{
  if(!open){presetProductRef.current=null;return}
  if(id||!initialProductId||!products.length)return;
  if(presetProductRef.current===initialProductId)return;
  const preset=products.find(x=>x.id===initialProductId);
  if(!preset)return;
  presetProductRef.current=initialProductId;
  selectProduct(0,preset);
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[open,id,initialProductId,products]);
 // Son satır dolduğunda boş satır kendiliğinden açılır; add() yalnızca yedek yol olarak kalır.
 useAutoTrailingLine(open,lines,setLines,blankLine);
 const add=()=>setLines(v=>[...v,blankLine()]);
 const focusLineField=(lineIndex:number,field:string)=>setTimeout(()=>{(document.querySelector(`[data-line="${lineIndex}"][data-field="${field}"] input`) as HTMLInputElement|null)?.focus()},40);
 const addAndFocusProduct=()=>{if(isLineBlank(lines[lines.length-1])){focusLineField(lines.length-1,'product');return}const nextIndex=lines.length;add();focusLineField(nextIndex,'product')};
 const remove=(i:number)=>setLines(v=>v.filter((_,n)=>n!==i));
 const stockOf=(pid:number|null)=>decimal(products.find(x=>x.id===pid)?.warehouse_stock||0);
 const scanProduct=(i:number,value:string)=>{const raw=value.trim();const query=raw.toLocaleLowerCase('tr-TR');if(!query)return false;const found=products.find(p=>String(p.barcode||'').trim().toLocaleLowerCase('tr-TR')===query||String(p.product_code||'').trim().toLocaleLowerCase('tr-TR')===query);if(!found){setError(`"${raw}" kodu veya barkoduyla ürün bulunamadı. Hızlı ürün kartı açıldı.`);const numeric=/^\d{6,}$/.test(raw);openQuickProduct(i,numeric?{barcode:raw}:{product_code:raw});return false}setError('');selectProduct(i,found);focusLineField(i+1,'product');return true};
 const openQuickProduct=(lineIndex:number,initial:Record<string,unknown>={})=>{setQuickProductLine(lineIndex);setQuickProductInitial(initial);setQuickProductOpen(true)};
 const refreshProductsAndSelect=async(created?:any)=>{const response=await api.get('/products',{params:{limit:2000,warehouse_id:warehouseId}});setProducts(response.data);const selected=created?.id?response.data.find((x:any)=>x.id===created.id):response.data[response.data.length-1];if(selected)selectProduct(quickProductLine,selected)};

 const save=async()=>{if(saving||loadingInitial||loadingProducts)return;try{setSaving(true);setError('');if(!entity)throw new Error('Cari seçin.');if(!warehouseId)throw new Error('Depo seçin.');if(!date)throw new Error('Belge tarihi zorunludur.');if(dueDate&&dueDate<date)throw new Error('Vade tarihi belge tarihinden önce olamaz.');
  const items=lines.filter(x=>x.product_id).map(x=>({product_id:x.product_id,quantity:decimalPayload(x.quantity),unit_price:decimalPayload(x.unit_price),vat_rate:decimalPayload(x.vat_rate),discount_percent:decimalPayload(x.discount_percent||0)}));
  if(!items.length)throw new Error('En az bir ürün ekleyin.');
  if(items.some(x=>!decimal(x.quantity).isFinite()||decimal(x.quantity).lessThanOrEqualTo(0)))throw new Error('Tüm miktarlar sıfırdan büyük olmalıdır.');
  if(items.some(x=>!decimal(x.unit_price).isFinite()||decimal(x.unit_price).isNegative()))throw new Error('Birim fiyat negatif olamaz.');
  if(items.some(x=>decimal(x.vat_rate).isNegative()||decimal(x.vat_rate).greaterThan(100)))throw new Error('KDV oranı 0 ile 100 arasında olmalıdır.');
  if(new Set(items.map(x=>x.product_id)).size!==items.length)throw new Error('Aynı ürünü birden fazla satırda eklemeyin; miktarı tek satırda birleştirin.');
  if(decimal(effectivePaid).greaterThan(total))throw new Error('Ödenen tutar belge toplamını aşamaz.');
  if(manualTotalError)throw new Error(manualTotalError);
  if(kind==='sale'&&paymentTerm==='HARMAN_VADELI'&&!dueDate)throw new Error('Harman vadeli satış için vade (harman) tarihi zorunludur.');
  if(splitPayment&&paymentAllocations.some(x=>decimal(x.amount).lessThanOrEqualTo(0)))throw new Error('Ödeme dağılımındaki tutarlar sıfırdan büyük olmalıdır.');
  const payload={entity_id:entity.id,transaction_date:date,document_no:doc||null,due_date:dueDate||null,note:note||null,warehouse_id:warehouseId,status,payment_method:splitPayment?'mixed':paymentMethod,payment_term:paymentTerm,paid_amount:decimalPayload(effectivePaid),payment_account_id:splitPayment?null:(paymentAccountId||null),payment_allocations:splitPayment?paymentAllocations.map(x=>({payment_method:x.payment_method,amount:decimalPayload(x.amount),account_id:x.account_id||null})):[],discount_percent:decimalPayload(manualAdjustment?.percent??discountPercent),...(manualAdjustment?{discount_amount:decimalPayload(manualAdjustment.discount)}:{}),items};
  const headers=overrideReason.trim()?policyOverrideHeaders(overrideReason):undefined;
  const path=kind==='sale'?'/orders':'/purchases';const response=id?await api.put(`${path}/${id}`,payload,{headers}):await api.post(path,payload,{headers});
  if(manualAdjustment&&!decimal(response.data.final_total).equals(manualAdjustment.target)){const difference=decimal(response.data.final_total).minus(manualAdjustment.target);setError(`Sunucu toplamı istenen tutarla uyuşmadı. Fark: ${money(difference.abs().toFixed(2))} (${difference.isPositive()?'fazla':'eksik'}). Belge oluşturuldu; tekrar kaydetmeyin.`);return}
  onSaved(response.data);onClose()
 }catch(e:any){const detail=e.response?.data?.detail||e.message;setError(detail);if(e.response?.status===409&&String(detail).includes('Yönetici onayıyla'))setNeedsOverride(true)}finally{setSaving(false)}};

 useEffect(()=>{if(!open)return;const onKey=(event:KeyboardEvent)=>{if(event.key==='F2'){event.preventDefault();firstProductInputRef.current?.focus()}else if(event.key==='F4'){event.preventDefault();entityInputRef.current?.focus()}else if(event.key==='F6'){event.preventDefault();paidInputRef.current?.focus()}else if(event.key==='F8'){event.preventDefault();addAndFocusProduct()}else if(event.key==='F9'){event.preventDefault();void save()}else if(event.key==='Escape'&&!saving){event.preventDefault();onClose()}};window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey)},[open,entity,warehouseId,date,dueDate,doc,note,status,paymentMethod,paymentAccountId,paidAmount,splitPayment,paymentAllocations,discountPercent,lines,overrideReason,saving]);

 return <>
 <Dialog open={open} onClose={saving?undefined:onClose} fullScreen={full} maxWidth="lg" fullWidth>
  <DialogTitle sx={{borderBottom:'1px solid',borderColor:'divider'}}><Stack direction="row" justifyContent="space-between" alignItems="center"><Box><Typography variant="h5">{id?'Belgeyi Düzenle':kind==='sale'?'Yeni Satış':'Yeni Alış'}</Typography><Typography variant="body2" color="text.secondary" fontWeight={400} mt={.4}>{kind==='sale'?'Müşteri, ürün ve ödeme bilgilerini tek akışta tamamlayın.':'Tedarikçi ve alış belgesi bilgilerini tamamlayın.'}</Typography></Box><Chip size="small" variant="outlined" label={id?'Düzenleme':'Yeni belge'}/></Stack></DialogTitle>
  <DialogContent sx={{px:{xs:1.2,sm:3},bgcolor:'background.default'}}><Stack spacing={2} mt={2}>
   {error&&<Alert severity="error" action={<Button color="inherit" size="small" onClick={()=>setError('')}>Kapat</Button>}>{error}</Alert>}{loadingInitial&&<Alert severity="info">Müşteri, depo ve ödeme hesapları yükleniyor...</Alert>}{needsOverride&&(user?.role==='admin'||user?.role==='yonetici')&&<TextField label="Yönetici onay gerekçesi" value={overrideReason} onChange={e=>setOverrideReason(e.target.value)} helperText="En az 5 karakter. İşlem yeniden kaydedildiğinde denetim kaydı oluşturulur." required/>}
   {!loadingInitial&&entities.length===0&&<Alert severity="warning">{kind==='sale'?'Henüz müşteri kartı yok. Hızlı Ekle ile ilk müşteriyi oluşturabilirsiniz.':'Henüz tedarikçi kartı yok. Hızlı Ekle ile ilk tedarikçiyi oluşturabilirsiniz.'}</Alert>}
   {!loadingInitial&&warehouses.length===0&&<Alert severity="error">Aktif depo bulunamadı. Belge kaydetmeden önce bir depo oluşturulmalıdır.</Alert>}
   <Typography variant="overline" color="primary.main" fontWeight={900} letterSpacing=".08em">1 · Belge ve cari bilgileri</Typography>
   <Stack direction={{xs:'column',md:'row'}} spacing={1.2}>
    <Stack direction="row" spacing={.7} sx={{flex:1}}><Autocomplete sx={{flex:1}} options={entities} value={entity} isOptionEqualToValue={(a,b)=>a.id===b.id} getOptionLabel={x=>x?.name||''} onChange={(_,v)=>setEntity(v)} renderInput={p=><TextField {...p} inputRef={entityInputRef} label={kind==='sale'?'Müşteri':'Tedarikçi'} size="small" helperText="F4 ile odaklan"/>}/><Button variant="outlined" startIcon={<PersonAddAltIcon/>} onClick={()=>setQuickEntityOpen(true)} sx={{whiteSpace:'nowrap'}}>Hızlı Ekle</Button></Stack>
    <TextField label="Tarih" type="date" value={date} onChange={e=>setDate(e.target.value)} size="small" slotProps={{inputLabel:{shrink:true}}}/>
    <TextField label={kind==='sale'&&paymentTerm==='HARMAN_VADELI'?'Harman Vade Tarihi':'Vade'} type="date" value={dueDate} onChange={e=>setDueDate(e.target.value)} size="small" inputProps={{min:date}} required={kind==='sale'&&paymentTerm==='HARMAN_VADELI'} error={kind==='sale'&&paymentTerm==='HARMAN_VADELI'&&!dueDate} helperText={kind==='sale'&&paymentTerm==='HARMAN_VADELI'?'Harman sonu ödeme tarihi':undefined} slotProps={{inputLabel:{shrink:true}}}/>
    <TextField label="Belge No" value={doc} onChange={e=>setDoc(e.target.value)} size="small"/>
    <TextField select label="Depo" value={warehouseId} onChange={e=>setWarehouseId(Number(e.target.value))} size="small" sx={{minWidth:190}}>{warehouses.map(w=><MenuItem key={w.id} value={w.id}><Stack direction="row" gap={1} alignItems="center"><WarehouseOutlinedIcon fontSize="small"/>{w.name}</Stack></MenuItem>)}</TextField>
   </Stack>
   <Typography variant="overline" color="primary.main" fontWeight={900} letterSpacing=".08em">2 · Ödeme bilgileri</Typography>
   <Stack direction={{xs:'column',md:'row'}} spacing={1.2}>
    <TextField select label="Belge Durumu" value={status} onChange={e=>setStatus(e.target.value)} size="small" sx={{minWidth:180}}><MenuItem value="draft">Taslak</MenuItem><MenuItem value="pending">Onay Bekliyor</MenuItem><MenuItem value="approved">Onaylandı</MenuItem><MenuItem value="completed">Tamamlandı</MenuItem><MenuItem value="cancelled">İptal</MenuItem></TextField>
    {kind==='sale'&&<TextField select label="Ödeme Vadesi" value={paymentTerm} onChange={e=>setPaymentTerm(e.target.value)} size="small" sx={{minWidth:180}}><MenuItem value="PESIN">Peşin</MenuItem><MenuItem value="HARMAN_VADELI">Harman Vadeli</MenuItem></TextField>}
    {!splitPayment&&<><TextField select label="Ödeme Yöntemi" value={paymentMethod} onChange={e=>{setPaymentMethod(e.target.value);setPaymentAccountId('')}} size="small" sx={{minWidth:180}}><MenuItem value="credit">Vadeli</MenuItem><MenuItem value="cash">Nakit</MenuItem><MenuItem value="card">Kredi Kartı / POS</MenuItem><MenuItem value="bank_transfer">Havale / EFT</MenuItem><MenuItem value="check">Çek</MenuItem><MenuItem value="promissory_note">Senet</MenuItem></TextField>
    {accountTypeForMethod[paymentMethod]&&<TextField select label="Kasa / Banka / POS" value={paymentAccountId} onChange={e=>setPaymentAccountId(Number(e.target.value))} size="small" sx={{minWidth:190}}><MenuItem value="">Otomatik hesap</MenuItem>{compatibleAccounts.map(a=><MenuItem key={a.id} value={a.id}>{a.name}</MenuItem>)}</TextField>}
    <TextField inputRef={paidInputRef} label="Ödenen Tutar" type="number" value={paidAmount} onChange={e=>setPaidAmount(e.target.value)} size="small" inputProps={{min:0}} helperText="F6 ile odaklan"/></>}
    <TextField label="Genel İskonto %" type="number" value={discountPercent} onChange={e=>{setDiscountPercent(e.target.value);setManualTotal('')}} size="small" inputProps={{min:0,max:100}}/>
    {kind==='sale'&&<TextField label="Genel Toplam" value={manualTotal||total.toFixed(2)} onFocus={()=>{if(!manualTotal)setManualTotal(total.toFixed(2))}} onChange={e=>{setManualTotal(e.target.value);setDiscountPercent(0)}} error={!!manualTotalError} helperText={manualTotalError||'Elle değişiklik belge iskontosu olarak kaydedilir.'} inputProps={{inputMode:'decimal','aria-label':'Elle genel toplam'}} size="small"/>}
    <Button variant={splitPayment?'contained':'outlined'} onClick={()=>{setSplitPayment(v=>!v);if(!splitPayment&&!paymentAllocations.length)setPaymentAllocations([{payment_method:'cash',amount:total.toFixed(2),account_id:''}])}}>Ödemeyi Böl</Button>
   </Stack>
   {splitPayment&&<Box sx={{p:1.5,border:'1px solid',borderColor:'divider',borderRadius:2}}><Stack spacing={1}><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography fontWeight={800}>Çoklu Ödeme Dağılımı</Typography><Button size="small" startIcon={<AddIcon/>} onClick={addPaymentAllocation}>Ödeme Ekle</Button></Stack>{paymentAllocations.map((item,index)=><Stack key={index} direction={{xs:'column',sm:'row'}} spacing={1}><TextField select label="Yöntem" size="small" value={item.payment_method} onChange={e=>patchPaymentAllocation(index,{payment_method:e.target.value,account_id:''})} sx={{minWidth:180}}><MenuItem value="cash">Nakit</MenuItem><MenuItem value="card">Kredi Kartı / POS</MenuItem><MenuItem value="bank_transfer">Havale / EFT</MenuItem><MenuItem value="check">Çek</MenuItem><MenuItem value="promissory_note">Senet</MenuItem></TextField>{accountTypeForMethod[item.payment_method]&&<TextField select label="Hesap" size="small" value={item.account_id} onChange={e=>patchPaymentAllocation(index,{account_id:Number(e.target.value)})} sx={{minWidth:200}}><MenuItem value="">Otomatik hesap</MenuItem>{compatibleAccountsFor(item.payment_method).map(a=><MenuItem key={a.id} value={a.id}>{a.name}</MenuItem>)}</TextField>}<TextField label="Tutar" type="number" size="small" value={item.amount} onChange={e=>patchPaymentAllocation(index,{amount:e.target.value})} inputProps={{min:0,step:0.01}}/><IconButton color="error" onClick={()=>removePaymentAllocation(index)}><DeleteOutlineIcon/></IconButton></Stack>)}<Stack direction="row" justifyContent="flex-end" spacing={2}><Typography>Dağıtılan: <b>{money(allocationTotal.toFixed(2))}</b></Typography><Typography color={allocationTotal.greaterThan(total)?'error.main':'text.primary'}>Kalan: <b>{money(decimal(total).minus(allocationTotal).isNegative()?'0.00':decimal(total).minus(allocationTotal).toFixed(2))}</b></Typography></Stack></Stack></Box>}
   {entitySummary&&<Alert severity={entitySummary.risk_exceeded?'error':'info'} icon={false}><Stack direction={{xs:'column',sm:'row'}} spacing={1} justifyContent="space-between" flexWrap="wrap"><Typography><b>Mevcut bakiye:</b> {money(entitySummary.current_balance)}</Typography><Typography><b>Risk limiti:</b> {decimal(entitySummary.risk_limit).greaterThan(0)?money(entitySummary.risk_limit):'Limitsiz'}</Typography><Typography><b>{kind==='sale'?'Bu satış sonrası':'Bu alış sonrası'}:</b> {money(decimal(entitySummary.current_balance).plus(remaining).toFixed(2))}</Typography>{decimal(entitySummary.risk_limit).greaterThan(0)&&<Chip size="small" color={decimal(entitySummary.current_balance).plus(remaining).greaterThan(entitySummary.risk_limit)?'error':'success'} label={`Kalan limit: ${money(decimal(entitySummary.risk_limit).minus(entitySummary.current_balance).minus(remaining).toFixed(2))}`}/>}</Stack></Alert>}
   {warehouseId&&!loadingProducts&&products.length===0&&<Alert severity="warning">Bu depoda satışa uygun ürün bulunamadı. Hızlı Ürün ile ilk ürünü oluşturabilirsiniz.</Alert>}
   {loadingProducts&&<Alert severity="info">Depo ürünleri yükleniyor...</Alert>}
   <Divider/>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1}><Box><Typography variant="overline" color="primary.main" fontWeight={900} letterSpacing=".08em">3 · Ürün kalemleri</Typography><Typography variant="body2" color="text.secondary">Ürün, miktar ve fiyat bilgilerini kontrol edin.</Typography></Box><Button size="small" variant="text" onClick={()=>setShowShortcuts(value=>!value)} aria-expanded={showShortcuts}>{showShortcuts?'Kısayolları gizle':'Klavye kısayolları'}</Button></Stack>
   <Collapse in={showShortcuts}><Alert severity="info" icon={false} sx={{py:.4}}><Typography variant="caption"><b>F2</b> ürün · <b>F4</b> cari · <b>F6</b> ödeme · <b>F8</b> satır ekle · <b>F9</b> kaydet · <b>Esc</b> kapat</Typography></Alert></Collapse>
   <Stack spacing={1.2}>{lines.map((line,i)=>{const stock=stockOf(line.product_id);const insufficient=kind==='sale'&&!!line.product_id&&decimal(line.quantity).greaterThan(stock);return <Stack key={i} direction={{xs:'column',md:'row'}} spacing={1} alignItems={{md:'center'}} sx={{p:1.2,border:'1px solid',borderColor:insufficient?'error.main':'divider',borderRadius:2}}>
    <Stack direction={{xs:'column',sm:'row'}} spacing={.7} sx={{flex:1,minWidth:{md:430}}}>
     <Autocomplete data-line={i} data-field="product" sx={{flex:1,minWidth:{md:320}}} options={products} value={products.find(x=>x.id===line.product_id)||null} getOptionLabel={x=>`${x.name}${x.product_code?` · ${x.product_code}`:''}`} onChange={(_,p)=>selectProduct(i,p)} renderOption={(props,p)=><li {...props} key={p.id}><Box sx={{flex:1}}><Typography fontWeight={700}>{p.name}</Typography><Typography variant="caption" color="text.secondary">{p.product_code||'Kodsuz'}{p.barcode?` · ${p.barcode}`:''} · Depo stok: {decimal(p.warehouse_stock||0).toString()} {p.unit||''}</Typography></Box></li>} renderInput={p=><TextField {...p} inputRef={i===0?firstProductInputRef:undefined} label="Ürün / Kod / Barkod" size="small" helperText={i===0?'F2 ile odaklan · Barkod okut ve Enter':'Barkod okut ve Enter'} onKeyDown={event=>{if(event.key==='Enter'){const value=(event.target as HTMLInputElement).value;if(value.trim()){event.preventDefault();event.stopPropagation();scanProduct(i,value)}}}}/>}/>
     <Button variant="outlined" color="success" startIcon={<AddBoxOutlinedIcon/>} onClick={()=>openQuickProduct(i)} sx={{whiteSpace:'nowrap',alignSelf:{sm:'flex-start'}}}>Hızlı Ürün</Button>
    </Stack>
    <TextField data-line={i} data-field="quantity" label="Miktar" type="number" size="small" value={line.quantity} onChange={e=>patch(i,{quantity:e.target.value})} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();focusLineField(i,'price')}}} inputProps={{min:0.001,step:0.001}} error={insufficient} helperText={line.product_id?`Stok: ${stock.toString()}`:''} sx={{width:{md:125}}}/>
    <TextField data-line={i} data-field="price" label="Birim Fiyat" type="number" size="small" value={line.unit_price} onChange={e=>patch(i,{unit_price:e.target.value})} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();focusLineField(i,'vat')}}} inputProps={{min:0,step:0.01}} sx={{width:{md:150}}}/>
    <TextField data-line={i} data-field="vat" label="KDV %" type="number" size="small" value={line.vat_rate} onChange={e=>patch(i,{vat_rate:e.target.value})} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();focusLineField(i,'discount')}}} inputProps={{min:0,max:100,step:1}} sx={{width:{md:90}}}/>
    <TextField data-line={i} data-field="discount" label="İsk. %" type="number" size="small" value={line.discount_percent} onChange={e=>patch(i,{discount_percent:e.target.value})} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();if(i===lines.length-1){addAndFocusProduct()}else focusLineField(i+1,'product')}}} sx={{width:{md:90}}} inputProps={{min:0,max:100}}/>
    <Stack minWidth={145}><Typography fontWeight={800}>{money(lineGrossDecimal(line).toFixed(2))}</Typography>{line.product_id&&lastPrices[line.product_id]&&<Button size="small" sx={{justifyContent:'flex-start',px:0,textTransform:'none'}} onClick={()=>patch(i,{unit_price:decimalPayload(lastPrices[line.product_id!].unit_price),discount_percent:decimalPayload(lastPrices[line.product_id!].discount_percent||0)})}>Son {kind==='sale'?'satış':'alış'}: {money(lastPrices[line.product_id].unit_price)} · {lastPrices[line.product_id].order_date||lastPrices[line.product_id].purchase_date}</Button>}</Stack>
    {line.product_id&&<Chip size="small" color={insufficient?'error':stock.lessThanOrEqualTo(decimal(products.find(x=>x.id===line.product_id)?.warehouse_critical_stock||0))?'warning':'success'} label={`${stock.toString()} stok`}/>}<IconButton color="error" aria-label="Satırı sil" onClick={()=>remove(i)} disabled={lines.length===1||(i===lines.length-1&&isLineBlank(line))}><DeleteOutlineIcon/></IconButton>
   </Stack>})}</Stack>
   <Button variant="outlined" startIcon={<AddIcon/>} onClick={addAndFocusProduct} sx={{alignSelf:'flex-start'}}>Kalem ekle · F8</Button>
   <TextField label="Not" multiline minRows={2} value={note} onChange={e=>setNote(e.target.value)}/>
   <Box sx={{ml:'auto',minWidth:{xs:'100%',sm:340},p:2,bgcolor:'action.hover',borderRadius:2}}><Stack spacing={.6}><Stack direction="row" justifyContent="space-between"><Typography color="text.secondary">Ara toplam</Typography><Typography fontWeight={700}>{money(gross.toFixed(2))}</Typography></Stack><Stack direction="row" justifyContent="space-between"><Typography color="text.secondary">KDV dahilindeki KDV</Typography><Typography fontWeight={700}>{money(vatTotal.toFixed(2))}</Typography></Stack><Stack direction="row" justifyContent="space-between"><Typography color="text.secondary">Genel iskonto</Typography><Typography fontWeight={700}>-{money((manualAdjustment?.discount??percentageDiscount).toFixed(2))}</Typography></Stack><Stack direction="row" justifyContent="space-between"><Typography color="text.secondary">Ödenen</Typography><Typography fontWeight={700}>{money(decimal(effectivePaid).toFixed(2))}</Typography></Stack><Divider/><Stack direction="row" justifyContent="space-between"><Typography variant="h6" fontWeight={900}>Genel toplam</Typography><Typography variant="h6" fontWeight={900}>{money(total.toFixed(2))}</Typography></Stack><Stack direction="row" justifyContent="space-between"><Typography fontWeight={800}>Kalan</Typography><Typography fontWeight={900} color={remaining.greaterThan(0)?'warning.main':'success.main'}>{money(remaining.toFixed(2))}</Typography></Stack>{!splitPayment&&paymentMethod!=='credit'&&<Button size="small" onClick={()=>setPaidAmount(total.toFixed(2))}>Tamamını ödendi yap</Button>}</Stack></Box>
  </Stack></DialogContent>
  {/* Dar ekranda butonlar alt alta. `minWidth:'100%'` satır düzeninde kapsayıcı
      genişliğinin TAMAMINI isteyip yanındaki "Vazgeç" + boşluk kadar taşıyordu
      (388px kapsayıcıda 452px içerik; birincil buton right=453). Kolon
      düzeninde genişliği `fullWidth` verir; MUI'nin kardeş `margin-left`i
      yalnız xs'te sıfırlanır. sm ve üzeri bugünkü satır düzeninde, 8px kardeş
      boşluğunda ve 260px asgari genişlikte kalır. */}
  <DialogActions sx={{position:'sticky',bottom:0,zIndex:2,bgcolor:'background.paper',borderTop:'1px solid',borderColor:'divider',px:2,py:1.5,gap:1,flexDirection:{xs:'column',sm:'row'},alignItems:{xs:'stretch',sm:'center'},justifyContent:{xs:'flex-start',sm:'space-between'},'& > :not(style) ~ :not(style)':{ml:{xs:0,sm:1}}}}>
   {/* Dokunma eşiği için xs'te 44px; `down('sm')` ile mobile hapsedilir. `{xs:44}`
       sm karşılığı olmadan masaüstüne de sızıyordu (1280px'te 44px). sm ve üzeri
       tema `MuiButton` özgün 40px'inde kalır. */}
   <Button onClick={onClose} disabled={saving} fullWidth sx={theme=>({width:{sm:'auto'},[theme.breakpoints.down('sm')]:{minHeight:44}})}>Vazgeç</Button>
   <Button variant="contained" color={kind==='sale'?'secondary':'primary'} size="large" startIcon={<PointOfSaleIcon/>} onClick={save} disabled={saving||loadingInitial||loadingProducts||warehouses.length===0} fullWidth sx={{width:{sm:'auto'},minWidth:{sm:260},fontWeight:900,boxShadow:kind==='sale'?'0 10px 24px rgba(245,196,0,.28)':4,'&:hover':kind==='sale'?{bgcolor:'#ffd329'}:undefined}}>{saving?'Kaydediliyor...':kind==='sale'?'SATIŞI TAMAMLA (F9)':'ALIŞI KAYDET (F9)'}</Button>
  </DialogActions>
 </Dialog>
 <ProductDialog open={quickProductOpen} initialValues={quickProductInitial} onClose={()=>{setQuickProductOpen(false);setQuickProductInitial({})}} onSaved={async created=>{setQuickProductOpen(false);setQuickProductInitial({});await refreshProductsAndSelect(created)}}/>
 <EntityDialog open={quickEntityOpen} type={kind==='sale'?'customer':'supplier'} onClose={()=>setQuickEntityOpen(false)} onSaved={(created)=>{setQuickEntityOpen(false);api.get(kind==='sale'?'/customers':'/suppliers').then(r=>{setEntities(r.data);const selected=created?.id?r.data.find((x:any)=>x.id===created.id):r.data[r.data.length-1];if(selected)setEntity(selected)})}}/>
 </>
}
