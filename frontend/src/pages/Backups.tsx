import {useEffect,useState} from 'react';
import {
  Alert,Box,Button,Dialog,DialogActions,DialogContent,DialogTitle,
  LinearProgress,Paper,Stack,Step,StepLabel,Stepper,TextField,Typography,
} from '@mui/material';
import BackupIcon from '@mui/icons-material/Backup';
import DownloadIcon from '@mui/icons-material/Download';
import RestoreIcon from '@mui/icons-material/Restore';
import {api} from '../api';

type BackupItem={name:string;created_at:string;size_bytes:number;revision:string;sha256:string};
type RestoreResult={revision:string;table_counts:Record<string,number>;pre_restore_backup:BackupItem;rollback_instruction:string};
type ActiveOperation={operation_id:string;operation_kind:string;owner:string;started_at:string};

const steps=['Bütünlük doğrulaması','Yazılı onay','Geri yükleme','Sonuç'];

export default function Backups(){
 const [items,setItems]=useState<BackupItem[]>([]);
 const [activeOperation,setActiveOperation]=useState<ActiveOperation|null>(null);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const [selected,setSelected]=useState<BackupItem|null>(null);
 const [step,setStep]=useState(0);
 const [confirmation,setConfirmation]=useState('');
 const [result,setResult]=useState<RestoreResult|null>(null);
 const load=()=>api.get('/platform/backups').then(r=>{setItems(r.data.items);setActiveOperation(r.data.active_operation||null)});
 useEffect(()=>{load().catch(e=>setError(e.response?.data?.detail||'Yedekler yüklenemedi'))},[]);
 const create=async()=>{setBusy(true);setError('');try{await api.post('/platform/backups');await load()}catch(e:any){setError(e.response?.data?.detail||'Yedek alınamadı')}finally{setBusy(false)}};
 const download=async(item:BackupItem)=>{
  try{
   const response=await api.get(`/platform/backups/${item.name}/download`,{responseType:'blob'});
   const url=URL.createObjectURL(response.data);const anchor=document.createElement('a');
   anchor.href=url;anchor.download=item.name;anchor.click();URL.revokeObjectURL(url);
  }catch(e:any){setError(e.response?.data?.detail||'Yedek indirilemedi')}
 };
 const begin=async(item:BackupItem)=>{
  setSelected(item);setStep(0);setConfirmation('');setResult(null);setError('');
  try{await api.post(`/platform/backups/${item.name}/verify`);setStep(1)}
  catch(e:any){setError(e.response?.data?.detail||'Bütünlük doğrulaması başarısız');setSelected(null)}
 };
 const restore=async()=>{
  if(!selected)return;setStep(2);setBusy(true);
  try{const response=await api.post(`/platform/backups/${selected.name}/restore`,{confirmation});setResult(response.data);setStep(3);await load()}
  catch(e:any){setError(e.response?.data?.detail||'Geri yükleme başarısız');setStep(1)}
  finally{setBusy(false)}
 };
 return <Stack spacing={2.5}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2}>
   <Box><Typography variant="h5" fontWeight={900}>Yedekler</Typography><Typography color="text.secondary">Platform operatörü · tüm şirketlerin verisi</Typography></Box>
   <Button variant="contained" startIcon={<BackupIcon/>} onClick={create} disabled={busy}>Yedek Al</Button>
  </Stack>
  <Alert severity="error"><b>ÇOK KRİTİK:</b> Bu yedekler sistemdeki bütün şirketlerin verisini içerir. Geri yükleme mevcut veritabanını bütünüyle değiştirir ve işlem boyunca tüm yazma trafiğini durdurur.</Alert>
  <Alert severity="warning">Yedek dump dosyaları bilinçli olarak şifresizdir. Aktarım yalnız sunucu TLS koruması ve platform operatörünün güvenli saklama sorumluluğuyla yapılmalıdır. Şifreli arşiv ve off-site senkronizasyon ayrı faz kapsamındadır.</Alert>
  {activeOperation&&<Alert severity="info">Süren işlem: <b>{activeOperation.operation_kind}</b> · {activeOperation.owner} · {new Date(activeOperation.started_at).toLocaleString('tr-TR')} · ID {activeOperation.operation_id}</Alert>}
  {busy&&<LinearProgress/>}{error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  <Stack spacing={1.5}>{items.length===0?<Paper sx={{p:4,textAlign:'center'}}>Henüz yedek yok.</Paper>:items.map(item=>
   <Paper key={item.name} sx={{p:2}}><Stack direction={{xs:'column',md:'row'}} alignItems={{md:'center'}} gap={1.5}>
    <Box flex={1}><Typography fontWeight={800}>{item.name}</Typography><Typography variant="body2" color="text.secondary">{new Date(item.created_at).toLocaleString('tr-TR')} · {(item.size_bytes/1024/1024).toFixed(2)} MB · revision {item.revision}</Typography><Typography variant="caption" sx={{wordBreak:'break-all'}}>SHA-256: {item.sha256}</Typography></Box>
    <Button startIcon={<DownloadIcon/>} onClick={()=>download(item)}>İndir</Button>
    <Button color="error" variant="outlined" startIcon={<RestoreIcon/>} onClick={()=>begin(item)}>Geri Yükle</Button>
   </Stack></Paper>)}</Stack>
  <Dialog open={Boolean(selected)} onClose={busy?undefined:()=>setSelected(null)} fullWidth maxWidth="md">
   <DialogTitle>VERİTABANI GERİ YÜKLEME</DialogTitle><DialogContent>
    <Stepper activeStep={step} sx={{py:2}}>{steps.map(label=><Step key={label}><StepLabel>{label}</StepLabel></Step>)}</Stepper>
    {step===1&&<Stack spacing={2}><Alert severity="error">Seçilen yedek doğrulandı. Devam ederseniz sistem önce atlanamaz bir ön-yedek alacak, yazmaları 503 ile durduracak ve veritabanının tamamını değiştirecek.</Alert><Typography><b>{selected?.name}</b> dosyasını geri yüklemek için aşağıya aynen <b>GERI YUKLE</b> yazın.</Typography><TextField autoFocus label="Onay metni" value={confirmation} onChange={e=>setConfirmation(e.target.value)} /></Stack>}
    {step===2&&<Stack spacing={2} alignItems="center" sx={{py:5}}><LinearProgress sx={{width:'100%'}}/><Typography>Ön-yedek alınıyor, veritabanı geri yükleniyor ve sonuç doğrulanıyor. Bu pencereyi kapatmayın.</Typography></Stack>}
    {step===3&&result&&<Stack spacing={2}><Alert severity="success">Geri yükleme tamamlandı ve Alembic revision / temel tablo sayımları doğrulandı.</Alert><Typography>Revision: <b>{result.revision}</b></Typography><Typography>Ön-yedek: <b>{result.pre_restore_backup.name}</b></Typography><pre>{JSON.stringify(result.table_counts,null,2)}</pre><Alert severity="warning">{result.rollback_instruction}</Alert></Stack>}
   </DialogContent><DialogActions><Button onClick={()=>setSelected(null)} disabled={busy}>{step===3?'Kapat':'Vazgeç'}</Button>{step===1&&<Button color="error" variant="contained" disabled={confirmation!=='GERI YUKLE'} onClick={restore}>Kalıcı Olarak Geri Yükle</Button>}</DialogActions>
  </Dialog>
 </Stack>;
}
