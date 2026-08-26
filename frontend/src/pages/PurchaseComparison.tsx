import React from 'react';
import {useEffect,useMemo,useRef,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Alert,Box,Button,Chip,CircularProgress,IconButton,Link,Pagination,Paper,Snackbar,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Tooltip,Typography} from '@mui/material';
import BoltIcon from '@mui/icons-material/Bolt';
import AddShoppingCartIcon from '@mui/icons-material/AddShoppingCart';
import ReplayIcon from '@mui/icons-material/Replay';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import {api,money,errorDetail} from '../api';
import SupplierPriceImportDialog from '../components/SupplierPriceImportDialog';
import {useAuth} from '../AuthContext';

// Raporlar altında tedarikçi fiyat karşılaştırma tablosu: her ürün satırında
// tedarikçi sütunları ve TL bazında en iyi teklif. Fiyatlar backend'de indirim
// düşülüp (efektif) override -> global TCMB -> TRY=1 sırasıyla TL'ye normalize
// edilir; sıralama efektif TL fiyatına göredir. ⚡ rozeti: en ucuz teklife çok
// yakın ama daha hızlı / stokta olan alternatif tedarikçiyi işaret eder.
// Increment C: her satırda en iyi teklife tek tıkla sipariş (taslak alım) ve
// düşük stoklu ürünler için "Yeniden Sipariş Önerileri" paneli.
const ALT_REASON:Record<string,string>={
 faster:'daha hızlı teslim',
 in_stock:'stokta',
 faster_in_stock:'daha hızlı + stokta',
};
export default function PurchaseComparison(){
 const nav=useNavigate();
 const {can}=useAuth();
 const [q,setQ]=useState('');const [page,setPage]=useState(1);
 const [data,setData]=useState<any|null>(null);const [loading,setLoading]=useState(false);const [error,setError]=useState('');
 const [reorder,setReorder]=useState<any[]|null>(null);
 const [busy,setBusy]=useState<string>('');const [toast,setToast]=useState('');
 const [importOpen,setImportOpen]=useState(false);
 const seq=useRef(0);
 // Comparison reads expose supplier cost and margin, so the backend gates them
 // on the ``purchases`` permission. A read-only role now gets 403 here; say so
 // plainly instead of showing a generic "could not load".
 const denied=(e:any)=>e?.response?.status===403;
 const load=()=>{const s=++seq.current;setLoading(true);setError('');api.get('/purchase-comparison',{params:{q,page,page_size:50}}).then(r=>{if(s===seq.current)setData(r.data)}).catch(e=>{if(s===seq.current)setError(denied(e)?'Bu ekran satın alma yetkisi gerektirir (maliyet ve kâr marjı verisi).':errorDetail(e,'Karşılaştırma yüklenemedi.'))}).finally(()=>{if(s===seq.current)setLoading(false)})};
 const loadReorder=()=>{api.get('/purchase-comparison/reorder-suggestions').then(r=>setReorder(r.data.items||[])).catch(()=>setReorder([]))};
 useEffect(()=>{const t=setTimeout(load,250);return()=>clearTimeout(t)},[q,page]);
 useEffect(()=>{setPage(1)},[q]);
 useEffect(()=>{loadReorder()},[]);
 // Tek tıkla taslak alım siparişi: seçilen (genelde en iyi) tedarikçiye ürün için
 // POST /purchase-orders. Miktar boş -> backend MOQ (yoksa 1) uygular.
 const createPO=(productId:number,supplierId:number,label:string,quantity?:string)=>{
  const key=`${productId}:${supplierId}`;
  if(busy)return;
  if(!window.confirm(`${label} için taslak sipariş oluşturulsun mu?`))return;
  setBusy(key);setError('');
  const body:any={product_id:productId,supplier_id:supplierId};
  if(quantity)body.quantity=quantity;
  api.post('/purchase-orders',body).then(r=>{
   setToast(`Taslak sipariş #${r.data.purchase_id} oluşturuldu (${money(r.data.final_total)}).`);
   loadReorder();
  }).catch(e=>setError(errorDetail(e,'Sipariş oluşturulamadı.'))).finally(()=>setBusy(''));
 };
 const suppliers:any[]=data?.suppliers||[];
 const items:any[]=data?.items||[];
 const supplierName=useMemo(()=>{const m:Record<number,string>={};suppliers.forEach(s=>{m[s.id]=s.name});return m},[suppliers]);
 return <Stack spacing={2}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1}>
   <Typography variant="h4" fontWeight={900}>Tedarikçi Fiyat Karşılaştırma</Typography>
   {can('purchases')&&<Button variant="contained" startIcon={<UploadFileIcon/>} onClick={()=>setImportOpen(true)}>Tedarikçi Listesi Yükle</Button>}
  </Stack>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  {reorder&&reorder.length>0&&<Paper sx={{p:1.5}}>
   <Stack direction="row" alignItems="center" spacing={1} sx={{mb:1}}>
    <ReplayIcon color="warning"/>
    <Typography variant="h6" fontWeight={800}>Yeniden Sipariş Önerileri</Typography>
    <Chip size="small" color="warning" variant="outlined" label={`${reorder.length} ürün`}/>
   </Stack>
   <TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small">
    <TableHead><TableRow>
     <TableCell sx={{fontWeight:800}}>Ürün</TableCell>
     <TableCell align="right" sx={{fontWeight:800}}>Stok / Min</TableCell>
     <TableCell sx={{fontWeight:800}}>Önerilen Tedarikçi</TableCell>
     <TableCell align="right" sx={{fontWeight:800}}>Önerilen Miktar</TableCell>
     <TableCell align="right" sx={{fontWeight:800}}>Sipariş</TableCell>
    </TableRow></TableHead>
    <TableBody>{reorder.map(row=>{const best=row.best_supplier;const key=best?`${row.product_id}:${best.supplier_id}`:'';
     return <TableRow key={row.product_id} hover>
      <TableCell><Link sx={{cursor:'pointer'}} onClick={()=>nav(`/urunler/${row.product_id}`)}>{row.product_name}</Link>{row.product_code?<Typography component="span" variant="caption" color="text.secondary"> · {row.product_code}</Typography>:null}</TableCell>
      <TableCell align="right"><Typography component="span" color="error" fontWeight={700}>{money(row.current_stock)}</Typography> / {money(row.minimum_stock)}</TableCell>
      <TableCell>{best?<span>{best.supplier_name||`#${best.supplier_id}`} · {money(best.effective_price_in_try)}{best.lead_time_days!=null?` · ⏱ ${best.lead_time_days}g`:''}</span>:<Typography component="span" color="text.secondary">Tedarikçi fiyatı yok</Typography>}</TableCell>
      <TableCell align="right">{money(row.suggested_quantity)}</TableCell>
      <TableCell align="right"><Button size="small" variant="contained" startIcon={<AddShoppingCartIcon/>} disabled={!best||busy===key}
       onClick={()=>best&&createPO(row.product_id,best.supplier_id,row.product_name,row.suggested_quantity)}>Sipariş</Button></TableCell>
     </TableRow>})}</TableBody>
   </Table></TableContainer>
  </Paper>}
  <Stack direction={{xs:'column',sm:'row'}} spacing={1} alignItems="center">
   <TextField size="small" sx={{flex:1,minWidth:260}} label="Parça no, açıklama veya barkod ara" value={q} onChange={e=>setQ(e.target.value)}/>
   <Chip variant="outlined" label={`${data?.total??0} ürün`}/>
  </Stack>
  <Paper>
   {loading&&!data?<Box py={6} display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>
    :items.length===0?<Typography color="text.secondary" p={3}>Karşılaştırılacak tedarikçi fiyatı bulunamadı.</Typography>
    :<TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small" stickyHeader>
     <TableHead><TableRow>
      <TableCell sx={{fontWeight:800,minWidth:120}}>Parça No</TableCell>
      <TableCell sx={{fontWeight:800,minWidth:200}}>Açıklama</TableCell>
      {suppliers.map(s=><TableCell key={s.id} align="right" sx={{fontWeight:800,minWidth:120}}>{s.name}</TableCell>)}
      <TableCell align="right" sx={{fontWeight:800,minWidth:180}}>En İyi Teklif</TableCell>
      <TableCell align="center" sx={{fontWeight:800,minWidth:90}}>Sipariş</TableCell>
     </TableRow></TableHead>
     <TableBody>{items.map(row=>{const bestId=row.best_offer?.supplier_id;
      const poKey=bestId!=null?`${row.product_id}:${bestId}`:'';
      return <TableRow key={row.product_id} hover>
       <TableCell>{row.product_code||'-'}</TableCell>
       <TableCell><Link sx={{cursor:'pointer'}} onClick={()=>nav(`/urunler/${row.product_id}`)}>{row.product_name}</Link></TableCell>
       {suppliers.map(s=>{const offer=row.offers?.[s.id];const isBest=bestId===s.id;
        const isAlt=!!offer?.faster_alternative;
        // Sıralama efektif (indirimli) TL fiyatına göre; hücrede de onu göster,
        // yoksa ham normalize fiyata düş.
        const eff=offer?(offer.effective_price_in_try??offer.price_in_try):null;
        const meta:string[]=[];
        if(offer){
         if(offer.discount_percent!=null&&Number(offer.discount_percent)>0)meta.push(`-%${Number(offer.discount_percent)} ind.`);
         if(offer.lead_time_days!=null)meta.push(`⏱ ${offer.lead_time_days}g`);
         if(offer.supplier_stock!=null)meta.push(`📦 ${Number(offer.supplier_stock)}`);
         if(offer.moq!=null&&Number(offer.moq)>0)meta.push(`min ${Number(offer.moq)}`);
        }
        return <TableCell key={s.id} align="right" sx={{bgcolor:isBest?'rgba(46,125,50,.14)':isAlt?'rgba(2,136,209,.10)':'transparent',fontWeight:isBest?800:400,verticalAlign:'top'}}>
         {offer&&eff!=null?<Stack spacing={0.25} alignItems="flex-end">
          <Box display="flex" alignItems="center" gap={0.5}>
           {isAlt&&<Tooltip title={`En ucuza yakın, ${ALT_REASON[offer.alternative_reason]||'daha iyi'}`}><BoltIcon fontSize="inherit" color="info"/></Tooltip>}
           <span>{money(eff)}</span>
           {offer.is_manual&&<Typography component="span" variant="caption" color="text.secondary">M</Typography>}
          </Box>
          {meta.length>0&&<Typography variant="caption" color="text.secondary" sx={{lineHeight:1.2}}>{meta.join(' · ')}</Typography>}
         </Stack>:'-'}
        </TableCell>})}
       <TableCell align="right">{row.best_offer?<Chip size="small" color="success" label={`${supplierName[bestId]||`#${bestId}`} · ${money(row.best_offer.effective_price_in_try??row.best_offer.price_in_try)}`}/>:'-'}</TableCell>
       <TableCell align="center">{row.best_offer?<Tooltip title={`${supplierName[bestId]||`#${bestId}`} tedarikçisine taslak sipariş oluştur`}>
        <span><IconButton size="small" color="primary" disabled={busy===poKey}
         onClick={()=>createPO(row.product_id,bestId,`${row.product_name} (${supplierName[bestId]||`#${bestId}`})`)}><AddShoppingCartIcon fontSize="small"/></IconButton></span>
        </Tooltip>:'-'}</TableCell>
      </TableRow>})}</TableBody>
    </Table></TableContainer>}
  </Paper>
  <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
   <Typography variant="caption" color="text.secondary">{"M: manuel fiyat · efektif (indirim düşülmüş) TL fiyatına göre sıralanır · ⏱ teslim süresi (gün) · 📦 tedarikçi stoğu · ⚡ en ucuza yakın ama daha hızlı/stokta alternatif · 🛒 en iyi teklife tek tıkla taslak sipariş · tüm tutarlar TL'ye normalize edilmiştir"}</Typography>
   {data&&data.pages>1&&<Pagination count={data.pages} page={page} onChange={(_,v)=>setPage(v)} size="small"/>}
  </Stack>
  <Snackbar open={!!toast} autoHideDuration={4000} onClose={()=>setToast('')} anchorOrigin={{vertical:'bottom',horizontal:'center'}}>
   <Alert severity="success" onClose={()=>setToast('')} variant="filled">{toast}</Alert>
  </Snackbar>
  <SupplierPriceImportDialog open={importOpen} onClose={()=>setImportOpen(false)} onImported={()=>{load();loadReorder()}}/>
 </Stack>;
}
