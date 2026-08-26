import {useEffect,useMemo,useRef,useState} from 'react';
import {Alert,Autocomplete,Box,Button,Card,CardContent,Chip,CircularProgress,Dialog,DialogActions,DialogContent,DialogTitle,FormControl,GlobalStyles,InputLabel,MenuItem,Select,Stack,TextField,Typography} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import PointOfSaleIcon from '@mui/icons-material/PointOfSale';
import TouchAppIcon from '@mui/icons-material/TouchApp';
import PrintIcon from '@mui/icons-material/Print';
import {api,errorDetail,money,policyOverrideHeaders,policyOverrideRequired} from '../api';
import OnScreenKeyboard,{type OnScreenKeyboardMode} from '../components/OnScreenKeyboard';
import PolicyOverrideDialog from '../components/PolicyOverrideDialog';
import ResponsiveTable from '../components/ResponsiveTable';
import type {components} from '../api/types.gen';
import {decimal,decimalPayload,documentGrossDecimal,lineGrossDecimal,manualTotalDiscount,percentageDiscountDecimal} from '../utils/documentMoney';

// unit_price is editable per line, so the cart keeps its own string copy: the
// product's list price stays untouched and the input can hold a half-typed
// value ("12,") without being coerced back on every keystroke.
type Product=components['schemas']['PosLookupProduct'];
type CartLine=Product&{quantity:string;price:string;discount:string};
// Tipler artık elle değil, backend OpenAPI şemasından üretiliyor
// (src/api/types.gen.ts; CI drift kontrolü şema ile tipleri senkron tutar).
// Kanonik kontrat: para/miktar alanları sabit ölçekli STRING ("12.50",
// "6.0000") — bkz. backend/app/pos_contracts.py. numeric() yine de
// string|number kabul eder: prod'daki "d.replace is not a function" çökmesi
// (sayı gelen unit_price) bir daha asla çalışma anına ulaşmamalı.
type QuickPick=components['schemas']['QuickPickItem'];
type Customer={id:number;name:string};
type Warehouse={id:number;name:string;is_default:boolean};
type PartSearchItem={id:number;name:string;product_code?:string|null;sale_price:string|number;warehouse_stock:string|number;match_type:string};
type PartSearchResponse={items:PartSearchItem[]};
type KeyboardSession={
 id:string;
 label:string;
 mode:OnScreenKeyboardMode;
 value:string;
 replaceOnNextKey:boolean;
 onChange:(value:string)=>void;
 onEnter?:(value:string)=>void;
};
type Receipt={saleId:number;customerName:string;items:{name:string;quantity:string;price:string;total:string}[];finalTotal:string;paidAmount:string;remainingAmount:string;tenderedAmount:string;paymentType:string;createdAt:string};
const numeric=(value:string|number)=>decimal(String(value).replace(',','.'));
const discountInRange=(value:string|number)=>{
 const normalized=String(value).trim();
 if(!normalized)return true;
 const parsed=decimal(normalized.replace(',','.'));
 return parsed.isFinite()&&parsed.greaterThanOrEqualTo(0)&&parsed.lessThanOrEqualTo(100);
};
const DISCOUNT_HELPER='iskonto 0–100 arası olmalı';
// Mirrors money.compute_line on the server: raw, then the line discount. The
// document discount is applied once over the sum, never folded into a line.
const lineNet=(line:CartLine)=>lineGrossDecimal({quantity:line.quantity,unit_price:line.price,discount_percent:line.discount});

// POS dokunmatik ölçeği. Bu ekran parmakla — çoğu zaman eldivenle ve tozlu
// ortamda — kullanılıyor; uygulamanın geri kalanındaki 40px'lik masaüstü
// yoğunluğu burada yanlış dokunuş üretiyor. #163'te ekran klavyesi için
// kurulan 44px sözleşmesi POS'un tamamına genişletildi (WCAG 2.5.5).
// Kapsam yalnız bu ekrandır: diğer sayfaların bilgi yoğunluğu korunur.
export const POS_TOUCH_MIN=44;
const POS_TOUCH_RENDERED_MIN=POS_TOUCH_MIN+1;
const posTouchSx={
 '& .MuiOutlinedInput-root':{minHeight:POS_TOUCH_RENDERED_MIN},
 // Chromium can resolve nominal 44px controls to 43.999969px at this
 // viewport; one physical CSS pixel of headroom keeps the measured box above the gate.
 '& .MuiInputBase-input':{minHeight:POS_TOUCH_RENDERED_MIN,boxSizing:'border-box'},
 '& .MuiIconButton-root':{minWidth:POS_TOUCH_RENDERED_MIN,minHeight:POS_TOUCH_RENDERED_MIN},
 '& .MuiButton-root':{minHeight:POS_TOUCH_RENDERED_MIN},
 '& .MuiChip-root':{minHeight:POS_TOUCH_RENDERED_MIN},
} as const;

export default function Pos(){
 const barcodeRef=useRef<HTMLInputElement>(null);
 const saleKeyRef=useRef<string|null>(null);
 // Re-entry guard. `busy` (state) is stale within a single synchronous tick, so
 // two clicks fired before React re-renders both read busy===false and would
 // each POST. This ref flips synchronously, so the second same-tick call bails
 // before issuing a duplicate request. The server Idempotency-Key stays as the
 // second line of defence for anything that still slips through.
 const submittingRef=useRef(false);
 const [barcode,setBarcode]=useState('');const [cart,setCart]=useState<CartLine[]>([]);
 const [warehouses,setWarehouses]=useState<Warehouse[]>([]);const [warehouseId,setWarehouseId]=useState<number|null>(null);const [cartWarehouseId,setCartWarehouseId]=useState<number|null>(null);
 // Arama/hızlı seçim sonuçları GELDİKLERİ depoyla birlikte tutulur. Depo
 // değişince eski sonuçlar ekranda kalırsa onlara tıklamak, A deposunun
 // stoğunu B deposu etiketiyle sepete sokuyordu: ekranda A'nın stoğu görünür,
 // satış B'den düşerdi. Etiket veriyle taşındığı için geç gelen bir yanıt bile
 // yanlış depoya yazılamaz.
 const [manualQuery,setManualQuery]=useState('');const [manualResults,setManualResults]=useState<{warehouseId:number|null;items:PartSearchItem[]}>({warehouseId:null,items:[]});const [manualBusy,setManualBusy]=useState(false);
 const [quickPicks,setQuickPicks]=useState<{warehouseId:number|null;items:QuickPick[]}>({warehouseId:null,items:[]});const [quickPickLoading,setQuickPickLoading]=useState(true);const [quickPickError,setQuickPickError]=useState(false);
 const [paymentType,setPaymentType]=useState('cash');const [note,setNote]=useState('');
 const [receivedAmount,setReceivedAmount]=useState('');
 const [customers,setCustomers]=useState<Customer[]>([]);const [customer,setCustomer]=useState<Customer|null>(null);
 const [customerInput,setCustomerInput]=useState('');
 const [docDiscount,setDocDiscount]=useState('0');
 const [manualTotal,setManualTotal]=useState('');
 const [keyboard,setKeyboard]=useState<KeyboardSession|null>(null);
 const [touchMode,setTouchMode]=useState(true);
 const [overrideOpen,setOverrideOpen]=useState(false);const [overrideDetail,setOverrideDetail]=useState('');const [overrideReason,setOverrideReason]=useState('');
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [success,setSuccess]=useState('');
 const [receipt,setReceipt]=useState<Receipt|null>(null);
 const focusScanner=()=>window.setTimeout(()=>barcodeRef.current?.focus(),0);
 const openKeyboard=(element:HTMLElement,session:Omit<KeyboardSession,'replaceOnNextKey'>)=>{
  if(touchMode!==true)return;
  setKeyboard({...session,replaceOnNextKey:session.mode==='numeric'});
  window.setTimeout(()=>{
   if(typeof element.scrollIntoView==='function')element.scrollIntoView({block:'center'});
  },0);
 };
 const chooseTouchMode=(enabled:boolean)=>{
  setTouchMode(enabled);
  if(!enabled)setKeyboard(null);
 };
 const syncKeyboardValue=(id:string,value:string)=>setKeyboard(current=>current?.id===id?{...current,value,replaceOnNextKey:false}:current);
 const updateFromKeyboard=(value:string)=>{
  if(!keyboard)return;
  keyboard.onChange(value);
  setKeyboard(current=>current?.id===keyboard.id?{...current,value,replaceOnNextKey:false}:current);
 };
 useEffect(()=>{focusScanner();void api.get<Warehouse[]>('/warehouses').then(({data})=>{const rows=Array.isArray(data)?data:[];setWarehouses(rows);setWarehouseId((rows.find(item=>item.is_default)||rows[0])?.id??null)}).catch(()=>setError('Depolar yüklenemedi.'))},[]);
 // Depo değişince eski hızlı seçimler DERHAL düşer ve uçuştaki istek abort
 // edilir; geç gelen bir yanıt yeni deponun listesini ezmesin. Yanıt yine de
 // istendiği depoyla etiketlenir (abort'u kaçıran yarış için ikinci savunma).
 useEffect(()=>{
  setQuickPicks({warehouseId:null,items:[]});
  if(!warehouseId)return;
  const controller=new AbortController();
  setQuickPickLoading(true);setQuickPickError(false);
  void api.get<QuickPick[]>('/quick-pick',{params:{limit:20,days:180,warehouse_id:warehouseId},signal:controller.signal})
   .then(({data})=>setQuickPicks({warehouseId,items:Array.isArray(data)?data:[]}))
   .catch(()=>{if(!controller.signal.aborted)setQuickPickError(true)})
   .finally(()=>{if(!controller.signal.aborted)setQuickPickLoading(false)});
  return()=>controller.abort();
 },[warehouseId]);
 // Customer list is optional context: a walk-in sale must still work if it fails.
 useEffect(()=>{void api.get<Customer[]>('/customers').then(({data})=>setCustomers(Array.isArray(data)?data:[])).catch(()=>setCustomers([]))},[]);
 const grossTotal=useMemo(()=>documentGrossDecimal(cart.map(line=>({quantity:line.quantity,unit_price:line.price,discount_percent:line.discount}))),[cart]);
 const manualAdjustment=useMemo(()=>{if(!manualTotal.trim())return null;try{return manualTotalDiscount(grossTotal,manualTotal)}catch{return null}},[grossTotal,manualTotal]);
 const manualTotalError=useMemo(()=>{if(!manualTotal.trim())return '';try{manualTotalDiscount(grossTotal,manualTotal);return ''}catch(e:any){return e.message}},[grossTotal,manualTotal]);
 const documentDiscount=useMemo(()=>manualAdjustment?.discount??percentageDiscountDecimal(grossTotal,docDiscount),[grossTotal,docDiscount,manualAdjustment]);
 const grandTotal=manualAdjustment?.target??grossTotal.minus(documentDiscount);
 const lineDiscountInvalid=cart.some(line=>!discountInRange(line.discount));
 const docDiscountInvalid=!discountInRange(docDiscount);
 const discountInvalid=lineDiscountInvalid||docDiscountInvalid;
 // Anonymous credit would leave nobody to collect from; the backend refuses it
 // with 400 and the button is blocked here so the counter sees it sooner.
 const creditNeedsCustomer=paymentType==='credit'&&!customer;
 const tenderedAmount=receivedAmount.trim()?numeric(receivedAmount):(paymentType==='credit'?decimal(0):grandTotal);
 const recordedPaidAmount=tenderedAmount.lessThan(grandTotal)?tenderedAmount:grandTotal;
 const partialNeedsCustomer=paymentType!=='credit'&&recordedPaidAmount.lessThan(grandTotal)&&!customer;
 const invalidReceivedAmount=receivedAmount.trim()!==''&&(!tenderedAmount.isFinite()||tenderedAmount.isNegative());
 const activeWarehouseId=cartWarehouseId??warehouseId;
 // Depo değişir değişmez eski arama sonuçları ekrandan kalkar: kullanıcı, artık
 // geçerli olmayan bir stok değerine tıklayamamalı. Sepetin ilk ürünü
 // eklendiğinde cartWarehouseId aynı değere kurulduğu için bu etki tetiklenmez.
 // Ref, uçuştaki isteğin .then'i içinde GÜNCEL depoyu okuyabilmek için: closure
 // içindeki activeWarehouseId istek anında donmuştur.
 const activeWarehouseRef=useRef<number|null>(null);
 useEffect(()=>{activeWarehouseRef.current=activeWarehouseId;setManualResults({warehouseId:null,items:[]})},[activeWarehouseId]);
 const addProduct=(product:Product):boolean=>{if(!activeWarehouseId||product.warehouse_id!==activeWarehouseId){setError('Ürün seçili depoyla eşleşmiyor. Sepeti boşaltıp tekrar deneyin.');return false}setCartWarehouseId(activeWarehouseId);setCart(current=>current.some(line=>line.id===product.id)?current.map(line=>line.id===product.id?{...line,quantity:numeric(line.quantity).plus(1).toString()}:line):[...current,{...product,quantity:'1',price:String(product.unit_price??0),discount:'0'}]);focusScanner();return true};
 // Sonucu KENDİ deposuyla ekler. Güncel depo değişmişse reddedilir; sonucu
 // güncel depoyla damgalamak addProduct'taki kontrolü etkisiz kılıyordu.
 const addFromResults=(sourceWarehouseId:number|null,product:Omit<Product,'warehouse_id'>):boolean=>{
  if(!activeWarehouseId||sourceWarehouseId!==activeWarehouseId){setError('Sonuçlar başka bir depoya ait. Depo değiştiyse aramayı yenileyin.');return false}
  return addProduct({...product,warehouse_id:sourceWarehouseId});
 };
 const patchLine=(id:number,patch:Partial<CartLine>)=>setCart(current=>current.map(line=>line.id===id?{...line,...patch}:line));
 const scan=async(rawValue=barcode)=>{
  const value=rawValue.trim();if(!value||busy)return;setKeyboard(null);setBusy(true);setError('');setSuccess('');
  if(!activeWarehouseId){setBusy(false);setError('Önce depo seçin.');return}
  try{const {data}=await api.get<Product>('/pos/lookup',{params:{barcode:value,input_source:'barcode_scanner',warehouse_id:activeWarehouseId}});addProduct(data);setBarcode('');syncKeyboardValue('barcode','')}
  catch(err:any){const detail=err?.response?.data?.detail;setError(detail?.code==='AMBIGUOUS_BARCODE'?`Bu barkod ${detail.candidate_count} aktif ürünle eşleşiyor; ürün kartlarını düzeltin.`:errorDetail(err,'Barkod okunamadı.'))}finally{setBusy(false);focusScanner()}
 };
 const manualSearch=async(rawQuery=manualQuery)=>{
  const q=rawQuery.trim();const requestedWarehouseId=activeWarehouseId;if(!q||!requestedWarehouseId)return;setKeyboard(null);setManualBusy(true);setError('');
  // Sonuçlar istendikleri depoyla etiketlenir; arama sırasında depo değiştiyse
  // yanıt kullanılmaz (geç gelen yanıt eski stoğu yeni depoya taşıyamaz).
  try{const {data}=await api.get<PartSearchResponse>('/search/parts',{params:{q,limit:20,warehouse_id:requestedWarehouseId}});
   // Yanıt gecikip depo bu arada değiştiyse sonuç TAMAMEN düşer; ekranda başka
   // deponun stoğu görünmez. Etiket yine de yazılır, çünkü ekleme anındaki
   // karşılaştırma son savunmadır.
   if(activeWarehouseRef.current===requestedWarehouseId)setManualResults({warehouseId:requestedWarehouseId,items:data.items||[]})}
  catch(err){setError(errorDetail(err,'Ürün araması yapılamadı.'))}finally{setManualBusy(false)}
 };
 const addManual=(item:PartSearchItem)=>{
  const added=addFromResults(manualResults.warehouseId,{id:item.id,name:item.name,code:item.product_code??null,unit_price:String(item.sale_price),stock:String(item.warehouse_stock)});
  if(added){setManualResults({warehouseId:null,items:[]});setManualQuery('');syncKeyboardValue('manual-search','')}
 };
 const completeSale=async(overrideReason?:string)=>{
  if(busy||submittingRef.current||!cart.length||cart.some(line=>numeric(line.quantity).lessThanOrEqualTo(0))||creditNeedsCustomer||partialNeedsCustomer||discountInvalid||invalidReceivedAmount||!!manualTotalError)return;submittingRef.current=true;setBusy(true);setError('');setSuccess('');
  const idempotencyKey=saleKeyRef.current||(saleKeyRef.current=globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random()}`);
  const headers={'Idempotency-Key':idempotencyKey,...(overrideReason?policyOverrideHeaders(overrideReason):{})};
  try{const receiptItems=cart.map(line=>({name:line.name,quantity:line.quantity,price:decimalPayload(line.price),total:lineNet(line).toFixed(2)}));const {data}=await api.post<components['schemas']['PosSaleResult']>('/pos/sale',{items:cart.map(line=>({product_id:line.id,quantity:String(line.quantity).replace(',','.'),unit_price:String(line.price).replace(',','.'),discount_percent:String(line.discount||0).replace(',','.')})),payment_type:paymentType,note:note.trim()||undefined,customer_id:customer?.id??null,warehouse_id:cartWarehouseId,discount_percent:decimalPayload(manualAdjustment?.percent??docDiscount),...(manualAdjustment?{discount_amount:decimalPayload(manualAdjustment.discount)}:{}),...(paymentType!=='credit'&&receivedAmount.trim()&&tenderedAmount.lessThan(grandTotal)?{paid_amount:tenderedAmount.toFixed(2)}:{})},{headers});
   if(manualAdjustment&&!decimal(data.final_total).equals(manualAdjustment.target)){const difference=decimal(data.final_total).minus(manualAdjustment.target);setError(`Sunucu toplamı istenen tutarla uyuşmadı. Fark: ${money(difference.abs().toFixed(2))} (${difference.isPositive()?'fazla':'eksik'}). Satış oluştu; tekrar göndermeyin.`);saleKeyRef.current=null;return}
   const serverTotal=decimal(data.final_total);if(data.paid_amount==null||data.remaining_amount==null)throw new Error('Sunucu ödeme bilgilerini döndürmedi; fiş oluşturulmadı.');const serverPaid=decimal(data.paid_amount);const serverRemaining=decimal(data.remaining_amount);
   setOverrideOpen(false);saleKeyRef.current=null;setReceipt({saleId:data.sale_id,customerName:data.customer_name||'Perakende Satış',items:receiptItems,finalTotal:serverTotal.toFixed(2),paidAmount:serverPaid.toFixed(2),remainingAmount:serverRemaining.toFixed(2),tenderedAmount:tenderedAmount.toFixed(2),paymentType,createdAt:new Date().toLocaleString('tr-TR')});setSuccess(`Satış #${data.sale_id} tamamlandı · ${data.customer_name} · ${money(data.final_total)}`);setCart([]);setCartWarehouseId(null);setNote('');setDocDiscount('0');setManualTotal('');setReceivedAmount('');setCustomer(null);setCustomerInput('');setKeyboard(null)}
   catch(err){if(policyOverrideRequired(err)){setOverrideDetail(errorDetail(err,'Yönetici onayı gerekiyor.'));setOverrideOpen(true)}else setError(errorDetail(err,'Satış tamamlanamadı.'))}finally{setBusy(false);submittingRef.current=false;focusScanner()}
 };
 const removeLine=(line:CartLine)=>setCart(current=>{const next=current.filter(item=>item.id!==line.id);if(!next.length)setCartWarehouseId(null);return next});
 const priceInput=(line:CartLine)=><TextField type="text" size="small" value={line.price} onFocus={event=>openKeyboard(event.currentTarget,{id:`line-${line.id}-price`,label:`${line.name} birim fiyat`,mode:'numeric',value:line.price,onChange:value=>patchLine(line.id,{price:value})})} onChange={event=>{patchLine(line.id,{price:event.target.value});syncKeyboardValue(`line-${line.id}-price`,event.target.value)}} inputProps={{inputMode:'decimal','aria-label':`${line.name} birim fiyat`}} sx={{width:120}}/>;
 const quantityInput=(line:CartLine)=><TextField type="text" size="small" value={line.quantity} onFocus={event=>openKeyboard(event.currentTarget,{id:`line-${line.id}-quantity`,label:`${line.name} miktar`,mode:'numeric',value:line.quantity,onChange:value=>patchLine(line.id,{quantity:value})})} onChange={event=>{patchLine(line.id,{quantity:event.target.value});syncKeyboardValue(`line-${line.id}-quantity`,event.target.value)}} inputProps={{inputMode:'decimal','aria-label':`${line.name} miktar`}} sx={{width:120}}/>;
 const discountInput=(line:CartLine)=>{const invalid=!discountInRange(line.discount);return <TextField type="text" size="small" value={line.discount} onFocus={event=>openKeyboard(event.currentTarget,{id:`line-${line.id}-discount`,label:`${line.name} iskonto`,mode:'numeric',value:line.discount,onChange:value=>patchLine(line.id,{discount:value})})} onChange={event=>{patchLine(line.id,{discount:event.target.value});syncKeyboardValue(`line-${line.id}-discount`,event.target.value)}} error={invalid} helperText={invalid?DISCOUNT_HELPER:undefined} inputProps={{inputMode:'decimal','aria-label':`${line.name} iskonto`}} sx={{width:150}}/>};
 const cartColumns:GridColDef[]=[
  {field:'name',headerName:'Ürün',minWidth:220,flex:1,renderCell:({row})=><Box><Typography fontWeight={700}>{row.name}</Typography><Typography variant="caption" color="text.secondary">{row.code||'Kod yok'} · Stok: {row.stock}{!numeric(row.price).equals(numeric(row.unit_price))?` · Liste: ${money(row.unit_price)}`:''}</Typography></Box>},
  {field:'price',headerName:'Birim fiyat',width:140,renderCell:({row})=>priceInput(row)},
  {field:'quantity',headerName:'Miktar',width:140,renderCell:({row})=>quantityInput(row)},
  {field:'discount',headerName:'İskonto %',width:170,renderCell:({row})=>discountInput(row)},
  {field:'total',headerName:'Toplam',width:140,valueGetter:(_value,row)=>lineNet(row).toFixed(2),valueFormatter:value=>money(value)},
 ];
 return <Stack spacing={2.5} sx={posTouchSx}>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{xs:'flex-start',sm:'center'}} gap={1.5}><Box><Typography variant="h4" fontWeight={800}>Hızlı Satış</Typography><Typography color="text.secondary">Barkodu okutun, Enter&apos;a basın ve satışı tamamlayın.</Typography></Box><Button variant="outlined" size="small" startIcon={<TouchAppIcon/>} onClick={()=>chooseTouchMode(touchMode!==true)}>{touchMode===true?'Dokunmatik klavye açık':'Dokunmatik klavye kapalı'}</Button></Stack>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}{success&&<Alert severity="success" onClose={()=>setSuccess('')}>{success}</Alert>}
  <FormControl size="small" sx={{maxWidth:360}} disabled={cart.length>0||!warehouses.length}><InputLabel id="pos-warehouse-label">Depo</InputLabel><Select labelId="pos-warehouse-label" label="Depo" value={warehouseId??''} onChange={event=>setWarehouseId(Number(event.target.value))}>{warehouses.map(item=><MenuItem key={item.id} value={item.id}>{item.name}{item.is_default?' · Varsayılan':''}</MenuItem>)}</Select></FormControl>
  <Card variant="outlined"><CardContent><Stack spacing={1.5}><Box><Typography variant="h6" fontWeight={800}>Hızlı Seçimler</Typography><Typography variant="body2" color="text.secondary">Son 180 gündeki sık satışlardan öğrenildi.</Typography></Box>
   {quickPickLoading?<Box sx={{display:'flex',alignItems:'center',gap:1,color:'text.secondary'}}><CircularProgress size={18}/><Typography variant="body2">Hızlı seçimler yükleniyor…</Typography></Box>
   :quickPickError?<Typography variant="body2" color="text.secondary">Hızlı seçimler şu anda yüklenemedi. Barkodla satışa devam edebilirsiniz.</Typography>
   :!quickPicks.items.length?<Typography variant="body2" color="text.secondary">Henüz hızlı seçim oluşturacak satış geçmişi yok.</Typography>
   :<Stack direction="row" useFlexGap flexWrap="wrap" gap={1}>{quickPicks.items.map(item=><Chip key={item.product_id} clickable label={`${item.product_name}${item.product_code?` · ${item.product_code}`:''}`} title={`${item.times_sold} satış · ${item.total_quantity} adet`} onClick={()=>addFromResults(quickPicks.warehouseId,{id:item.product_id,name:item.product_name,code:item.product_code,unit_price:item.unit_price,stock:item.current_stock})}/>)}</Stack>}
  </Stack></CardContent></Card>
  <Card variant="outlined"><CardContent><TextField inputRef={barcodeRef} fullWidth label="Barkod" placeholder="Barkodu okutun…" value={barcode} disabled={busy||!warehouseId} onFocus={event=>openKeyboard(event.currentTarget,{id:'barcode',label:'Barkod',mode:'numeric',value:barcode,onChange:setBarcode,onEnter:value=>void scan(value)})} onChange={event=>{setBarcode(event.target.value);syncKeyboardValue('barcode',event.target.value)}} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();void scan()}}} inputProps={{autoComplete:'off',inputMode:'numeric','aria-label':'Barkod'}}/></CardContent></Card>
  <Card variant="outlined"><CardContent><Stack spacing={1.5}><Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth label="Elle ürün / parça ara" value={manualQuery} onFocus={event=>openKeyboard(event.currentTarget,{id:'manual-search',label:'Ürün / parça arama',mode:'text',value:manualQuery,onChange:setManualQuery,onEnter:value=>void manualSearch(value)})} onChange={event=>{setManualQuery(event.target.value);syncKeyboardValue('manual-search',event.target.value)}} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();void manualSearch()}}}/><Button variant="outlined" disabled={manualBusy||!manualQuery.trim()||!activeWarehouseId} onClick={()=>void manualSearch()}>Ara</Button></Stack>
   {manualResults.items.map(item=><Button key={item.id} variant="text" sx={{justifyContent:'space-between'}} onClick={()=>addManual(item)}><span>{item.name}{item.product_code?` · ${item.product_code}`:''}</span><span>{item.match_type.endsWith('_ocr')?'Benzer / OCR':'Doğrudan eşleşme'} · Stok {item.warehouse_stock}</span></Button>)}
  </Stack></CardContent></Card>
   <Card variant="outlined"><Box data-testid="pos-cart-data-surface" sx={{p:1.5}}>
    <ResponsiveTable
     rows={cart}
     columns={cartColumns}
     cardTitle={line=>line.name}
     cardSubtitle={line=>`${line.code||'Kod yok'} · Stok: ${line.stock}`}
     cardFields={[
      {label:'Birim fiyat',value:priceInput},
      {label:'Miktar',value:quantityInput},
      {label:'İskonto %',value:discountInput},
      {label:'Toplam',value:line=>money(lineNet(line).toFixed(2))},
     ]}
     cardActions={[{label:'Sil',ariaLabel:line=>`${line.name} sil`,icon:<DeleteOutlineIcon/>,color:'error',onClick:removeLine}]}
     cardActionsOnDesktop
     getRowId={line=>line.id}
     desktopRowHeight={76}
    />
   </Box></Card>
  <Card variant="outlined"><CardContent><Stack spacing={2}>
   <Stack direction={{xs:'column',md:'row'}} spacing={2} alignItems={{md:'center'}}>
    {/* Empty = "Perakende Satış" system cari, the pre-existing walk-in behaviour. */}
    <Autocomplete sx={{flex:1,minWidth:240}} options={customers} value={customer} inputValue={customerInput} isOptionEqualToValue={(a,b)=>a.id===b.id} getOptionLabel={option=>option?.name||''} onInputChange={(_,value,reason)=>{setCustomerInput(value);if(reason==='input')setCustomer(null);syncKeyboardValue('customer',value)}} onChange={(_,value)=>{setCustomer(value);setCustomerInput(value?.name||'');syncKeyboardValue('customer',value?.name||'')}} renderInput={params=><TextField {...params} label="Müşteri" placeholder="Perakende Satış" size="small" onFocus={event=>openKeyboard(event.currentTarget,{id:'customer',label:'Müşteri arama',mode:'text',value:customerInput,onChange:value=>{setCustomerInput(value);setCustomer(null)}})} error={creditNeedsCustomer||partialNeedsCustomer} helperText={creditNeedsCustomer?'Veresiye satış için müşteri seçin.':partialNeedsCustomer?'Kalan tutarı bakiyeye yazmak için müşteri seçin.':'Boş bırakılırsa perakende satış olarak kaydedilir.'}/>}/>
    <FormControl size="small" sx={{minWidth:190}}><InputLabel id="pos-payment-label">Ödeme</InputLabel><Select labelId="pos-payment-label" id="pos-payment" label="Ödeme" value={paymentType} onChange={event=>{setPaymentType(event.target.value);if(event.target.value==='credit')setReceivedAmount('')}}><MenuItem value="cash">Nakit</MenuItem><MenuItem value="card">Kart</MenuItem><MenuItem value="bank_transfer">Havale / EFT</MenuItem><MenuItem value="credit">Veresiye</MenuItem></Select></FormControl>
    <TextField label="Alınan tutar" type="text" size="small" value={receivedAmount} disabled={paymentType==='credit'} placeholder={grandTotal.toFixed(2)} onFocus={event=>openKeyboard(event.currentTarget,{id:'received-amount',label:'Alınan tutar',mode:'numeric',value:receivedAmount,onChange:setReceivedAmount})} onChange={event=>{setReceivedAmount(event.target.value);syncKeyboardValue('received-amount',event.target.value)}} error={invalidReceivedAmount||partialNeedsCustomer} helperText={partialNeedsCustomer?`${money(grandTotal.minus(recordedPaidAmount).toFixed(2))} müşteri bakiyesine yazılacak.`:tenderedAmount.greaterThan(grandTotal)?`${money(tenderedAmount.minus(grandTotal).toFixed(2))} para üstü`:'Boş bırakılırsa tamamı tahsil edilir.'} inputProps={{inputMode:'decimal','aria-label':'Alınan tutar'}} sx={{width:210}}/>
    <TextField label="Belge iskontosu %" type="text" size="small" value={docDiscount} onFocus={event=>openKeyboard(event.currentTarget,{id:'document-discount',label:'Belge iskontosu',mode:'numeric',value:docDiscount,onChange:value=>{setDocDiscount(value);setManualTotal('')}})} onChange={event=>{setDocDiscount(event.target.value);setManualTotal('');syncKeyboardValue('document-discount',event.target.value)}} error={docDiscountInvalid} helperText={docDiscountInvalid?DISCOUNT_HELPER:undefined} inputProps={{inputMode:'decimal','aria-label':'Belge iskontosu'}} sx={{width:200}}/>
    <TextField label="Genel toplam" type="text" size="small" value={manualTotal||grandTotal.toFixed(2)} onFocus={event=>{if(!manualTotal)setManualTotal(grandTotal.toFixed(2));openKeyboard(event.currentTarget,{id:'manual-total',label:'Genel toplam',mode:'numeric',value:manualTotal||grandTotal.toFixed(2),onChange:value=>{setManualTotal(value);setDocDiscount('0')}})}} onChange={event=>{setManualTotal(event.target.value);setDocDiscount('0');syncKeyboardValue('manual-total',event.target.value)}} error={!!manualTotalError} helperText={manualTotalError||'Fark belge iskontosu olarak kaydedilir.'} inputProps={{inputMode:'decimal','aria-label':'Elle genel toplam'}} sx={{width:220}}/>
    <TextField label="Not (isteğe bağlı)" size="small" value={note} onFocus={event=>openKeyboard(event.currentTarget,{id:'note',label:'Satış notu',mode:'text',value:note,onChange:setNote})} onChange={event=>{setNote(event.target.value);syncKeyboardValue('note',event.target.value)}} sx={{flex:1,minWidth:180}}/>
   </Stack>
   <Stack direction={{xs:'column',md:'row'}} spacing={2} alignItems={{md:'center'}} justifyContent="flex-end">
    <Box sx={{textAlign:{md:'right'}}}>
     {(documentDiscount.greaterThan(0)||cart.some(line=>numeric(line.discount).greaterThan(0)))&&<Typography variant="body2" color="text.secondary">Ara toplam {money(grossTotal.toFixed(2))}{documentDiscount.greaterThan(0)?` · Belge iskontosu −${money(documentDiscount.toFixed(2))}`:''}</Typography>}
     <Typography variant="caption" color="text.secondary">GENEL TOPLAM</Typography><Typography variant="h4" fontWeight={900}>{money(grandTotal.toFixed(2))}</Typography>
    </Box>
    <Button variant="contained" size="large" startIcon={<PointOfSaleIcon/>} disabled={busy||!cart.length||cart.some(line=>numeric(line.quantity).lessThanOrEqualTo(0))||creditNeedsCustomer||partialNeedsCustomer||discountInvalid||invalidReceivedAmount||!!manualTotalError} onClick={()=>void completeSale()} sx={{minHeight:56,minWidth:210,fontWeight:800}}>Satışı Tamamla</Button>
   </Stack>
  </Stack></CardContent></Card>
  {keyboard&&<>
   <Box aria-hidden sx={{height:keyboard.mode==='numeric'?330:414,flexShrink:0}}/>
   <OnScreenKeyboard
    mode={keyboard.mode}
    value={keyboard.value}
    activeLabel={keyboard.label}
    replaceOnNextKey={keyboard.replaceOnNextKey}
    onChange={updateFromKeyboard}
    onEnter={keyboard.onEnter?()=>keyboard.onEnter?.(keyboard.value):undefined}
    onClose={()=>setKeyboard(null)}
   />
  </>}
  <PolicyOverrideDialog open={overrideOpen} detail={overrideDetail} reason={overrideReason} busy={busy} onReasonChange={setOverrideReason} onClose={()=>{setOverrideOpen(false);setOverrideReason('')}} onConfirm={()=>void completeSale(overrideReason)}/>
  <GlobalStyles styles={{'@media print':{'body *':{visibility:'hidden!important'},'#pos-receipt, #pos-receipt *':{visibility:'visible!important'},'#pos-receipt':{position:'absolute!important',left:0,top:0,width:'80mm!important',boxShadow:'none!important',border:0}}}}/>
  <Dialog open={!!receipt} onClose={()=>setReceipt(null)} maxWidth="xs" fullWidth>
   <DialogTitle>Satış tamamlandı</DialogTitle>
   <DialogContent>
    {receipt&&<Box id="pos-receipt" sx={{p:2,color:'#111',bgcolor:'#fff'}}>
     <Typography textAlign="center" fontWeight={950} fontSize={20}>SUNGUR TARIM</Typography><Typography textAlign="center" variant="body2">Satış Fişi #{receipt.saleId}</Typography><Typography textAlign="center" variant="caption" display="block" mb={2}>{receipt.createdAt}</Typography>
     <Stack spacing={.8}>{receipt.items.map((item,index)=><Box key={`${item.name}-${index}`} pb={.8} borderBottom="1px dashed #bbb"><Typography fontWeight={750}>{item.name}</Typography><Box display="flex" justifyContent="space-between"><Typography variant="body2">{item.quantity} × {money(item.price)}</Typography><Typography variant="body2" fontWeight={800}>{money(item.total)}</Typography></Box></Box>)}</Stack>
     <Stack spacing={.5} mt={2}><Box display="flex" justifyContent="space-between"><Typography fontWeight={900}>TOPLAM</Typography><Typography fontWeight={900}>{money(receipt.finalTotal)}</Typography></Box><Box display="flex" justifyContent="space-between"><Typography>Alınan</Typography><Typography>{money(receipt.paidAmount)}</Typography></Box>{decimal(receipt.remainingAmount).greaterThan(0)&&<Box display="flex" justifyContent="space-between"><Typography>Kalan bakiye</Typography><Typography fontWeight={850}>{money(receipt.remainingAmount)}</Typography></Box>}{decimal(receipt.tenderedAmount).greaterThan(receipt.finalTotal)&&<Box display="flex" justifyContent="space-between"><Typography>Para üstü</Typography><Typography fontWeight={850}>{money(decimal(receipt.tenderedAmount).minus(receipt.finalTotal).toFixed(2))}</Typography></Box>}</Stack>
     <Typography mt={2} variant="body2">Müşteri: {receipt.customerName}</Typography><Typography variant="body2">Ödeme: {receipt.paymentType}</Typography><Typography textAlign="center" variant="caption" display="block" mt={3}>Teşekkür ederiz.</Typography>
    </Box>}
   </DialogContent>
   <DialogActions><Button onClick={()=>setReceipt(null)}>Kapat</Button><Button variant="contained" startIcon={<PrintIcon/>} onClick={()=>window.print()}>Fiş Yazdır</Button></DialogActions>
  </Dialog>
 </Stack>;
}
