import React from 'react';
import {useEffect,useState} from 'react';
import {Alert,Autocomplete,Button,Dialog,DialogActions,DialogContent,DialogTitle,FormControlLabel,MenuItem,Stack,Switch,TextField} from '@mui/material';
import {api} from '../api';

export const MACHINE_STATUS_LABELS:Record<string,string>={active:'Aktif',maintenance:'Bakımda',idle:'Beklemede',sold:'Satıldı',scrapped:'Hurda'};

// PUT /api/machines/:id tam değiştirme (full replacement) yaptığı için dialog
// MachineCreate'in TÜM alanlarını taşımalı; aksi halde düzenleme kayıtlı
// alanları siler.
const blank={customer_id:null as number|null,brand:'',manufacturer:'',model:'',variant:'',serial_number:'',chassis_number:'',registration_number:'',engine_number:'',model_year:'' as number|string,working_hours:0,status:'active',notes:'',is_active:true};

export default function MachineDialog({open,id,onClose,onSaved}:{open:boolean;id?:number|null;onClose:()=>void;onSaved:(machine?:any)=>void}){
 const [v,setV]=useState<any>(blank);const [customers,setCustomers]=useState<any[]>([]);const [error,setError]=useState('');const [saving,setSaving]=useState(false);
 useEffect(()=>{if(!open)return;setError('');
  api.get('/customers',{params:{active:'all'}}).then(r=>setCustomers(r.data||[])).catch(()=>setCustomers([]));
  if(id)api.get(`/machines/${id}`).then(r=>setV({...blank,...r.data,model_year:r.data.model_year??'',is_active:Boolean(r.data.is_active)})).catch(e=>setError(e.response?.data?.detail||e.message));
  else setV(blank);
 },[open,id]);
 const f=(k:string)=>(e:any)=>setV({...v,[k]:e.target.value});
 const save=async()=>{
  try{
   setSaving(true);setError('');
   const payload={...v,
    customer_id:v.customer_id||null,
    model_year:v.model_year===''||v.model_year===null?null:Number(v.model_year),
    working_hours:Number(v.working_hours||0),
    manufacturer:v.manufacturer||null,variant:v.variant||null,serial_number:v.serial_number||null,
    chassis_number:v.chassis_number||null,registration_number:v.registration_number||null,
    engine_number:v.engine_number||null,notes:v.notes||null,
   };
   delete payload.id;delete payload.company_id;delete payload.customer_name;delete payload.created_at;delete payload.updated_at;
   const response=id?await api.put(`/machines/${id}`,payload):await api.post('/machines',payload);
   onSaved(response.data);onClose();
  }catch(e:any){
   // 409: aynı firmada kayıtlı seri/şasi numarası — backend'in net Türkçe
   // mesajını olduğu gibi göster.
   setError(e.response?.data?.detail||e.message);
  }finally{setSaving(false)}
 };
 const selectedCustomer=customers.find(c=>c.id===v.customer_id)||null;
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="md"><DialogTitle>{id?'Makine Düzenle':'Yeni Makine'}</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}>{error&&<Alert severity="error">{error}</Alert>}
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth required label="Marka" value={v.brand||''} onChange={f('brand')}/><TextField fullWidth required label="Model" value={v.model||''} onChange={f('model')}/></Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth label="Üretici" value={v.manufacturer||''} onChange={f('manufacturer')}/><TextField fullWidth label="Varyant" value={v.variant||''} onChange={f('variant')}/></Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth label="Seri No" value={v.serial_number||''} onChange={f('serial_number')}/><TextField fullWidth label="Şasi No" value={v.chassis_number||''} onChange={f('chassis_number')}/></Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth label="Plaka" value={v.registration_number||''} onChange={f('registration_number')}/><TextField fullWidth label="Motor No" value={v.engine_number||''} onChange={f('engine_number')}/></Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
   <Autocomplete fullWidth size="medium" options={customers} getOptionLabel={o=>o?.name||''} isOptionEqualToValue={(o,val)=>o.id===val.id} value={selectedCustomer} onChange={(_,val)=>setV({...v,customer_id:val?val.id:null})} renderInput={params=><TextField {...params} label="Müşteri (Makine Sahibi)"/>}/>
   <TextField fullWidth select label="Durum" value={v.status||'active'} onChange={f('status')}>{Object.entries(MACHINE_STATUS_LABELS).map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField>
  </Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth type="number" label="Model Yılı" value={v.model_year??''} onChange={f('model_year')} inputProps={{min:1900,max:new Date().getFullYear()+1}}/><TextField fullWidth type="number" label="Çalışma Saati" value={v.working_hours??0} onChange={f('working_hours')} inputProps={{min:0,step:.01}}/></Stack>
  <TextField label="Notlar" multiline minRows={2} value={v.notes||''} onChange={f('notes')}/>
  <FormControlLabel control={<Switch checked={Boolean(v.is_active)} onChange={(_,checked)=>setV({...v,is_active:checked})}/>} label={v.is_active?'Aktif makine':'Pasif makine'}/>
 </Stack></DialogContent><DialogActions><Button onClick={onClose}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={saving||!String(v.brand||'').trim()||!String(v.model||'').trim()}>{saving?'Kaydediliyor...':'Kaydet'}</Button></DialogActions></Dialog>
}
