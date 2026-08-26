import {useCallback,useEffect,useMemo,useState} from 'react';
import {
 Alert,Box,Button,Chip,CircularProgress,Dialog,DialogActions,DialogContent,
 DialogContentText,DialogTitle,Drawer,FormControlLabel,IconButton,MenuItem,Paper,
 Stack,Switch,Table,TableBody,TableCell,TableContainer,TableHead,TablePagination,
 TableRow,TextField,Tooltip,Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import ArchiveOutlinedIcon from '@mui/icons-material/ArchiveOutlined';
import CloseIcon from '@mui/icons-material/Close';
import LaunchIcon from '@mui/icons-material/Launch';
import UnarchiveOutlinedIcon from '@mui/icons-material/UnarchiveOutlined';
import {Link as RouterLink} from 'react-router-dom';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';

type ActivityRow={
 id:number;company_id:number;user_id:number|null;action_type:string;action_label:string;
 resource_type:string;resource_id:number|null;summary:string;
 details:Record<string,unknown>|null;correlation_id:string|null;created_at:string|null;
 archived_at:string|null;archived_by:number|null;archive_reason:string|null;is_archived:boolean;
};
type ActivityPage={items:ActivityRow[];total:number;limit:number;offset:number};
type CatalogAction={value:string;label:string};
type Catalog={action_types:CatalogAction[];resource_types:string[];max_limit:number};
type UserRow={id:number;username:string;display_name:string;role:string};

const RESOURCE_LABEL:Record<string,string>={
 sale:'Satış',payment:'Tahsilat / Ödeme',return:'İade',product:'Ürün',stock:'Stok',
 work_order:'İş Emri',invoice:'Fatura',late_fee_charge:'Vade Farkı',
 payment_allocation:'Tahsis',user:'Kullanıcı',activity_log:'Aktivite Kaydı',
};

// Kaynak bağlantısı yalnız gerçekten bir detay sayfası olan türler için üretilir;
// aksi hâlde satır düz metin kalır (kırık link üretmeyiz).
const RESOURCE_LINK:Record<string,(id:number)=>string>={
 product:(id)=>`/urunler/${id}`,
 work_order:(id)=>`/is-emirleri/${id}`,
 invoice:(id)=>`/faturalar/${id}`,
};
const RESOURCE_LIST_LINK:Record<string,string>={
 sale:'/satislar',payment:'/odemeler',return:'/belge-akislari',stock:'/stok-hareketleri',
 payment_allocation:'/tahsis-defteri',user:'/kullanicilar',late_fee_charge:'/alacaklar',
};

const PAGE_SIZES=[25,50,100];
const MIN_REASON=5;

/** Zaman damgası her zaman sunucudan ISO-UTC gelir; yerel saate çevrilir. */
const formatMoment=(value:string|null)=>{
 if(!value)return '—';
 const parsed=new Date(value);
 return Number.isNaN(parsed.getTime())?value:parsed.toLocaleString('tr-TR');
};

/**
 * Detay çekmecesinde eski/yeni değerler OLDUĞU GİBİ gösterilir. Para alanları
 * sunucudan Decimal metni olarak gelir; burada Number()'a çevrilmez, aksi hâlde
 * kuruş kaybı riski doğar. Özet satırındaki tutar zaten sunucuda Türkçe
 * biçimlendirilmiştir.
 */
const renderValue=(value:unknown):string=>{
 if(value===null||value===undefined)return '—';
 if(typeof value==='string')return value;
 if(typeof value==='boolean')return value?'evet':'hayır';
 if(typeof value==='number')return String(value);
 return JSON.stringify(value,null,2);
};

type ChangeEntry={field:string;old:unknown;next:unknown};

const changeEntries=(details:Record<string,unknown>|null):ChangeEntry[]=>{
 const changes=details?.changes;
 if(!changes||typeof changes!=='object')return [];
 return Object.entries(changes as Record<string,unknown>).map(([field,value])=>{
  const pair=(value&&typeof value==='object'?value:{}) as Record<string,unknown>;
  return {field,old:pair.old,next:pair.new};
 });
};

export default function ActivityLog(){
 const {user}=useAuth();
 const isAdmin=user?.role==='admin';

 const [rows,setRows]=useState<ActivityRow[]>([]);
 const [total,setTotal]=useState(0);
 const [page,setPage]=useState(0);
 const [pageSize,setPageSize]=useState(50);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');

 const [catalog,setCatalog]=useState<Catalog|null>(null);
 const [users,setUsers]=useState<UserRow[]>([]);

 const [userId,setUserId]=useState('');
 const [actionType,setActionType]=useState('');
 const [resourceType,setResourceType]=useState('');
 const [dateFrom,setDateFrom]=useState('');
 const [dateTo,setDateTo]=useState('');
 const [includeArchived,setIncludeArchived]=useState(false);

 const [selected,setSelected]=useState<ActivityRow|null>(null);
 const [archiveTarget,setArchiveTarget]=useState<ActivityRow|null>(null);
 const [reason,setReason]=useState('');
 const [saving,setSaving]=useState(false);
 const [actionError,setActionError]=useState('');

 const userName=useCallback((id:number|null)=>{
  if(id===null)return 'Sistem';
  const match=users.find(row=>row.id===id);
  return match?match.display_name||match.username:`#${id}`;
 },[users]);

 const load=useCallback(async()=>{
  setLoading(true);setError('');
  try{
   const params:Record<string,string|number|boolean>={limit:pageSize,offset:page*pageSize};
   if(userId)params.user_id=Number(userId);
   if(actionType)params.action_type=actionType;
   if(resourceType)params.resource_type=resourceType;
   if(dateFrom)params.date_from=dateFrom;
   if(dateTo)params.date_to=dateTo;
   if(includeArchived)params.include_archived=true;
   const response=await api.get<ActivityPage>('/activity-logs',{params});
   setRows(response.data.items);
   setTotal(response.data.total);
  }catch(exception){
   setRows([]);setTotal(0);
   setError(errorDetail(exception,'Aktivite kayıtları yüklenemedi'));
  }finally{
   setLoading(false);
  }
 },[actionType,dateFrom,dateTo,includeArchived,page,pageSize,resourceType,userId]);

 useEffect(()=>{void load()},[load]);

 useEffect(()=>{
  api.get<Catalog>('/activity-logs/catalog').then(r=>setCatalog(r.data)).catch(()=>setCatalog(null));
  api.get<UserRow[]>('/users').then(r=>setUsers(r.data)).catch(()=>setUsers([]));
 },[]);

 // Filtre değiştiğinde ilk sayfaya dön: aksi hâlde boş bir offset'te kalınır.
 const resetPage=()=>setPage(0);

 const archive=async()=>{
  if(!archiveTarget)return;
  setSaving(true);setActionError('');
  try{
   await api.post(`/activity-logs/${archiveTarget.id}/archive`,{reason:reason.trim()});
   setArchiveTarget(null);setReason('');setSelected(null);
   await load();
  }catch(exception){
   setActionError(errorDetail(exception,'Aktivite kaydı arşivlenemedi'));
  }finally{
   setSaving(false);
  }
 };

 const unarchive=async(row:ActivityRow)=>{
  setSaving(true);setActionError('');
  try{
   await api.post(`/activity-logs/${row.id}/unarchive`,{});
   setSelected(null);
   await load();
  }catch(exception){
   setActionError(errorDetail(exception,'Aktivite kaydı arşivden çıkarılamadı'));
  }finally{
   setSaving(false);
  }
 };

 const resourceCell=(row:ActivityRow)=>{
  const label=RESOURCE_LABEL[row.resource_type]||row.resource_type;
  const detailHref=row.resource_id!==null?RESOURCE_LINK[row.resource_type]?.(row.resource_id):undefined;
  const href=detailHref||RESOURCE_LIST_LINK[row.resource_type];
  const text=row.resource_id!==null?`${label} #${row.resource_id}`:label;
  if(!href)return <Typography variant="body2">{text}</Typography>;
  return (
   <Button component={RouterLink} to={href} size="small" endIcon={<LaunchIcon fontSize="inherit"/>}
     onClick={event=>event.stopPropagation()} sx={{textTransform:'none',px:0.5}}>
    {text}
   </Button>
  );
 };

 const detailPairs=useMemo(()=>changeEntries(selected?.details??null),[selected]);
 const rawDetails=useMemo(()=>{
  if(!selected?.details)return '';
  const clone={...selected.details};
  delete clone.changes;
  return Object.keys(clone).length?JSON.stringify(clone,null,2):'';
 },[selected]);

 const resourceText=(row:ActivityRow)=>{
  const label=RESOURCE_LABEL[row.resource_type]||row.resource_type;
  return row.resource_id!==null?`${label} #${row.resource_id}`:label;
 };
 const columns:GridColDef<ActivityRow>[]=[
  {field:'created_at',headerName:'Zaman',width:180,renderCell:params=>formatMoment(params.row.created_at)},
  {field:'user_id',headerName:'Kullanıcı',width:170,renderCell:params=>userName(params.row.user_id)},
  {field:'summary',headerName:'Özet',flex:1,minWidth:320,renderCell:params=><Stack
    direction="row" alignItems="center" gap={1} flexWrap="wrap" height="100%">
    <Chip size="small" label={params.row.action_label||params.row.action_type} variant="outlined"/>
    <Typography variant="body2">{params.row.summary}</Typography>
    {params.row.is_archived&&<Chip size="small" color="warning" label="Arşivli"/>}
  </Stack>},
  {field:'resource_type',headerName:'Kaynak',width:190,renderCell:params=>resourceCell(params.row)},
  ...(isAdmin?[{field:'__archive',headerName:'İşlem',width:90,sortable:false,filterable:false,
    renderCell:({row}: {row:ActivityRow})=>row.is_archived
      ?<Tooltip title="Arşivden çıkar"><span><IconButton size="small" disabled={saving}
         aria-label={`${row.summary} — Arşivden çıkar`}
         onClick={event=>{event.stopPropagation();void unarchive(row)}}><UnarchiveOutlinedIcon fontSize="small"/></IconButton></span></Tooltip>
      :<Tooltip title="Arşivle"><span><IconButton size="small" disabled={saving}
         aria-label={`${row.summary} — Arşivle`}
         onClick={event=>{event.stopPropagation();setArchiveTarget(row);setReason('')}}><ArchiveOutlinedIcon fontSize="small"/></IconButton></span></Tooltip>,
  }]:[]),
 ];
 const cardActions=isAdmin
  ?[
    {label:'Arşivle',icon:<ArchiveOutlinedIcon/>,color:'warning' as const,
      disabled:(row:ActivityRow)=>row.is_archived||saving,
      onClick:(row:ActivityRow)=>{setArchiveTarget(row);setReason('')}},
    ...(includeArchived?[{label:'Arşivden çıkar',icon:<UnarchiveOutlinedIcon/>,
      disabled:(row:ActivityRow)=>!row.is_archived||saving,
      onClick:(row:ActivityRow)=>{void unarchive(row)}}]:[]),
  ]:undefined;

 return <Stack spacing={2}>
  <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1}>
   <Box>
    <Typography variant="h5" fontWeight={900}>Aktivite</Typography>
    <Typography variant="body2" color="text.secondary">
     Kim ne yaptı: satış, tahsilat, stok, iş emri, fatura ve yönetim işlemleri.
     Kayıtlar değiştirilemez; yalnız admin arşivleyebilir.
    </Typography>
   </Box>
   {isAdmin&&<FormControlLabel sx={{'& .MuiSwitch-input':{minHeight:{xs:44,md:38}}}}
    control={<Switch checked={includeArchived} onChange={e=>{setIncludeArchived(e.target.checked);resetPage()}}/>}
    label="Arşivlenenleri göster"
   />}
  </Stack>

  {error&&<Alert severity="error">{error}</Alert>}
  {actionError&&<Alert severity="error" onClose={()=>setActionError('')}>{actionError}</Alert>}

  <Paper sx={{p:2,'& input':{minHeight:{xs:44,md:21},boxSizing:'border-box'}}}>
   <Stack direction={{xs:'column',md:'row'}} spacing={1.5} flexWrap="wrap">
    <TextField select size="small" label="Kullanıcı" value={userId} sx={{minWidth:200}}
      onChange={e=>{setUserId(e.target.value);resetPage()}}>
     <MenuItem value="">Tümü</MenuItem>
     {users.map(row=><MenuItem key={row.id} value={String(row.id)}>{row.display_name||row.username}</MenuItem>)}
    </TextField>
    <TextField select size="small" label="İşlem tipi" value={actionType} sx={{minWidth:220}}
      onChange={e=>{setActionType(e.target.value);resetPage()}}>
     <MenuItem value="">Tümü</MenuItem>
     {(catalog?.action_types||[]).map(item=>
      <MenuItem key={item.value} value={item.value}>{item.label}</MenuItem>)}
    </TextField>
    <TextField select size="small" label="Kaynak" value={resourceType} sx={{minWidth:180}}
      onChange={e=>{setResourceType(e.target.value);resetPage()}}>
     <MenuItem value="">Tümü</MenuItem>
     {(catalog?.resource_types||[]).map(item=>
      <MenuItem key={item} value={item}>{RESOURCE_LABEL[item]||item}</MenuItem>)}
    </TextField>
    <TextField size="small" type="date" label="Başlangıç" value={dateFrom}
      slotProps={{inputLabel:{shrink:true}}}
      onChange={e=>{setDateFrom(e.target.value);resetPage()}}/>
    <TextField size="small" type="date" label="Bitiş" value={dateTo}
      slotProps={{inputLabel:{shrink:true}}}
      onChange={e=>{setDateTo(e.target.value);resetPage()}}/>
   </Stack>
  </Paper>

  {loading?<Paper sx={{py:4,textAlign:'center'}}><CircularProgress size={26}/></Paper>:null}
  {!loading&&rows.length===0?<Paper sx={{py:4,textAlign:'center'}}>
   <Typography variant="body2" color="text.secondary">Seçilen filtrelerde aktivite kaydı yok.</Typography>
  </Paper>:null}
  {!loading&&rows.length>0?<Box data-testid="activity-log-data-surface">
   <ResponsiveTable
    rows={rows}
    columns={columns}
    hideFooter
    cardTitle={row=>row.summary}
    cardSubtitle={row=>row.action_label||row.action_type}
    cardFields={[
     {label:'Zaman',value:(row:ActivityRow)=>formatMoment(row.created_at)},
     {label:'Kullanıcı',value:(row:ActivityRow)=>userName(row.user_id)},
     {label:'Kaynak',value:(row:ActivityRow)=>resourceText(row)},
    ]}
    cardActions={cardActions}
    onRowClick={row=>setSelected(row)}
    desktopRowHeight={64}
   />
  </Box>:null}
  <Paper>
   <TablePagination sx={{
    '& .MuiIconButton-root':{minWidth:{xs:44,md:38},minHeight:{xs:44,md:38}},
    '& input':{minHeight:{xs:44,md:21},boxSizing:'border-box'},
    '& .MuiTablePagination-select':{minHeight:{xs:44,md:32},display:'flex',alignItems:'center'},
   }}
    component="div"
    count={total}
    page={page}
    rowsPerPage={pageSize}
    rowsPerPageOptions={PAGE_SIZES}
    labelRowsPerPage="Sayfa başına"
    labelDisplayedRows={({from,to,count})=>`${from}–${to} / ${count}`}
    onPageChange={(_,next)=>setPage(next)}
   onRowsPerPageChange={e=>{setPageSize(Number(e.target.value));resetPage()}}
   />
  </Paper>

  <Drawer anchor="right" open={Boolean(selected)} onClose={()=>setSelected(null)}
    slotProps={{paper:{sx:{width:{xs:'100%',sm:460},p:2}}}}>
   {selected&&<Stack spacing={2}>
    <Stack direction="row" alignItems="center" justifyContent="space-between">
     <Typography variant="h6" fontWeight={800}>Aktivite Detayı</Typography>
     <IconButton size="small" onClick={()=>setSelected(null)}><CloseIcon fontSize="small"/></IconButton>
    </Stack>
    <Stack spacing={0.5}>
     <Typography variant="body2" color="text.secondary">{formatMoment(selected.created_at)}</Typography>
     <Typography variant="body1" fontWeight={700}>{selected.summary}</Typography>
     <Stack direction="row" gap={1} flexWrap="wrap" mt={0.5}>
      <Chip size="small" label={selected.action_label||selected.action_type}/>
      <Chip size="small" variant="outlined"
        label={RESOURCE_LABEL[selected.resource_type]||selected.resource_type}/>
      {selected.is_archived&&<Chip size="small" color="warning" label="Arşivli"/>}
     </Stack>
     <Typography variant="caption" color="text.secondary" mt={1}>
      Kullanıcı: {userName(selected.user_id)}
     </Typography>
     {selected.correlation_id&&<Typography variant="caption" color="text.secondary">
      İstek kimliği: {selected.correlation_id}
     </Typography>}
    </Stack>

    {selected.is_archived&&<Alert severity="warning">
     Arşiv gerekçesi: {selected.archive_reason}
     <br/>Arşivleyen: {userName(selected.archived_by)} · {formatMoment(selected.archived_at)}
    </Alert>}

    {detailPairs.length>0&&<Box>
     <Typography variant="subtitle2" fontWeight={800} gutterBottom>Değişen alanlar</Typography>
     <TableContainer component={Paper} variant="outlined">
      <Table size="small">
       <TableHead><TableRow>
        <TableCell>Alan</TableCell><TableCell>Eski</TableCell><TableCell>Yeni</TableCell>
       </TableRow></TableHead>
       <TableBody>
        {detailPairs.map(entry=><TableRow key={entry.field}>
         <TableCell>{entry.field}</TableCell>
         <TableCell sx={{whiteSpace:'pre-wrap',wordBreak:'break-word'}}>{renderValue(entry.old)}</TableCell>
         <TableCell sx={{whiteSpace:'pre-wrap',wordBreak:'break-word'}}>{renderValue(entry.next)}</TableCell>
        </TableRow>)}
       </TableBody>
      </Table>
     </TableContainer>
    </Box>}

    {rawDetails&&<Box>
     <Typography variant="subtitle2" fontWeight={800} gutterBottom>Kayıt detayı</Typography>
     <Paper variant="outlined" sx={{p:1.5,bgcolor:'action.hover'}}>
      <Typography component="pre" variant="caption"
        sx={{m:0,whiteSpace:'pre-wrap',wordBreak:'break-word',fontFamily:'monospace'}}>
       {rawDetails}
      </Typography>
     </Paper>
    </Box>}

    {!detailPairs.length&&!rawDetails&&<Typography variant="body2" color="text.secondary">
     Bu kayıt için ek detay tutulmamış.
    </Typography>}

    {isAdmin&&<Stack direction="row" spacing={1}>
     {selected.is_archived
      ?<Button startIcon={<UnarchiveOutlinedIcon/>} disabled={saving}
         onClick={()=>void unarchive(selected)}>Arşivden çıkar</Button>
      :<Button startIcon={<ArchiveOutlinedIcon/>} color="warning" disabled={saving}
         onClick={()=>{setArchiveTarget(selected);setReason('')}}>Arşivle</Button>}
    </Stack>}
   </Stack>}
  </Drawer>

  <Dialog open={Boolean(archiveTarget)} onClose={()=>setArchiveTarget(null)} fullWidth maxWidth="sm">
   <DialogTitle>Aktivite kaydını arşivle</DialogTitle>
   <DialogContent>
    <DialogContentText sx={{mb:2}}>
     Kayıt silinmez; yalnız varsayılan listeden gizlenir. Arşivleme işleminin kendisi de
     ayrı bir aktivite kaydı olarak yazılır.
    </DialogContentText>
    {archiveTarget&&<Alert severity="info" sx={{mb:2}}>{archiveTarget.summary}</Alert>}
    <TextField
     autoFocus fullWidth multiline minRows={2} label="Gerekçe" value={reason}
     onChange={e=>setReason(e.target.value)}
     helperText={`En az ${MIN_REASON} karakter. Gerekçe denetim kaydına yazılır.`}
     error={reason.trim().length>0&&reason.trim().length<MIN_REASON}
    />
   </DialogContent>
   <DialogActions>
    <Button onClick={()=>setArchiveTarget(null)}>Vazgeç</Button>
    <Button variant="contained" color="warning" disabled={saving||reason.trim().length<MIN_REASON}
      onClick={()=>void archive()}>
     {saving?'Arşivleniyor...':'Arşivle'}
    </Button>
   </DialogActions>
  </Dialog>
 </Stack>;
}
