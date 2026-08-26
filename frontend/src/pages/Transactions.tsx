import React from 'react';
import {useEffect,useRef,useState} from 'react';
import {Link as RouterLink,useLocation,useNavigate} from 'react-router-dom';
import {Alert,Box,Button,Chip,Dialog,DialogActions,DialogContent,DialogTitle,Divider,IconButton,InputAdornment,Link,MenuItem,Paper,Stack,TextField,Typography} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf';
import TableViewIcon from '@mui/icons-material/TableView';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import SearchIcon from '@mui/icons-material/Search';
import {GridColDef} from '@mui/x-data-grid';
import ResponsiveTable from '../components/ResponsiveTable';
import TransactionDialog from '../components/TransactionDialog';
import ExcelImportDialog from '../components/ExcelImportDialog';
import PolicyOverrideDialog from '../components/PolicyOverrideDialog';
import {api,errorDetail,money,openAuthenticated,policyOverrideHeaders,policyOverrideRequired} from '../api';
import {useAuth} from '../AuthContext';
import {formatDate} from './ReceivablesAging';

type PolicyAction={detail:string;run:(headers:Record<string,string>)=>Promise<void>};

export default function Transactions({kind}:{kind:'sale'|'purchase'}){
 const location=useLocation();
 const navigate=useNavigate();
 const {user}=useAuth();
 const [rows,setRows]=useState<any[]>([]);
 const [q,setQ]=useState('');
 const [status,setStatus]=useState('');
 const [dateFrom,setDateFrom]=useState('');
 const [dateTo,setDateTo]=useState('');
 const [sort,setSort]=useState('date_desc');
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const [open,setOpen]=useState(false);
 const [importOpen,setImportOpen]=useState(false);
 const [editId,setEditId]=useState<number|null>(null);
 const [detail,setDetail]=useState<any|null>(null);
 const [saved,setSaved]=useState<any|null>(null);
 const [initialEntityId,setInitialEntityId]=useState<number|null>(null);
 const [initialProductId,setInitialProductId]=useState<number|null>(null);
 const [policyAction,setPolicyAction]=useState<PolicyAction|null>(null);
 const [overrideReason,setOverrideReason]=useState('');
 const [overrideError,setOverrideError]=useState('');
 const [overrideBusy,setOverrideBusy]=useState(false);
 const [deletingId,setDeletingId]=useState<number|null>(null);
 const requestSeq=useRef(0);

 const endpoint=kind==='sale'?'/orders':'/purchases';
 const title=kind==='sale'?'Satışlar':'Alışlar';
 const entityField=kind==='sale'?'customer_name':'supplier_name';
 const manager=user?.role==='admin'||user?.role==='yonetici';

 const load=()=>{
  const seq=++requestSeq.current;
  setLoading(true);setError('');
  api.get(endpoint,{params:{q,status:status||undefined,date_from:dateFrom||undefined,date_to:dateTo||undefined,sort}})
   .then(response=>{if(seq===requestSeq.current)setRows(response.data)})
    .catch(err=>{if(seq===requestSeq.current)setError(errorDetail(err,'Belge listesi yüklenemedi.'))})
   .finally(()=>{if(seq===requestSeq.current)setLoading(false)});
 };

 useEffect(()=>{const timer=setTimeout(load,250);return()=>clearTimeout(timer)},[q,kind,status,dateFrom,dateTo,sort]);
 useEffect(()=>{
  const state:any=location.state;
  if(!state)return;
   if(state.openId){api.get(`${endpoint}/${state.openId}`).then(response=>setDetail(response.data)).catch(err=>setError(errorDetail(err,'Belge detayı açılamadı.')))}
  if(state.newForEntityId){setEditId(null);setInitialEntityId(Number(state.newForEntityId));setOpen(true)}
  // Open a fresh document with a product preselected (e.g. from the critical-stock worklist).
  if(state.newForProductId){setEditId(null);setInitialProductId(Number(state.newForProductId));setOpen(true)}
  navigate(location.pathname,{replace:true,state:null});
 },[location.state,endpoint]);

 const remove=async(id:number)=>{
  if(deletingId!==null)return;
  setDeletingId(id);setError('');
  const action=async(headers?:Record<string,string>)=>{await api.delete(`${endpoint}/${id}`,headers?{headers}:undefined)};
  try{await action();load()}
  catch(err:any){
    const message=errorDetail(err,'Belge silinemedi.');
   if(manager&&policyOverrideRequired(err)){
    setOverrideReason('');setOverrideError('');
    setPolicyAction({detail:message,run:async headers=>{await action(headers);load()}});
   }else setError(message);
  }finally{setDeletingId(null)}
 };

 const confirmOverride=async()=>{
  if(!policyAction||overrideReason.trim().length<5)return;
  setOverrideBusy(true);setOverrideError('');
  try{
   await policyAction.run(policyOverrideHeaders(overrideReason));
   setPolicyAction(null);setOverrideReason('');
   }catch(err:any){setOverrideError(errorDetail(err,'Yönetici onayı uygulanamadı.'))}
  finally{setOverrideBusy(false)}
 };

  const columns:GridColDef[]=[
   {field:'document_no',headerName:'Belge No',width:120,valueGetter:(_,row)=>row.document_no||`#${row.id}`},
   {field:entityField,headerName:kind==='sale'?'Müşteri':'Tedarikçi',flex:1,minWidth:160,
    renderCell:params=>kind==='sale'&&params.row.entity_id
     ?<Link component={RouterLink} to={`/musteriler/${params.row.entity_id}`} onClick={event=>event.stopPropagation()}>{params.value}</Link>
     :params.value},
   {field:'transaction_date',headerName:'Tarih',width:110},
   {field:'status',headerName:'Durum',width:130,renderCell:params=>{const map:any={draft:['Taslak','default'],pending:['Onay bekliyor','warning'],approved:['Onaylandı','info'],completed:['Tamamlandı','success'],cancelled:['İptal','error']};const item=map[params.value]||[params.value,'default'];return <Chip size="small" variant="outlined" color={item[1]} label={item[0]}/>}},
   {field:'warehouse_name',headerName:'Depo',width:120},
   {field:'item_count',headerName:'Kalem',width:70},
   {field:'remaining_amount',headerName:'Kalan',width:120,valueFormatter:value=>money(value)},
   {field:'final_total',headerName:'Toplam',flex:1,minWidth:120,valueFormatter:value=>money(value)},
   {field:'actions',headerName:'',width:100,sortable:false,renderCell:params=><>
    <IconButton size="small" aria-label="Belgeyi düzenle" onClick={event=>{event.stopPropagation();setEditId(params.row.id);setOpen(true)}}><EditIcon fontSize="small"/></IconButton>
    <IconButton size="small" aria-label="Belgeyi sil" color="error" disabled={deletingId!==null} onClick={event=>{event.stopPropagation();void remove(params.row.id)}}><DeleteIcon fontSize="small"/></IconButton>
   </>},
  ];

  const show=async(row:any)=>{try{const response=await api.get(`${endpoint}/${row.id}`);setDetail(response.data)}catch(err:any){setError(errorDetail(err,'Belge detayı açılamadı.'))}};

 return <Stack spacing={2.5}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={2}>
   <Box><Typography variant="h3">{title}</Typography><Typography color="text.secondary" mt={.5}>{kind==='sale'?'Satış belgelerini, tahsilat durumlarını ve müşteri hareketlerini yönetin.':'Alış belgelerini ve tedarikçi hareketlerini yönetin.'}</Typography></Box>
   <Stack direction={{xs:'column',sm:'row'}} spacing={1} sx={{width:{xs:'100%',sm:'auto'}}}>
    {kind==='sale'&&manager&&<Button variant="outlined" color="warning" size="large" startIcon={<UploadFileIcon/>} onClick={()=>setImportOpen(true)} sx={{minWidth:{xs:'100%',sm:172}}}>Satış Raporu Yükle</Button>}
    <Button variant="contained" color={kind==='sale'?'primary':'primary'} size="large" startIcon={<AddIcon/>} onClick={()=>{setEditId(null);setInitialEntityId(null);setOpen(true)}} sx={{minWidth:{xs:'100%',sm:172}}}>Yeni {kind==='sale'?'satış':'alış'}</Button>
   </Stack>
  </Stack>
  {kind==='sale'&&<ExcelImportDialog open={importOpen} kind="sales" onClose={()=>setImportOpen(false)} onDone={load}/>}
  {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}
  <Paper variant="outlined" sx={{p:{xs:1.5,sm:2},borderRadius:3}}><Stack direction={{xs:'column',lg:'row'}} spacing={1.25}>
   <TextField placeholder="Müşteri veya belge numarası ara" value={q} onChange={event=>setQ(event.target.value)} sx={{minWidth:{lg:300},flex:1}} slotProps={{input:{startAdornment:<InputAdornment position="start"><SearchIcon fontSize="small"/></InputAdornment>},htmlInput:{'aria-label':'Müşteri veya belge numarası ara'}}}/>
   <TextField select size="small" label="Durum" value={status} onChange={event=>setStatus(event.target.value)} sx={{minWidth:180}}>
    <MenuItem value="">Tümü</MenuItem><MenuItem value="draft">Taslak</MenuItem><MenuItem value="pending">Onay Bekliyor</MenuItem><MenuItem value="approved">Onaylandı</MenuItem><MenuItem value="completed">Tamamlandı</MenuItem><MenuItem value="cancelled">İptal</MenuItem>
   </TextField>
   <TextField size="small" type="date" label="Başlangıç" value={dateFrom} onChange={event=>setDateFrom(event.target.value)} InputLabelProps={{shrink:true}}/>
   <TextField size="small" type="date" label="Bitiş" value={dateTo} onChange={event=>setDateTo(event.target.value)} InputLabelProps={{shrink:true}}/>
   <TextField select size="small" label="Sıralama" value={sort} onChange={event=>setSort(event.target.value)} sx={{minWidth:190}}>
    <MenuItem value="date_desc">Tarih yeni → eski</MenuItem><MenuItem value="date_asc">Tarih eski → yeni</MenuItem><MenuItem value="total_desc">Tutar yüksek → düşük</MenuItem><MenuItem value="total_asc">Tutar düşük → yüksek</MenuItem><MenuItem value="entity_asc">Cari A → Z</MenuItem><MenuItem value="entity_desc">Cari Z → A</MenuItem>
   </TextField>
  </Stack></Paper>
   <ResponsiveTable rows={rows} columns={columns} loading={loading} onRowClick={show} cardBreakpoint="lg" cardTitle={row=>row[entityField]} cardSubtitle={row=>`${row.document_no||`Belge #${row.id}`} · ${row.transaction_date}`} cardFields={[{label:'Depo',value:row=>row.warehouse_name||'Merkez Depo'},{label:'Durum',value:row=>({draft:'Taslak',pending:'Onay Bekliyor',approved:'Onaylandı',completed:'Tamamlandı',cancelled:'İptal'} as any)[row.status]||row.status},{label:'Kalan',value:row=>money(row.remaining_amount)},{label:'Toplam',value:row=>money(row.final_total)}]} cardActions={[{label:'Düzenle',color:'primary',onClick:row=>{setEditId(row.id);setOpen(true)}},{label:'Sil',color:'error',disabled:row=>deletingId===row.id,onClick:row=>void remove(row.id)}]}/>
  <TransactionDialog open={open} kind={kind} id={editId} initialEntityId={initialEntityId} initialProductId={initialProductId} onClose={()=>{setOpen(false);setInitialEntityId(null);setInitialProductId(null)}} onSaved={result=>{load();setSaved(result)}}/>
  <Dialog open={!!detail} onClose={()=>setDetail(null)} maxWidth="md" fullWidth>
   <DialogTitle>{kind==='sale'?'Satış':'Alış'} {detail?.document?.document_no||`#${detail?.document?.id}`}</DialogTitle>
   <DialogContent>
    {kind==='sale'&&(detail?.document?.customer_id||detail?.document?.entity_id)
     ?<Link component="button" underline="hover" fontWeight={800} onClick={()=>{const customerId=detail.document.customer_id||detail.document.entity_id;setDetail(null);navigate(`/musteriler/${customerId}`)}}>{detail.document.customer_name}</Link>
     :<Typography fontWeight={800}>{detail?.document?.customer_name||detail?.document?.supplier_name}</Typography>}
    <Typography color="text.secondary">{detail?.document?.transaction_date}</Typography>
    <Typography color="text.secondary" mb={2}>Durum: {({draft:'Taslak',pending:'Onay Bekliyor',approved:'Onaylandı',completed:'Tamamlandı',cancelled:'İptal'} as any)[detail?.document?.status]||detail?.document?.status} · Ödeme: {detail?.document?.payment_method||'credit'} · Depo: {detail?.document?.warehouse_name||'Merkez Depo'}{detail?.document?.due_date?` · Vade: ${formatDate(detail.document.due_date)}`:''}</Typography>
    <Stack spacing={1}>{detail?.items?.map((item:any)=><Stack key={item.id} direction="row" justifyContent="space-between"><Typography>{item.product_name} · {item.quantity}{item.discount_percent?` · İsk. %${item.discount_percent}`:''}</Typography><Typography fontWeight={800}>{money(item.line_total)}</Typography></Stack>)}</Stack>
    {detail?.payments?.length>0&&<><Divider sx={{my:2}}/><Typography fontWeight={900} mb={1}>Ödeme Dağılımı</Typography><Stack spacing={.7}>{detail.payments.map((payment:any)=><Stack key={payment.id} direction="row" justifyContent="space-between"><Typography>{({cash:'Nakit',card:'Kredi Kartı / POS',bank_transfer:'Havale / EFT',check:'Çek',promissory_note:'Senet'} as any)[payment.payment_method]||payment.payment_method}</Typography><Typography fontWeight={800}>{money(payment.amount)}</Typography></Stack>)}</Stack></>}
    <Typography variant="h6" textAlign="right" mt={2} fontWeight={900}>{money(detail?.document?.final_total)}</Typography>
   </DialogContent>
   <DialogActions>
    <Button startIcon={<PictureAsPdfIcon/>} onClick={()=>openAuthenticated(`/documents/${kind==='sale'?'orders':'purchases'}/${detail?.document?.id}/pdf`)}>PDF</Button>
    <Button startIcon={<TableViewIcon/>} onClick={()=>openAuthenticated(`/documents/${kind==='sale'?'orders':'purchases'}/${detail?.document?.id}/xlsx`,`${kind}-${detail?.document?.document_no||detail?.document?.id}.xlsx`)}>Excel</Button>
    <Button onClick={()=>setDetail(null)}>Kapat</Button>
   </DialogActions>
  </Dialog>
  <Dialog open={!!saved} onClose={()=>setSaved(null)} maxWidth="sm" fullWidth onKeyDown={event=>{if(event.key==='Escape')setSaved(null)}}>
   <DialogTitle>{kind==='sale'?'Satış':'Alış'} kaydedildi</DialogTitle>
   <DialogContent>
    <Alert severity="success" sx={{mb:2}}>Belge başarıyla kaydedildi.</Alert>
    <Typography fontWeight={800}>Belge: {saved?.document_no||`#${saved?.id}`}</Typography>
    <Typography color="text.secondary">Toplam: {money(saved?.final_total)} · Kalan: {money(saved?.remaining_amount)}</Typography>
    <Typography variant="caption" color="text.secondary">Enter: yeni {kind==='sale'?'satış':'alış'} · Esc: kapat</Typography>
   </DialogContent>
   <DialogActions sx={{flexWrap:'wrap'}}>
    <Button startIcon={<PictureAsPdfIcon/>} onClick={()=>openAuthenticated(`/documents/${kind==='sale'?'orders':'purchases'}/${saved?.id}/pdf`)}>PDF / Yazdır</Button>
    <Button onClick={async()=>{const response=await api.get(`${endpoint}/${saved?.id}`);setDetail(response.data);setSaved(null)}}>Belgeyi Gör</Button>
    <Button autoFocus variant="contained" startIcon={<AddIcon/>} onClick={()=>{setSaved(null);setEditId(null);setInitialEntityId(null);setOpen(true)}}>Yeni {kind==='sale'?'Satış':'Alış'}</Button>
    <Button onClick={()=>setSaved(null)}>Kapat</Button>
   </DialogActions>
  </Dialog>
  <PolicyOverrideDialog open={!!policyAction} detail={policyAction?.detail||''} reason={overrideReason} busy={overrideBusy} error={overrideError} onReasonChange={setOverrideReason} onClose={()=>{setPolicyAction(null);setOverrideReason('');setOverrideError('')}} onConfirm={confirmOverride}/>
 </Stack>;
}
