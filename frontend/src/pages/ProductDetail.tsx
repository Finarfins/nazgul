import React from 'react';
import {useEffect,useMemo,useState,type ReactNode} from 'react';
import {Alert,Box,Button,Card,CardContent,Chip,CircularProgress,Divider,Stack,Tab,Tabs,Typography} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import EditIcon from '@mui/icons-material/Edit';
import QrCode2Icon from '@mui/icons-material/QrCode2';
import LocalPrintshopIcon from '@mui/icons-material/LocalPrintshop';
import Inventory2Icon from '@mui/icons-material/Inventory2';
import TimelineIcon from '@mui/icons-material/Timeline';
import ShoppingCartIcon from '@mui/icons-material/ShoppingCart';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import PointOfSaleIcon from '@mui/icons-material/PointOfSale';
import AddShoppingCartIcon from '@mui/icons-material/AddShoppingCart';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import PriceCheckIcon from '@mui/icons-material/PriceCheck';
import {useNavigate,useParams} from 'react-router-dom';
import ProductDialog from '../components/ProductDialog';
import ProductLabelDialog from '../components/ProductLabelDialog';
import SupplierPricesPanel from '../components/SupplierPricesPanel';
import TransactionDialog from '../components/TransactionDialog';
import TransferDialog from '../components/TransferDialog';
import {api,errorDetail,money,openAuthenticated} from '../api';
import {movementLabel,movementTarget} from '../utils/stock';

// Re-exported so existing tests importing it from this module keep working.
export {movementTarget};

const splitTags=(value?:string)=>String(value||'').split(/[\n,;]+/).map(x=>x.trim()).filter(Boolean);

const labelSx={fontSize:13.5,lineHeight:1.45,color:'text.secondary'} as const;
const metaSx={fontSize:13.5,lineHeight:1.5,color:'text.secondary'} as const;

function Metric({label,value,color,hint}:{label:string;value:ReactNode;color?:string;hint?:string}){
 return <Card variant="outlined" sx={{flex:'1 1 168px',minWidth:150}}><CardContent sx={{p:1.75,'&:last-child':{pb:1.75}}}>
  <Typography sx={labelSx} fontWeight={700}>{label}</Typography>
  <Typography fontWeight={900} color={color} sx={{fontSize:{xs:20,sm:23},mt:.35,wordBreak:'break-word'}}>{value}</Typography>
  {hint?<Typography sx={metaSx}>{hint}</Typography>:null}
 </CardContent></Card>;
}

/** Satır: hedefi olmayan kayıtlar etkileşimsiz kalır, olanlar klavyeyle de açılır. */
function Row({onOpen,ariaLabel,children}:{onOpen?:()=>void;ariaLabel?:string;children:ReactNode}){
 if(!onOpen)return <Box sx={{p:1.5,borderBottom:'1px solid',borderColor:'divider'}}>{children}</Box>;
 return <Box role="button" tabIndex={0} aria-label={ariaLabel} onClick={onOpen}
  onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onOpen()}}}
  sx={{p:1.5,borderBottom:'1px solid',borderColor:'divider',borderRadius:1,cursor:'pointer',
   '&:hover':{bgcolor:'action.hover'},
   '&:focus-visible':{outline:'2px solid',outlineColor:'primary.main',outlineOffset:2}}}>{children}</Box>;
}

/**
 * Satış/alış geçmişi satırı. Belge ve cari bağımsız düğmelerdir; kararlı bir kimlik
 * gelmediğinde ilgili düğme düz metne düşer, sahte bağlantı üretilmez.
 */
function HistoryRow({document,entity,meta,unitPrice,lineTotal,onOpenDocument,documentAria,onOpenEntity,entityAria}:{
 document:string;entity:string;meta:string;unitPrice:number|string|null;lineTotal:number|string|null;
 onOpenDocument?:()=>void;documentAria:string;onOpenEntity?:()=>void;entityAria:string;
}){
 return <Box sx={{p:1.5,borderBottom:'1px solid',borderColor:'divider'}}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1}>
   <Box>
    {onOpenDocument
     ?<Button size="small" onClick={onOpenDocument} aria-label={documentAria} sx={{px:.75,minWidth:0,fontSize:15,fontWeight:800}}>{document}</Button>
     :<Typography fontWeight={800} sx={{fontSize:15}}>{document}</Typography>}
    <Typography sx={metaSx}>{meta}</Typography>
    {onOpenEntity
     ?<Button size="small" color="secondary" onClick={onOpenEntity} aria-label={entityAria} sx={{px:.75,minWidth:0,fontSize:13.5}}>{entity}</Button>
     :<Typography sx={{fontSize:14,fontWeight:700}}>{entity}</Typography>}
   </Box>
   <Box textAlign={{sm:'right'}}>
    <Typography fontWeight={900} sx={{fontSize:16}}>{money(unitPrice)}</Typography>
    <Typography sx={metaSx}>Satır toplamı: {money(lineTotal)}</Typography>
   </Box>
  </Stack>
 </Box>;
}

function Empty({children}:{children:ReactNode}){
 return <Typography sx={{fontSize:14,color:'text.secondary',py:2.5}}>{children}</Typography>;
}

export default function ProductDetail(){
 const {id}=useParams();
 const productId=Number(id);
 const nav=useNavigate();
 const [data,setData]=useState<any|null>(null);
 const [error,setError]=useState('');
 const [tab,setTab]=useState(0);
 const [editOpen,setEditOpen]=useState(false);
 const [txnKind,setTxnKind]=useState<'sale'|'purchase'|null>(null);
 const [transferOpen,setTransferOpen]=useState(false);
 const [labelOpen,setLabelOpen]=useState(false);
 const [warehouses,setWarehouses]=useState<any[]>([]);
 const load=()=>{setData(null);setError('');api.get(`/products/${productId}`).then(r=>setData(r.data)).catch(e=>setError(errorDetail(e,'Ürün bilgileri yüklenemedi.')));};
 useEffect(load,[productId]);
 // Parça supersession: eski parça numarası güncel parçaya çözülüyorsa başlıkta
 // "X yerine Y geçti" uyarısı gösterilir. Hata sayfayı bloklamaz.
 const [supersession,setSupersession]=useState<any|null>(null);
 useEffect(()=>{setSupersession(null);api.get(`/products/${productId}/current`).then(r=>{if(r.data?.resolved)setSupersession(r.data)}).catch(()=>setSupersession(null));},[productId]);
 // Warehouse list is only needed for the transfer dialog; its failure must not block the page.
 useEffect(()=>{api.get('/warehouses').then(r=>setWarehouses(r.data)).catch(()=>setWarehouses([]));},[]);

 const p=data?.product;
 const condition=useMemo(()=>{
  if(!p)return null;
  const stock=Number(p.stock||0);
  // Kritik eşiği panodaki kuralla aynı: critical_stock ve minimum_stock'un büyüğü.
  const threshold=Math.max(Number(p.critical_stock||0),Number(p.minimum_stock||0));
  if(stock<0)return {key:'negative',label:'Negatif Stok',color:'error' as const,icon:<ErrorOutlineIcon/>,threshold,shortage:threshold-stock};
  if(stock===0)return {key:'out',label:'Stok Yok',color:'error' as const,icon:<ErrorOutlineIcon/>,threshold,shortage:threshold};
  if(threshold>0&&stock<=threshold)return {key:'critical',label:'Kritik',color:'warning' as const,icon:<WarningAmberIcon/>,threshold,shortage:threshold-stock};
  return {key:'normal',label:'Normal',color:'success' as const,icon:<CheckCircleOutlineIcon/>,threshold,shortage:0};
 },[p]);

 if(error)return <Alert severity="error">{error}</Alert>;
 if(!data||!p||!condition)return <Box textAlign="center" p={5}><CircularProgress aria-label="Ürün yükleniyor"/></Box>;

 const summary=data.commercial_summary||{};
 const movements:any[]=data.movements||[];
 const warehouseStocks:any[]=data.warehouse_stocks||[];
 const salesHistory:any[]=data.sales_history||[];
 const purchaseHistory:any[]=data.purchase_history||[];
 // Geçmişler tarihe göre azalan sıralı geldiği için ilk satır en son işlemdir.
 const lastSaleDate=salesHistory[0]?.order_date||null;
 const lastPurchaseDate=purchaseHistory[0]?.purchase_date||null;
 const unit=p.unit||'';
 const alternativeOems=splitTags(p.alternative_oem);
 const compatibleModels=splitTags(p.compatible_models);
 const openSale=()=>setTxnKind('sale');
 const openPurchase=()=>setTxnKind('purchase');

 return <Stack spacing={2}>
  <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav(-1)} sx={{alignSelf:'flex-start'}}>Geri Dön</Button>

  <Card><CardContent>
   <Stack direction={{xs:'column',lg:'row'}} justifyContent="space-between" gap={2}>
    <Box>
     <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Typography variant="h4" fontWeight={900} sx={{fontSize:{xs:24,sm:30}}}>{p.name}</Typography>
      {p.category&&<Chip label={p.category} color="primary" size="small"/>}
      {p.oem_number&&<Chip label={`OEM: ${p.oem_number}`} color="secondary" size="small"/>}
      {/* Renk tek başına anlam taşımasın diye durum metinle ve ikonla birlikte verilir. */}
      <Chip icon={condition.icon} label={`Stok durumu: ${condition.label}`} color={condition.color} size="small"/>
      {p.active===false&&<Chip label="Pasif Ürün" size="small" variant="outlined"/>}
      {supersession&&<Chip icon={<SwapHorizIcon/>} color="warning" size="small"
        label={supersession.note||`Güncel parça: ${supersession.current?.product_code||supersession.current?.name}`}
        onClick={()=>nav(`/urunler/${supersession.current?.id}`)} sx={{cursor:'pointer'}}/>}
     </Stack>
     <Typography sx={{...metaSx,mt:.75}}>
      Kod: {p.product_code||'-'} · Barkod: {p.barcode||'-'} · Birim: {unit||'-'} · KDV %{p.vat_rate} · Raf: {p.location||'-'}
     </Typography>
    </Box>
    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{alignItems:'flex-start'}}>
     <Button variant="contained" color="success" startIcon={<PointOfSaleIcon/>} onClick={openSale}>Satış Oluştur</Button>
     <Button variant="contained" startIcon={<AddShoppingCartIcon/>} onClick={openPurchase}>Alış Oluştur</Button>
     <Button variant="outlined" startIcon={<SwapHorizIcon/>} onClick={()=>setTransferOpen(true)} disabled={warehouses.length<2}>Depoya Transfer</Button>
     <Button variant="outlined" startIcon={<EditIcon/>} onClick={()=>setEditOpen(true)}>Ürünü Düzenle</Button>
     <Button startIcon={<QrCode2Icon/>} onClick={()=>openAuthenticated(`/products/${productId}/qr.png`)}>QR</Button>
     <Button startIcon={<LocalPrintshopIcon/>} onClick={()=>setLabelOpen(true)}>Etiket Yazdır</Button>
    </Stack>
   </Stack>

   <Divider sx={{my:2}}/>
   <Stack direction="row" spacing={1.2} flexWrap="wrap" useFlexGap>
    <Metric label="TOPLAM STOK" value={`${Number(p.stock||0)} ${unit}`} color={condition.key==='normal'?'text.primary':'error.main'} hint={condition.threshold>0?`Kritik seviye: ${condition.threshold} ${unit}`:'Kritik seviye tanımsız'}/>
    <Metric label="SATIŞ FİYATI" value={money(p.sale_price)} color="primary.main"/>
    <Metric label="ALIŞ FİYATI" value={money(p.purchase_price)}/>
    <Metric label="SON SATIŞ FİYATI" value={summary.last_sale_price==null?'-':money(summary.last_sale_price)} color="primary.main" hint={lastSaleDate?`Son satış: ${lastSaleDate}`:'Henüz satılmadı'}/>
    <Metric label="SON ALIŞ FİYATI" value={summary.last_purchase_price==null?'-':money(summary.last_purchase_price)} hint={lastPurchaseDate?`Son alış: ${lastPurchaseDate}`:'Henüz alınmadı'}/>
    <Metric label="TOPLAM SATILAN" value={`${Number(summary.total_sold_quantity||0)} ${unit}`} color="success.main"/>
    <Metric label="TOPLAM ALINAN" value={`${Number(summary.total_purchased_quantity||0)} ${unit}`}/>
   </Stack>
  </CardContent></Card>

  {condition.key!=='normal'&&<Alert severity={condition.color==='warning'?'warning':'error'}
   action={<Button color="inherit" size="small" startIcon={<AddShoppingCartIcon/>} onClick={openPurchase}>Alış Oluştur</Button>}>
   <Typography fontWeight={800} sx={{fontSize:14.5}}>
    {condition.key==='negative'?'Negatif stok: kayıtlar sistemde eksi görünüyor.':condition.key==='out'?'Stok tükendi.':'Stok kritik seviyede.'}
   </Typography>
   <Typography sx={{fontSize:14}}>
    Mevcut stok {Number(p.stock||0)} {unit}
    {condition.threshold>0?` · Kritik seviye ${condition.threshold} ${unit} · Eksik ${Number(condition.shortage.toFixed(4))} ${unit}`:' · Kritik seviye tanımlı değil'}
   </Typography>
  </Alert>}

  <Card><CardContent>
   <Typography variant="h6" fontWeight={900} gutterBottom>Teknik ve Uyumluluk Bilgileri</Typography>
   <Stack spacing={1.75}>
    <Stack direction={{xs:'column',md:'row'}} spacing={2}>
     <Box flex={1}><Typography sx={labelSx} fontWeight={700}>Marka / Üretici</Typography><Typography fontWeight={800} sx={{fontSize:15}}>{[p.brand,p.manufacturer].filter(Boolean).join(' / ')||'-'}</Typography></Box>
     <Box flex={1}><Typography sx={labelSx} fontWeight={700}>Raf / Konum</Typography><Typography fontWeight={800} sx={{fontSize:15}}>{p.location||'-'}</Typography></Box>
    </Stack>
    <Box><Typography sx={labelSx} fontWeight={700}>Alternatif OEM</Typography><Stack direction="row" gap={1} flexWrap="wrap" useFlexGap mt={.5}>{alternativeOems.length?alternativeOems.map(x=><Chip key={x} label={x} size="small"/>):<Typography sx={{fontSize:14}}>-</Typography>}</Stack></Box>
    <Box><Typography sx={labelSx} fontWeight={700}>Uyumlu Makine / Modeller</Typography><Stack direction="row" gap={1} flexWrap="wrap" useFlexGap mt={.5}>{compatibleModels.length?compatibleModels.map(x=><Chip key={x} label={x} color="info" variant="outlined" size="small"/>):<Typography sx={{fontSize:14}}>-</Typography>}</Stack></Box>
    <Box><Typography sx={labelSx} fontWeight={700}>Teknik Notlar</Typography><Typography sx={{whiteSpace:'pre-wrap',fontSize:14.5}}>{p.technical_notes||'-'}</Typography></Box>
   </Stack>
  </CardContent></Card>

  <Card>
   <Tabs value={tab} onChange={(_,v)=>setTab(v)} variant="scrollable" scrollButtons="auto">
    <Tab icon={<Inventory2Icon/>} iconPosition="start" label={`Depo Stokları (${warehouseStocks.length})`}/>
    <Tab icon={<TimelineIcon/>} iconPosition="start" label={`Stok Hareketleri (${movements.length})`}/>
    <Tab icon={<ShoppingCartIcon/>} iconPosition="start" label={`Satış Geçmişi (${salesHistory.length})`}/>
    <Tab icon={<LocalShippingIcon/>} iconPosition="start" label={`Alış Geçmişi (${purchaseHistory.length})`}/>
    <Tab icon={<PriceCheckIcon/>} iconPosition="start" label="Tedarikçi Fiyatları"/>
   </Tabs>
   <Divider/>
   <CardContent>

    {tab===0&&(warehouseStocks.length?<Stack spacing={1}>
     {/* Depo detay rotası bulunmadığı için satırlar bilinçli olarak etkileşimsizdir. */}
     {warehouseStocks.map((w:any)=>{const qty=Number(w.quantity||0);const wt=Number(w.critical_stock||0);
      return <Stack key={w.warehouse_id} direction="row" justifyContent="space-between" alignItems="center" gap={2} sx={{p:1.5,border:'1px solid',borderColor:'divider',borderRadius:2}}>
       <Box><Typography fontWeight={800} sx={{fontSize:15}}>{w.name}</Typography><Typography sx={metaSx}>{wt>0?`Kritik seviye: ${wt} ${unit}`:'Kritik seviye tanımsız'}</Typography></Box>
       <Box textAlign="right">
        <Typography fontWeight={900} sx={{fontSize:17}} color={qty<0?'error.main':qty===0?'error.main':wt>0&&qty<=wt?'warning.main':'text.primary'}>{qty} {unit}</Typography>
        <Typography sx={metaSx}>{qty<0?'Negatif stok':qty===0?'Stok yok':wt>0&&qty<=wt?'Kritik':'Normal'}</Typography>
       </Box>
      </Stack>})}
    </Stack>:<Empty>Depo bazında stok bilgisi bulunmuyor.</Empty>)}

    {tab===1&&(movements.length?<Stack>
     {movements.map((m:any)=>{
      const qty=Number(m.quantity||0);
      const target=movementTarget(m.reference_type,m.reference_id);
      const direction=qty>0?'Giriş':qty<0?'Çıkış':'Nötr';
      const label=movementLabel(m.movement_type);
      return <Row key={m.id} onOpen={target?()=>nav(target.path,{state:{openId:target.openId}}):undefined}
       ariaLabel={target?`${label} hareketinin belgesini aç · ${m.movement_date}`:undefined}>
       <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1}>
        <Box>
         <Typography fontWeight={800} sx={{fontSize:15}}>{label} · {m.warehouse_name||'Depo belirtilmemiş'}</Typography>
         <Typography sx={metaSx}>{m.movement_date} · {m.note||'Açıklama yok'}{target?' · Belgeyi açmak için tıklayın':''}</Typography>
        </Box>
        <Box textAlign={{sm:'right'}}>
         <Typography fontWeight={900} sx={{fontSize:16}} color={qty<0?'error.main':'success.main'}>{qty>0?'+':''}{qty} {unit}</Typography>
         <Typography sx={metaSx}>{direction}</Typography>
        </Box>
       </Stack>
      </Row>})}
    </Stack>:<Empty>Bu ürün için henüz stok hareketi bulunmuyor.</Empty>)}

    {/* Belge ve cari ayrı düğmelerdir: iç içe etkileşimli öğe oluşturulmaz. */}
    {tab===2&&(salesHistory.length?<Stack>
     {salesHistory.map((s:any,index:number)=>{
      const customerId=Number(s.customer_id)||null;
      const orderId=Number(s.id)||null;
      const document=s.document_no||`Satış #${s.id}`;
      return <HistoryRow key={`${s.id}-${index}`} document={document} entity={s.customer_name}
       meta={`${s.order_date} · ${Number(s.quantity)} ${unit} · İskonto %${Number(s.discount_percent||0)}`}
       unitPrice={s.unit_price} lineTotal={s.line_total}
       onOpenDocument={orderId?()=>nav('/satislar',{state:{openId:orderId}}):undefined}
       documentAria={`${document} satış belgesini aç`}
       onOpenEntity={customerId?()=>nav(`/musteriler/${customerId}`):undefined}
       entityAria={`${s.customer_name} müşteri kartını aç`}/>})}
    </Stack>:<Empty>Bu ürün henüz satılmamış.</Empty>)}

    {tab===3&&(purchaseHistory.length?<Stack>
     {purchaseHistory.map((s:any,index:number)=>{
      const supplierId=Number(s.supplier_id)||null;
      const purchaseId=Number(s.id)||null;
      const document=s.document_no||`Alış #${s.id}`;
      return <HistoryRow key={`${s.id}-${index}`} document={document} entity={s.supplier_name}
       meta={`${s.purchase_date} · ${Number(s.quantity)} ${unit} · İskonto %${Number(s.discount_percent||0)}`}
       unitPrice={s.unit_price} lineTotal={s.line_total}
       onOpenDocument={purchaseId?()=>nav('/alislar',{state:{openId:purchaseId}}):undefined}
       documentAria={`${document} alış belgesini aç`}
       onOpenEntity={supplierId?()=>nav(`/tedarikciler/${supplierId}`):undefined}
       entityAria={`${s.supplier_name} tedarikçi kartını aç`}/>})}
    </Stack>:<Empty>Bu ürün için alış kaydı bulunmuyor.</Empty>)}

    {tab===4&&<SupplierPricesPanel productId={productId}/>}

   </CardContent>
  </Card>

  <ProductDialog open={editOpen} id={productId} onClose={()=>setEditOpen(false)} onSaved={load}/>
  <TransactionDialog open={txnKind!==null} kind={txnKind||'sale'} initialProductId={productId}
   onClose={()=>setTxnKind(null)} onSaved={()=>{setTxnKind(null);load()}}/>
  <TransferDialog open={transferOpen} warehouses={warehouses}
   initialProduct={{id:productId,name:p.name,product_code:p.product_code,unit:p.unit}}
   onClose={()=>setTransferOpen(false)} onDone={()=>{setTransferOpen(false);load()}}/>
  <ProductLabelDialog open={labelOpen} productIds={[productId]} productName={p.name}
   onClose={()=>setLabelOpen(false)}/>
 </Stack>;
}
