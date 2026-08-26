import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Autocomplete,Box,Button,Card,CardContent,Dialog,DialogActions,DialogContent,DialogTitle,IconButton,MenuItem,Stack,TextField,Typography} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import {api,errorDetail} from '../api';

type Warehouse={id:number;name:string};
type StockRow={product_id:number;name:string;product_code?:string;unit?:string;quantity:number;critical_stock:number;location?:string};
type CountLine={product_id:number;name:string;product_code?:string;unit?:string;system_quantity:number;counted_quantity:number;critical_stock:number;location?:string};

type Props={
 open:boolean;
 warehouses:Warehouse[];
 initialWarehouseId?:number|null;
 onClose:()=>void;
 onDone:(result:any)=>void;
};

const today=()=>new Date().toISOString().slice(0,10);

export default function InventoryCountDialog({open,warehouses,initialWarehouseId,onClose,onDone}:Props){
 const [warehouseId,setWarehouseId]=useState<number|''>('');
 const [stocks,setStocks]=useState<StockRow[]>([]);
 const [selectedProduct,setSelectedProduct]=useState<StockRow|null>(null);
 const [lines,setLines]=useState<CountLine[]>([]);
 const [date,setDate]=useState(today());
 const [note,setNote]=useState('Fiziksel depo sayımı');
 const [loading,setLoading]=useState(false);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');

 useEffect(()=>{
  if(!open)return;
  const first=initialWarehouseId&&warehouses.some(w=>w.id===initialWarehouseId)
   ?initialWarehouseId:(warehouses[0]?.id??'');
  setWarehouseId(first);
  setStocks([]);setLines([]);setSelectedProduct(null);
  setDate(today());setNote('Fiziksel depo sayımı');setError('');
 },[open,initialWarehouseId,warehouses]);

 useEffect(()=>{
  if(!open||!warehouseId)return;
  let cancelled=false;
  setLoading(true);setError('');setLines([]);setSelectedProduct(null);
  api.get('/warehouses/stock',{params:{warehouse_id:warehouseId}})
   .then(r=>{if(!cancelled)setStocks(r.data||[]);})
   .catch(e=>{if(!cancelled)setError(errorDetail(e,'Depo stokları yüklenemedi.'));})
   .finally(()=>{if(!cancelled)setLoading(false);});
  return()=>{cancelled=true;};
 },[open,warehouseId]);

 const selectedIds=useMemo(()=>new Set(lines.map(x=>x.product_id)),[lines]);
 const options=useMemo(()=>stocks.filter(x=>!selectedIds.has(x.product_id)),[stocks,selectedIds]);
 const totalVariance=useMemo(()=>lines.reduce((sum,line)=>sum+Math.abs(Number(line.counted_quantity)-Number(line.system_quantity)),0),[lines]);
 const changedCount=useMemo(()=>lines.filter(line=>Number(line.counted_quantity)!==Number(line.system_quantity)).length,[lines]);

 const addProduct=()=>{
  if(!selectedProduct)return;
  setLines(current=>[...current,{
   product_id:selectedProduct.product_id,
   name:selectedProduct.name,
   product_code:selectedProduct.product_code,
   unit:selectedProduct.unit,
   system_quantity:Number(selectedProduct.quantity||0),
   counted_quantity:Number(selectedProduct.quantity||0),
   critical_stock:Number(selectedProduct.critical_stock||0),
   location:selectedProduct.location,
  }]);
  setSelectedProduct(null);setError('');
 };

 const patchLine=(productId:number,patch:Partial<CountLine>)=>{
  setLines(current=>current.map(line=>line.product_id===productId?{...line,...patch}:line));
  setError('');
 };

 const submit=async()=>{
  try{
   setBusy(true);setError('');
   if(!warehouseId)throw new Error('Depo seçin.');
   if(lines.length===0)throw new Error('Sayıma en az bir ürün ekleyin.');
   for(const line of lines){
    if(!Number.isFinite(Number(line.counted_quantity))||Number(line.counted_quantity)<0){
     throw new Error(`${line.name} için sayılan miktar sıfır veya daha büyük olmalıdır.`);
    }
    if(!Number.isFinite(Number(line.critical_stock))||Number(line.critical_stock)<0){
     throw new Error(`${line.name} için kritik stok sıfır veya daha büyük olmalıdır.`);
    }
   }
   const response=await api.post('/warehouses/counts',{
    warehouse_id:warehouseId,
    count_date:date,
    note:note||null,
    items:lines.map(line=>({
     product_id:line.product_id,
     counted_quantity:Number(line.counted_quantity),
     critical_stock:Number(line.critical_stock),
    })),
   });
   onDone(response.data);
  }catch(e:any){
   setError(e.message&&!e.response?e.message:errorDetail(e,'Stok sayımı kaydedilemedi.'));
  }finally{setBusy(false);}
 };

 const close=()=>{if(!busy)onClose();};

 return <Dialog open={open} onClose={close} fullWidth maxWidth="md">
  <DialogTitle>Çok Satırlı Fiziksel Stok Sayımı</DialogTitle>
  <DialogContent>
   <Stack spacing={2} mt={1}>
    {error&&<Alert severity="error">{error}</Alert>}
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField select fullWidth label="Depo" value={warehouseId}
      onChange={e=>setWarehouseId(Number(e.target.value))} disabled={loading||busy}>
      {warehouses.map(w=><MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
     </TextField>
     <TextField fullWidth type="date" label="Sayım tarihi" value={date}
      onChange={e=>setDate(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
    </Stack>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1} alignItems={{sm:'center'}}>
     <Autocomplete fullWidth options={options} value={selectedProduct} loading={loading}
      onChange={(_,value)=>setSelectedProduct(value)}
      getOptionLabel={x=>`${x.name}${x.product_code?` · ${x.product_code}`:''}`}
      renderOption={(props,option)=>{
       // MUI supplies this key; it is not a component prop requiring validation.
       // eslint-disable-next-line react/prop-types
       const {key,...optionProps}=props;
       return <li key={key} {...optionProps}>
        <Box><Typography fontWeight={750}>{option.name}</Typography><Typography variant="caption">{option.product_code||'Kodsuz'} · Sistem: {option.quantity} {option.unit||''} · Raf: {option.location||'-'}</Typography></Box>
       </li>;
      }}
      renderInput={params=><TextField {...params} label="Sayıma ürün ekle"/>}/>
     <Button variant="outlined" startIcon={<AddIcon/>} onClick={addProduct} disabled={!selectedProduct||busy} sx={{whiteSpace:'nowrap'}}>Ürün Ekle</Button>
    </Stack>

    {lines.length===0
     ?<Alert severity="info">Ürün seçip “Ürün Ekle” düğmesine basın. Sayım tek işlem halinde kaydedilecektir.</Alert>
     :<Stack spacing={1}>
      {lines.map(line=>{
       const variance=Number(line.counted_quantity)-Number(line.system_quantity);
       return <Card variant="outlined" key={line.product_id}><CardContent sx={{p:1.5,'&:last-child':{pb:1.5}}}>
        <Stack direction={{xs:'column',md:'row'}} spacing={1.25} alignItems={{md:'center'}}>
         <Box sx={{flex:1,minWidth:180}}>
          <Typography fontWeight={850}>{line.name}</Typography>
          <Typography sx={{fontSize:13.5,color:'text.secondary'}}>{line.product_code||'Kodsuz'} · Raf: {line.location||'-'} · Sistem: <b>{line.system_quantity} {line.unit||''}</b></Typography>
         </Box>
         <TextField type="number" size="small" label="Sayılan" value={line.counted_quantity}
          onChange={e=>patchLine(line.product_id,{counted_quantity:Number(e.target.value)})}
          inputProps={{min:0,step:'0.0001'}} sx={{width:{xs:'100%',md:140}}}/>
         <TextField type="number" size="small" label="Kritik stok" value={line.critical_stock}
          onChange={e=>patchLine(line.product_id,{critical_stock:Number(e.target.value)})}
          inputProps={{min:0,step:'0.0001'}} sx={{width:{xs:'100%',md:140}}}/>
         <Box sx={{minWidth:110}}><Typography sx={{fontSize:12.5,color:'text.secondary'}}>FARK</Typography><Typography fontWeight={900} color={variance<0?'error.main':variance>0?'success.main':'text.primary'}>{variance>0?'+':''}{Number(variance.toFixed(4))}</Typography></Box>
         <IconButton aria-label={`${line.name} sayım satırını kaldır`} onClick={()=>setLines(current=>current.filter(x=>x.product_id!==line.product_id))}><DeleteOutlineIcon/></IconButton>
        </Stack>
       </CardContent></Card>;
      })}
     </Stack>}

    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <Alert severity={changedCount?'warning':'info'} sx={{flex:1}}>Satır: <b>{lines.length}</b> · Değişecek: <b>{changedCount}</b> · Mutlak fark: <b>{Number(totalVariance.toFixed(4))}</b></Alert>
    </Stack>
    <TextField label="Sayım açıklaması" value={note} onChange={e=>setNote(e.target.value)} multiline minRows={2} inputProps={{maxLength:500}}/>
   </Stack>
  </DialogContent>
  <DialogActions>
   <Button onClick={close} disabled={busy}>Vazgeç</Button>
   <Button variant="contained" onClick={submit} disabled={busy||loading||lines.length===0}>{busy?'Sayım Kaydediliyor...':'Sayımı Kaydet'}</Button>
  </DialogActions>
 </Dialog>;
}
