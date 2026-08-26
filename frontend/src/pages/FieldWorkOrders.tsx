import {useCallback,useEffect,useState} from 'react';
import {Alert,Box,Button,Card,CardActionArea,CardContent,Chip,Divider,Stack,TextField,Typography} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import PhotoCameraIcon from '@mui/icons-material/PhotoCamera';
import {useParams,useNavigate} from 'react-router-dom';
import {useAuth} from '../AuthContext';
import {syncFieldSnapshot} from '../field/snapshotSource';
import {subscribeFieldLogout} from '../field/sessionCoordinator';
import {flushFieldOutbox,queueFieldLabor,queueFieldPhoto,queueFieldStatus} from '../field/fieldOutbox';
import {fotografAl} from '../field/fieldCamera';
import {dismissFieldConflict,offlineSessionExpiresAt,readFieldAccess,readFieldConflicts,
 readFieldOutbox,type FieldAccess,type FieldConflict,type FieldOutboxEntry,type FieldStatusOutbox} from '../field/store';

const statusLabel:Record<string,string>={SCHEDULED:'Planlandı',OPEN:'Açık',IN_PROGRESS:'Devam ediyor',
 WAITING_PARTS:'Parça bekliyor',WAITING_CUSTOMER:'Müşteri bekliyor',COMPLETED:'Tamamlandı'};

/**
 * Sahadan yapılabilecek geçişler — sunucudaki FIELD_ALLOWED_TRANSITIONS'ın
 * AYNISI olmak zorunda. Burada fazladan bir hedef göstermek, teknisyene
 * basınca reddedilecek bir düğme göstermek demektir. İptal ve teslim bilerek
 * yok: ticari sonuç doğurdukları için panelde kalıyorlar.
 */
const fieldTransitions:Record<string,string[]>={
 SCHEDULED:['OPEN'],
 OPEN:['IN_PROGRESS'],
 IN_PROGRESS:['WAITING_PARTS','WAITING_CUSTOMER','COMPLETED'],
 WAITING_PARTS:['IN_PROGRESS','WAITING_CUSTOMER'],
 WAITING_CUSTOMER:['IN_PROGRESS','WAITING_PARTS'],
 COMPLETED:[],DELIVERED:[],CANCELLED:[],
};

export default function FieldWorkOrders(){
 const {id}=useParams();const navigate=useNavigate();const {user,activeCompany}=useAuth();
 const [access,setAccess]=useState<FieldAccess|null>(null);const [online,setOnline]=useState(navigator.onLine);
 const [error,setError]=useState('');
 const [pending,setPending]=useState<FieldOutboxEntry[]>([]);
 const [conflicts,setConflicts]=useState<FieldConflict[]>([]);
 const [saat,setSaat]=useState('');
 const loadCache=useCallback(async()=>{if(!user||!activeCompany){setAccess(null);setPending([]);setConflicts([]);return}
  setAccess(await readFieldAccess(activeCompany.id,user.id));
  setPending(await readFieldOutbox(activeCompany.id,user.id));
  setConflicts(await readFieldConflicts(activeCompany.id,user.id))},[user,activeCompany]);
 // Kuyruk SNAPSHOT'TAN ÖNCE boşaltılır. Sıra ters olsaydı taze snapshot,
 // henüz gönderilmemiş değişikliğin öncesini gösterir ve teknisyen
 // değişikliğinin kaybolduğunu sanardı.
 const sync=useCallback(async()=>{if(!user||!activeCompany)return;try{
  await flushFieldOutbox(activeCompany.id,user.id);
  await syncFieldSnapshot(activeCompany.id,user.id);
  setOnline(true);setError('');await loadCache();
 }catch{setOnline(false);setError('Sunucuya ulaşılamadı; son doğrulanmış saha görünümü gösteriliyor.');await loadCache()}},[user,activeCompany,loadCache]);
 const changeStatus=useCallback(async(workOrder:FieldAccess['workOrders'][number],status:string)=>{
  if(!user||!activeCompany)return;
  await queueFieldStatus(activeCompany.id,user.id,workOrder,status);
  await loadCache();
  // Çevrimiçiysek hemen göndermeyi dene; değilsek kuyrukta bekler.
  void sync();
 },[user,activeCompany,loadCache,sync]);
 const iscilikEkle=useCallback(async(workOrderId:number)=>{
  if(!user||!activeCompany)return;
  const temiz=saat.trim().replace(',', '.');
  // Sayıya ÇEVİRMEDEN gönderiyoruz: saat, ücretle çarpılıp faturaya giriyor ve
  // float yuvarlaması kuruş kaydırır. Sunucu Decimal olarak alıyor.
  if(!/^\d+(\.\d{1,2})?$/.test(temiz)||Number(temiz)<=0)return;
  await queueFieldLabor(activeCompany.id,user.id,workOrderId,temiz);
  setSaat('');
  await loadCache();
  void sync();
 },[user,activeCompany,saat,loadCache,sync]);
 const cekVeKuyrukla=useCallback(async(workOrderId:number)=>{
  if(!user||!activeCompany)return;
  const foto=await fotografAl();
  if(!foto)return; // iptal edildi
  await queueFieldPhoto(activeCompany.id,user.id,workOrderId,foto.blob,foto.fileName);
  await loadCache();
  void sync();
 },[user,activeCompany,loadCache,sync]);
 useEffect(()=>{void sync();const revalidate=()=>void loadCache();const visible=()=>{if(document.visibilityState==='visible')revalidate()};
  window.addEventListener('online',sync);window.addEventListener('focus',revalidate);window.addEventListener('pageshow',revalidate);
  document.addEventListener('visibilitychange',visible);const periodic=window.setInterval(revalidate,30000);
  return()=>{window.removeEventListener('online',sync);window.removeEventListener('focus',revalidate);window.removeEventListener('pageshow',revalidate);
   document.removeEventListener('visibilitychange',visible);window.clearInterval(periodic)}},[sync,loadCache]);
 useEffect(()=>{if(!access)return;const remaining=offlineSessionExpiresAt(access.meta)-Date.now();
  if(remaining<=0){void loadCache();return}const timer=window.setTimeout(()=>void loadCache(),remaining);
  return()=>window.clearTimeout(timer)},[access,loadCache]);
 useEffect(()=>subscribeFieldLogout(()=>setAccess(null)),[]);
 const selected=access?.workOrders.find(item=>String(item.id)===id);
 // Bekleyen değişiklik önbelleğe YAZILMADIĞI için (sunucu gerçeği kirlenmesin)
 // görünen durumu burada birleştiriyoruz. Aynı iş emri için birden çok kayıt
 // varsa en sonuncusu geçerli.
 // Yalnız DURUM kuyruğu görünen durumu değiştirir; bekleyen bir fotoğraf
 // iş emrinin durumunu etkilemez.
 const pendingOf=(workOrderId:number)=>pending.filter(item=>item.workOrderId===workOrderId&&item.kind==='status').at(-1) as FieldStatusOutbox|undefined;
 const pendingAttachments=(workOrderId:number)=>pending.filter(item=>item.workOrderId===workOrderId&&item.kind==='attachment');
 const durumEtiketi=(kod:string)=>statusLabel[kod]||kod;
 const bekleyenUyarisi=(entry:FieldStatusOutbox|undefined)=>entry?<Alert severity="info" sx={{mt:1}}>
  «{durumEtiketi(entry.status)}» değişikliği gönderilmeyi bekliyor{entry.lastError?` · ${entry.lastError}`:''}. Şebeke gelince otomatik gönderilecek.</Alert>:null;
 if(!access)return <Box className="field-page"><Alert severity="warning">Saha oturumunun 12 saatlik doğrulama süresi doldu. Verileri yeniden açmak için çevrimiçi giriş yapın.</Alert></Box>;
 if(id&&selected){const bekleyen=pendingOf(selected.id);
  const gorunenDurum=bekleyen?.status??selected.status;
  const hedefler=fieldTransitions[selected.status]??[];
  return <Box className="field-page"><Button className="field-touch" onClick={()=>navigate('/saha/')}>← İşlere dön</Button>
  <Stack spacing={2} mt={2}><Alert severity={online?'success':'warning'}>{online?'Çevrimiçi':'Çevrimdışı · değişiklikler kuyruğa alınır'}</Alert>
  {conflicts.filter(item=>item.workOrderId===selected.id).map(item=><Alert key={item.operationId} severity="warning"
   action={<Button size="small" onClick={()=>{void dismissFieldConflict(item.operationId).then(loadCache)}}>Tamam</Button>}>
   «{durumEtiketi(item.attemptedStatus)}» değişikliğiniz uygulanamadı. Sunucudaki durum: {durumEtiketi(item.serverStatus)}.</Alert>)}
  <Typography variant="h5">{selected.work_order_no}</Typography>
  <Chip label={durumEtiketi(gorunenDurum)} color={bekleyen?'info':'default'} sx={{alignSelf:'flex-start'}}/>
  {bekleyenUyarisi(bekleyen)}
  {!bekleyen&&hedefler.length>0&&<Card><CardContent><Typography variant="overline">Durumu güncelle</Typography>
   <Stack direction="row" flexWrap="wrap" gap={1} mt={1}>{hedefler.map(hedef=>
    <Button key={hedef} className="field-touch" variant="outlined"
     onClick={()=>{void changeStatus(selected,hedef)}}>{durumEtiketi(hedef)}</Button>)}</Stack></CardContent></Card>}
  <Card><CardContent><Typography variant="overline">Müşteri</Typography><Typography>{selected.customer.display_name}</Typography>
  {selected.customer.service_address&&<Typography color="text.secondary">{selected.customer.service_address}</Typography>}<Divider sx={{my:2}}/>
  <Typography variant="overline">Makine</Typography><Typography>{[selected.machine.brand,selected.machine.model].filter(Boolean).join(' ')||'—'}</Typography>
  <Typography color="text.secondary">Seri: {selected.machine.serial_number||'—'} · Şasi: {selected.machine.chassis_number||'—'}</Typography></CardContent></Card>
  <Card><CardContent><Typography variant="overline">Kullanılan parçalar</Typography>
   {selected.parts.length===0
    ? <Typography color="text.secondary">Bu iş emrine henüz parça girilmemiş.</Typography>
    : <Stack spacing={1} mt={1}>{selected.parts.map(part=>
       <Stack key={part.id} direction="row" justifyContent="space-between" gap={2}>
        <Typography sx={{overflowWrap:'anywhere'}}>{part.product_name}</Typography>
        <Typography color="text.secondary" whiteSpace="nowrap">
         {part.quantity}{part.unit?` ${part.unit}`:''}
         {part.line_status==='RESERVED'?' · ayrıldı':part.line_status==='RETURNED'?' · iade':''}
        </Typography></Stack>)}</Stack>}
  </CardContent></Card>
  <Card><CardContent><Typography variant="overline">İşçilik</Typography>
   <Typography variant="caption" color="text.secondary" display="block" mb={1}>
    Saat ücreti iş emrinden alınır; kayıt ofis onayına düşer.</Typography>
   {(()=>{const bekleyenler=pending.filter(x=>x.workOrderId===selected.id&&x.kind==='labor');
    return bekleyenler.length>0&&<Alert severity="info" sx={{mb:1}}>
     {bekleyenler.length} işçilik kaydı gönderilmeyi bekliyor.</Alert>})()}
   <Stack direction="row" gap={1} alignItems="center">
    <TextField size="small" label="Süre (saat)" value={saat} inputMode="decimal"
     onChange={e=>setSaat(e.target.value)} sx={{maxWidth:160}}/>
    <Button className="field-touch" variant="outlined"
     onClick={()=>{void iscilikEkle(selected.id)}}>Ekle</Button></Stack>
  </CardContent></Card>
  <Card><CardContent>
   <Stack direction="row" justifyContent="space-between" alignItems="center" gap={1}>
    <Typography variant="overline">Fotoğraflar</Typography>
    <Button className="field-touch" size="small" variant="outlined" startIcon={<PhotoCameraIcon/>}
     onClick={()=>{void cekVeKuyrukla(selected.id)}}>Fotoğraf ekle</Button></Stack>
   {(()=>{const bekleyenler=pendingAttachments(selected.id);
    if(!selected.attachments.length&&!bekleyenler.length)
     return <Typography color="text.secondary">Henüz fotoğraf yok.</Typography>;
    return <Stack spacing={1} mt={1}>
     {selected.attachments.map(ek=><Stack key={ek.id} direction="row" justifyContent="space-between" gap={2}>
      <Typography sx={{overflowWrap:'anywhere'}}>{ek.file_name}</Typography>
      <Typography color="text.secondary" whiteSpace="nowrap">
       {ek.kind==='signature'?'imza':'fotoğraf'} · {new Date(ek.created_at).toLocaleDateString('tr-TR')}</Typography></Stack>)}
     {bekleyenler.map(ek=><Stack key={ek.operationId} direction="row" justifyContent="space-between" gap={2}>
      <Typography color="info.main" sx={{overflowWrap:'anywhere'}}>
       {ek.kind==='attachment'?ek.fileName:''}</Typography>
      <Typography variant="caption" color="info.main" whiteSpace="nowrap">gönderilmeyi bekliyor</Typography></Stack>)}
    </Stack>})()}
  </CardContent></Card>
  {[['Şikayet',selected.complaint],['Teşhis',selected.diagnosis],['Yapılan işlem',selected.repair_summary],['Teknisyen notu',selected.technician_notes]].filter(([,v])=>v).map(([label,value])=><Card key={label}><CardContent><Typography variant="overline">{label}</Typography><Typography sx={{overflowWrap:'anywhere'}}>{value}</Typography></CardContent></Card>)}</Stack></Box>}
 if(id)return <Box className="field-page"><Alert severity="warning">İş emri bu kullanıcıya atanmış açık saha kayıtları arasında bulunamadı.</Alert><Button className="field-touch" onClick={()=>navigate('/saha/')}>İşlere dön</Button></Box>;
 return <Box className="field-page"><Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1}><Box><Typography variant="h4">Saha İşlerim</Typography><Typography color="text.secondary">{access.meta.lastSyncAt?`Son sunucu güncellemesi: ${new Date(access.meta.lastSyncAt).toLocaleString('tr-TR')}`:'Henüz snapshot yok'}</Typography></Box>
 <Button className="field-touch" startIcon={<RefreshIcon/>} onClick={()=>void sync()}>Şimdi yenile</Button></Stack>
 <Alert severity={online?'success':'warning'} sx={{my:2}}>{online?'Çevrimiçi':'Çevrimdışı · değişiklikler kuyruğa alınır'}</Alert>{error&&<Alert severity="info" sx={{mb:2}}>{error}</Alert>}
 {pending.length>0&&<Alert severity="info" sx={{mb:2}}>{pending.length} değişiklik gönderilmeyi bekliyor.</Alert>}
 {conflicts.length>0&&<Alert severity="warning" sx={{mb:2}}>{conflicts.length} değişiklik uygulanamadı; ilgili iş emrini açıp bakın.</Alert>}
 <Stack spacing={1.5}>{access.workOrders.map(item=>{const bekleyen=pendingOf(item.id);
  return <Card key={item.id}><CardActionArea className="field-touch" onClick={()=>navigate(`/saha/${item.id}`)}><CardContent><Stack direction="row" justifyContent="space-between" gap={1}><Typography fontWeight={700}>{item.work_order_no}</Typography><Chip size="small" color={bekleyen?'info':'default'} label={durumEtiketi(bekleyen?.status??item.status)}/></Stack><Typography>{item.customer.display_name}</Typography><Typography color="text.secondary">{[item.machine.brand,item.machine.model,item.machine.serial_number].filter(Boolean).join(' · ')}</Typography>{bekleyen&&<Typography variant="caption" color="info.main">Gönderilmeyi bekliyor</Typography>}</CardContent></CardActionArea></Card>})}
 {!access.workOrders.length&&<Alert severity="info">Atanmış açık saha işi bulunmuyor.</Alert>}</Stack></Box>;
}
