import {useCallback,useEffect,useMemo,useState} from 'react';
import {
 Alert,Box,Button,Card,CardContent,Chip,Dialog,DialogActions,
 DialogContent,DialogTitle,Divider,MenuItem,Paper,Stack,Tab,Tabs,TextField,Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import HistoryIcon from '@mui/icons-material/History';
import RedoIcon from '@mui/icons-material/Redo';
import UndoIcon from '@mui/icons-material/Undo';

import {api,errorDetail,money} from '../api';
import {useAuth} from '../AuthContext';
import type {components} from '../api/types.gen';
import ResponsiveTable from '../components/ResponsiveTable';

type Allocation=components['schemas']['AllocationView'];
type MutationResult=components['schemas']['AllocationMutationResult'];
type Reconciliation=components['schemas']['ReconciliationView'];

type PaymentRow={
 id:number;entity_type:string;entity_id:number;entity_name:string|null;amount:string|number;
 payment_date:string;reference_type:string|null;reference_id:number|null;
};
type ReceivableRow={
 id:number;customer_id:number;customer_name:string;document_no:string|null;due_date:string|null;
 total:string|number;paid:string|number;outstanding:string|number;status:string;
};
type MutationKind='manual'|'reversal'|'reallocation';
type MutationState={kind:MutationKind;allocation?:Allocation};
type MutationSummary={
 result:MutationResult;
 payment:PaymentRow|null;
 order:ReceivableRow|null;
};

const TODAY=new Date().toISOString().slice(0,10);
const STATUS_LABEL:Record<string,string>={
 active:'Aktif',partial:'Kısmen Geri Alındı',reversed:'Tamamen Geri Alındı',
 reversal:'Geri Alma Kaydı',not_effective:'Seçilen Tarihte Etkin Değil',
};
const STATUS_COLOR:Record<string,'success'|'warning'|'default'|'info'>={
 active:'success',partial:'warning',reversed:'default',reversal:'info',not_effective:'default',
};
const TYPE_LABEL:Record<string,string>={
 manual:'Manuel',fifo:'FIFO',document_create:'Belge Oluşturma',provider:'Sağlayıcı',
};
const OPERATION_LABEL:Record<string,string>={
 manual_allocation:'Manuel tahsis',reversal:'Geri alma',reallocation:'Yeniden tahsis',
};

const isPositiveDecimal=(value:string)=>{
 const text=value.trim();
 if(!/^\+?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text))return false;
 return /[1-9]/.test(text);
};

const idempotencyKey=()=>{
 if(typeof crypto!=='undefined'&&typeof crypto.randomUUID==='function')return crypto.randomUUID();
 return `allocation-ui-${Date.now()}-${Math.random().toString(36).slice(2)}`;
};

function MutationDialog({
 state,payments,reconciliation,engineDisabled,onEngineDisabled,onClose,onSuccess,
}:{
 state:MutationState|null;
 payments:PaymentRow[];
 reconciliation:Reconciliation[];
 engineDisabled:boolean;
 onEngineDisabled:()=>void;
 onClose:()=>void;
 onSuccess:(result:MutationResult)=>Promise<void>;
}){
 const [paymentId,setPaymentId]=useState('');
 const [orderId,setOrderId]=useState('');
 const [amount,setAmount]=useState('');
 const [effectiveDate,setEffectiveDate]=useState(TODAY);
 const [reason,setReason]=useState('');
 const [confirming,setConfirming]=useState(false);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const [requestKey,setRequestKey]=useState('');

 useEffect(()=>{
  if(!state)return;
  setPaymentId(state.allocation?String(state.allocation.payment_id):'');
  setOrderId('');
  setAmount(state.allocation?state.allocation.net_amount:'');
  setEffectiveDate(TODAY);setReason('');setConfirming(false);setBusy(false);setError('');
  setRequestKey(idempotencyKey());
 },[state]);

 if(!state)return null;
 const source=state.allocation;
 const title=state.kind==='manual'?'Manuel Tahsis'
  :state.kind==='reversal'?'Tahsis Geri Alma':'Yeniden Tahsis';
 const target=Number(orderId)||null;
 const targetRow=reconciliation.find(row=>row.order_id===target);
 const targetQuarantined=targetRow?.status==='quarantined';
 const reasonValid=reason.trim().length>=5;
 const amountValid=isPositiveDecimal(amount);
 const selectionValid=state.kind==='manual'
  ?Number(paymentId)>0&&Boolean(target)
  :state.kind==='reallocation'?Boolean(target)&&target!==source?.order_id
  :Boolean(source);
 const valid=reasonValid&&amountValid&&selectionValid&&!targetQuarantined;

 const next=()=>{
  setError('');
  if(engineDisabled)return;
  if(!amountValid){setError('Sıfırdan büyük bir tutar girin.');return}
  if(!reasonValid){setError('Gerekçe en az 5 karakter olmalıdır.');return}
  if(!selectionValid){
   setError(state.kind==='reallocation'
    ?'Kaynak belgeden farklı bir hedef belge seçin.'
    :'Ödeme ve hedef belge seçimi zorunludur.');
   return;
  }
  if(targetQuarantined){setError('Karantinadaki belge tahsis hedefi olarak seçilemez.');return}
  setConfirming(true);
 };

 const submit=async()=>{
  if(!valid||engineDisabled)return;
  setBusy(true);setError('');
  try{
   let url='/payment-allocations/manual';
   let payload:Record<string,unknown>={
    payment_id:Number(paymentId),order_id:Number(orderId),amount,
    effective_date:effectiveDate,note:reason.trim(),
   };
   if(state.kind==='reversal'){
    url=`/payment-allocations/${source?.id}/reversal`;
    payload={amount,effective_date:effectiveDate,note:reason.trim()};
   }else if(state.kind==='reallocation'){
    url='/payment-allocations/reallocation';
    payload={
     allocation_id:source?.id,target_order_id:Number(orderId),amount,
     effective_date:effectiveDate,note:reason.trim(),
    };
   }
   const response=await api.post<MutationResult>(url,payload,{
    headers:{'Idempotency-Key':requestKey},
   });
   await onSuccess(response.data);
  }catch(requestError){
   const detail=errorDetail(requestError,'Tahsis işlemi tamamlanamadı.');
   const status=(requestError as {response?:{status?:number}})?.response?.status;
   const engineOff=status===409&&detail.toLocaleLowerCase('tr-TR').includes('motoru devre dışı');
   if(engineOff)onEngineDisabled();
   setError(engineOff
    ?'Tahsis motoru devre dışı. Okuma ve mutabakat ekranları çalışmaya devam eder; yazma işlemi yapılmadı.'
    :detail);
   if(!engineOff)setConfirming(false);
  }finally{setBusy(false)}
 };

 // V2c mutabakat raporu belge dışı (tahakkuk/XOR/yetim) satırlar da içerir;
 // hedef belge listesinde yalnız gerçek sipariş satırları gösterilir.
 const orderOptions=reconciliation.filter(row=>
  row.order_id!==null&&row.order_id!==undefined&&
  (state.kind!=='reallocation'||row.order_id!==source?.order_id)
 ).map(row=>({...row,order_id:row.order_id as number}));
 return <Dialog open onClose={busy?undefined:onClose} fullWidth maxWidth="sm">
  <DialogTitle>{confirming?'İşlemi Onaylayın':title}</DialogTitle>
  <DialogContent>
   <Stack spacing={2} mt={1}>
    {error&&<Alert severity={/motoru devre dışı/i.test(error)?'warning':'error'}>{error}</Alert>}
    {confirming?<>
     <Alert severity="warning">
      Bu işlem tahsis defterine kalıcı kayıt ekler. Bilgileri kontrol edip onaylayın.
     </Alert>
     <Paper variant="outlined" sx={{p:2}}>
      <Stack spacing={1}>
       <Typography><b>İşlem:</b> {title}</Typography>
       <Typography><b>Ödeme:</b> #{state.kind==='manual'?paymentId:source?.payment_id}</Typography>
       {state.kind!=='reversal'&&<Typography><b>Hedef belge:</b> #{orderId}</Typography>}
       {source&&<Typography><b>Kaynak tahsis:</b> #{source.id} · Belge #{source.order_id}</Typography>}
       <Typography><b>Tutar:</b> {money(amount)}</Typography>
       <Typography><b>Etkin tarih:</b> {effectiveDate}</Typography>
       <Typography><b>Gerekçe:</b> {reason.trim()}</Typography>
      </Stack>
     </Paper>
    </>:<>
     {source&&<Alert severity="info">
      Tahsis #{source.id} · Ödeme #{source.payment_id} · Belge #{source.order_id} ·
      Sunucu neti {money(source.net_amount)}
     </Alert>}
     {state.kind==='manual'&&<TextField select required label="Müşteri Tahsilatı"
      value={paymentId} onChange={event=>setPaymentId(event.target.value)}>
      {payments.length===0&&<MenuItem disabled value="">Uygun müşteri tahsilatı yok</MenuItem>}
      {payments.map(payment=><MenuItem key={payment.id} value={payment.id}>
       #{payment.id} · {payment.entity_name||`Cari #${payment.entity_id}`} · {money(payment.amount)} · {payment.payment_date}
      </MenuItem>)}
     </TextField>}
     {state.kind!=='reversal'&&<TextField select required label="Hedef Alacak Belgesi"
      value={orderId} onChange={event=>setOrderId(event.target.value)}
      helperText={targetQuarantined?'Bu belge karantinada; hedef olarak kullanılamaz.':'Yalnız mutabakat raporundaki belgeler listelenir.'}
      error={targetQuarantined}>
      {orderOptions.length===0&&<MenuItem disabled value="">Uygun hedef belge yok</MenuItem>}
      {orderOptions.map(row=><MenuItem key={row.order_id} value={row.order_id}
       disabled={row.status==='quarantined'}>
       {row.document_no||`Belge #${row.order_id}`} · #{row.order_id}
       {row.status==='quarantined'?' · KARANTİNA':''}
      </MenuItem>)}
     </TextField>}
     <TextField required label={state.kind==='reversal'?'Geri Alınacak Tutar':'Tahsis Tutarı'}
      type="number" value={amount} onChange={event=>setAmount(event.target.value)}
      inputProps={{min:'0.01',step:'0.01'}}/>
     <TextField required label="Etkin Tarih" type="date" value={effectiveDate}
      onChange={event=>setEffectiveDate(event.target.value)}
      slotProps={{inputLabel:{shrink:true}}}/>
     <TextField required label="İşlem Gerekçesi" multiline minRows={3}
      value={reason} onChange={event=>setReason(event.target.value)}
      helperText="Zorunlu; en az 5 karakter. Tahsis geçmişinde saklanır."/>
    </>}
   </Stack>
  </DialogContent>
  <DialogActions>
   {confirming&&<Button disabled={busy} onClick={()=>setConfirming(false)}>Geri Dön</Button>}
   <Button disabled={busy} onClick={onClose}>Vazgeç</Button>
   {confirming
    ?<Button variant="contained" color={state.kind==='reversal'?'warning':'primary'}
      disabled={engineDisabled||busy||!valid} onClick={submit}>
      {busy?'Uygulanıyor...':'Onayla ve Uygula'}
     </Button>
    :<Button variant="contained" disabled={engineDisabled||!valid} onClick={next}>Onaya Geç</Button>}
  </DialogActions>
 </Dialog>;
}

export default function PaymentAllocations(){
 const {can}=useAuth();
 const canFinance=can('finance');
 const [tab,setTab]=useState(0);
 const [resourceType,setResourceType]=useState<'payment'|'order'>('payment');
 const [resourceId,setResourceId]=useState('');
 const [asOf,setAsOf]=useState('');
 const [allocations,setAllocations]=useState<Allocation[]>([]);
 const [searched,setSearched]=useState(false);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const [financeError,setFinanceError]=useState('');
 const [reconciliation,setReconciliation]=useState<Reconciliation[]>([]);
 const [reconciliationFilter,setReconciliationFilter]=useState('all');
 const [payments,setPayments]=useState<PaymentRow[]>([]);
 const [mutation,setMutation]=useState<MutationState|null>(null);
 const [summary,setSummary]=useState<MutationSummary|null>(null);
 const [engineDisabled,setEngineDisabled]=useState(false);
 // Motor kapalıyken tahsis geçmişi HİÇBİR satır üretemez. Bayrak okunmazsa
 // arayüz "kayıt yok" ile "özellik kapalı"yı aynı boş tabloyla gösterir.
 // İKİ AYRI KAVRAM. `engineDisabled` yazma kilidi (409 yolundan gelir ve okuma
 // görünümünü KAPATMAZ). `historyUnavailable` ise yapılandırma gerçeği: motor
 // kapalıyken tahsis defteri hiç tutulmaz, dolayısıyla gösterilecek bir geçmiş
 // YOKTUR ve boş tablo yerine açık bir devre dışı durumu çizilir.
 const [historyUnavailable,setHistoryUnavailable]=useState(false);
 useEffect(()=>{void api.get<{enabled:boolean}>('/payment-allocations/engine-state')
  .then(response=>setHistoryUnavailable(!response.data.enabled))
  .catch(()=>{/* sinyal alınamazsa mevcut davranış korunur */})},[]);

 const fetchFinanceData=useCallback(async()=>{
  if(!canFinance)return {payments:[] as PaymentRow[],receivables:[] as ReceivableRow[],reconciliation:[] as Reconciliation[]};
  setFinanceError('');
  try{
   const [reportResponse,paymentResponse,receivableResponse]=await Promise.all([
    api.get<Reconciliation[]>('/payment-allocations/reconciliation'),
    api.get<PaymentRow[]>('/payments',{params:{entity_type:'customer',limit:3000}}),
    api.get<{receivables:ReceivableRow[]}>('/receivables',{params:{term:'harman',limit:2000}}),
   ]);
   const next={
    reconciliation:reportResponse.data||[],
    payments:(paymentResponse.data||[]).filter(row=>row.entity_type==='customer'),
    receivables:receivableResponse.data.receivables||[],
   };
   setReconciliation(next.reconciliation);setPayments(next.payments);
   return next;
  }catch(requestError){
   setFinanceError(errorDetail(requestError,'Tahsis yönetim verileri yüklenemedi.'));
   return {payments:[] as PaymentRow[],receivables:[] as ReceivableRow[],reconciliation:[] as Reconciliation[]};
  }
 },[canFinance]);

 useEffect(()=>{void fetchFinanceData()},[fetchFinanceData]);

 const fetchAllocations=useCallback(async(type:'payment'|'order',id:number,dateValue:string)=>{
  setLoading(true);setError('');setSearched(true);
  try{
   const segment=type==='payment'?'payments':'orders';
   const response=await api.get<Allocation[]>(`/payment-allocations/${segment}/${id}`,{
    params:{as_of:dateValue||undefined},
   });
   setAllocations(response.data||[]);
  }catch(requestError){
   setAllocations([]);
   setError(errorDetail(requestError,'Tahsis geçmişi yüklenemedi.'));
  }finally{setLoading(false)}
 },[]);

 const search=()=>{
  const id=Number(resourceId);
  if(!Number.isInteger(id)||id<=0){setError('Geçerli bir ödeme veya sipariş numarası girin.');return}
  void fetchAllocations(resourceType,id,asOf);
 };

 const handleMutationSuccess=async(result:MutationResult)=>{
  const latest=await fetchFinanceData();
  const payment=latest.payments.find(row=>row.id===result.payment_id)||null;
  const order=latest.receivables.find(row=>row.id===result.order_id)||null;
  setMutation(null);setSummary({result,payment,order});
  setResourceType('payment');setResourceId(String(result.payment_id));
  await fetchAllocations('payment',result.payment_id,asOf);
 };

 const filteredReconciliation=useMemo(()=>
  reconciliationFilter==='all'?reconciliation
   :reconciliation.filter(row=>row.status===reconciliationFilter),
 [reconciliation,reconciliationFilter]);
 const quarantinedCount=reconciliation.filter(row=>row.status==='quarantined').length;
 const activeRows=allocations.filter(row=>
  row.reversal_of_allocation_id===null&&isPositiveDecimal(row.net_amount)
 );
 const allocationColumns:GridColDef[]=[
  {field:'id',headerName:'Kayıt',width:190,renderCell:({row})=><Box><Typography variant="body2">#{row.id}</Typography>{row.reversal_of_allocation_id&&<Typography display="block" variant="caption" color="text.secondary">#{row.reversal_of_allocation_id} kaydının geri alması</Typography>}</Box>},
  {field:'effective_date',headerName:'Etkin Tarih',width:125},
  {field:'payment_id',headerName:'Ödeme',width:100,valueFormatter:value=>`#${value}`},
  {field:'order_id',headerName:'Belge',width:100,valueFormatter:value=>`#${value}`},
  {field:'allocation_type',headerName:'Tür',width:145,valueFormatter:value=>TYPE_LABEL[value]||value},
  {field:'amount',headerName:'Tutar',width:130,valueFormatter:value=>money(value)},
  {field:'net_amount',headerName:'Net',width:130,valueFormatter:value=>money(value)},
  {field:'status',headerName:'Durum',width:175,renderCell:({row})=><Chip size="small" color={STATUS_COLOR[row.status]||'default'} label={STATUS_LABEL[row.status]||row.status}/>},
  {field:'note',headerName:'Gerekçe / Not',flex:1,minWidth:190,valueFormatter:value=>value||'—'},
 ];
 const reconciliationColumns:GridColDef[]=[
  {field:'document_no',headerName:'Belge',minWidth:170,flex:1,valueFormatter:(value,row)=>value||`#${row.order_id}`},
  {field:'customer_id',headerName:'Müşteri',width:105,valueFormatter:value=>`#${value}`},
  {field:'stored_paid_amount',headerName:'Kayıtlı Ödenen',width:150,valueFormatter:value=>money(value)},
  {field:'linked_payment_total',headerName:'Bağlı Ödeme',width:140,valueFormatter:value=>money(value)},
  {field:'unlinked_customer_payment_total',headerName:'Bağlantısız Tahsilat',width:175,valueFormatter:value=>money(value)},
  {field:'active_allocation_total',headerName:'Aktif Tahsis',width:135,valueFormatter:value=>money(value)},
  {field:'candidate_residual',headerName:'Aday Legacy Fark',width:165,valueFormatter:value=>money(value)},
  {field:'document_allocation_excess',headerName:'Belge Aşımı',width:135,valueFormatter:value=>money(value)},
  {field:'payment_allocation_excess',headerName:'Ödeme Aşımı',width:135,valueFormatter:value=>money(value)},
  {field:'status',headerName:'Durum / Kanıt',width:270,renderCell:({row})=><Stack justifyContent="center" height="100%"><Chip size="small" color={row.status==='quarantined'?'warning':'success'} label={row.status==='quarantined'?'Karantina':'Doğrulandı'}/><Typography variant="caption">{row.reason}</Typography><Typography variant="caption" color="text.secondary">{row.evidence_source} · {row.confidence}</Typography></Stack>},
 ];

 return <Stack spacing={2} sx={{'@media (max-width:899.95px)':{'& .MuiButton-root':{minHeight:44},'& .MuiInputBase-input':{minHeight:44,boxSizing:'border-box'}}}}>
  <Stack direction={{xs:'column',md:'row'}} justifyContent="space-between" gap={1}>
   <Box>
    <Typography variant="h4" fontWeight={900}>Tahsis Defteri</Typography>
    <Typography color="text.secondary">
     Tahsilatların alacak belgelerine dağılımı, geri alma ve yeniden tahsis geçmişi.
    </Typography>
   </Box>
   {canFinance&&<Button variant="contained" startIcon={<AccountBalanceWalletIcon/>}
    disabled={engineDisabled}
    onClick={()=>setMutation({kind:'manual'})}>Manuel Tahsis</Button>}
  </Stack>

  {engineDisabled&&<Alert severity="warning">
   Tahsis motoru devre dışı. Okuma ve mutabakat ekranları çalışmaya devam eder;
   yazma kontrolleri kilitlendi.
  </Alert>}

  <Tabs value={tab} onChange={(_,value)=>setTab(value)}>
   <Tab icon={<HistoryIcon/>} iconPosition="start" label="Tahsis Görünümü"/>
   {canFinance&&<Tab label={`Mutabakat${quarantinedCount?` (${quarantinedCount})`:''}`}/>}
  </Tabs>

  {tab===0&&<>
   <Card variant="outlined"><CardContent>
    <Stack direction={{xs:'column',md:'row'}} spacing={1.2} alignItems={{md:'center'}}>
     <TextField select size="small" label="Kaynak" value={resourceType}
      onChange={event=>setResourceType(event.target.value as 'payment'|'order')} sx={{minWidth:170}}>
      <MenuItem value="payment">Ödeme / Tahsilat</MenuItem>
      <MenuItem value="order">Satış Belgesi</MenuItem>
     </TextField>
     <TextField size="small" label={resourceType==='payment'?'Ödeme No':'Sipariş No'}
      type="number" value={resourceId} onChange={event=>setResourceId(event.target.value)}
      inputProps={{min:1}} sx={{minWidth:170}}/>
     <TextField size="small" label="Tarih İtibarıyla" type="date" value={asOf}
      onChange={event=>setAsOf(event.target.value)}
      slotProps={{inputLabel:{shrink:true}}} helperText="Boşsa bugün"/>
     <Button variant="outlined" onClick={search} disabled={loading}>Geçmişi Getir</Button>
    </Stack>
   </CardContent></Card>

   {error&&<Alert severity="error">{error}</Alert>}
   {historyUnavailable?<Paper variant="outlined" sx={{p:3}} data-testid="payment-allocations-history-disabled">
    <Typography fontWeight={700} textAlign="center">Tahsis geçmişi kapalı</Typography>
    <Typography color="text.secondary" textAlign="center" mt={1}>
     Tahsis motoru devre dışı olduğu için tahsis defteri tutulmuyor. Bu bir
     &laquo;kayıt yok&raquo; durumu DEĞİLDİR: özellik açılana kadar geçmiş oluşmaz.
    </Typography>
   </Paper>:!searched&&!loading?<Paper variant="outlined" sx={{p:3}}>
    <Typography color="text.secondary" textAlign="center">
     Ödeme veya satış belgesi numarasıyla tahsis geçmişini açın.
    </Typography>
   </Paper>:<Box data-testid="payment-allocations-history-data-surface">
    <ResponsiveTable
     rows={allocations}
     columns={allocationColumns}
     loading={loading}
     cardTitle={row=>`Tahsis #${row.id}`}
     cardSubtitle={row=>row.effective_date}
     cardFields={[
      {label:'Ödeme / Belge',value:row=>`#${row.payment_id} / #${row.order_id}`},
      {label:'Tür',value:row=>TYPE_LABEL[row.allocation_type]||row.allocation_type},
      {label:'Tutar',value:row=>money(row.amount)},
      {label:'Net',value:row=>money(row.net_amount)},
      {label:'Durum',value:row=>STATUS_LABEL[row.status]||row.status},
      {label:'Gerekçe / Not',value:row=>row.note||'—'},
      {label:'Geri alma',value:row=>row.reversal_of_allocation_id?`#${row.reversal_of_allocation_id} kaydının geri alması`:'—'},
     ]}
     cardActions={canFinance?[
      {label:'Geri Al',ariaLabel:()=> 'Geri Al',icon:<UndoIcon/>,color:'warning',hidden:row=>row.reversal_of_allocation_id!==null||!isPositiveDecimal(row.net_amount),disabled:()=>engineDisabled,onClick:row=>setMutation({kind:'reversal',allocation:row})},
      {label:'Yeniden Tahsis',ariaLabel:()=> 'Yeniden Tahsis',icon:<RedoIcon/>,hidden:row=>row.reversal_of_allocation_id!==null||!isPositiveDecimal(row.net_amount),disabled:()=>engineDisabled,onClick:row=>setMutation({kind:'reallocation',allocation:row})},
     ]:undefined}
     cardActionsOnDesktop
     getRowId={row=>row.id}
     desktopRowHeight={72}
    />
   </Box>}
   {searched&&allocations.length>0&&<Typography variant="caption" color="text.secondary">
    {activeRows.length} net-açık normal tahsis var. Tutar ve net değerler sunucunun Decimal yanıtıdır;
    tarayıcı bakiye hesaplamaz.
   </Typography>}
  </>}

  {canFinance&&tab===1&&<>
   {financeError&&<Alert severity="error">{financeError}</Alert>}
   <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
    <TextField select size="small" label="Mutabakat Durumu" value={reconciliationFilter}
     onChange={event=>setReconciliationFilter(event.target.value)} sx={{minWidth:220}}>
     <MenuItem value="all">Tümü</MenuItem><MenuItem value="confirmed">Doğrulandı</MenuItem>
     <MenuItem value="quarantined">Karantina</MenuItem>
    </TextField>
    <Chip color={quarantinedCount?'warning':'success'}
     label={`${quarantinedCount} karantina kaydı`}/>
   </Stack>
   <Alert severity="info">
    Bu rapor salt okunurdur. Karantinadaki belgeler manuel veya yeniden tahsis hedefi olarak seçilemez.
   </Alert>
   <Box data-testid="payment-allocations-reconciliation-data-surface">
    <ResponsiveTable
     rows={filteredReconciliation}
     columns={reconciliationColumns}
     cardTitle={row=>row.document_no||`Belge #${row.order_id}`}
     cardSubtitle={row=>`Sipariş #${row.order_id} · Müşteri #${row.customer_id}`}
     cardFields={[
      {label:'Kayıtlı ödenen',value:row=>money(row.stored_paid_amount)},
      {label:'Bağlı ödeme',value:row=>money(row.linked_payment_total)},
      {label:'Bağlantısız tahsilat',value:row=>money(row.unlinked_customer_payment_total)},
      {label:'Aktif tahsis',value:row=>money(row.active_allocation_total)},
      {label:'Aday legacy fark',value:row=>money(row.candidate_residual)},
      {label:'Durum',value:row=>row.status==='quarantined'?'Karantina':'Doğrulandı'},
      {label:'Gerekçe',value:row=>row.reason},
      {label:'Kanıt',value:row=>`${row.evidence_source} · ${row.confidence}`},
     ]}
     getRowId={row=>row.order_id}
     desktopRowHeight={84}
    />
   </Box>
  </>}

  <MutationDialog state={mutation} payments={payments} reconciliation={reconciliation}
   engineDisabled={engineDisabled} onEngineDisabled={()=>setEngineDisabled(true)}
   onClose={()=>setMutation(null)} onSuccess={handleMutationSuccess}/>

  <Dialog open={Boolean(summary)} onClose={()=>setSummary(null)} fullWidth maxWidth="sm">
   <DialogTitle>İşlem Tamamlandı</DialogTitle>
   <DialogContent>
    {summary&&<Stack spacing={2} mt={1}>
     <Alert severity="success">
      {OPERATION_LABEL[summary.result.operation]||summary.result.operation} tahsis defterine kaydedildi.
     </Alert>
     <Card variant="outlined"><CardContent>
      <Typography variant="overline">Sunucu İşlem Sonucu</Typography>
      <Typography>İşlem tutarı: <b>{money(summary.result.amount)}</b></Typography>
      <Typography>Tahsis neti: <b>{money(summary.result.net_amount)}</b></Typography>
      <Typography>Ödeme: #{summary.result.payment_id} · Belge: #{summary.result.order_id}</Typography>
     </CardContent></Card>
     <Divider/>
     <Typography fontWeight={900}>İşlem Sonrası Toplamlar</Typography>
     {summary.payment?<Typography>
      Ödeme toplamı: <b>{money(summary.payment.amount)}</b> · Tarih: {summary.payment.payment_date}
     </Typography>:<Typography color="text.secondary">Ödeme toplamı listede bulunamadı.</Typography>}
     {summary.order?<>
      <Typography>Belge toplamı: <b>{money(summary.order.total)}</b></Typography>
      <Typography>Belgenin açık kalanı: <b>{money(summary.order.outstanding)}</b></Typography>
     </>:<Typography color="text.secondary">Belge toplamı açık alacak listesinde bulunamadı.</Typography>}
     <Typography variant="caption" color="text.secondary">
      Tüm toplamlar işlem sonrasında sunucudan yeniden okunmuştur.
     </Typography>
    </Stack>}
   </DialogContent>
   <DialogActions><Button variant="contained" onClick={()=>setSummary(null)}>Kapat</Button></DialogActions>
  </Dialog>
 </Stack>;
}
