import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Autocomplete,Box,Button,Chip,CircularProgress,Dialog,DialogActions,DialogContent,DialogTitle,IconButton,MenuItem,Stack,Table,TableBody,TableCell,TableHead,TableRow,TextField,Tooltip,Typography} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';import EditIcon from '@mui/icons-material/Edit';import DeleteIcon from '@mui/icons-material/Delete';import EmojiEventsIcon from '@mui/icons-material/EmojiEvents';
import {api,money,errorDetail} from '../api';
import {useAuth} from '../AuthContext';

const CURRENCIES=['TRY','EUR','USD'];
const dash=(v:any)=>v==null||v===''?'-':v;

// Manuel tedarikçi fiyatı ekle/düzenle
function ManualPriceDialog({open,productId,price,onClose,onSaved}:{open:boolean;productId:number;price:any|null;onClose:()=>void;onSaved:()=>void}){
 const blank={supplier_id:null as number|null,price:'',currency:'TRY',note:''};
 const [v,setV]=useState<any>(blank);const [suppliers,setSuppliers]=useState<any[]>([]);const [error,setError]=useState('');const [saving,setSaving]=useState(false);
 useEffect(()=>{if(!open)return;setError('');
  api.get('/suppliers',{params:{active:'all'}}).then(r=>setSuppliers(r.data||[])).catch(()=>setSuppliers([]));
  if(price)setV({supplier_id:price.supplier_id,price:String(price.manual_price??''),currency:price.manual_currency||'TRY',note:price.note||''});
  else setV(blank);
 },[open,price]);
 const save=async()=>{try{setSaving(true);setError('');
   if(price&&price.manual_price_id){
    await api.put(`/supplier-prices/${price.manual_price_id}`,{price:Number(v.price||0),currency:v.currency,note:v.note||null});
   }else{
    await api.post('/supplier-prices',{supplier_id:v.supplier_id,product_id:productId,price:Number(v.price||0),currency:v.currency,note:v.note||null});
   }
   onSaved();onClose();
  }catch(e:any){setError(errorDetail(e,'Fiyat kaydedilemedi.'))}finally{setSaving(false)}};
 const isEdit=Boolean(price&&price.manual_price_id);
 const canSave=Boolean((isEdit||v.supplier_id)&&String(v.price).trim()!==''&&Number(v.price)>=0);
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm"><DialogTitle>{isEdit?'Manuel Fiyat Düzenle':'Manuel Tedarikçi Fiyatı'}</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}>{error&&<Alert severity="error">{error}</Alert>}
  {isEdit
   ?<TextField disabled label="Tedarikçi" value={price?.supplier_name||''}/>
   :<Autocomplete options={suppliers} getOptionLabel={o=>o?.name||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={suppliers.find(s=>s.id===v.supplier_id)||null} onChange={(_,val)=>setV({...v,supplier_id:val?val.id:null})} renderInput={params=><TextField {...params} required label="Tedarikçi"/>}/>}
  <Stack direction="row" spacing={1}>
   <TextField fullWidth required type="number" label="Fiyat" value={v.price} onChange={e=>setV({...v,price:e.target.value})} inputProps={{min:0,step:.0001}}/>
   <TextField select label="Para Birimi" value={v.currency} onChange={e=>setV({...v,currency:e.target.value})} sx={{minWidth:130}}>{CURRENCIES.map(c=><MenuItem key={c} value={c}>{c}</MenuItem>)}</TextField>
  </Stack>
  <TextField label="Not" multiline minRows={2} value={v.note} onChange={e=>setV({...v,note:e.target.value})}/>
 </Stack></DialogContent><DialogActions><Button onClick={onClose}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={saving||!canSave}>{saving?'Kaydediliyor...':'Kaydet'}</Button></DialogActions></Dialog>;
}

export default function SupplierPricesPanel({productId}:{productId:number}){
 const {can}=useAuth();const canWrite=can('purchases');
 const [data,setData]=useState<any|null>(null);const [loading,setLoading]=useState(true);const [error,setError]=useState('');
 const [dialogOpen,setDialogOpen]=useState(false);const [editPrice,setEditPrice]=useState<any|null>(null);
 const load=()=>{setLoading(true);setError('');api.get(`/products/${productId}/supplier-prices`).then(r=>setData(r.data)).catch(e=>setError(errorDetail(e,'Tedarikçi fiyatları yüklenemedi.'))).finally(()=>setLoading(false))};
 useEffect(()=>{load()},[productId]);
 const items:any[]=data?.items||[];
 // En pahalı (kırmızı) = sıralanabilir en yüksek TL karşılığı. En iyi teklif
 // (yeşil) backend'den best_offer ile gelir.
 const priciestId=useMemo(()=>{let worst:any=null;for(const it of items){if(it.price_in_try==null)continue;if(worst===null||Number(it.price_in_try)>Number(worst.price_in_try))worst=it}return worst&&!worst.best_offer?worst.supplier_id:null},[items]);
 const rowBg=(it:any)=>it.best_offer?'rgba(46,125,50,.14)':it.supplier_id===priciestId?'rgba(211,47,47,.12)':'transparent';
 const del=async(it:any)=>{if(!it.manual_price_id)return;try{await api.delete(`/supplier-prices/${it.manual_price_id}`);load()}catch(e:any){setError(errorDetail(e,'Fiyat silinemedi.'))}};
 if(loading)return <Box py={4} display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>;
 return <Stack spacing={1.5}>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
   <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
    <Typography variant="caption" color="text.secondary">Geçerli kurlar:</Typography>
    {data?.rates&&Object.entries(data.rates).map(([code,rate]:any)=><Chip key={code} size="small" variant="outlined" label={`${code}: ${rate==null?'—':rate}`}/>)}
   </Stack>
   {canWrite&&<Button size="small" variant="contained" startIcon={<AddIcon/>} onClick={()=>{setEditPrice(null);setDialogOpen(true)}}>Manuel Fiyat Ekle</Button>}
  </Stack>
  {items.length===0
   ?<Typography color="text.secondary" py={2}>Bu ürün için tedarikçi fiyatı (manuel veya alış geçmişi) bulunmuyor.</Typography>
   :<Table size="small"><TableHead><TableRow>
     <TableCell>Tedarikçi</TableCell><TableCell align="right">Manuel Fiyat</TableCell><TableCell align="right">Son Alış</TableCell>
     <TableCell align="right">Ort. Alış</TableCell><TableCell align="right">Alış Adedi</TableCell><TableCell align="right">TL Karşılığı</TableCell>
     <TableCell/>{canWrite&&<TableCell/>}</TableRow></TableHead>
    <TableBody>{items.map(it=><TableRow key={it.supplier_id} sx={{bgcolor:rowBg(it)}}>
     <TableCell><Stack direction="row" spacing={.5} alignItems="center">{it.best_offer&&<Tooltip title="En İyi Teklif"><EmojiEventsIcon fontSize="small" sx={{color:'#2e7d32'}}/></Tooltip>}<span>{it.supplier_name||`#${it.supplier_id}`}</span></Stack></TableCell>
     <TableCell align="right">{it.manual_price!=null?`${it.manual_price} ${it.manual_currency}`:'-'}</TableCell>
     <TableCell align="right">{it.last_purchase_price!=null?money(it.last_purchase_price):'-'}</TableCell>
     <TableCell align="right">{it.avg_purchase_price!=null?money(it.avg_purchase_price):'-'}</TableCell>
     <TableCell align="right">{it.purchase_count||0}</TableCell>
     <TableCell align="right"><b>{it.price_in_try!=null?money(it.price_in_try):'-'}</b>{it.best_offer&&<Chip size="small" color="success" label="En İyi Teklif" sx={{ml:.5}}/>}</TableCell>
     <TableCell align="right" sx={{whiteSpace:'nowrap',color:'text.secondary',fontSize:12}}>{dash(it.last_purchase_date)}</TableCell>
     {canWrite&&<TableCell align="right" sx={{whiteSpace:'nowrap'}}>
      <Tooltip title={it.manual_price_id?'Manuel fiyatı düzenle':'Manuel fiyat ekle'}><IconButton size="small" onClick={()=>{setEditPrice(it);setDialogOpen(true)}}><EditIcon fontSize="small"/></IconButton></Tooltip>
      {it.manual_price_id&&<Tooltip title="Manuel fiyatı sil"><IconButton size="small" color="error" onClick={()=>del(it)}><DeleteIcon fontSize="small"/></IconButton></Tooltip>}
     </TableCell>}
    </TableRow>)}</TableBody></Table>}
  <ManualPriceDialog open={dialogOpen} productId={productId} price={editPrice} onClose={()=>setDialogOpen(false)} onSaved={load}/>
 </Stack>;
}
