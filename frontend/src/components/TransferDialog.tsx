import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Autocomplete,Box,Button,Dialog,DialogActions,DialogContent,DialogTitle,MenuItem,Stack,TextField,Typography} from '@mui/material';
import {api,errorDetail,policyOverrideHeaders,policyOverrideRequired} from '../api';
import {useAuth} from '../AuthContext';

type Warehouse={id:number;name:string};
type Product={id:number;name:string;product_code?:string;unit?:string};

type Props={
 open:boolean;
 warehouses:Warehouse[];
 // Optional context so callers can preselect without duplicating the form.
 initialSourceId?:number|null;
 initialProduct?:Product|null;
 onClose:()=>void;
 onDone:()=>void;
};

const today=()=>new Date().toISOString().slice(0,10);

/**
 * Reusable warehouse-to-warehouse transfer dialog. Wraps the existing
 * POST /warehouses/transfers endpoint (no competing mechanism, no client-only
 * validation of critical rules). Shows the backend-confirmed available source
 * quantity for the selected product and forbids picking the same source/target.
 */
export default function TransferDialog({open,warehouses,initialSourceId,initialProduct,onClose,onDone}:Props){
 const {user}=useAuth();
 const manager=user?.role==='admin'||user?.role==='yonetici';
 const [source,setSource]=useState<number|''>('');
 const [target,setTarget]=useState<number|''>('');
 const [product,setProduct]=useState<Product|null>(null);
 const [products,setProducts]=useState<Product[]>([]);
 const [qty,setQty]=useState<number>(1);
 const [date,setDate]=useState(today());
 const [note,setNote]=useState('');
 const [available,setAvailable]=useState<number|null>(null);
 const [availableUnit,setAvailableUnit]=useState('');
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const [needsOverride,setNeedsOverride]=useState(false);
 const [overrideReason,setOverrideReason]=useState('');

 const resetPolicy=()=>{setError('');setNeedsOverride(false);setOverrideReason('');};

 // Initialise selections whenever the dialog opens.
 useEffect(()=>{
  if(!open)return;
  resetPolicy();
  const first=initialSourceId&&warehouses.some(w=>w.id===initialSourceId)?initialSourceId:(warehouses[0]?.id??'');
  const second=warehouses.find(w=>w.id!==first)?.id??'';
  setSource(first);
  setTarget(second);
  setProduct(initialProduct??null);
  setQty(1);
  setDate(today());
  setNote('');
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[open]);

 // Products list (for the picker) only when no fixed product was supplied.
 useEffect(()=>{
  if(!open||initialProduct)return;
  api.get('/products',{params:{limit:2000}}).then(r=>setProducts(r.data)).catch(()=>setProducts([]));
 },[open,initialProduct]);

 // Backend-confirmed available quantity in the source warehouse for this product.
 useEffect(()=>{
  setAvailable(null);setAvailableUnit('');
  if(!open||!source||!product?.id)return;
  let cancelled=false;
  api.get('/warehouses/stock',{params:{warehouse_id:source}})
   .then(r=>{
    if(cancelled)return;
    const row=(r.data||[]).find((x:any)=>x.product_id===product.id);
    setAvailable(row?Number(row.quantity):0);
    setAvailableUnit(row?.unit||product.unit||'');
   })
   .catch(()=>{if(!cancelled){setAvailable(null);}});
  return()=>{cancelled=true;};
 },[open,source,product?.id]);

 const sameWarehouse=source!==''&&target!==''&&source===target;
 const targetOptions=useMemo(()=>warehouses.filter(w=>w.id!==source),[warehouses,source]);

 const submit=async()=>{
  try{
   setBusy(true);setError('');
   if(!source||!target||!product)throw new Error('Kaynak depo, hedef depo ve ürün seçin.');
   if(source===target)throw new Error('Kaynak ve hedef depo farklı olmalıdır.');
   if(!Number.isFinite(Number(qty))||Number(qty)<=0)throw new Error('Miktar sıfırdan büyük olmalıdır.');
   const headers=overrideReason.trim()?policyOverrideHeaders(overrideReason):undefined;
   await api.post('/warehouses/transfers',{
    source_warehouse_id:source,
    target_warehouse_id:target,
    transfer_date:date,
    note:note||null,
    items:[{product_id:product.id,quantity:Number(qty)}],
   },{headers});
   onDone();
  }catch(e:any){
   setError(e.message&&!e.response?e.message:errorDetail(e,'Transfer oluşturulamadı.'));
   if(policyOverrideRequired(e))setNeedsOverride(true);
  }finally{setBusy(false);}
 };

 const close=()=>{if(!busy)onClose();};

 return <Dialog open={open} onClose={close} fullWidth maxWidth="sm">
  <DialogTitle>Depolar Arası Transfer</DialogTitle>
  <DialogContent>
   <Stack spacing={2} mt={1}>
    {error&&<Alert severity="error">{error}</Alert>}
    {needsOverride&&manager&&<TextField
     label="Yönetici onay gerekçesi" value={overrideReason}
     onChange={e=>setOverrideReason(e.target.value)}
     helperText="En az 5 karakter. Eksi stok transfer istisnası denetim kaydına yazılır."
     inputProps={{maxLength:500}} multiline minRows={2} required/>}
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField select fullWidth label="Kaynak depo" value={source}
      onChange={e=>{setSource(Number(e.target.value));resetPolicy();}}
      error={sameWarehouse} slotProps={{htmlInput:{'aria-label':'Kaynak depo'}}}>
      {warehouses.map(w=><MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
     </TextField>
     <TextField select fullWidth label="Hedef depo" value={target}
      onChange={e=>{setTarget(Number(e.target.value));resetPolicy();}}
      error={sameWarehouse}
      helperText={sameWarehouse?'Kaynak ve hedef depo farklı olmalıdır.':' '}
      slotProps={{htmlInput:{'aria-label':'Hedef depo'}}}>
      {/* Same warehouse cannot be chosen as target. */}
      {targetOptions.map(w=><MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
     </TextField>
    </Stack>
    {initialProduct
     ?<Box sx={{p:1.25,border:'1px solid',borderColor:'divider',borderRadius:1.5}}>
       <Typography sx={{fontSize:13.5,color:'text.secondary'}} fontWeight={700}>Ürün</Typography>
       <Typography fontWeight={800} sx={{fontSize:15}}>{initialProduct.name}{initialProduct.product_code?` · ${initialProduct.product_code}`:''}</Typography>
      </Box>
     :<Autocomplete options={products} value={product}
       onChange={(_,v)=>{setProduct(v);resetPolicy();}}
       getOptionLabel={x=>`${x.name}${x.product_code?` · ${x.product_code}`:''}`}
       renderInput={p=><TextField {...p} label="Ürün"/>}/>}
    {product&&<Alert severity={available!=null&&available<=0?'warning':'info'} sx={{py:.5}}>
     <Typography sx={{fontSize:14}}>
      Kaynak depo mevcut stoğu: <b>{available==null?'—':`${available} ${availableUnit}`.trim()}</b>
      {available!=null&&Number(qty)>available?' · İstenen miktar mevcut stoğun üzerinde':''}
     </Typography>
    </Alert>}
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField fullWidth type="number" label="Transfer miktarı" value={qty}
      onChange={e=>{setQty(Number(e.target.value));resetPolicy();}}
      inputProps={{min:0,step:'0.0001'}}
      helperText={availableUnit?`Birim: ${availableUnit}`:' '}/>
     <TextField fullWidth type="date" label="Tarih" value={date}
      onChange={e=>{setDate(e.target.value);resetPolicy();}} slotProps={{inputLabel:{shrink:true}}}/>
    </Stack>
    <TextField label="Transfer nedeni / not" value={note}
     onChange={e=>{setNote(e.target.value);resetPolicy();}} multiline minRows={2}/>
   </Stack>
  </DialogContent>
  <DialogActions>
   <Button onClick={close} disabled={busy}>Vazgeç</Button>
   <Button variant="contained" onClick={submit}
    disabled={busy||sameWarehouse||(needsOverride&&manager&&overrideReason.trim().length<5)}>
    {busy?'Aktarılıyor...':'Transfer Et'}
   </Button>
  </DialogActions>
 </Dialog>;
}
