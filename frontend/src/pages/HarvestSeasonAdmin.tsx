import {useCallback,useEffect,useMemo,useState} from 'react';
import {
 Alert,Box,Button,Card,CardContent,Chip,CircularProgress,Dialog,DialogActions,
 DialogContent,DialogContentText,DialogTitle,MenuItem,Paper,Stack,Tab,Tabs,Table,
 TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Tooltip,Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';
import type {components} from '../api/types.gen';

type Region=components['schemas']['HarvestRegionView'];
type Calendar=components['schemas']['HarvestCalendarView'];
type Rule=components['schemas']['HarvestDueRuleView'];
type Preview=components['schemas']['HarvestDuePreviewView'];
type Conflict=components['schemas']['HarvestRuleConflictView'];

type CustomerRow={id:number;name:string};
type ProductRow={id:number;name:string;category?:string|null};
type Section='regions'|'calendars'|'rules';
type EditorState={section:Section;row:Region|Calendar|Rule|null};
type DeleteState={section:Section;id:number;label:string};

const TODAY=new Date().toISOString().slice(0,10);

// Mirrors the resolver's specificity tiers (app/harvest_scheduling.py::_rule_scope).
// The numbers arrive from the server on every rule; this map only labels them.
const SCOPE_LABEL:Record<number,string>={
 50:'Müşteriye özel',40:'Bölge + ürün kategorisi',30:'Ürün kategorisi',
 20:'Bölge',10:'Genel (varsayılan)',
};
const SCOPE_ORDER=[50,40,30,20,10];

const SECTION_PATH:Record<Section,string>={
 regions:'/harvest-scheduling/regions',
 calendars:'/harvest-scheduling/calendars',
 rules:'/harvest-scheduling/rules',
};
const SECTION_LABEL:Record<Section,string>={
 regions:'bölge',calendars:'sezon takvimi',rules:'vade kuralı',
};

/** Turns a fail-closed engine response into an operator-readable warning. */
export function previewWarning(status:number|undefined,detail:string):{
 severity:'error'|'warning';title:string;hint:string;
}{
 const text=detail||'Vade çözümlenemedi.';
 if(status===409&&/Aynı öncelikte/i.test(text))return {
  severity:'warning',title:text,
  hint:'İki kural aynı kapsam ve aynı öncelikte eşleşti. Motor tahmin yürütmez; '
   +'kurallardan birinin önceliğini değiştirin veya birini pasifleştirin.',
 };
 if(status===409&&/farklı harman sezonlarına/i.test(text))return {
  severity:'warning',title:text,
  hint:'Satırdaki ürünler farklı sezonlara düşüyor. Satışı tek sezona ayırın '
   +'ya da ürün kategorilerini aynı sezona bağlayan bir kural tanımlayın.',
 };
 if(status===409)return {
  severity:'warning',title:text,
  hint:'Seçilen sezon müşterinin bölgesi veya ürünleriyle uyuşmuyor.',
 };
 if(status===422&&/tanımlı değil/i.test(text))return {
  severity:'error',title:text,
  hint:'Bu müşteri/ürün/tarih üçlüsüne uyan aktif bir kural yok. Kural ekleyin '
   +'veya mevcut kuralın geçerlilik aralığını genişletin.',
 };
 if(status===422&&/aktif takvim yok/i.test(text))return {
  severity:'error',title:text,
  hint:'Kural bir sezona işaret ediyor ama o sezonun satış tarihinden sonraki '
   +'aktif takvimi yok. Takvim ekleyin veya vade tarihini güncelleyin.',
 };
 if(status===404)return {
  severity:'error',title:text,
  hint:'Seçilen müşteri, ürün veya takvim bu firmada bulunamadı.',
 };
 return {severity:'error',title:text,hint:'Vade çözümlenemedi.'};
}

function regionName(regions:Region[],id:number|null|undefined){
 if(id===null||id===undefined)return 'Tüm bölgeler';
 return regions.find(row=>row.id===id)?.name||`Bölge #${id}`;
}

function EditorDialog({
 state,regions,customers,onClose,onSaved,
}:{
 state:EditorState|null;
 regions:Region[];
 customers:CustomerRow[];
 onClose:()=>void;
 onSaved:()=>Promise<void>;
}){
 const [form,setForm]=useState<Record<string,string>>({});
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');

 useEffect(()=>{
  if(!state)return;
  setBusy(false);setError('');
  const row=state.row as Record<string,unknown>|null;
  const text=(key:string,fallback='')=>{
   const value=row?.[key];
   return value===null||value===undefined?fallback:String(value);
  };
  if(state.section==='regions')setForm({
   code:text('code'),name:text('name'),active:row?text('active'):'true',
  });
  else if(state.section==='calendars')setForm({
   season_code:text('season_code'),name:text('name'),
   season_year:text('season_year',String(new Date().getFullYear())),
   region_id:text('region_id'),product_category:text('product_category'),
   due_date:text('due_date'),active:row?text('active'):'true',
  });
  else setForm({
   customer_id:text('customer_id'),region_id:text('region_id'),
   product_category:text('product_category'),season_code:text('season_code'),
   priority:text('priority','0'),effective_from:text('effective_from'),
   effective_to:text('effective_to'),active:row?text('active'):'true',
  });
 },[state]);

 if(!state)return null;
 const editing=Boolean(state.row);
 const set=(key:string)=>(event:{target:{value:string}})=>
  setForm(current=>({...current,[key]:event.target.value}));
 const optionalNumber=(value:string)=>value.trim()===''?null:Number(value);
 const optionalText=(value:string)=>value.trim()===''?null:value.trim();

 const valid=state.section==='regions'
  ?Boolean(form.code?.trim()&&form.name?.trim())
  :state.section==='calendars'
   ?Boolean(form.season_code?.trim()&&form.name?.trim()&&form.due_date&&form.season_year)
   :Boolean(form.season_code?.trim());

 const yearMismatch=state.section==='calendars'&&Boolean(form.due_date)
  &&form.due_date.slice(0,4)!==String(form.season_year);
 const rangeInverted=state.section==='rules'&&Boolean(form.effective_from)
  &&Boolean(form.effective_to)&&form.effective_to<form.effective_from;

 const submit=async()=>{
  if(!valid||yearMismatch||rangeInverted)return;
  setBusy(true);setError('');
  let payload:Record<string,unknown>;
  if(state.section==='regions')payload={
   code:form.code.trim(),name:form.name.trim(),active:form.active!=='false',
  };
  else if(state.section==='calendars')payload={
   season_code:form.season_code.trim(),name:form.name.trim(),
   season_year:Number(form.season_year),region_id:optionalNumber(form.region_id),
   product_category:optionalText(form.product_category),
   due_date:form.due_date,active:form.active!=='false',
  };
  else payload={
   customer_id:optionalNumber(form.customer_id),
   region_id:optionalNumber(form.region_id),
   product_category:optionalText(form.product_category),
   season_code:form.season_code.trim(),priority:Number(form.priority||0),
   effective_from:optionalText(form.effective_from),
   effective_to:optionalText(form.effective_to),
   active:form.active!=='false',
  };
  try{
   const base=SECTION_PATH[state.section];
   if(editing)await api.put(`${base}/${(state.row as {id:number}).id}`,payload);
   else await api.post(base,payload);
   await onSaved();
   onClose();
  }catch(requestError){
   setError(errorDetail(requestError,'Kayıt kaydedilemedi.'));
  }finally{setBusy(false)}
 };

 const title=`${SECTION_LABEL[state.section]} ${editing?'düzenle':'ekle'}`;
 return <Dialog open onClose={busy?undefined:onClose} fullWidth maxWidth="sm">
  <DialogTitle sx={{textTransform:'capitalize'}}>{title}</DialogTitle>
  <DialogContent>
   <Stack spacing={2} mt={1}>
    {error&&<Alert severity="error">{error}</Alert>}
    {state.section==='regions'&&<>
     <TextField required label="Bölge Kodu" value={form.code||''} onChange={set('code')}
      helperText="Firma içinde benzersiz. Büyük harfe çevrilir."/>
     <TextField required label="Bölge Adı" value={form.name||''} onChange={set('name')}/>
    </>}
    {state.section==='calendars'&&<>
     <TextField required label="Sezon Kodu" value={form.season_code||''}
      onChange={set('season_code')}
      helperText="Kurallar bu kodla takvime bağlanır. Örn. PANCAR_SOKUMU"/>
     <TextField required label="Sezon Adı" value={form.name||''} onChange={set('name')}
      helperText="Örn. Pancar Sökümü 2026"/>
     <TextField required label="Sezon Yılı" type="number" value={form.season_year||''}
      onChange={set('season_year')} inputProps={{min:2000,max:2200}}/>
     <TextField required label="Vade Tarihi" type="date" value={form.due_date||''}
      onChange={set('due_date')} slotProps={{inputLabel:{shrink:true}}}
      error={yearMismatch}
      helperText={yearMismatch
       ?'Vade tarihi sezon yılıyla aynı yılda olmalıdır.'
       :'Bu sezona düşen satışların vadesi. Örn. 30 Kasım 2026'}/>
     <TextField select label="Bölge" value={form.region_id||''} onChange={set('region_id')}
      helperText="Boş bırakılırsa takvim tüm bölgeler için geçerlidir.">
      <MenuItem value="">Tüm bölgeler</MenuItem>
      {regions.map(row=><MenuItem key={row.id} value={String(row.id)}>{row.name}</MenuItem>)}
     </TextField>
     <TextField label="Ürün Kategorisi" value={form.product_category||''}
      onChange={set('product_category')}
      helperText="Boş bırakılırsa takvim tüm kategoriler için geçerlidir."/>
    </>}
    {state.section==='rules'&&<>
     <TextField required label="Sezon Kodu" value={form.season_code||''}
      onChange={set('season_code')}
      helperText="Eşleşen satış bu sezonun takvimine yönlendirilir."/>
     <TextField select label="Müşteri" value={form.customer_id||''}
      onChange={set('customer_id')}
      helperText="Seçilirse kural yalnız bu müşteriye uygulanır (en yüksek kapsam).">
      <MenuItem value="">Tüm müşteriler</MenuItem>
      {customers.map(row=><MenuItem key={row.id} value={String(row.id)}>{row.name}</MenuItem>)}
     </TextField>
     <TextField select label="Bölge" value={form.region_id||''} onChange={set('region_id')}>
      <MenuItem value="">Tüm bölgeler</MenuItem>
      {regions.map(row=><MenuItem key={row.id} value={String(row.id)}>{row.name}</MenuItem>)}
     </TextField>
     <TextField label="Ürün Kategorisi" value={form.product_category||''}
      onChange={set('product_category')} helperText="Boş bırakılırsa tüm kategoriler."/>
     <TextField label="Öncelik" type="number" value={form.priority||'0'}
      onChange={set('priority')}
      helperText="Aynı kapsamda birden çok kural eşleşirse yüksek öncelik kazanır."/>
     <TextField label="Geçerlilik Başlangıcı" type="date" value={form.effective_from||''}
      onChange={set('effective_from')} slotProps={{inputLabel:{shrink:true}}}/>
     <TextField label="Geçerlilik Bitişi" type="date" value={form.effective_to||''}
      onChange={set('effective_to')} slotProps={{inputLabel:{shrink:true}}}
      error={rangeInverted}
      helperText={rangeInverted?'Bitiş tarihi başlangıçtan önce olamaz.':' '}/>
    </>}
    <TextField select label="Durum" value={form.active||'true'} onChange={set('active')}
     helperText="Pasif kayıtlar çözümlemede dikkate alınmaz.">
     <MenuItem value="true">Aktif</MenuItem>
     <MenuItem value="false">Pasif</MenuItem>
    </TextField>
   </Stack>
  </DialogContent>
  <DialogActions>
   <Button disabled={busy} onClick={onClose}>Vazgeç</Button>
   <Button variant="contained" disabled={busy||!valid||yearMismatch||rangeInverted}
    onClick={submit}>{busy?'Kaydediliyor...':'Kaydet'}</Button>
  </DialogActions>
 </Dialog>;
}

export default function HarvestSeasonAdmin(){
 const {can}=useAuth();
 const canManage=can('finance');
 const [tab,setTab]=useState(0);
 const [regions,setRegions]=useState<Region[]>([]);
 const [calendars,setCalendars]=useState<Calendar[]>([]);
 const [rules,setRules]=useState<Rule[]>([]);
 const [conflicts,setConflicts]=useState<Conflict[]>([]);
 const [customers,setCustomers]=useState<CustomerRow[]>([]);
 const [products,setProducts]=useState<ProductRow[]>([]);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');
 const [editor,setEditor]=useState<EditorState|null>(null);
 const [pendingDelete,setPendingDelete]=useState<DeleteState|null>(null);
 const [deleteError,setDeleteError]=useState('');

 const [previewCustomer,setPreviewCustomer]=useState('');
 const [previewProducts,setPreviewProducts]=useState<string[]>([]);
 const [previewDate,setPreviewDate]=useState(TODAY);
 const [preview,setPreview]=useState<Preview|null>(null);
 const [previewBusy,setPreviewBusy]=useState(false);
 const [previewIssue,setPreviewIssue]=useState<ReturnType<typeof previewWarning>|null>(null);

 const load=useCallback(async()=>{
  setLoading(true);setError('');
  try{
   // Çakışma analizi sunucuda, motorun kendi eşleştirme fonksiyonuyla
   // hesaplanır; burada yeniden türetmek motordan sapmaya yol açardı.
   const [regionRes,calendarRes,ruleRes,conflictRes]=await Promise.all([
    api.get<Region[]>('/harvest-scheduling/regions'),
    api.get<Calendar[]>('/harvest-scheduling/calendars'),
    api.get<Rule[]>('/harvest-scheduling/rules'),
    api.get<Conflict[]>('/harvest-scheduling/rules/conflicts'),
   ]);
   setRegions(regionRes.data||[]);
   setCalendars(calendarRes.data||[]);
   setRules(ruleRes.data||[]);
   setConflicts(conflictRes.data||[]);
  }catch(requestError){
   setError(errorDetail(requestError,'Harman sezon tanımları yüklenemedi.'));
  }finally{setLoading(false)}
 },[]);

 useEffect(()=>{void load()},[load]);

 useEffect(()=>{
  // Preview pickers only; a failure here must not block the definition tabs.
  void (async()=>{
   try{
    const [customerRes,productRes]=await Promise.all([
     api.get<CustomerRow[]|{items:CustomerRow[]}>('/customers',{params:{limit:1000}}),
     api.get<ProductRow[]|{items:ProductRow[]}>('/products',{params:{limit:1000}}),
    ]);
    const rows=<T,>(data:T[]|{items:T[]}|undefined):T[]=>
     Array.isArray(data)?data:data?.items||[];
    setCustomers(rows(customerRes.data));
    setProducts(rows(productRes.data));
   }catch{
    setCustomers([]);setProducts([]);
   }
  })();
 },[]);

 const runPreview=async()=>{
  const customerId=Number(previewCustomer);
  if(!customerId||previewProducts.length===0||!previewDate)return;
  setPreviewBusy(true);setPreview(null);setPreviewIssue(null);
  try{
   const search=new URLSearchParams();
   search.set('customer_id',String(customerId));
   search.set('transaction_date',previewDate);
   previewProducts.forEach(id=>search.append('product_ids',id));
   const response=await api.get<Preview>(`/harvest-scheduling/preview?${search.toString()}`);
   setPreview(response.data);
  }catch(requestError){
   const status=(requestError as {response?:{status?:number}})?.response?.status;
   setPreviewIssue(previewWarning(status,errorDetail(requestError,'Vade çözümlenemedi.')));
  }finally{setPreviewBusy(false)}
 };

 const confirmDelete=async()=>{
  if(!pendingDelete)return;
  setDeleteError('');
  try{
   await api.delete(`${SECTION_PATH[pendingDelete.section]}/${pendingDelete.id}`);
   setPendingDelete(null);
   await load();
  }catch(requestError){
   setDeleteError(errorDetail(requestError,'Kayıt silinemedi.'));
  }
 };

 const activeCalendarCodes=useMemo(
  ()=>new Set(calendars.filter(row=>row.active).map(row=>row.season_code)),
  [calendars],
 );
 const orphanRules=useMemo(
  ()=>rules.filter(row=>row.active&&!activeCalendarCodes.has(row.season_code)),
  [rules,activeCalendarCodes],
 );
 // Sunucudan gelen çakışmalar; istemci kendi öncelik mantığını kurmaz.
 const collidingRuleIds=useMemo(
  ()=>new Set(conflicts.flatMap(row=>row.rule_ids)),
  [conflicts],
 );
 const conflictContext=(id:number)=>conflicts.find(row=>row.rule_ids.includes(id));

 const addButton=(section:Section)=>canManage&&<Button variant="contained" size="small"
  startIcon={<AddIcon/>} onClick={()=>setEditor({section,row:null})}>Yeni</Button>;
 const rowActions=(section:Section,id:number,label:string,row:Region|Calendar|Rule)=>
  canManage&&<Stack direction="row" justifyContent="flex-end">
   <Tooltip title="Düzenle"><span><Button size="small" startIcon={<EditIcon/>}
    onClick={()=>setEditor({section,row})}>Düzenle</Button></span></Tooltip>
   <Tooltip title="Sil"><span><Button size="small" color="error" startIcon={<DeleteIcon/>}
    onClick={()=>{setDeleteError('');setPendingDelete({section,id,label})}}>Sil</Button></span></Tooltip>
  </Stack>;

 const previewReady=Boolean(previewCustomer)&&previewProducts.length>0&&Boolean(previewDate);

 return <Stack spacing={2}>
  <Box>
   <Typography variant="h4" fontWeight={900}>Harman Sezon Takvimi</Typography>
   <Typography color="text.secondary">
    Harman vadeli satışların vade tarihini belirleyen bölgeler, sezon takvimleri ve
    eşleştirme kuralları.
   </Typography>
  </Box>

  {!canManage&&<Alert severity="info">
   Bu ekranı görüntüleyebilirsiniz ancak tanım ekleme, düzenleme ve silme yetkisi
   finans/yönetici rollerine aittir.
  </Alert>}
  {error&&<Alert severity="error">{error}</Alert>}

  <Card variant="outlined"><CardContent>
   <Typography variant="overline">Çözümleyici Önceliği</Typography>
   <Typography variant="body2" color="text.secondary" mb={1}>
    Bir satış için önce en dar kapsamlı kural seçilir; eşit kapsamda öncelik değeri
    yüksek olan kazanır. Hâlâ berabere kalınırsa motor tahmin yürütmez ve satışı
    reddeder.
   </Typography>
   <Stack direction="row" flexWrap="wrap" gap={1} alignItems="center">
    {SCOPE_ORDER.map((scope,index)=><Box key={scope} display="flex" alignItems="center" gap={1}>
     <Chip size="small" color={index===0?'primary':'default'}
      label={`${index+1}. ${SCOPE_LABEL[scope]}`}/>
     {index<SCOPE_ORDER.length-1&&<Typography color="text.secondary">›</Typography>}
    </Box>)}
   </Stack>
   <Typography variant="caption" color="text.secondary" display="block" mt={1}>
    Kapsam eşitse: <b>öncelik değeri</b> → yine eşitse <b>satış reddedilir (409)</b>.
    Takvim seçiminde ise en yakın vade, eşit tarihte en özel takvim kazanır.
   </Typography>
  </CardContent></Card>

  <Tabs value={tab} onChange={(_,value)=>setTab(value)} variant="scrollable"
   allowScrollButtonsMobile>
   <Tab label={`Bölgeler (${regions.length})`}/>
   <Tab label={`Sezon Takvimleri (${calendars.length})`}/>
   <Tab label={`Kurallar (${rules.length})`}/>
   <Tab label="Önizleme"/>
  </Tabs>

  {loading?<Box py={6} display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>:<>

   {tab===0&&<Paper variant="outlined">
    <Stack direction="row" justifyContent="space-between" alignItems="center" p={1.5}>
     <Typography fontWeight={800}>Hasat Bölgeleri</Typography>
     {addButton('regions')}
    </Stack>
    <TableContainer><Table size="small">
     <TableHead><TableRow>
      <TableCell>Kod</TableCell><TableCell>Ad</TableCell><TableCell>Durum</TableCell>
      {canManage&&<TableCell align="right">İşlem</TableCell>}
     </TableRow></TableHead>
     <TableBody>
      {regions.length===0&&<TableRow><TableCell colSpan={canManage?4:3}>
       <Typography color="text.secondary" textAlign="center" py={3}>
        Henüz hasat bölgesi tanımlanmamış.
       </Typography>
      </TableCell></TableRow>}
      {regions.map(row=><TableRow key={row.id} hover>
       <TableCell>{row.code}</TableCell>
       <TableCell>{row.name}</TableCell>
       <TableCell><Chip size="small" color={row.active?'success':'default'}
        label={row.active?'Aktif':'Pasif'}/></TableCell>
       {canManage&&<TableCell align="right">
        {rowActions('regions',row.id,row.name,row)}
       </TableCell>}
      </TableRow>)}
     </TableBody>
    </Table></TableContainer>
   </Paper>}

   {tab===1&&<Paper variant="outlined">
    <Stack direction="row" justifyContent="space-between" alignItems="center" p={1.5}>
     <Typography fontWeight={800}>Sezon Takvimleri</Typography>
     {addButton('calendars')}
    </Stack>
    <TableContainer><Table size="small">
     <TableHead><TableRow>
      <TableCell>Sezon Kodu</TableCell><TableCell>Ad</TableCell><TableCell>Yıl</TableCell>
      <TableCell>Bölge</TableCell><TableCell>Ürün Kategorisi</TableCell>
      <TableCell>Vade Tarihi</TableCell><TableCell>Durum</TableCell>
      {canManage&&<TableCell align="right">İşlem</TableCell>}
     </TableRow></TableHead>
     <TableBody>
      {calendars.length===0&&<TableRow><TableCell colSpan={canManage?8:7}>
       <Typography color="text.secondary" textAlign="center" py={3}>
        Henüz sezon takvimi tanımlanmamış.
       </Typography>
      </TableCell></TableRow>}
      {calendars.map(row=><TableRow key={row.id} hover>
       <TableCell>{row.season_code}</TableCell>
       <TableCell>{row.name}</TableCell>
       <TableCell>{row.season_year}</TableCell>
       <TableCell>{regionName(regions,row.region_id)}</TableCell>
       <TableCell>{row.product_category||'Tüm kategoriler'}</TableCell>
       <TableCell sx={{fontWeight:800}}>{row.due_date}</TableCell>
       <TableCell><Chip size="small" color={row.active?'success':'default'}
        label={row.active?'Aktif':'Pasif'}/></TableCell>
       {canManage&&<TableCell align="right">
        {rowActions('calendars',row.id,`${row.season_code} · ${row.name}`,row)}
       </TableCell>}
      </TableRow>)}
     </TableBody>
    </Table></TableContainer>
   </Paper>}

   {tab===2&&<Stack spacing={2}>
    {conflicts.length>0&&<Alert severity="warning">
     <Typography fontWeight={800} gutterBottom>
      Motor {conflicts.length} kural çiftini sıralayamıyor
     </Typography>
     <Typography variant="body2" gutterBottom>
      Aşağıdaki bağlamlarda gerçek satış <b>409</b> ile reddedilir. Çiftlerden
      birinin önceliğini değiştirin, filtresini daraltın veya pasifleştirin.
      Bu analiz sunucuda motorun kendi eşleştirme mantığıyla hesaplanır.
     </Typography>
     <Stack component="ul" spacing={0.5} sx={{pl:2,m:0}}>
      {conflicts.map(row=><Typography component="li" variant="body2"
       key={row.rule_ids.join('-')}>
       Kural {row.rule_ids.map(id=>`#${id}`).join(' ↔ ')} — öncelik {row.priority},
       kapsam {String(SCOPE_LABEL[row.scope]??row.scope).toLocaleLowerCase('tr-TR')};
       {row.customer_id
        ?` ${customers.find(item=>item.id===row.customer_id)?.name||`müşteri #${row.customer_id}`}`
        :' tüm müşteriler'},
       {row.product_category?` ${row.product_category} kategorisi`:' tüm kategoriler'},
       {row.region_id?` ${regionName(regions,row.region_id)}`:' tüm bölgeler'}
       {` · örnek tarih ${row.sample_date}`}
      </Typography>)}
     </Stack>
    </Alert>}
    {orphanRules.length>0&&<Alert severity="warning">
     {orphanRules.length} aktif kural, aktif bir sezon takvimi olmayan sezon koduna
     işaret ediyor. Bu kurallara düşen satışlar vade bulamaz.
    </Alert>}
    <Paper variant="outlined">
     <Stack direction="row" justifyContent="space-between" alignItems="center" p={1.5}>
      <Typography fontWeight={800}>Vade Kuralları</Typography>
      {addButton('rules')}
     </Stack>
     <TableContainer><Table size="small">
      <TableHead><TableRow>
       <TableCell>Kapsam</TableCell><TableCell>Müşteri</TableCell><TableCell>Bölge</TableCell>
       <TableCell>Ürün Kategorisi</TableCell><TableCell>Sezon Kodu</TableCell>
       <TableCell align="right">Öncelik</TableCell><TableCell>Geçerlilik</TableCell>
       <TableCell>Durum</TableCell>
       {canManage&&<TableCell align="right">İşlem</TableCell>}
      </TableRow></TableHead>
      <TableBody>
       {rules.length===0&&<TableRow><TableCell colSpan={canManage?9:8}>
        <Typography color="text.secondary" textAlign="center" py={3}>
         Henüz vade kuralı tanımlanmamış.
        </Typography>
       </TableCell></TableRow>}
       {rules.map(row=>{
        const colliding=collidingRuleIds.has(row.id);
        const orphan=orphanRules.some(item=>item.id===row.id);
        return <TableRow key={row.id} hover
         sx={colliding?{bgcolor:'warning.50'}:undefined}>
         <TableCell>
          <Chip size="small" label={SCOPE_LABEL[row.scope]||`Kapsam ${row.scope}`}/>
          {colliding&&<Typography variant="caption" color="warning.main" display="block">
           Aynı öncelikte çakışma
           {(()=>{const context=conflictContext(row.id);
            if(!context)return '';
            const other=context.rule_ids.filter(id=>id!==row.id)
             .map(id=>`#${id}`).join(', ');
            const scopeText=context.product_category
             ?`${context.product_category} kategorisinde`:'tüm kategorilerde';
            return ` — ${other} ile ${scopeText}`;
           })()}
          </Typography>}
          {orphan&&<Typography variant="caption" color="warning.main" display="block">
           Aktif takvimi yok
          </Typography>}
         </TableCell>
         <TableCell>{row.customer_id
          ?customers.find(item=>item.id===row.customer_id)?.name||`Müşteri #${row.customer_id}`
          :'Tüm müşteriler'}</TableCell>
         <TableCell>{regionName(regions,row.region_id)}</TableCell>
         <TableCell>{row.product_category||'Tüm kategoriler'}</TableCell>
         <TableCell>{row.season_code}</TableCell>
         <TableCell align="right" sx={{fontWeight:800}}>{row.priority}</TableCell>
         <TableCell>{row.effective_from||'—'} / {row.effective_to||'—'}</TableCell>
         <TableCell><Chip size="small" color={row.active?'success':'default'}
          label={row.active?'Aktif':'Pasif'}/></TableCell>
         {canManage&&<TableCell align="right">
          {rowActions('rules',row.id,`${row.season_code} kuralı`,row)}
         </TableCell>}
        </TableRow>;
       })}
      </TableBody>
     </Table></TableContainer>
    </Paper>
   </Stack>}

   {tab===3&&<Stack spacing={2}>
    <Alert severity="info">
     Önizleme salt okunurdur: hiçbir satış veya vade kaydı oluşturmaz. Sunucu, gerçek
     bir harman vadeli satışta uygulayacağı çözümlemeyi aynen döndürür.
    </Alert>
    <Card variant="outlined"><CardContent>
     <Stack direction={{xs:'column',md:'row'}} spacing={1.2} alignItems={{md:'flex-start'}}>
      <TextField select size="small" label="Müşteri" value={previewCustomer}
       onChange={event=>setPreviewCustomer(event.target.value)} sx={{minWidth:220}}>
       {customers.length===0&&<MenuItem disabled value="">Müşteri listesi yüklenemedi</MenuItem>}
       {customers.map(row=><MenuItem key={row.id} value={String(row.id)}>{row.name}</MenuItem>)}
      </TextField>
      <TextField select size="small" label="Ürünler" value={previewProducts}
       slotProps={{select:{multiple:true}}} sx={{minWidth:260}}
       onChange={event=>setPreviewProducts(
        typeof event.target.value==='string'
         ?event.target.value.split(',')
         :(event.target.value as unknown as string[]),
       )}
       helperText="Birden fazla ürün seçerek karma sezon uyarısını sınayabilirsiniz.">
       {products.length===0&&<MenuItem disabled value="">Ürün listesi yüklenemedi</MenuItem>}
       {products.map(row=><MenuItem key={row.id} value={String(row.id)}>
        {row.name}{row.category?` · ${row.category}`:''}
       </MenuItem>)}
      </TextField>
      <TextField size="small" label="Satış Tarihi" type="date" value={previewDate}
       onChange={event=>setPreviewDate(event.target.value)}
       slotProps={{inputLabel:{shrink:true}}}/>
      <Button variant="contained" startIcon={<PlayArrowIcon/>} disabled={!previewReady||previewBusy}
       onClick={runPreview}>{previewBusy?'Çözümleniyor...':'Vadeyi Çözümle'}</Button>
     </Stack>
    </CardContent></Card>

    {previewIssue&&<Alert severity={previewIssue.severity}>
     <Typography fontWeight={800}>{previewIssue.title}</Typography>
     <Typography variant="body2">{previewIssue.hint}</Typography>
    </Alert>}

    {preview&&<Card variant="outlined"><CardContent>
     <Typography variant="overline">Çözümlenen Vade</Typography>
     <Typography variant="h5" fontWeight={900} mb={1}>{preview.due_date}</Typography>
     <Stack spacing={0.5}>
      <Typography><b>Sezon:</b> {preview.season_name} ({preview.season_code} · {preview.season_year})</Typography>
      <Typography><b>Takvim:</b> #{preview.calendar_id}</Typography>
      <Typography><b>Çözümleme yolu:</b> {preview.mode==='explicit_calendar'
       ?'Elle seçilen takvim':'Kural eşleşmesi'}</Typography>
      <Typography><b>Müşteri bölgesi:</b> {regionName(regions,preview.customer_region_id)}</Typography>
      <Typography><b>Ürün kategorileri:</b> {preview.product_categories
       .map(item=>item||'kategorisiz').join(', ')}</Typography>
      <Typography><b>Uygulanan kurallar:</b> {preview.rule_ids.length
       ?preview.rule_ids.map(id=>`#${id}`).join(', '):'—'}</Typography>
     </Stack>
     <Typography variant="caption" color="text.secondary" display="block" mt={1}>
      Bu sonuç sunucunun çözümlemesidir; tarayıcı vade hesaplamaz.
     </Typography>
    </CardContent></Card>}
   </Stack>}
  </>}

  <EditorDialog state={editor} regions={regions} customers={customers}
   onClose={()=>setEditor(null)} onSaved={load}/>

  <Dialog open={Boolean(pendingDelete)} onClose={()=>setPendingDelete(null)}
   fullWidth maxWidth="xs">
   <DialogTitle>Kaydı Sil</DialogTitle>
   <DialogContent>
    {deleteError&&<Alert severity="error" sx={{mb:2}}>{deleteError}</Alert>}
    <DialogContentText>
     <b>{pendingDelete?.label}</b> kaydı silinecek. Kullanımdaki kayıtlar sunucu
     tarafından korunur ve silme reddedilir.
    </DialogContentText>
   </DialogContent>
   <DialogActions>
    <Button onClick={()=>setPendingDelete(null)}>Vazgeç</Button>
    <Button color="error" variant="contained" onClick={confirmDelete}>Sil</Button>
   </DialogActions>
  </Dialog>
 </Stack>;
}
