import {useCallback,useEffect,useState} from 'react';
import {
 Alert,Box,Button,Chip,MenuItem,Paper,Stack,TextField,Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';

type TemplateRow={
 id:number;company_id:number;code:string;channel:string;name:string;body:string;
 message_class:string;is_active:boolean;version:number;
 created_by:number|null;created_at:string;updated_by:number|null;updated_at:string;
 approved_by:number|null;approved_at:string|null;
};

const CHANNELS=['SMS','WHATSAPP','EMAIL'] as const;
const CLASSES=[
 {value:'SERVICE_TRANSACTIONAL',label:'İşlemsel (hizmet bildirimi)'},
 {value:'COMMERCIAL',label:'Ticari (İYS onayı gerekir)'},
] as const;

const EMPTY={code:'',channel:'SMS',name:'',body:'',message_class:''};

export default function NotificationTemplates(){
 const {can}=useAuth();
 const canAdmin=can('notifications_admin');

 const [rows,setRows]=useState<TemplateRow[]>([]);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');
 const [form,setForm]=useState<typeof EMPTY>(EMPTY);
 const [editing,setEditing]=useState<TemplateRow|null>(null);
 const [busy,setBusy]=useState(false);

 const load=useCallback(async()=>{
  setLoading(true);setError('');
  try{
   const {data}=await api.get<TemplateRow[]>('/notifications/templates');
   setRows(data||[]);
  }catch(err){setError(errorDetail(err,'Şablonlar yüklenemedi'));}
  finally{setLoading(false);}
 },[]);

 useEffect(()=>{void load();},[load]);

 const save=async()=>{
  setBusy(true);setError('');setNotice('');
  try{
   if(editing){
    await api.patch(`/notifications/templates/${editing.id}`,{
     name:form.name,body:form.body,message_class:form.message_class||undefined,
    });
    setNotice('Şablon güncellendi. Gövde veya sınıf değiştiyse yeniden onay gerekir.');
   }else{
    await api.post('/notifications/templates',form);
    setNotice('Şablon oluşturuldu. Kullanılabilmesi için sınıf onayı gerekir.');
   }
   setForm(EMPTY);setEditing(null);await load();
  }catch(err){setError(errorDetail(err,'Şablon kaydedilemedi'));}
  finally{setBusy(false);}
 };

 const approve=async(row:TemplateRow)=>{
  setBusy(true);setError('');
  try{
   await api.post(`/notifications/templates/${row.id}/approve`,{});
   setNotice('Şablon onaylandı ve etkinleştirildi.');await load();
  }catch(err){setError(errorDetail(err,'Şablon onaylanamadı'));}
  finally{setBusy(false);}
 };
 const edit=(row:TemplateRow)=>{
  setEditing(row);
  setForm({code:row.code,channel:row.channel,name:row.name,
   body:row.body,message_class:row.message_class});
 };
 const columns:GridColDef[]=[
  {field:'code',headerName:'Kod',minWidth:170,flex:1},
  {field:'channel',headerName:'Kanal',width:105},
  {field:'name',headerName:'Ad',minWidth:220,flex:1},
  {field:'message_class',headerName:'Sınıf',width:125,renderCell:({row})=><Chip size="small" label={row.message_class==='COMMERCIAL'?'Ticari':'İşlemsel'} color={row.message_class==='COMMERCIAL'?'warning':'default'}/>},
  {field:'version',headerName:'Sürüm',width:90,valueFormatter:value=>`v${value}`},
  {field:'is_active',headerName:'Durum',width:190,renderCell:({row})=>row.is_active?<Chip size="small" color="success" label="Etkin"/>:<Chip size="small" icon={<WarningAmberIcon/>} color="warning" label="Sınıf onayı bekliyor"/>},
 ];

 return <Box sx={{p:2,'@media (max-width:899.95px)':{'& .MuiButton-root':{minHeight:44},'& .MuiInputBase-input':{minHeight:44,boxSizing:'border-box'}}}}>
  <Typography variant="h5" sx={{mb:1}}>Bildirim Şablonları</Typography>
  <Alert severity="info" sx={{mb:2}}>
   Her şablon bir <strong>mesaj sınıfı</strong> beyan etmek zorundadır ve
   onaylanmadan gönderimde kullanılamaz. Şablon metnine bağlantı adresi
   yazılamaz.
  </Alert>

  {error&&<Alert severity="error" sx={{mb:2}} onClose={()=>setError('')}>{error}</Alert>}
  {notice&&<Alert severity="success" sx={{mb:2}} onClose={()=>setNotice('')}>{notice}</Alert>}

  {canAdmin&&<Paper sx={{p:2,mb:2}}>
   <Typography variant="subtitle1" sx={{mb:2}}>
    {editing?`Şablonu düzenle: ${editing.code}`:'Yeni şablon'}
   </Typography>
   <Stack spacing={2}>
    <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
     <TextField label="Kod" size="small" fullWidth value={form.code}
      disabled={Boolean(editing)}
      onChange={event=>setForm({...form,code:event.target.value})}/>
     <TextField label="Kanal" size="small" select fullWidth value={form.channel}
      disabled={Boolean(editing)}
      onChange={event=>setForm({...form,channel:event.target.value})}>
      {CHANNELS.map(channel=><MenuItem key={channel} value={channel}>{channel}</MenuItem>)}
     </TextField>
     <TextField label="Mesaj sınıfı" size="small" select fullWidth required
      value={form.message_class}
      helperText="Zorunlu — varsayılan yoktur"
      onChange={event=>setForm({...form,message_class:event.target.value})}>
      {CLASSES.map(item=><MenuItem key={item.value} value={item.value}>{item.label}</MenuItem>)}
     </TextField>
    </Stack>
    <TextField label="Ad" size="small" value={form.name}
     onChange={event=>setForm({...form,name:event.target.value})}/>
    <TextField label="Gövde" multiline minRows={3} value={form.body}
     helperText="Değişkenler: {musteri_adi} {makine_adi} {randevu_tarihi} {vade_tarihi} {tutar} {firma_adi}"
     onChange={event=>setForm({...form,body:event.target.value})}/>
    <Stack direction="row" spacing={1}>
     <Button variant="contained" disabled={busy} onClick={()=>void save()}>
      {editing?'Güncelle':'Oluştur'}
     </Button>
     {editing&&<Button onClick={()=>{setEditing(null);setForm(EMPTY);}}>Vazgeç</Button>}
    </Stack>
   </Stack>
  </Paper>}

  <Paper>
   <Box data-testid="notification-templates-data-surface" sx={{p:1.5}}>
    <ResponsiveTable
     rows={rows}
     columns={columns}
     loading={loading}
     cardTitle={row=>row.name}
     cardSubtitle={row=>`${row.code} · ${row.channel}`}
     cardFields={[
      {label:'Sınıf',value:row=>row.message_class==='COMMERCIAL'?'Ticari':'İşlemsel'},
      {label:'Sürüm',value:row=>`v${row.version}`},
      {label:'Durum',value:row=>row.is_active?'Etkin':'Sınıf onayı bekliyor'},
     ]}
     cardActions={canAdmin?[
      {label:'Düzenle',onClick:edit},
      {label:'Onayla',hidden:row=>row.is_active,disabled:()=>busy,onClick:row=>void approve(row)},
     ]:undefined}
     cardActionsOnDesktop
     getRowId={row=>row.id}
    />
   </Box>
  </Paper>
 </Box>;
}
