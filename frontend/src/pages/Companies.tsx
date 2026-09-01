import React from 'react';
import {useEffect,useState} from 'react';
import {Alert,Box,Button,Card,CardContent,Dialog,DialogActions,DialogContent,DialogTitle,Divider,MenuItem,Stack,TextField,Typography} from '@mui/material';
import AddBusinessIcon from '@mui/icons-material/AddBusiness';
import StoreIcon from '@mui/icons-material/Store';
import AgricultureIcon from '@mui/icons-material/Agriculture';
import PolicyIcon from '@mui/icons-material/Policy';
import {api} from '../api';
import {useAuth} from '../AuthContext';

type Branch={id:number;name:string};
type PolicyMode='block'|'manager_override'|'allow';
type OverrideLog={id:number;policy_name:string;username:string;reason:string;resource_type:string;resource_id?:number;created_at:string};
const policyLabels:Record<PolicyMode,string>={block:'Kesin engelle',manager_override:'Yönetici onayıyla izin ver',allow:'İzin ver'};

export default function Companies(){
 const {companies,activeCompany,can,user}=useAuth();
 const [branches,setBranches]=useState<Branch[]>([]);const [open,setOpen]=useState(false);const [name,setName]=useState('');const [tax,setTax]=useState('');const [settingsTax,setSettingsTax]=useState('');const [error,setError]=useState('');const [saved,setSaved]=useState('');
 // Tarla kuralları (mobil-erp#2, FAZ 9). Varsayılanlar SIKI: sunucu
 // okuyamadığında da sıkı tarafa düşüyor, arayüz de öyle başlıyor.
 const [areaPolicy,setAreaPolicy]=useState<'allow'|'require_reason'|'block'>('require_reason');
 const [harvestPolicy,setHarvestPolicy]=useState<'warn'|'require_reason'|'block'>('require_reason');
 const [monoPolicy,setMonoPolicy]=useState<'warn'|'require_reason'|'block'>('require_reason');
 const [reentryPolicy,setReentryPolicy]=useState<'warn'|'require_reason'|'block'>('require_reason');
 const [doseRequired,setDoseRequired]=useState(true);
 const [negativePolicy,setNegativePolicy]=useState<PolicyMode>('block');const [creditPolicy,setCreditPolicy]=useState<PolicyMode>('block');const [logs,setLogs]=useState<OverrideLog[]>([]);const [savingPolicies,setSavingPolicies]=useState(false);
 const canApprove=user?.role==='admin'||user?.role==='yonetici';
 const load=async()=>{try{setError('');const [b,s]=await Promise.all([api.get('/branches'),api.get('/company-settings')]);setBranches(b.data);setSettingsTax(s.data.tax_number||'');setNegativePolicy(s.data.negative_stock_policy||'block');setCreditPolicy(s.data.credit_limit_policy||'block');setAreaPolicy(s.data.farm_area_override_policy||'require_reason');setHarvestPolicy(s.data.farm_early_harvest_policy||'require_reason');setMonoPolicy(s.data.farm_monoculture_policy||'require_reason');setReentryPolicy(s.data.farm_reentry_policy||'require_reason');setDoseRequired(s.data.farm_spraying_dose_required!==false);if(canApprove){const l=await api.get('/policy-overrides',{params:{limit:20}});setLogs(l.data)}}catch(e:any){setError(e.response?.data?.detail||'Firma ayarları yüklenemedi')}};
 useEffect(()=>{void load()},[activeCompany?.id]);
 const save=async()=>{try{setError('');await api.post('/companies',{name,tax_number:tax||null});setOpen(false);window.location.reload()}catch(e:any){setError(e?.response?.data?.detail||'Firma kaydedilemedi')}};
 const savePolicies=async()=>{try{setSavingPolicies(true);setError('');setSaved('');const {data}=await api.put('/company-settings',{negative_stock_policy:negativePolicy,credit_limit_policy:creditPolicy,tax_number:settingsTax||null,farm_area_override_policy:areaPolicy,farm_early_harvest_policy:harvestPolicy,farm_monoculture_policy:monoPolicy,farm_reentry_policy:reentryPolicy,farm_spraying_dose_required:doseRequired});setSaved(data.warning||'Firma ayarları kaydedildi.')}catch(e:any){setError(e.response?.data?.detail||'Firma ayarları kaydedilemedi')}finally{setSavingPolicies(false)}};
 return <Stack spacing={2.5}>
  <Box display="flex" justifyContent="space-between" alignItems="center"><Box><Typography variant="h4" fontWeight={900}>Firma ve Şubeler</Typography><Typography color="text.secondary">Aktif firma: {activeCompany?.name}</Typography></Box>{can('*')&&<Button variant="contained" startIcon={<AddBusinessIcon/>} onClick={()=>setOpen(true)}>Yeni Firma</Button>}</Box>
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}{saved&&<Alert severity="success" onClose={()=>setSaved('')}>{saved}</Alert>}
  <Stack direction={{xs:'column',md:'row'}} spacing={2}>{companies.map(c=><Card key={c.id} variant={c.id===activeCompany?.id?'outlined':undefined} sx={{minWidth:260,borderColor:c.id===activeCompany?.id?'secondary.main':undefined}}><CardContent><Typography variant="overline" color="text.secondary">{c.id===activeCompany?.id?'AKTİF FİRMA':'FİRMA'}</Typography><Typography variant="h6" fontWeight={800}>{c.name}</Typography><Typography color="text.secondary">Vergi No: {c.tax_number||'—'}</Typography></CardContent></Card>)}</Stack>
  {!settingsTax&&<Alert severity="warning">Fatura kesebilmek için vergi numaranızı girin.</Alert>}
  <Card><CardContent><Stack spacing={2}><Typography variant="h6" fontWeight={800}>Fatura Bilgileri</Typography><TextField label="VKN / TCKN" value={settingsTax} onChange={e=>setSettingsTax(e.target.value)} helperText="10 haneli VKN veya 11 haneli TCKN. Algoritma uyarısı kaydetmeyi engellemez." disabled={!can('*')}/></Stack></CardContent></Card>
  <Card><CardContent><Stack direction="row" spacing={1} alignItems="center" mb={2}><PolicyIcon color="secondary"/><Box><Typography variant="h6" fontWeight={800}>Operasyon Politikaları</Typography><Typography variant="body2" color="text.secondary">Stok ve müşteri risk sınırlarının belge kaydı sırasında nasıl uygulanacağını belirler.</Typography></Box></Stack><Stack direction={{xs:'column',md:'row'}} spacing={2}><TextField select fullWidth label="Negatif stok" value={negativePolicy} onChange={e=>setNegativePolicy(e.target.value as PolicyMode)} disabled={!can('*')}>{Object.entries(policyLabels).map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField><TextField select fullWidth label="Müşteri risk limiti" value={creditPolicy} onChange={e=>setCreditPolicy(e.target.value as PolicyMode)} disabled={!can('*')}>{Object.entries(policyLabels).map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField></Stack><Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} mt={2} gap={1}><Typography variant="caption" color="text.secondary">“Yönetici onayı” modunda admin veya yönetici rolü en az 5 karakterlik gerekçe girmelidir; istisna kaydı denetim listesine yazılır.</Typography>{can('*')&&<Button variant="contained" onClick={savePolicies} disabled={savingPolicies}>{savingPolicies?'Kaydediliyor...':'Politikaları Kaydet'}</Button>}</Stack></CardContent></Card>
  <Card><CardContent><Stack direction="row" spacing={1} alignItems="center" mb={2}><AgricultureIcon color="secondary"/><Box><Typography variant="h6" fontWeight={800}>Tarla Kuralları</Typography><Typography variant="body2" color="text.secondary">Tarla kayıtlarında hangi durumların engelleneceğini, hangilerinin gerekçe isteyeceğini belirler.</Typography></Box></Stack>
   <Stack direction={{xs:'column',md:'row'}} spacing={2}>
    <TextField select fullWidth label="Faaliyet alanı parseli aşarsa" value={areaPolicy} onChange={e=>setAreaPolicy(e.target.value as any)} disabled={!can('*')} helperText="Aşmak her zaman hata değildir; ikinci geçiş olabilir.">
     <MenuItem value="allow">Serbest — sessizce kaydedilsin</MenuItem>
     <MenuItem value="require_reason">Gerekçe iste (önerilen)</MenuItem>
     <MenuItem value="block">Reddet</MenuItem>
    </TextField>
    <TextField select fullWidth label="İlaç bekleme süresi dolmadan hasat" value={harvestPolicy} onChange={e=>setHarvestPolicy(e.target.value as any)} disabled={!can('*')} helperText="En gevşek seviye bile kaydı tutar; kontrol tamamen kapatılamaz.">
     <MenuItem value="warn">Uyar, engelleme — durum kayda yazılır</MenuItem>
     <MenuItem value="require_reason">Gerekçe iste (önerilen)</MenuItem>
     <MenuItem value="block">Reddet</MenuItem>
    </TextField>
    <TextField select fullWidth label="İlaçlamada doz" value={doseRequired?'1':'0'} onChange={e=>setDoseRequired(e.target.value==='1')} disabled={!can('*')} helperText="Kapatılırsa dekar başına ilaç kullanımı hesaplanamaz.">
     <MenuItem value="1">Zorunlu (önerilen)</MenuItem>
     <MenuItem value="0">Opsiyonel</MenuItem>
    </TextField>
   </Stack>
   <Stack direction={{xs:'column',md:'row'}} spacing={2} mt={2}>
    <TextField select fullWidth label="Aynı parsele üçüncü yıl aynı ürün" value={monoPolicy} onChange={e=>setMonoPolicy(e.target.value as any)} disabled={!can('*')} helperText="ÇKS tek ürün. En gevşek seviye bile kaydı tutar; kontrol kapatılamaz.">
     <MenuItem value="warn">Uyar, engelleme — durum kayda yazılır</MenuItem>
     <MenuItem value="require_reason">Gerekçe iste (önerilen)</MenuItem>
     <MenuItem value="block">Reddet</MenuItem>
    </TextField>
    <TextField select fullWidth label="Tarlaya giriş yasağı dolmadan faaliyet" value={reentryPolicy} onChange={e=>setReentryPolicy(e.target.value as any)} disabled={!can('*')} helperText="İlaçlama sonrası giriş yasağı. PHI kilidinin hasat tarafındaki ikizi.">
     <MenuItem value="warn">Uyar, engelleme — durum kayda yazılır</MenuItem>
     <MenuItem value="require_reason">Gerekçe iste (önerilen)</MenuItem>
     <MenuItem value="block">Reddet</MenuItem>
    </TextField>
   </Stack>
   {/* Bu metin bir ürün kararının açıklaması: erken hasat kontrolünde
       "hiç bakma" seçeneği BİLEREK yok. Kalıntı riski taşıyan bir kontrolü
       tamamen kapatabilen ayar, bir kez kapatılıp unutulur. */}
   <Alert severity="info" sx={{mt:2}}>Erken hasat, ÇKS tek ürün ve tarlaya giriş yasağı kontrolleri tamamen kapatılamaz. “Uyar, engelleme” seçildiğinde kayıt oluşur ama sistemin bulduğu ihlal satıra yazılır; denetimde görünür.</Alert>
   <Stack direction={{xs:'column',sm:'row'}} justifyContent="flex-end" mt={2}>{can('*')&&<Button variant="contained" onClick={savePolicies} disabled={savingPolicies}>{savingPolicies?'Kaydediliyor...':'Tarla Kurallarını Kaydet'}</Button>}</Stack>
  </CardContent></Card>
  <Card><CardContent><Stack direction="row" spacing={1} alignItems="center" mb={2}><StoreIcon color="secondary"/><Typography variant="h6" fontWeight={800}>Şubeler</Typography></Stack><Stack spacing={1}>{branches.map(b=><Box key={b.id} p={1.5} border="1px solid" borderColor="divider" borderRadius={2}>{b.name}</Box>)}</Stack></CardContent></Card>
  {canApprove&&<Card><CardContent><Typography variant="h6" fontWeight={800}>Son Politika İstisnaları</Typography><Typography variant="body2" color="text.secondary" mb={2}>Gerekçeli yönetici onaylarının son 20 kaydı.</Typography><Stack divider={<Divider flexItem/>}>{logs.length===0?<Typography color="text.secondary">Henüz istisna kaydı yok.</Typography>:logs.map(log=><Box key={log.id} py={1}><Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1}><Box><Typography fontWeight={800}>{log.policy_name==='negative_stock'?'Negatif stok':'Risk limiti'} · {log.resource_type}{log.resource_id?` #${log.resource_id}`:''}</Typography><Typography variant="body2">{log.reason}</Typography></Box><Box textAlign={{sm:'right'}}><Typography variant="body2" fontWeight={700}>{log.username}</Typography><Typography variant="caption" color="text.secondary">{new Date(log.created_at).toLocaleString('tr-TR')}</Typography></Box></Stack></Box>)}</Stack></CardContent></Card>}
  <Dialog open={open} onClose={()=>setOpen(false)} fullWidth maxWidth="sm"><DialogTitle>Yeni Firma</DialogTitle><DialogContent><Stack spacing={2} mt={1}>{error&&<Alert severity="error">{error}</Alert>}<TextField label="Firma adı" value={name} onChange={e=>setName(e.target.value)} autoFocus/><TextField label="Vergi numarası" value={tax} onChange={e=>setTax(e.target.value)}/></Stack></DialogContent><DialogActions><Button onClick={()=>setOpen(false)}>Vazgeç</Button><Button variant="contained" onClick={save} disabled={name.trim().length<2}>Kaydet</Button></DialogActions></Dialog>
 </Stack>;
}
