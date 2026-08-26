import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import {Alert,Box,Button,Chip,CircularProgress,Dialog,DialogActions,DialogContent,DialogTitle,Divider,IconButton,MenuItem,Stack,TextField,Typography,useMediaQuery,useTheme} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import AddShoppingCartIcon from '@mui/icons-material/AddShoppingCart';
import PaymentsIcon from '@mui/icons-material/Payments';
import HistoryIcon from '@mui/icons-material/History';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import EditIcon from '@mui/icons-material/Edit';
import PhoneIcon from '@mui/icons-material/Phone';
import {useNavigate} from 'react-router-dom';
import {api,money} from '../api';
import {useAuth} from '../AuthContext';
import EntityDialog from './EntityDialog';
import TransactionDialog from './TransactionDialog';

type EntityType='customer'|'supplier';
type Props={open:boolean;type:EntityType;entity:any|null;onClose:()=>void;onChanged:()=>void};
const paymentLabel:Record<string,string>={cash:'Nakit',card:'Kart / POS',bank_transfer:'Havale / EFT',credit:'Vadeli',check:'Çek',promissory_note:'Senet'};
const accountTypeForMethod:Record<string,string>={cash:'cash',card:'pos',bank_transfer:'bank'};

export default function EntityQuickActions({open,type,entity,onClose,onChanged}:Props){
 const theme=useTheme();const fullScreen=useMediaQuery(theme.breakpoints.down('sm'));const nav=useNavigate();const {can}=useAuth();const canPayments=can('payments');
 const [data,setData]=useState<any|null>(null);const [loading,setLoading]=useState(false);const [error,setError]=useState('');
 const [transactionOpen,setTransactionOpen]=useState(false);const [paymentOpen,setPaymentOpen]=useState(false);const [editOpen,setEditOpen]=useState(false);
 const [documentId,setDocumentId]=useState<number|null>(null);
 const [amount,setAmount]=useState(0);const [date,setDate]=useState(new Date().toISOString().slice(0,10));const [note,setNote]=useState('');const [method,setMethod]=useState('cash');const [accountId,setAccountId]=useState<number|''>('');const [accounts,setAccounts]=useState<any[]>([]);const [paymentError,setPaymentError]=useState('');
 const endpoint=type==='customer'?'/customers':'/suppliers';
 const load=()=>{if(!entity?.id)return;setLoading(true);setError('');api.get(`${endpoint}/${entity.id}`).then(r=>setData(r.data)).catch(e=>setError(e.response?.data?.detail||'Cari detayları yüklenemedi.')).finally(()=>setLoading(false))};
 useEffect(()=>{if(!open)return;setData(null);setAmount(0);setDate(new Date().toISOString().slice(0,10));setNote('');setMethod('cash');setAccountId('');setPaymentError('');load();if(canPayments)api.get('/payments/accounts',{params:{active_only:true}}).then(r=>setAccounts(r.data)).catch(()=>setAccounts([]));else setAccounts([])},[open,entity?.id,type,canPayments]);
 const compatibleAccounts=useMemo(()=>{const expected=accountTypeForMethod[method];return expected?accounts.filter(a=>a.account_type===expected):[]},[accounts,method]);
 const summary=data?.summary||{};
 const documents=(data?.documents||[]).slice(0,6);
 const savePayment=async()=>{try{setPaymentError('');if(amount<=0)throw new Error('Geçerli bir tutar girin.');if(!date)throw new Error('İşlem tarihi zorunludur.');const expected=accountTypeForMethod[method];if(expected&&accountId){const selected=accounts.find(a=>a.id===Number(accountId));if(!selected||selected.account_type!==expected)throw new Error('Seçilen finans hesabı ödeme yöntemiyle uyumlu değil.')}await api.post('/payments',{entity_type:type,entity_id:entity.id,amount,payment_date:date,note:note||null,payment_method:method,account_id:accountId||null});setPaymentOpen(false);load();onChanged()}catch(e:any){setPaymentError(e.response?.data?.detail||e.message)}};
 const openDetail=()=>{onClose();nav(type==='customer'?`/musteriler/${entity.id}`:`/tedarikciler/${entity.id}`)};
 if(!entity)return null;
 return <>
  <Dialog open={open} onClose={onClose} fullScreen={fullScreen} maxWidth="md" fullWidth>
   <DialogTitle sx={{pr:6}}><Typography fontWeight={900}>{entity.name}</Typography><Typography variant="body2" color="text.secondary">Hızlı işlem merkezi</Typography><IconButton onClick={onClose} sx={{position:'absolute',right:12,top:12}}><CloseIcon/></IconButton></DialogTitle>
   <DialogContent dividers><Stack spacing={2}>
    {error&&<Alert severity="error">{error}</Alert>}{loading&&!data&&<Box textAlign="center" py={4}><CircularProgress/></Box>}
    {data&&<>
     <Stack direction={{xs:'column',sm:'row'}} spacing={1} flexWrap="wrap">
      <Chip color={Number(summary.current_balance||0)>0?'warning':'success'} label={`Mevcut Bakiye: ${money(summary.current_balance||0)}`}/>
      <Chip color={Number(summary.overdue_amount||0)>0?'error':'default'} label={`Vadesi Geçen: ${money(summary.overdue_amount||0)}`}/>
      <Chip variant="outlined" label={`Son işlem: ${summary.last_activity||'-'}`}/>
      {Number(summary.risk_limit||0)>0&&<Chip color={summary.risk_exceeded?'error':'success'} label={`Risk %${Math.round(Number(summary.risk_usage_percent||0))}`}/>} 
     </Stack>
     <Stack direction={{xs:'column',sm:'row'}} spacing={1} flexWrap="wrap">
      <Button variant="contained" color="success" startIcon={<AddShoppingCartIcon/>} onClick={()=>setTransactionOpen(true)}>{type==='customer'?'Yeni Satış':'Yeni Alış'}</Button>
      {canPayments&&<Button variant="contained" color="warning" startIcon={<PaymentsIcon/>} onClick={()=>setPaymentOpen(true)}>{type==='customer'?'Tahsilat':'Ödeme'}</Button>}
      <Button variant="outlined" startIcon={<OpenInNewIcon/>} onClick={openDetail}>Cari Kartını Aç</Button>
      <Button variant="outlined" startIcon={<EditIcon/>} onClick={()=>setEditOpen(true)}>Düzenle</Button>
      {entity.phone&&<Button variant="outlined" startIcon={<PhoneIcon/>} component="a" href={`tel:${entity.phone}`}>Ara</Button>}
     </Stack>
     <Divider/>
     <Stack direction="row" alignItems="center" spacing={1}><HistoryIcon color="action"/><Typography variant="h6" fontWeight={900}>{type==='customer'?'Son Satışlar':'Son Alışlar'}</Typography></Stack>
     {!documents.length?<Alert severity="info">Henüz belge bulunmuyor.</Alert>:<Stack spacing={1}>{documents.map((doc:any)=><Box key={doc.id} role="button" tabIndex={0} onClick={()=>setDocumentId(Number(doc.id))} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setDocumentId(Number(doc.id))}}} sx={{display:'grid',gridTemplateColumns:{xs:'1fr auto',sm:'1.2fr 1fr auto'},gap:1,p:1.2,border:'1px solid',borderColor:'divider',borderRadius:2,cursor:'pointer','&:hover':{bgcolor:'action.hover'}}}><Box><Typography fontWeight={800}>{doc.document_no||`#${doc.id}`}</Typography><Typography variant="caption" color="text.secondary">{doc.transaction_date}</Typography></Box><Typography color="text.secondary" sx={{display:{xs:'none',sm:'block'}}}>{doc.status||'-'}</Typography><Typography fontWeight={900}>{money(doc.final_total||0)}</Typography></Box>)}</Stack>}
    </>}
   </Stack></DialogContent>
   <DialogActions><Button onClick={onClose}>Kapat</Button></DialogActions>
  </Dialog>
  <TransactionDialog open={transactionOpen||documentId!==null} kind={type==='customer'?'sale':'purchase'} id={documentId} initialEntityId={entity.id} onClose={()=>{setTransactionOpen(false);setDocumentId(null)}} onSaved={()=>{setTransactionOpen(false);setDocumentId(null);load();onChanged()}}/>
  <EntityDialog open={editOpen} type={type} id={entity.id} onClose={()=>setEditOpen(false)} onSaved={()=>{setEditOpen(false);load();onChanged()}}/>
  {canPayments&&<Dialog open={paymentOpen} onClose={()=>setPaymentOpen(false)} maxWidth="sm" fullWidth>
   <DialogTitle>{type==='customer'?'Tahsilat Ekle':'Ödeme Ekle'} — {entity.name}</DialogTitle>
   <DialogContent><Stack spacing={1.5} mt={1}>{paymentError&&<Alert severity="error">{paymentError}</Alert>}<TextField label="Tutar" type="number" value={amount} onChange={e=>setAmount(Number(e.target.value))} inputProps={{min:0,step:0.01}}/><TextField label="Tarih" type="date" value={date} onChange={e=>setDate(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/><TextField select label="Ödeme Yöntemi" value={method} onChange={e=>{setMethod(e.target.value);setAccountId('')}}>{Object.entries(paymentLabel).map(([k,v])=><MenuItem key={k} value={k}>{v}</MenuItem>)}</TextField>{accountTypeForMethod[method]&&<TextField select label="Kasa / Banka / POS" value={accountId} onChange={e=>setAccountId(Number(e.target.value))}><MenuItem value="">Hesap seçme</MenuItem>{compatibleAccounts.map(a=><MenuItem key={a.id} value={a.id}>{a.name}</MenuItem>)}</TextField>}<TextField label="Not" multiline minRows={2} value={note} onChange={e=>setNote(e.target.value)}/></Stack></DialogContent>
   <DialogActions><Button onClick={()=>setPaymentOpen(false)}>Vazgeç</Button><Button variant="contained" onClick={savePayment}>Kaydet</Button></DialogActions>
  </Dialog>}
 </>;
}
