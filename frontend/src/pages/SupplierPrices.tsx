import {useCallback,useEffect,useState} from 'react';
import {
 Alert,Box,Button,Chip,CircularProgress,Dialog,DialogActions,DialogContent,
 DialogContentText,DialogTitle,MenuItem,Paper,Stack,Tab,Tabs,Table,TableBody,TableCell,
 TableContainer,TableHead,TableRow,TextField,Typography,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';
import SupplierProfileManager,{type SupplierProfileSummary} from '../components/SupplierProfileManager';

type ImportSummary={
 id:number;status:string;source_filename?:string|null;parsed_row_count:number;created_at:string;
};
type Line={
 line_no:number;supplier_code?:string|null;description?:string|null;state:string;
 old_price_cash?:string|null;old_price_term?:string|null;price_cash?:string|null;
 price_term?:string|null;currency?:string|null;deviation_pct?:string|null;
 match_source?:string|null;override_at?:string|null;override_reason?:string|null;
};
type Report={
 id:number;status:string;report_digest:string;expected_revision:string;
 parsed_row_count:number;created_at:string;source_filename?:string|null;lines:Line[];
};
type ConfirmAction={kind:'apply'|'revert';title:string;message:string}|null;
const states=['OK','WARNING','BLOCKED','UNMATCHED','REVIEW','SKIPPED'] as const;
const labels={OK:'Uygulanacak',WARNING:'Uyarı %25+',BLOCKED:'Bloklu %100+',UNMATCHED:'Eşleşmeyen',REVIEW:'İncelenecek',SKIPPED:'Atlanan'};
const dateTime=(value:string)=>new Intl.DateTimeFormat('tr-TR',{dateStyle:'short',timeStyle:'short'}).format(new Date(value));
const decimalText=(value?:string|null)=>value??'—';

export default function SupplierPrices(){
 const {can}=useAuth();
 const [imports,setImports]=useState<ImportSummary[]>([]);
 const [profiles,setProfiles]=useState<SupplierProfileSummary[]>([]);
 const [importId,setImportId]=useState('');
 const [report,setReport]=useState<Report|null>(null);
 const [sectionTab,setSectionTab]=useState(0);
 const [reportTab,setReportTab]=useState(0);
 const [loadingList,setLoadingList]=useState(true);
 const [loadingProfiles,setLoadingProfiles]=useState(true);
 const [loadingReport,setLoadingReport]=useState(false);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const [success,setSuccess]=useState('');
 const [profileId,setProfileId]=useState('');
 const [file,setFile]=useState<File|null>(null);
 const [confirmAction,setConfirmAction]=useState<ConfirmAction>(null);
 const [overrideLine,setOverrideLine]=useState<Line|null>(null);
 const [overrideReason,setOverrideReason]=useState('');

 const loadImports=useCallback(async()=>{
  setLoadingList(true);setError('');
  try{setImports((await api.get<ImportSummary[]>('/supplier-prices/imports')).data);}
  catch(e){setError(errorDetail(e,'Import listesi yüklenemedi.'))}
  finally{setLoadingList(false)}
 },[]);
 useEffect(()=>{void loadImports()},[loadImports]);
 const loadProfiles=useCallback(async()=>{
  setLoadingProfiles(true);setError('');
  try{
   const items=(await api.get<SupplierProfileSummary[]>('/supplier-prices/profiles')).data;
   setProfiles(items);
   setProfileId(current=>items.some(item=>String(item.id)===current)?current:(items[0]?String(items[0].id):''));
  }catch(e){setError(errorDetail(e,'Eşleme profilleri yüklenemedi.'))}
  finally{setLoadingProfiles(false)}
 },[]);
 useEffect(()=>{void loadProfiles()},[loadProfiles]);

 const loadReport=useCallback(async(id:string|number)=>{
  const normalized=String(id).trim();
  if(!normalized)return;
  setLoadingReport(true);setError('');setSuccess('');
  try{
   const data=(await api.get<Report>(`/supplier-prices/imports/${normalized}`)).data;
   setReport(data);setImportId(String(data.id));setReportTab(0);
  }catch(e){setError(errorDetail(e,'Import raporu yüklenemedi.'))}
  finally{setLoadingReport(false)}
 },[]);

 const upload=async()=>{
  if(!profileId||!file)return;
  const form=new FormData();form.append('profile_id',profileId);form.append('file',file);
  setBusy(true);setError('');setSuccess('');
  try{
   const {data}=await api.post<{id:number}>('/supplier-prices/imports',form);
   setFile(null);
   await loadImports();await loadReport(data.id);
   setSuccess(`Import #${data.id} oluşturuldu.`);
  }catch(e){setError(errorDetail(e,'Fiyat dosyası yüklenemedi.'))}
  finally{setBusy(false)}
 };

 const runConfirmed=async()=>{
  if(!report||!confirmAction)return;
  const action=confirmAction.kind;setBusy(true);setError('');setSuccess('');
  try{
   if(action==='apply'){
    await api.post(`/supplier-prices/imports/${report.id}/apply`,{
     expected_revision:report.expected_revision,report_digest:report.report_digest,
    });
   }else await api.post(`/supplier-prices/imports/${report.id}/revert`);
   setConfirmAction(null);
   await loadImports();await loadReport(report.id);
   setSuccess(action==='apply'?'Import uygulandı.':'Import geri alındı.');
  }catch(e){setError(errorDetail(e,action==='apply'?'Import uygulanamadı.':'Import geri alınamadı.'))}
  finally{setBusy(false)}
 };

 const override=async()=>{
  if(!report||!overrideLine)return;
  setBusy(true);setError('');setSuccess('');
  try{
   await api.post(`/supplier-prices/imports/${report.id}/lines/${overrideLine.line_no}/override`,{reason:overrideReason.trim()});
   setOverrideLine(null);setOverrideReason('');
   await loadReport(report.id);
   setSuccess(`${overrideLine.line_no}. satır kabul edildi.`);
  }catch(e){setError(errorDetail(e,'Bloklu satır kabul edilemedi.'))}
  finally{setBusy(false)}
 };

 const visible=report?.lines.filter(line=>line.state===states[reportTab])??[];
 return <Stack spacing={2}>
  <Box>
   <Typography variant="h4">Tedarikçi Fiyatları</Typography>
   <Typography color="text.secondary">Dry-run raporunu inceleyin; bloklu, eşleşmeyen ve inceleme gereken satırlar fiyat yazmaz.</Typography>
  </Box>
  {error&&<Alert severity="error">{error}</Alert>}
  {success&&<Alert severity="success">{success}</Alert>}
  <Paper variant="outlined"><Tabs value={sectionTab} onChange={(_,value)=>setSectionTab(value)}>
   <Tab label="İçe Aktarımlar"/><Tab label="Eşleme Profilleri"/>
  </Tabs></Paper>

  {sectionTab===0&&<>
  {can('supplier_prices.import')&&<Paper sx={{p:2}}>
   <Stack spacing={1.5}>
    <Box><Typography variant="h6">Yeni import oluştur</Typography><Typography variant="body2" color="text.secondary">Excel veya PDF fiyat listeniz için bir eşleme profili seçin.</Typography></Box>
    {loadingProfiles?<Typography>Profiller yükleniyor…</Typography>:profiles.length===0?<Alert severity="warning" action={<Button color="inherit" onClick={()=>setSectionTab(1)}>Profilleri aç</Button>}>Önce eşleme profili oluşturun.</Alert>:<Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField select label="Eşleme profili" value={profileId} onChange={event=>setProfileId(event.target.value)} disabled={busy} sx={{minWidth:240}}>
      {profiles.map(profile=><MenuItem key={profile.id} value={profile.id}>{profile.name} · {profile.supplier_name} ({profile.source_type.toUpperCase()})</MenuItem>)}
     </TextField>
     <Button component="label" variant="outlined" startIcon={<UploadFileIcon/>} disabled={busy||!profileId}>
      {file?.name||'Dosya Seç'}<input hidden type="file" accept=".xlsx,.xlsm,.pdf" onChange={event=>setFile(event.target.files?.[0]||null)}/>
     </Button>
     <Button variant="contained" onClick={()=>void upload()} disabled={busy||!profileId||!file}>{busy?'Yükleniyor…':'Dry-run Oluştur'}</Button>
    </Stack>}
   </Stack>
  </Paper>}

  <Paper sx={{p:2}}>
   <Stack spacing={1.5}>
    <Typography variant="h6">Son importlar</Typography>
    {loadingList?<Stack direction="row" spacing={1} alignItems="center"><CircularProgress size={20}/><Typography>Importlar yükleniyor…</Typography></Stack>:
     imports.length===0?<Alert severity="info">Henüz tedarikçi fiyat importu yok. Yetkiniz varsa yukarıdan bir eşleme profili ve fiyat dosyası seçerek ilk dry-run raporunu oluşturun.</Alert>:
     <TableContainer><Table size="small">
      <TableHead><TableRow><TableCell>Import</TableCell><TableCell>Tarih</TableCell><TableCell>Durum</TableCell><TableCell align="right">Satır</TableCell></TableRow></TableHead>
      <TableBody>{imports.map(item=><TableRow hover key={item.id} onClick={()=>void loadReport(item.id)} sx={{cursor:'pointer'}} aria-label={`Import #${item.id}`}>
       <TableCell><Typography fontWeight={700}>#{item.id}</Typography><Typography variant="caption" color="text.secondary">{item.source_filename||'Dosya adı yok'}</Typography></TableCell>
       <TableCell>{dateTime(item.created_at)}</TableCell><TableCell><Chip size="small" label={item.status}/></TableCell><TableCell align="right">{item.parsed_row_count}</TableCell>
      </TableRow>)}</TableBody>
     </Table></TableContainer>}
    <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
     <TextField size="small" label="Import No (elle)" value={importId} onChange={event=>setImportId(event.target.value.replace(/\D/g,''))} inputProps={{inputMode:'numeric'}}/>
     <Button variant="outlined" onClick={()=>void loadReport(importId)} disabled={!importId||loadingReport}>{loadingReport?'Açılıyor…':'Raporu Aç'}</Button>
    </Stack>
   </Stack>
  </Paper>

  {loadingReport&&<Paper sx={{p:3,textAlign:'center'}}><CircularProgress aria-label="Rapor yükleniyor"/></Paper>}
  {report&&!loadingReport&&<Paper>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" spacing={1} sx={{p:2,pb:0}}>
    <Box><Typography variant="h6">Import #{report.id} · {report.source_filename||'Dosya adı yok'}</Typography><Typography variant="body2" color="text.secondary">{dateTime(report.created_at)} · {report.parsed_row_count} satır</Typography></Box>
    <Stack direction="row" spacing={1} alignItems="center">
     <Chip label={report.status}/>
     {can('supplier_prices.apply')&&report.status==='DRY_RUN_READY'&&<Button variant="contained" color="success" onClick={()=>setConfirmAction({kind:'apply',title:'Importu uygula',message:'Uygulanabilir satırlar tedarikçi fiyatlarına yazılacak. Devam edilsin mi?'})}>Uygula</Button>}
     {can('supplier_prices.apply')&&report.status==='APPLIED'&&<Button variant="outlined" color="warning" onClick={()=>setConfirmAction({kind:'revert',title:'Importu geri al',message:'Bu importun fiyat değişiklikleri geri alınacak. Devam edilsin mi?'})}>Geri al</Button>}
    </Stack>
   </Stack>
   <Tabs value={reportTab} onChange={(_,value)=>setReportTab(value)} variant="scrollable">{states.map(state=><Tab key={state} label={`${labels[state]} (${report.lines.filter(line=>line.state===state).length})`}/>)}</Tabs>
   <Stack spacing={1} sx={{p:2}}>{visible.length===0?<Typography color="text.secondary">Bu bölümde satır yok.</Typography>:visible.map(line=><Paper variant="outlined" sx={{p:1.5}} key={line.line_no}>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" spacing={1}>
     <Box>
      <Typography fontWeight={700}>{line.supplier_code||`Satır ${line.line_no}`} · {line.currency||'—'}</Typography>
      {line.description&&<Typography variant="body2">{line.description}</Typography>}
      <Typography variant="body2">Peşin: {decimalText(line.old_price_cash)} → {decimalText(line.price_cash)} · Vadeli: {decimalText(line.old_price_term)} → {decimalText(line.price_term)} · Sapma: %{decimalText(line.deviation_pct)} · {line.match_source??'eşleşme yok'}</Typography>
      {line.override_at&&<Typography variant="caption" color="success.main">Kabul edildi: {line.override_reason}</Typography>}
     </Box>
     {line.state==='BLOCKED'&&!line.override_at&&can('supplier_prices.override_block')&&<Button size="small" color="warning" onClick={()=>{setOverrideLine(line);setOverrideReason('')}}>Satırı kabul et</Button>}
    </Stack>
   </Paper>)}</Stack>
  </Paper>}
  </>}
  {sectionTab===1&&<SupplierProfileManager profiles={profiles} loading={loadingProfiles} onReload={()=>void loadProfiles()} canManage={can('supplier_prices.import')}/>}

  <Dialog open={Boolean(confirmAction)} onClose={busy?undefined:()=>setConfirmAction(null)}>
   <DialogTitle>{confirmAction?.title}</DialogTitle><DialogContent><DialogContentText>{confirmAction?.message}</DialogContentText></DialogContent>
   <DialogActions><Button onClick={()=>setConfirmAction(null)} disabled={busy}>Vazgeç</Button><Button variant="contained" color={confirmAction?.kind==='revert'?'warning':'success'} onClick={()=>void runConfirmed()} disabled={busy}>Onayla</Button></DialogActions>
  </Dialog>
  <Dialog open={Boolean(overrideLine)} onClose={busy?undefined:()=>setOverrideLine(null)} fullWidth maxWidth="sm">
   <DialogTitle>Bloklu satırı kabul et</DialogTitle><DialogContent><Stack spacing={1.5} sx={{pt:1}}>
    <DialogContentText>{overrideLine?.line_no}. satır eşik üstü fiyat sapmasına rağmen uygulanabilir olacak. Gerekçeniz denetim kaydına yazılır.</DialogContentText>
    <TextField autoFocus required multiline minRows={2} label="Kabul gerekçesi" value={overrideReason} onChange={event=>setOverrideReason(event.target.value)} helperText="En az 3 karakter"/>
   </Stack></DialogContent><DialogActions><Button onClick={()=>setOverrideLine(null)} disabled={busy}>Vazgeç</Button><Button variant="contained" color="warning" onClick={()=>void override()} disabled={busy||overrideReason.trim().length<3}>Onayla ve kabul et</Button></DialogActions>
  </Dialog>
 </Stack>;
}
