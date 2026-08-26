import React from 'react';
import {useEffect,useState} from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,DialogTitle,FormControlLabel,Stack,Switch,TextField} from '@mui/material';
import {api} from '../api';

export default function EntityDialog({open,type,id,onClose,onSaved}:{open:boolean;type:'customer'|'supplier';id?:number|null;onClose:()=>void;onSaved:(entity?:any)=>void}){
 const blank={name:'',owner_name:'',phone:'',email:'',address:'',tax_number:'',opening_balance:0,risk_limit:0,payment_term_days:0,notes:'',is_active:true};
 const [v,setV]=useState<any>(blank);const [error,setError]=useState('');const [saving,setSaving]=useState(false);const path=type==='customer'?'/customers':'/suppliers';
 useEffect(()=>{if(!open)return;setError('');if(id)api.get(`${path}/${id}`).then(r=>setV({...blank,...(r.data.customer||r.data.supplier||r.data),is_active:Boolean((r.data.customer||r.data.supplier||r.data).is_active??1)})).catch(e=>setError(e.response?.data?.detail||e.message));else setV(blank)},[open,id,type]);
 const f=(k:string)=>(e:any)=>setV({...v,[k]:['opening_balance','risk_limit','payment_term_days'].includes(k)?Number(e.target.value):e.target.value});
 const save=async()=>{try{setSaving(true);setError('');const response=id?await api.put(`${path}/${id}`,v):await api.post(path,v);onSaved(response.data);onClose()}catch(e:any){setError(e.response?.data?.detail||e.message)}finally{setSaving(false)}};
 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="md"><DialogTitle>{id?(type==='customer'?'Müşteri Düzenle':'Tedarikçi Düzenle'):(type==='customer'?'Yeni Müşteri':'Yeni Tedarikçi')}</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}>{error&&<Alert severity="error">{error}</Alert>}
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth required label="İsim / Ünvan" value={v.name||''} onChange={f('name')}/><TextField fullWidth label="Yetkili / İlgili Kişi" value={v.owner_name||''} onChange={f('owner_name')}/></Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth label="Telefon" value={v.phone||''} onChange={f('phone')}/><TextField fullWidth type="email" label="E-posta" value={v.email||''} onChange={f('email')}/></Stack>
  <TextField label="Adres" multiline minRows={2} value={v.address||''} onChange={f('address')}/>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth label="Vergi No" value={v.tax_number||''} onChange={f('tax_number')}/><TextField fullWidth type="number" label="Açılış Bakiyesi" value={v.opening_balance??0} onChange={f('opening_balance')} inputProps={{step:.01}}/></Stack>
  <Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth type="number" label="Risk Limiti" value={v.risk_limit??0} onChange={f('risk_limit')} inputProps={{min:0,step:.01}}/><TextField fullWidth type="number" label="Varsayılan Vade (Gün)" value={v.payment_term_days??0} onChange={f('payment_term_days')} inputProps={{min:0,max:3650}}/></Stack>
  <TextField label="Genel Not" multiline minRows={3} value={v.notes||''} onChange={f('notes')}/>
  <FormControlLabel control={<Switch checked={Boolean(v.is_active)} onChange={(_,checked)=>setV({...v,is_active:checked})}/>} label={v.is_active?'Aktif cari':'Pasif cari'}/>
 </Stack></DialogContent><DialogActions><Button onClick={onClose}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={saving||!String(v.name||'').trim()}>{saving?'Kaydediliyor...':'Kaydet'}</Button></DialogActions></Dialog>
}
