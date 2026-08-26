import {useEffect,useState} from 'react';
import {
 Alert,Box,Button,Dialog,DialogActions,DialogContent,DialogContentText,DialogTitle,
 IconButton,MenuItem,Paper,Stack,Table,TableBody,TableCell,
 TableContainer,TableHead,TableRow,TextField,Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';

import {api,errorDetail} from '../api';

export type SupplierProfileSummary={
 id:number;supplier_id:number;supplier_name:string;name:string;source_type:'xlsx'|'pdf';
 currency_mode:string;term_days:number;vat_source:string;warning_threshold:string;
 blocked_threshold:string;created_at:string;import_count:number;
};
type Section={
 page_start:string;page_end:string;header_signature:string;
 column_map:Record<string,string>;
};
type ProfileForm={
 supplier_id:string;name:string;source_type:'xlsx'|'pdf';currency_mode:string;
 term_days:string;vat_source:string;warning_threshold:string;blocked_threshold:string;
 sheet_selector:string;header_row_strategy:string;column_map:Record<string,string>;
 page_sections:Section[];
};
type Supplier={id:number;name:string};

const requiredKeys=['code','price_cash','price_term'] as const;
const mappingFields=[
 ['code','Ürün / tedarikçi kodu'],['description','Açıklama'],['price_cash','Peşin fiyat'],
 ['price_term','Vadeli fiyat'],['unit','Birim'],['status_note','Durum notu'],
 ['currency','Para birimi'],['vat_rate','KDV oranı'],
] as const;
const blankSection=():Section=>({
 page_start:'',page_end:'',header_signature:'',
 column_map:Object.fromEntries(mappingFields.map(([key])=>[key,''])),
});
const blankForm=():ProfileForm=>({
 supplier_id:'',name:'',source_type:'xlsx',currency_mode:'inline',term_days:'0',
 vat_source:'fixed:20',warning_threshold:'25',blocked_threshold:'100',
 sheet_selector:'.*',header_row_strategy:'detect',
 column_map:Object.fromEntries(mappingFields.map(([key])=>[key,''])),
 page_sections:[blankSection()],
});
const cleanMap=(mapping:Record<string,string>,numeric=false)=>Object.fromEntries(
 Object.entries(mapping).filter(([,value])=>String(value).trim()).map(([key,value])=>[
  key,numeric?Number(value):String(value).trim(),
 ]),
);

function validate(form:ProfileForm):string{
 if(!form.supplier_id)return 'Tedarikçi seçin.';
 if(!form.name.trim())return 'Profil adı zorunludur.';
 if(Number(form.term_days)<0||Number(form.term_days)>3650)return 'Vade günü 0 ile 3650 arasında olmalıdır.';
 if(Number(form.blocked_threshold)<Number(form.warning_threshold))return 'Blok eşiği uyarı eşiğinden küçük olamaz.';
 if(form.source_type==='xlsx'){
  const missing=requiredKeys.filter(key=>!form.column_map[key]?.trim());
  if(missing.length)return 'Ürün kodu, peşin fiyat ve vadeli fiyat başlıkları zorunludur.';
  if(!form.sheet_selector.trim())return 'Excel sayfa seçicisi zorunludur.';
  if(!/^(detect|fixed:\d+)$/.test(form.header_row_strategy))return 'Başlık satırı detect veya fixed:<satır no> olmalıdır.';
 }else{
  if(!form.page_sections.length)return 'PDF profili en az bir sayfa bölümü içermelidir.';
  for(let index=0;index<form.page_sections.length;index++){
   const section=form.page_sections[index];
   const hasRange=section.page_start!==''||section.page_end!=='';
   if(hasRange&&(!section.page_start||!section.page_end||Number(section.page_end)<Number(section.page_start))){
    return `${index+1}. bölüm için geçerli başlangıç ve bitiş sayfası girin.`;
   }
   if(!hasRange&&!section.header_signature.trim())return `${index+1}. bölüm için sayfa aralığı veya başlık imzası zorunludur.`;
   if(requiredKeys.some(key=>{
    const value=section.column_map[key];
    return value===''||!Number.isInteger(Number(value))||Number(value)<0;
   })){
    return `${index+1}. bölümde ürün kodu, peşin ve vadeli fiyat sütun indeksleri zorunludur.`;
   }
   for(let otherIndex=index+1;otherIndex<form.page_sections.length;otherIndex++){
    const other=form.page_sections[otherIndex];
    const rangesOverlap=section.page_start!==''&&other.page_start!==''&&
     Number(section.page_start)<=Number(other.page_end)&&Number(other.page_start)<=Number(section.page_end);
    const signaturesOverlap=section.header_signature.trim()!==''&&other.header_signature.trim()!==''&&
     section.header_signature.trim().toLocaleLowerCase('tr-TR')===other.header_signature.trim().toLocaleLowerCase('tr-TR');
    if(rangesOverlap||signaturesOverlap)return `PDF bölümleri ${index+1} ve ${otherIndex+1} çakışıyor.`;
   }
  }
 }
 return '';
}

function payload(form:ProfileForm){
 return {
  supplier_id:Number(form.supplier_id),name:form.name.trim(),source_type:form.source_type,
  sheet_selector:form.sheet_selector.trim()||'.*',header_row_strategy:form.header_row_strategy,
  column_map:form.source_type==='xlsx'?cleanMap(form.column_map):{},
  page_sections:form.source_type==='pdf'?form.page_sections.map(section=>({
   ...(section.page_start?{page_start:Number(section.page_start),page_end:Number(section.page_end)}:{}),
   ...(section.header_signature.trim()?{header_signature:section.header_signature.trim()}:{}),
   column_map:cleanMap(section.column_map,true),
  })):[],
  currency_mode:form.currency_mode,term_days:Number(form.term_days),vat_source:form.vat_source,
  warning_threshold:form.warning_threshold,blocked_threshold:form.blocked_threshold,
 };
}

function ProfileDialog({open,profileId,onClose,onSaved}:{
 open:boolean;profileId:number|null;onClose:()=>void;onSaved:()=>void;
}){
 const [form,setForm]=useState<ProfileForm>(blankForm);
 const [suppliers,setSuppliers]=useState<Supplier[]>([]);
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 useEffect(()=>{
  if(!open)return;
  setError('');setForm(blankForm());
  void api.get<Supplier[]>('/suppliers').then(response=>setSuppliers(response.data))
   .catch(requestError=>setError(errorDetail(requestError,'Tedarikçiler yüklenemedi.')));
  if(profileId)void api.get(`/supplier-prices/profiles/${profileId}`).then(response=>{
   const data=response.data;
   setForm({
    supplier_id:String(data.supplier_id),name:data.name,source_type:data.source_type,
    currency_mode:data.currency_mode,term_days:String(data.term_days),vat_source:data.vat_source,
    warning_threshold:String(data.warning_threshold),blocked_threshold:String(data.blocked_threshold),
    sheet_selector:data.sheet_selector,header_row_strategy:data.header_row_strategy,
    column_map:{...blankForm().column_map,...data.column_map},
    page_sections:(data.page_sections?.length?data.page_sections:[blankSection()]).map((section:any)=>({
     page_start:section.page_start==null?'':String(section.page_start),
     page_end:section.page_end==null?'':String(section.page_end),
     header_signature:section.header_signature||'',
     column_map:{...blankSection().column_map,...Object.fromEntries(
      Object.entries(section.column_map||{}).map(([key,value])=>[key,String(value)]),
     )},
    })),
   });
  }).catch(requestError=>setError(errorDetail(requestError,'Profil yüklenemedi.')));
 },[open,profileId]);
 const set=(key:keyof ProfileForm,value:any)=>setForm(current=>({...current,[key]:value}));
 const save=async()=>{
  const validation=validate(form);if(validation){setError(validation);return}
  setBusy(true);setError('');
  try{
   if(profileId)await api.put(`/supplier-prices/profiles/${profileId}`,payload(form));
   else await api.post('/supplier-prices/profiles',payload(form));
   onSaved();onClose();
  }catch(requestError){setError(errorDetail(requestError,'Profil kaydedilemedi.'))}
  finally{setBusy(false)}
 };
 const updateSection=(index:number,key:keyof Section,value:any)=>setForm(current=>({
  ...current,page_sections:current.page_sections.map((section,itemIndex)=>
   itemIndex===index?{...section,[key]:value}:section),
 }));
 return <Dialog open={open} onClose={busy?undefined:onClose} fullWidth maxWidth="md">
  <DialogTitle>{profileId?'Eşleme profilini düzenle':'Yeni eşleme profili'}</DialogTitle>
  <DialogContent><Stack spacing={2} sx={{pt:1}}>
   {error&&<Alert severity="error">{error}</Alert>}
   <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
    <TextField select required fullWidth label="Tedarikçi" value={form.supplier_id} onChange={event=>set('supplier_id',event.target.value)}>
     {suppliers.map(supplier=><MenuItem key={supplier.id} value={supplier.id}>{supplier.name}</MenuItem>)}
    </TextField>
    <TextField required fullWidth label="Profil adı" value={form.name} onChange={event=>set('name',event.target.value)}/>
    <TextField select fullWidth label="Dosya tipi" value={form.source_type} onChange={event=>set('source_type',event.target.value)}>
     <MenuItem value="xlsx">Excel (xlsx)</MenuItem><MenuItem value="pdf">PDF</MenuItem>
    </TextField>
   </Stack>
   <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
    <TextField select fullWidth label="Para birimi kaynağı" value={form.currency_mode} onChange={event=>set('currency_mode',event.target.value)}>
     <MenuItem value="inline">Fiyat metninden</MenuItem><MenuItem value="column">Sütundan</MenuItem>
     <MenuItem value="fixed:TRY">Sabit TRY</MenuItem><MenuItem value="fixed:EUR">Sabit EUR</MenuItem><MenuItem value="fixed:USD">Sabit USD</MenuItem>
    </TextField>
    <TextField fullWidth type="number" label="Vade günü" value={form.term_days} onChange={event=>set('term_days',event.target.value)} inputProps={{min:0,max:3650}}/>
    <TextField select fullWidth label="KDV kaynağı" value={form.vat_source} onChange={event=>set('vat_source',event.target.value)}>
     <MenuItem value="indeks_sheet">İndeks sayfası</MenuItem><MenuItem value="column">Sütundan</MenuItem>
     <MenuItem value="fixed:0">Sabit %0</MenuItem><MenuItem value="fixed:10">Sabit %10</MenuItem><MenuItem value="fixed:20">Sabit %20</MenuItem>
    </TextField>
   </Stack>
   <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
    <TextField fullWidth type="number" label="Uyarı eşiği (%)" value={form.warning_threshold} onChange={event=>set('warning_threshold',event.target.value)} inputProps={{min:0,step:.01}}/>
    <TextField fullWidth type="number" label="Blok eşiği (%)" value={form.blocked_threshold} onChange={event=>set('blocked_threshold',event.target.value)} inputProps={{min:0,step:.01}}/>
   </Stack>
   <Alert severity="info">code = ürün/tedarikçi kodu; price_cash = peşin fiyat; price_term = vadeli fiyat. Açıklama, birim, durum notu, para birimi ve KDV opsiyoneldir.</Alert>
   {form.source_type==='xlsx'?<Stack spacing={1.5}>
    <Typography variant="h6">Excel eşlemesi</Typography>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField fullWidth label="Sayfa seçicisi (regex)" value={form.sheet_selector} onChange={event=>set('sheet_selector',event.target.value)}/>
     <TextField fullWidth label="Başlık satırı" value={form.header_row_strategy} onChange={event=>set('header_row_strategy',event.target.value)} helperText="detect veya fixed:<satır no>"/>
    </Stack>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1} flexWrap="wrap">
     {mappingFields.map(([key,label])=><TextField key={key} required={requiredKeys.includes(key as any)} label={`${label} başlığı`} value={form.column_map[key]} onChange={event=>set('column_map',{...form.column_map,[key]:event.target.value})} sx={{minWidth:220,flex:1}}/>)}
    </Stack>
   </Stack>:<Stack spacing={1.5}>
    <Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="h6">PDF sayfa bölümleri</Typography><Button startIcon={<AddIcon/>} onClick={()=>set('page_sections',[...form.page_sections,blankSection()])}>Bölüm ekle</Button></Stack>
    {form.page_sections.map((section,index)=><Paper variant="outlined" sx={{p:1.5}} key={index}>
     <Stack spacing={1}>
      <Stack direction="row" justifyContent="space-between" alignItems="center"><Typography fontWeight={700}>{index+1}. bölüm</Typography><IconButton aria-label={`${index+1}. bölümü sil`} disabled={form.page_sections.length===1} onClick={()=>set('page_sections',form.page_sections.filter((_,itemIndex)=>itemIndex!==index))}><DeleteIcon/></IconButton></Stack>
      <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
       <TextField type="number" label="Başlangıç sayfası" value={section.page_start} onChange={event=>updateSection(index,'page_start',event.target.value)} inputProps={{min:1}}/>
       <TextField type="number" label="Bitiş sayfası" value={section.page_end} onChange={event=>updateSection(index,'page_end',event.target.value)} inputProps={{min:1}}/>
       <TextField fullWidth label="veya başlık imzası" value={section.header_signature} onChange={event=>updateSection(index,'header_signature',event.target.value)}/>
      </Stack>
      <Stack direction={{xs:'column',sm:'row'}} spacing={1} flexWrap="wrap">
       {mappingFields.map(([key,label])=><TextField key={key} type="number" required={requiredKeys.includes(key as any)} label={`${label} indeksi`} value={section.column_map[key]} onChange={event=>updateSection(index,'column_map',{...section.column_map,[key]:event.target.value})} inputProps={{min:0}} sx={{minWidth:190,flex:1}}/>)}
      </Stack>
     </Stack>
    </Paper>)}
   </Stack>}
  </Stack></DialogContent>
  <DialogActions><Button onClick={onClose} disabled={busy}>Vazgeç</Button><Button variant="contained" onClick={()=>void save()} disabled={busy}>{busy?'Kaydediliyor…':'Kaydet'}</Button></DialogActions>
 </Dialog>;
}

export default function SupplierProfileManager({profiles,loading,onReload,canManage}:{
 profiles:SupplierProfileSummary[];loading:boolean;onReload:()=>void;canManage:boolean;
}){
 const [dialogOpen,setDialogOpen]=useState(false);
 const [editId,setEditId]=useState<number|null>(null);
 const [deleteTarget,setDeleteTarget]=useState<SupplierProfileSummary|null>(null);
 const [error,setError]=useState('');
 const remove=async()=>{
  if(!deleteTarget)return;
  try{await api.delete(`/supplier-prices/profiles/${deleteTarget.id}`);setDeleteTarget(null);onReload()}
  catch(requestError){setDeleteTarget(null);setError(errorDetail(requestError,'Profil silinemedi.'))}
 };
 return <Stack spacing={2}>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1}>
   <Box><Typography variant="h6">Eşleme Profilleri</Typography><Typography color="text.secondary" variant="body2">Tedarikçi dosyalarındaki alanların ürün ve fiyat kolonlarıyla nasıl eşleşeceğini yönetin.</Typography></Box>
   {canManage&&<Button variant="contained" startIcon={<AddIcon/>} onClick={()=>{setEditId(null);setDialogOpen(true)}}>Yeni profil</Button>}
  </Stack>
  {loading?<Typography>Profiller yükleniyor…</Typography>:profiles.length===0?<Alert severity="info" action={canManage?<Button color="inherit" onClick={()=>{setEditId(null);setDialogOpen(true)}}>Yeni profil</Button>:undefined}>Henüz eşleme profili yok. {canManage?'İlk Excel veya PDF fiyat listeniz için bir profil oluşturun.':'Profil oluşturmak için içe aktarma yetkisi gerekir.'}</Alert>:
   <TableContainer component={Paper} variant="outlined"><Table size="small">
    <TableHead><TableRow><TableCell>Profil</TableCell><TableCell>Tedarikçi</TableCell><TableCell>Tip</TableCell><TableCell>Eşikler</TableCell><TableCell align="right">Kullanım</TableCell><TableCell align="right">İşlem</TableCell></TableRow></TableHead>
    <TableBody>{profiles.map(profile=><TableRow key={profile.id}>
     <TableCell><Typography fontWeight={700}>{profile.name}</Typography></TableCell><TableCell>{profile.supplier_name}</TableCell>
     <TableCell>{profile.source_type==='pdf'?'PDF':'Excel'}</TableCell><TableCell>%{profile.warning_threshold} / %{profile.blocked_threshold}</TableCell>
     <TableCell align="right">{profile.import_count}</TableCell><TableCell align="right">{canManage&&<>
      <IconButton aria-label={`${profile.name} profilini düzenle`} onClick={()=>{setEditId(profile.id);setDialogOpen(true)}}><EditIcon/></IconButton>
      <IconButton aria-label={`${profile.name} profilini sil`} color="error" onClick={()=>setDeleteTarget(profile)}><DeleteIcon/></IconButton>
     </>}</TableCell>
    </TableRow>)}</TableBody>
   </Table></TableContainer>}
  <ProfileDialog open={dialogOpen} profileId={editId} onClose={()=>setDialogOpen(false)} onSaved={onReload}/>
  <Dialog open={Boolean(deleteTarget)} onClose={()=>setDeleteTarget(null)}>
   <DialogTitle>Eşleme profilini sil</DialogTitle><DialogContent><DialogContentText>{deleteTarget?.name} profili kalıcı olarak silinecek. Devam edilsin mi?</DialogContentText></DialogContent>
   <DialogActions><Button onClick={()=>setDeleteTarget(null)}>Vazgeç</Button><Button color="error" variant="contained" onClick={()=>void remove()}>Sil</Button></DialogActions>
  </Dialog>
 </Stack>;
}
