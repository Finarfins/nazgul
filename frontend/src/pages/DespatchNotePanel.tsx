import {useCallback,useEffect,useState} from 'react';
import {
 Alert,Box,Button,Chip,CircularProgress,Dialog,DialogActions,DialogContent,
 DialogTitle,Paper,Stack,TextField,Tooltip,Typography,
} from '@mui/material';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import SendIcon from '@mui/icons-material/Send';
import SyncIcon from '@mui/icons-material/Sync';
import {api,errorDetail} from '../api';

// e-İrsaliye durumları — `app/einvoice/edespatch.py::BILINEN` ile BİREBİR
// aynı SEKİZ değer. e-Fatura'nın (`EInvoiceStatus`) sözlüğünden AYRIDIR ve
// bu kasıtlı: aynı sayı iki üründe farklı şey demek (keşif §1/§5, `100`).
export type EDespatchStatus=
 'NONE'|'QUEUED'|'PROCESSING'|'SIGNED'|'SENT'|'DELIVERED'|'FAILED'|'UNKNOWN';

export type DespatchNote={
 id:number;
 invoice_id:number;
 despatch_uuid:string;
 despatch_number:string|null;
 edespatch_status:EDespatchStatus;
 edespatch_gib_status_code:string|null;
 edespatch_provider_uuid:string|null;
 edespatch_last_error:string|null;
 driver_name:string|null;
 vehicle_plate:string|null;
};

const STATUS_VIEW:Record<EDespatchStatus,{label:string;color:'default'|'info'|'success'|'error'|'warning'}>={
 NONE:{label:'e-İrsaliye: gönderilmedi',color:'default'},
 QUEUED:{label:'e-İrsaliye: kuyrukta',color:'info'},
 PROCESSING:{label:'e-İrsaliye: işleniyor',color:'info'},
 SIGNED:{label:'e-İrsaliye: imzalandı',color:'info'},
 SENT:{label:'e-İrsaliye: gönderildi',color:'info'},
 DELIVERED:{label:'e-İrsaliye: alındı',color:'success'},
 FAILED:{label:'e-İrsaliye: başarısız',color:'error'},
 // UNKNOWN `error` DEĞİL `warning`: bir hata değil, bir BELİRSİZLİK.
 // Kırmızı göstermek kullanıcıyı "yeniden gönder"e iter ve tam olarak o
 // yol kapalıdır (çift irsaliye riski) — doğru eylem SORGULAMAKTIR.
 UNKNOWN:{label:'e-İrsaliye: durum bilinmiyor',color:'warning'},
};

// Gönderim YALNIZ bu iki durumdan açıktır. Sunucudaki
// `edespatch.GONDERIM_KAPALI`nın tümleyeni; buton onunla AYNI kuralı
// gösterir ki kullanıcı 409 alan bir butona basmasın.
const GONDERILEBILIR:ReadonlySet<string>=new Set(['NONE','FAILED']);

// `UNKNOWN`da yapılacak TEK iş sorgudur ve arayüz bunu SÖYLER. Sunucu da
// aynı cümleyi 409 gövdesinde veriyor; burada kullanıcı o hatayı ALMADAN
// önce görüyor.
const BELIRSIZ_UYARISI=
 'Önceki gönderimin sonucu bilinmiyor. Çift irsaliye riskine karşı önce '+
 '"Durumu Sorgula" ile sağlayıcıya sorun.';

type FormState={
 actual_shipment_at:string;
 driver_name:string;
 driver_national_id:string;
 vehicle_plate:string;
 trailer_plate:string;
 delivery_address:string;
 delivery_postal_code:string;
};

const BOS_FORM:FormState={
 actual_shipment_at:'',driver_name:'',driver_national_id:'',
 vehicle_plate:'',trailer_plate:'',delivery_address:'',delivery_postal_code:'',
};

export function DespatchNotePanel({invoiceId}:{invoiceId:number}){
 const [note,setNote]=useState<DespatchNote|null>(null);
 const [loading,setLoading]=useState(true);
 const [busy,setBusy]=useState(false);
 const [dialogOpen,setDialogOpen]=useState(false);
 const [form,setForm]=useState<FormState>(BOS_FORM);
 const [error,setError]=useState('');
 const [notice,setNotice]=useState('');

 const load=useCallback(()=>{
  setLoading(true);setError('');
  api.get(`/despatch-notes?invoice_id=${invoiceId}`)
   .then(response=>{
    const items=(response.data?.items??[]) as DespatchNote[];
    // FATURA BAŞINA EN FAZLA BİR (E4a). Liste yine de bir DİZİ döner ve
    // ilki alınır — E4b kısmi sevki getirdiğinde burada bir tablo olacak
    // ve o gün bu satır tek yerde değişecek.
    setNote(items.length?items[0]:null);
   })
   .catch(err=>setError(errorDetail(err,'e-İrsaliye durumu yüklenemedi.')))
   .finally(()=>setLoading(false));
 },[invoiceId]);
 useEffect(load,[load]);

 const alan=(key:keyof FormState)=>(event:{target:{value:string}})=>
  setForm(current=>({...current,[key]:event.target.value}));

 const create=async()=>{
  setBusy(true);setError('');setNotice('');
  try{
   const response=await api.post('/despatch-notes',{
    invoice_id:invoiceId,
    actual_shipment_at:form.actual_shipment_at,
    driver_name:form.driver_name.trim(),
    driver_national_id:form.driver_national_id.trim(),
    vehicle_plate:form.vehicle_plate.trim(),
    // Boş dorse GÖNDERİLMEZ: sunucu `null` bekliyor, boş dize değil.
    trailer_plate:form.trailer_plate.trim()||null,
    delivery_address:form.delivery_address.trim(),
    delivery_postal_code:form.delivery_postal_code.trim(),
   });
   setNote(response.data as DespatchNote);
   setDialogOpen(false);setForm(BOS_FORM);
   setNotice('e-İrsaliye kaydı oluşturuldu. Göndermek için "Gönder"e basın.');
  }catch(err){setError(errorDetail(err,'e-İrsaliye oluşturulamadı.'))}
  finally{setBusy(false)}
 };

 const eylem=async(yol:'submit'|'sync',basarisiz:string)=>{
  if(!note)return;
  setBusy(true);setError('');setNotice('');
  try{
   const response=await api.post(`/despatch-notes/${note.id}/edespatch/${yol}`);
   setNote(response.data as DespatchNote);
  }catch(err){setError(errorDetail(err,basarisiz))}
  finally{setBusy(false)}
 };

 if(loading)return <Paper variant="outlined" sx={{p:2}}>
  <Stack direction="row" spacing={1.5} alignItems="center">
   <CircularProgress size={22}/>
   <Typography color="text.secondary">e-İrsaliye durumu yükleniyor…</Typography>
  </Stack>
 </Paper>;

 const status=note?.edespatch_status??'NONE';
 const view=STATUS_VIEW[status]??STATUS_VIEW.NONE;
 const gonderilebilir=!!note&&GONDERILEBILIR.has(status);
 const chip=<Chip label={view.label} color={view.color}
   variant={status==='NONE'?'outlined':'filled'}/>;
 // `zorunlular` PLAKA + ŞOFÖR dalıdır (E4a kapsamı). Kargo firması dalı
 // sunucuda ve şemada AÇIK ama bu ekranda YOK — E4b'nin işi.
 const zorunlular=Boolean(
  form.actual_shipment_at&&form.driver_name.trim()&&
  form.driver_national_id.trim().length===11&&form.vehicle_plate.trim()&&
  form.delivery_address.trim()&&form.delivery_postal_code.trim().length>=4,
 );

 return <Paper variant="outlined" sx={{p:2}}>
  <Stack spacing={1.5}>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between"
     alignItems={{sm:'center'}} gap={1}>
    <Box>
     <Typography variant="h6" fontWeight={800}>e-İrsaliye</Typography>
     <Typography variant="body2" color="text.secondary">
      Sevk irsaliyesi ve sağlayıcı durumu
     </Typography>
    </Box>
    {note?.edespatch_last_error
     ?<Tooltip title={note.edespatch_last_error} arrow>{chip}</Tooltip>
     :chip}
   </Stack>

   {notice&&<Alert severity="info" onClose={()=>setNotice('')}>{notice}</Alert>}
   {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
   {status==='UNKNOWN'&&<Alert severity="warning">{BELIRSIZ_UYARISI}</Alert>}
   {status==='FAILED'&&note?.edespatch_last_error&&
    <Alert severity="error">{note.edespatch_last_error}</Alert>}

   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between"
     alignItems={{sm:'center'}} gap={1}>
    <Typography variant="caption" color="text.secondary">
     {note
      ?`${note.despatch_number??'-'} · ETTN: ${note.despatch_uuid}`
      :'Bu fatura için henüz e-İrsaliye oluşturulmadı.'}
    </Typography>
    <Stack direction="row" gap={1} flexWrap="wrap">
     {!note&&<Button variant="contained" startIcon={<LocalShippingIcon/>}
       onClick={()=>setDialogOpen(true)} disabled={busy}
       sx={{minHeight:{xs:44,md:40}}}>e-İrsaliye Oluştur</Button>}
     {note&&<Button variant="contained" startIcon={<SendIcon/>}
       disabled={!gonderilebilir||busy}
       onClick={()=>eylem('submit','e-İrsaliye gönderilemedi.')}
       sx={{minHeight:{xs:44,md:40}}}>
      {busy?'İşleniyor…':'Gönder'}
     </Button>}
     {note&&status!=='NONE'&&<Button variant="outlined" startIcon={<SyncIcon/>}
       disabled={busy}
       onClick={()=>eylem('sync','Durum sorgulanamadı.')}
       sx={{minHeight:{xs:44,md:40}}}>Durumu Sorgula</Button>}
     {note&&status!=='NONE'&&<Button variant="text"
       href={`/api/despatch-notes/${note.id}/edespatch/download?format=xml`}
       sx={{minHeight:{xs:44,md:40}}}>XML</Button>}
    </Stack>
   </Stack>
   {/* PDF BUTONU YOK ve bu bir eksiklik değil: sağlayıcının PDF sözleşmesi
       DOĞRULANMADI (keşif §2.4) ve uç 501 döner. Görünmeyen bir buton,
       her basışta hata veren bir butondan iyidir. */}
  </Stack>

  <Dialog open={dialogOpen} onClose={()=>setDialogOpen(false)} fullWidth maxWidth="sm">
   <DialogTitle>e-İrsaliye Bilgileri</DialogTitle>
   <DialogContent>
    <Stack spacing={2} sx={{mt:1}}>
     <TextField label="Fiili Sevk Zamanı" type="datetime-local" required
       value={form.actual_shipment_at} onChange={alan('actual_shipment_at')}
       InputLabelProps={{shrink:true}}
       helperText="Malın fiilen yola çıktığı an; fatura tarihinden ayrıdır."/>
     <TextField label="Şoför Adı Soyadı" required
       value={form.driver_name} onChange={alan('driver_name')}/>
     <TextField label="Şoför T.C. Kimlik No" required
       value={form.driver_national_id} onChange={alan('driver_national_id')}
       inputProps={{maxLength:11}}
       helperText="11 hane"/>
     <TextField label="Araç Plakası" required
       value={form.vehicle_plate} onChange={alan('vehicle_plate')}
       inputProps={{maxLength:20}}/>
     <TextField label="Dorse Plakası"
       value={form.trailer_plate} onChange={alan('trailer_plate')}
       inputProps={{maxLength:20}} helperText="İsteğe bağlı"/>
     <TextField label="Teslim Adresi" required multiline minRows={2}
       value={form.delivery_address} onChange={alan('delivery_address')}/>
     {/* POSTA KODU ZORUNLU ve bu bir form kaprisi DEĞİL: GİB şematronu
         DeliveryAddress/PostalZone istiyor ve boş gönderilen belge
         REDDEDİLİYOR (sandbox'ta ölçüldü). Alan burada zorunlu olmasaydı
         kullanıcı hatayı ancak GÖNDERİMDE — geri alınamaz bir denemeden
         sonra — görürdü. */}
     <TextField label="Teslim Posta Kodu" required
       value={form.delivery_postal_code} onChange={alan('delivery_postal_code')}
       inputProps={{maxLength:10}} helperText="GİB zorunlu tutuyor (örn. 34710)"/>
    </Stack>
   </DialogContent>
   <DialogActions>
    <Button onClick={()=>setDialogOpen(false)} disabled={busy}>Vazgeç</Button>
    <Button variant="contained" onClick={create} disabled={!zorunlular||busy}>
     {busy?'Kaydediliyor…':'Oluştur'}
    </Button>
   </DialogActions>
  </Dialog>
 </Paper>;
}

export default DespatchNotePanel;
