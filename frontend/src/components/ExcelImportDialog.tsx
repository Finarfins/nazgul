import React from 'react';
import {useState} from 'react';
import {Alert,Button,Dialog,DialogActions,DialogContent,DialogTitle,LinearProgress,Stack,TextField,Typography} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import {api,openAuthenticated,policyOverrideHeaders,policyOverrideRequired} from '../api';
import {useAuth} from '../AuthContext';

type Props={open:boolean;kind:'customers'|'suppliers'|'products'|'sales'|'payments';onClose:()=>void;onDone:()=>void};

export default function ExcelImportDialog({open,kind,onClose,onDone}:Props){
 const {user}=useAuth();
 const manager=user?.role==='admin'||user?.role==='yonetici';
 const [file,setFile]=useState<File|null>(null);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const [result,setResult]=useState<any|null>(null);
 const [needsOverride,setNeedsOverride]=useState(false);
 const [duplicateWarning,setDuplicateWarning]=useState('');
 const [overrideReason,setOverrideReason]=useState('');
 const title=kind==='customers'?'Müşteri':kind==='suppliers'?'Tedarikçi':kind==='sales'?'Satış':kind==='payments'?'Tahsilat':'Ürün';

 const resetPolicy=()=>{
  setNeedsOverride(false);
  setOverrideReason('');
 };

 const close=()=>{
  if(loading)return;
  setFile(null);
  setError('');
  setResult(null);
  resetPolicy();
  onClose();
 };

 const upload=async(allowDuplicate=false)=>{
  if(!file){setError('Önce Excel dosyası seçin.');return;}
  try{
   setLoading(true);
   setError('');
   const fd=new FormData();
   fd.append('file',file);
   const headers:any={'Content-Type':'multipart/form-data'};
   if(overrideReason.trim())Object.assign(headers,policyOverrideHeaders(overrideReason));
   // Genel istemcinin 15 sn'lik zaman aşımı toplu içe aktarma için çok kısa:
   // binlerce satırlık bir satış raporu yavaş bir sunucuda daha uzun sürer.
   // Süre dolunca tarayıcı isteği keser ama SUNUCU İŞİ BİTİRİP KAYDEDER —
   // kullanıcı "başarısız" görür, kayıtlar aslında girmiştir. Gerçek kurulumda
   // tam olarak bu yaşandı: 439 satış belgesi oluştu, ekranda hata çıktı.
   const r=await api.post(`/imports/${kind}/excel`,fd,{
    headers,
    timeout:180000,
    params:allowDuplicate?{allow_duplicate:true}:undefined,
   });
   setResult(r.data);
   setNeedsOverride(false);
   setDuplicateWarning('');
   setFile(null); // Aynı dosyaya tekrar basıp mükerrer kayıt oluşturmayı engeller.
   onDone();
  }catch(e:any){
   const detail=e.response?.data?.detail||'Aktarım başarısız.';
   setError(detail);
   if(policyOverrideRequired(e))setNeedsOverride(true);
   // Sunucu "bu şirkette zaten satış raporu var" diyorsa (409) tek yol
   // allow_duplicate. Bunu varsayılan yapmak tüm geçmişi ikiye katlar, o yüzden
   // yalnız sunucu itiraz ettiğinde ve açık onayla sunulur. Eksik kalan satırları
   // (eşleşmeyen cari sonradan açıldığında) yüklemenin başka yolu yok.
   if(e.response?.status===409)setDuplicateWarning(detail);
  }finally{
   setLoading(false);
  }
 };

 return <Dialog open={open} onClose={close} fullWidth maxWidth="sm">
  <DialogTitle>Excel’den {title} Yükle</DialogTitle>
  <DialogContent>
   <Stack spacing={2} mt={1}>
    {loading&&<LinearProgress/>}
    {kind==='payments'
     ?<Typography color="text.secondary">{`Cari, Tarih ve Tutar sütunları zorunlu; Yöntem ve Not isteğe bağlı. Her satır normal bir tahsilat kaydı olarak işlenir ve cari bakiyeyi düşürür. Cari isimle eşleştirilir.`}</Typography>
     :kind==='sales'
     ?<Typography color="text.secondary">{`BizimHesap "Satış Raporu" export'unu olduğu gibi yükleyin. Kalemler müşteri + gün bazında satış belgelerine dönüşür; müşteri isimle, ürün kod/barkod/isim ile eşleştirilir. Belgeler taslak olarak girilir ve stoğu etkilemez.`}</Typography>
     :<><Typography color="text.secondary">Şablonu indirip kolon adlarını değiştirmeden doldurun. Mevcut kayıtlar isim/kod/barkod eşleşmesine göre güncellenir.</Typography>
    <Button startIcon={<DownloadIcon/>} onClick={()=>openAuthenticated(`/imports/${kind}/template.xlsx`,`${kind}-sablonu.xlsx`)}>Excel Şablonunu İndir</Button></>}
    <Button component="label" variant="outlined" startIcon={<UploadFileIcon/>}>
     {file?.name||'Excel Dosyası Seç'}
     <input hidden type="file" accept=".xlsx,.xlsm" onChange={e=>{setFile(e.target.files?.[0]||null);setResult(null);setError('');resetPolicy();}}/>
    </Button>
    {error&&<Alert severity="error">{error}</Alert>}
    {needsOverride&&manager&&kind==='products'&&<TextField
     label="Yönetici onay gerekçesi"
     value={overrideReason}
     onChange={e=>setOverrideReason(e.target.value)}
     helperText="En az 5 karakter. Excel içindeki eksi stok istisnaları kayıt altına alınır."
     inputProps={{maxLength:500}}
     multiline
     minRows={2}
     required
    />}
    {result&&kind==='sales'&&<Alert severity={result.errors?.length?'warning':'success'}>
     Belge: {result.documents_created} · Kalem: {result.lines_imported} · Eşleşmeyen müşteri: {result.unmatched_customer_count} · Eşleşmeyen ürün: {result.unmatched_product_count} · Hatalı satır: {result.errors?.length||0}
    </Alert>}
    {result&&kind!=='sales'&&<Alert severity={result.errors?.length?'warning':'success'}>
     Eklenen: {result.inserted} · Güncellenen: {result.updated} · Hatalı satır: {result.errors?.length||0}
     {Number(result.policy_overrides||0)>0?` · Yönetici istisnası: ${result.policy_overrides}`:''}
    </Alert>}
   </Stack>
  </DialogContent>
  <DialogActions>
   <Button onClick={close} disabled={loading}>Kapat</Button>
   {/* Sunucu 409 dediğinde tek çıkış yolu. Ayrı ve uyarı renkli bir düğme:
       basan kişi mükerrer riskini bilerek alsın. */}
   {duplicateWarning&&manager&&<Button color="warning" disabled={loading||!file} onClick={()=>upload(true)}>
    Yine de yükle
   </Button>}
   <Button variant="contained" disabled={loading||!file||(needsOverride&&manager&&overrideReason.trim().length<5)} onClick={()=>upload()}>
    {loading?'İşleniyor...':'Yükle ve İşle'}
   </Button>
  </DialogActions>
 </Dialog>;
}
