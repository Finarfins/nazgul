import React from 'react';
import {useEffect,useState} from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,DialogTitle,FormControlLabel,Stack,Switch,TextField} from '@mui/material';
import {api,policyOverrideHeaders,policyOverrideRequired} from '../api';
import {useAuth} from '../AuthContext';

const blankProduct=()=>({
 name:'',
 product_code:'',
 barcode:'',
 purchase_price:0,
 sale_price:0,
 vat_rate:20,
 stock:0,
 unit:'Adet',
 category:'',
 location:'',
 oem_number:'',
 alternative_oem:'',
 brand:'',
 manufacturer:'',
 compatible_models:'',
 technical_notes:'',
 active:true,
});

export default function ProductDialog({open,id,initialValues,onClose,onSaved}:{open:boolean;id?:number|null;initialValues?:Record<string,unknown>;onClose:()=>void;onSaved:(created?:any)=>void}){
 const {user}=useAuth();
 const manager=user?.role==='admin'||user?.role==='yonetici';
 const [v,setV]=useState<any>(blankProduct());
 const [error,setError]=useState('');
 const [saving,setSaving]=useState(false);
 const [needsOverride,setNeedsOverride]=useState(false);
 const [overrideReason,setOverrideReason]=useState('');

 useEffect(()=>{
  if(!open)return;
  setError('');
  setNeedsOverride(false);
  setOverrideReason('');
  if(id){
   api.get(`/products/${id}`)
    .then(r=>setV(r.data.product))
    .catch((e:any)=>setError(e.response?.data?.detail||'Ürün bilgisi alınamadı.'));
  }else setV({...blankProduct(),...(initialValues||{})});
 },[open,id,initialValues]);

 const f=(k:string)=>(e:any)=>{
  setNeedsOverride(false);
  setOverrideReason('');
  setV({...v,[k]:['purchase_price','sale_price','stock','vat_rate'].includes(k)?Number(e.target.value):e.target.value});
 };

 const close=()=>{
  if(saving)return;
  setError('');
  setNeedsOverride(false);
  setOverrideReason('');
  onClose();
 };

 const save=async()=>{
  try{
   setSaving(true);
   setError('');
   const headers=overrideReason.trim()?policyOverrideHeaders(overrideReason):undefined;
   const response=id?await api.put(`/products/${id}`,v,{headers}):await api.post('/products',v,{headers});
   onSaved(response.data);
   setNeedsOverride(false);
   setOverrideReason('');
   onClose();
  }catch(e:any){
   const detail=e.response?.data?.detail||e.message||'Ürün kaydedilemedi.';
   setError(detail);
   if(policyOverrideRequired(e))setNeedsOverride(true);
  }finally{
   setSaving(false);
  }
 };

 return <Dialog open={open} onClose={close} fullWidth maxWidth="sm">
  <DialogTitle>{id?'Ürün Düzenle':'Yeni Ürün'}</DialogTitle>
  <DialogContent>
   <Stack spacing={1.5} mt={1}>
    {error&&<Alert severity="error">{error}</Alert>}
    {needsOverride&&manager&&<TextField
     label="Yönetici onay gerekçesi"
     value={overrideReason}
     onChange={e=>setOverrideReason(e.target.value)}
     helperText="En az 5 karakter. Stok istisnası denetim kaydına yazılır."
     inputProps={{maxLength:500}}
     multiline
     minRows={2}
     required
    />}
    <TextField label="Ürün adı" value={v.name||''} onChange={f('name')}/>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField fullWidth label="Ürün kodu" value={v.product_code||''} onChange={f('product_code')}/>
     <TextField fullWidth label="Barkod" value={v.barcode||''} onChange={f('barcode')}/>
    </Stack>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField fullWidth type="number" label="Alış fiyatı" value={v.purchase_price??0} onChange={f('purchase_price')}/>
     <TextField fullWidth type="number" label="Satış fiyatı" value={v.sale_price??0} onChange={f('sale_price')}/>
    </Stack>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField fullWidth type="number" label="Stok" value={v.stock??0} onChange={f('stock')}/>
     <TextField fullWidth type="number" label="KDV %" value={v.vat_rate??0} onChange={f('vat_rate')}/>
     <TextField fullWidth label="Birim" value={v.unit||''} onChange={f('unit')}/>
    </Stack>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField fullWidth label="Kategori" value={v.category||''} onChange={f('category')}/>
     <TextField fullWidth label="Raf / Konum" value={v.location||''} onChange={f('location')}/>
    </Stack>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField fullWidth label="OEM No" value={v.oem_number||''} onChange={f('oem_number')}/>
     <TextField fullWidth label="Marka" value={v.brand||''} onChange={f('brand')}/>
     <TextField fullWidth label="Üretici" value={v.manufacturer||''} onChange={f('manufacturer')}/>
    </Stack>
    <TextField label="Alternatif OEM numaraları" value={v.alternative_oem||''} onChange={f('alternative_oem')} multiline minRows={2} helperText="Virgül veya satır sonuyla ayırabilirsiniz."/>
    <TextField label="Uyumlu makine / modeller" value={v.compatible_models||''} onChange={f('compatible_models')} multiline minRows={2} placeholder="CS640, TC56, CX8080"/>
    <TextField label="Teknik notlar" value={v.technical_notes||''} onChange={f('technical_notes')} multiline minRows={3}/>
    {/* Satışa kapatma. Kolon ve POS filtresi zaten vardı; değeri değiştirecek
        bir yüzey yoktu, yani ürün detayındaki "Pasif Ürün" rozeti hiç
        görünemiyordu. Silmek yerine kapatmak doğru olan: geçmiş satışlar
        ürüne bağlı kalır, ürün yalnız yeni satışlara çıkmaz. */}
    <FormControlLabel
     control={<Switch checked={v.active!==false} onChange={e=>setV({...v,active:e.target.checked})}/>}
     label={v.active!==false?'Satışa açık':'Satışa kapalı'}
    />
    {v.active===false&&<Alert severity="info">
     Ürün POS ve hızlı seçim listelerinde çıkmaz. Geçmiş satışları ve stok kaydı korunur.
    </Alert>}
   </Stack>
  </DialogContent>
  <DialogActions>
   <Button onClick={close} disabled={saving}>Vazgeç</Button>
   <Button variant="contained" onClick={save} disabled={saving||(needsOverride&&manager&&overrideReason.trim().length<5)}>
    {saving?'Kaydediliyor...':'Kaydet'}
   </Button>
  </DialogActions>
 </Dialog>;
}
