import React from 'react';
import {useEffect,useMemo,useState} from 'react';
import NotificationConsentPanel from '../components/NotificationConsentPanel';
import {
  Alert,Avatar,Box,Button,Card,CardContent,Checkbox,Chip,Dialog,DialogActions,
  DialogContent,DialogTitle,Divider,Grid,IconButton,LinearProgress,MenuItem,Skeleton,Stack,Tab,Tabs,
  TextField,Tooltip,Typography
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AddShoppingCartIcon from '@mui/icons-material/AddShoppingCart';
import PaymentsIcon from '@mui/icons-material/Payments';
import EditIcon from '@mui/icons-material/Edit';
import PhoneIcon from '@mui/icons-material/Phone';
import WhatsAppIcon from '@mui/icons-material/WhatsApp';
import EmailIcon from '@mui/icons-material/Email';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import Inventory2Icon from '@mui/icons-material/Inventory2';
import NotesIcon from '@mui/icons-material/Notes';
import ContactsIcon from '@mui/icons-material/Contacts';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import DeleteIcon from '@mui/icons-material/Delete';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import AddTaskIcon from '@mui/icons-material/AddTask';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import {useNavigate,useParams} from 'react-router-dom';
import {api,errorDetail,money} from '../api';
import {useAuth} from '../AuthContext';
import {headerDarkActionVariant} from '../theme';
import EntityDialog from '../components/EntityDialog';
import TransactionDialog from '../components/TransactionDialog';
import EntityLedgerOverview from '../components/EntityLedgerOverview';
import EntityDocumentList from '../components/EntityDocumentList';
import EntityStatementDialog from '../components/EntityStatementDialog';
import SupplierPriceHistory,{SupplierPriceHistoryTab} from '../components/SupplierPriceHistory';
import CustomerMachines,{CustomerMachinesTab} from '../components/CustomerMachines';

type EntityType='customer'|'supplier';
const statusLabel:Record<string,string>={draft:'Taslak',pending:'Onay Bekliyor',approved:'Onaylandı',completed:'Tamamlandı',cancelled:'İptal'};
const paymentLabel:Record<string,string>={cash:'Nakit',card:'Kart / POS',bank_transfer:'Havale / EFT',credit:'Vadeli',check:'Çek',promissory_note:'Senet'};
const accountTypeForMethod:Record<string,string>={cash:'cash',card:'pos',bank_transfer:'bank'};
const priorityColor:Record<string,'default'|'warning'|'error'>={low:'default',normal:'warning',high:'error'};
const metadataSx={fontSize:14,lineHeight:1.55,color:'text.secondary'} as const;
const interactiveRowSx={
  minHeight:54,
  px:1,
  py:1.1,
  borderBottom:'1px solid',
  borderColor:'divider',
  borderRadius:1,
  cursor:'pointer',
  transition:'background-color .15s ease',
  '&:hover':{bgcolor:'action.hover'},
  '&:focus-visible':{outline:'2px solid',outlineColor:'primary.main',outlineOffset:2},
} as const;

export default function EntityDetail({type}:{type:EntityType}){
  const {id}=useParams(); const entityId=Number(id); const nav=useNavigate(); const {can}=useAuth(); const canPayments=can('payments'); const canViewSupplierPrices=can('purchases');
  const [data,setData]=useState<any|null>(null); const [error,setError]=useState(''); const [tab,setTab]=useState(0);
  const [editOpen,setEditOpen]=useState(false); const [transactionOpen,setTransactionOpen]=useState(false); const [statementOpen,setStatementOpen]=useState(false);
  const [documentId,setDocumentId]=useState<number|null>(null);
  const [paymentOpen,setPaymentOpen]=useState(false); const [contactOpen,setContactOpen]=useState(false); const [taskOpen,setTaskOpen]=useState(false);
  const [paymentAmount,setPaymentAmount]=useState(0); const [paymentDate,setPaymentDate]=useState(new Date().toISOString().slice(0,10));
  const [paymentNote,setPaymentNote]=useState(''); const [paymentMethod,setPaymentMethod]=useState('cash'); const [paymentAccountId,setPaymentAccountId]=useState<number|''>('');
  const [accounts,setAccounts]=useState<any[]>([]); const [formError,setFormError]=useState(''); const [newNote,setNewNote]=useState('');
  const [contact,setContact]=useState({name:'',role:'',phone:'',email:'',is_primary:false,notes:''});
  const [task,setTask]=useState({title:'',due_date:'',priority:'normal',assigned_to:'',notes:''});
  const endpoint=type==='customer'?`/customers/${id}`:`/suppliers/${id}`;
  const load=()=>{setData(null);setError('');api.get(endpoint).then(r=>setData(r.data)).catch(e=>setError(errorDetail(e,'Cari bilgileri yüklenemedi.')))};
  useEffect(load,[endpoint]);
  useEffect(()=>{if(canPayments)api.get('/payments/accounts',{params:{active_only:true}}).then(r=>setAccounts(r.data)).catch(()=>setAccounts([]));else setAccounts([])},[canPayments]);

  const timeline=useMemo(()=>{
    if(!data)return[];
    const docs=(data.documents||[]).map((x:any)=>({date:x.transaction_date,type:'document',title:`${type==='customer'?'Satış':'Alış'} ${x.document_no||'#'+x.id}`,amount:Number(x.final_total||0),id:x.id,status:x.status}));
    const pays=(data.payments||[]).map((x:any)=>({date:x.payment_date,type:'payment',title:type==='customer'?'Tahsilat':'Ödeme',amount:-Number(x.amount||0),id:x.id,note:x.note}));
    const notes=(data.notes||[]).map((x:any)=>({date:String(x.created_at||'').slice(0,10),type:'note',title:'Not eklendi',amount:0,id:x.id,note:x.note}));
    const tasks=(data.tasks||[]).map((x:any)=>({date:x.due_date||String(x.created_at||'').slice(0,10),type:'task',title:`Görev: ${x.title}`,amount:0,id:x.id,note:x.status==='completed'?'Tamamlandı':'Açık'}));
    return [...docs,...pays,...notes,...tasks].sort((a,b)=>String(b.date).localeCompare(String(a.date))||b.id-a.id);
  },[data,type]);

  if(error)return <Alert severity="error">{error}</Alert>;
  // Çark "bekle" der, iskelet "geliyor ve şöyle görünecek" der. Ayrıca yer
  // baştan ayrıldığı için veri gelince içerik zıplamaz.
  if(!data)return <Stack spacing={2}>
   <Skeleton variant="rounded" height={96}/>
   <Grid container spacing={1.5}>
    {[0,1,2,3].map(i=><Grid key={i} size={{xs:12,sm:6,lg:3}}><Skeleton variant="rounded" height={108}/></Grid>)}
   </Grid>
   <Skeleton variant="rounded" height={340}/>
  </Stack>;
  const e=data.entity,s=data.summary;
  const riskPercent=Math.max(0,Math.min(Number(s.risk_usage_percent||0),100));
  const openDocument=(doc:any)=>setDocumentId(Number(doc.id));
  const openTimelineItem=(item:any)=>{
    if(item.type==='document'){openDocument(item);return}
    if(item.type==='payment'){setTab(2);return}
    if(item.type==='task'){setTab(6);return}
    if(item.type==='note')setTab(7);
  };
  const openProduct=(productId:number)=>nav(`/urunler/${productId}`,{state:{entityId,type}});
  // Servis bedelinin içeriği kendi kaydında değil, onu doğuran iş emrindedir.
  // Gecikme bedelinin arkasında iş emri yoktur; o satır tıklanabilir olmamalı.
  // Bu id'ler receivable_charge_documents tablosuna aittir, belge/transaction
  // id'leriyle aynı uzayda DEĞİLDİR — openDocument ile açılamazlar.
  const chargeOpenable=(charge:any)=>charge.charge_type==='service_fee'&&Boolean(charge.work_order_id);
  const openCharge=(charge:any)=>{if(chargeOpenable(charge))nav(`/is-emirleri/${charge.work_order_id}`)};
  const openPayment=()=>{setPaymentAmount(0);setPaymentDate(new Date().toISOString().slice(0,10));setPaymentNote('');setPaymentMethod('cash');setPaymentAccountId('');setFormError('');setPaymentOpen(true)};
  const savePayment=async()=>{try{setFormError('');if(paymentAmount<=0)throw new Error('Geçerli tutar girin.');await api.post('/payments',{entity_type:type,entity_id:entityId,amount:paymentAmount,payment_date:paymentDate,note:paymentNote||null,payment_method:paymentMethod,account_id:paymentAccountId||null});setPaymentOpen(false);load()}catch(err:unknown){setFormError(errorDetail(err,'Tahsilat veya ödeme kaydedilemedi.'))}};
  const saveNote=async()=>{try{setFormError('');if(!newNote.trim())throw new Error('Not boş bırakılamaz.');await api.post(`${endpoint}/notes`,{note:newNote});setNewNote('');load()}catch(err:unknown){setFormError(errorDetail(err,'Not kaydedilemedi.'))}};
  const saveContact=async()=>{try{setFormError('');await api.post(`${endpoint}/contacts`,contact);setContactOpen(false);setContact({name:'',role:'',phone:'',email:'',is_primary:false,notes:''});load()}catch(err:unknown){setFormError(errorDetail(err,'Yetkili kişi kaydedilemedi.'))}};
  const saveTask=async()=>{try{setFormError('');await api.post(`${endpoint}/tasks`,task);setTaskOpen(false);setTask({title:'',due_date:'',priority:'normal',assigned_to:'',notes:''});load()}catch(err:unknown){setFormError(errorDetail(err,'Görev kaydedilemedi.'))}};
  const setTaskStatus=async(taskId:number,status:string)=>{try{await api.patch(`${endpoint}/tasks/${taskId}`,{status});load()}catch(err:unknown){setFormError(errorDetail(err,'Görev durumu güncellenemedi.'))}};
  const remove=async(path:string)=>{try{await api.delete(path);load()}catch(err:unknown){setFormError(errorDetail(err,'Kayıt silinemedi.'))}};

  const kpis=[
    {label:'Açık Bakiye',value:money(s.current_balance),color:Number(s.current_balance)>0?'error.main':'success.main',tab:1,description:'İlgili belgeleri göster'},
    {label:type==='customer'?'Toplam Ciro':'Toplam Alış',value:money(s.document_total),color:'primary.main',tab:1,description:'Tüm belgeleri göster'},
    {label:'Vadesi Geçen',value:money(s.overdue_amount),color:Number(s.overdue_amount)>0?'error.main':'success.main',tab:1,description:'Belge listesini göster'},
    {label:'Risk Kullanımı',value:s.risk_limit?`%${Number(s.risk_usage_percent||0).toFixed(1)}`:'Limit Yok',color:s.risk_exceeded?'error.main':'warning.main',tab:0,description:'Risk özetini göster'},
  ];

  return <Stack spacing={2}>
    <Button startIcon={<ArrowBackIcon/>} onClick={()=>nav(-1)} sx={{alignSelf:'flex-start'}}>Geri Dön</Button>

    <Card sx={{
      position:'relative',
      overflow:'hidden',
      border:0,
      color:'#fff',
      background:'linear-gradient(125deg, #071e41 0%, #0b3567 62%, #164a8a 100%)',
      boxShadow:'0 18px 48px rgba(20,38,26,.18)',
      '&:after':{content:'""',position:'absolute',width:300,height:300,right:-90,top:-170,borderRadius:'50%',background:'rgba(255,255,255,.07)'},
    }}>
      <CardContent sx={{position:'relative',zIndex:1,p:{xs:2.25,sm:3},'&:last-child':{pb:{xs:2.25,sm:3}}}}>
        <Stack direction={{xs:'column',lg:'row'}} justifyContent="space-between" gap={2}>
          <Stack direction={{xs:'column',sm:'row'}} spacing={2} alignItems={{sm:'center'}}>
            <Avatar sx={{width:76,height:76,fontSize:31,fontWeight:900,bgcolor:'rgba(255,255,255,.14)',color:'#fff',border:'1px solid rgba(255,255,255,.22)',boxShadow:'0 10px 28px rgba(0,0,0,.14)'}}>{String(e.name||'?').slice(0,1).toUpperCase()}</Avatar>
            <Box>
              <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
                <Typography variant="h4" fontWeight={900} letterSpacing="-.02em">{e.name}</Typography>
                <Chip size="small" label={e.is_active?'Aktif':'Pasif'} sx={{color:'#fff',bgcolor:e.is_active?'rgba(123,205,130,.24)':'rgba(255,255,255,.14)',border:'1px solid rgba(255,255,255,.16)'}}/>
                {s.risk_exceeded&&<Chip size="small" color="error" icon={<WarningAmberIcon/>} label="Risk Limiti Aşıldı"/>}
              </Stack>
              <Typography sx={{fontSize:15,lineHeight:1.6,color:'rgba(255,255,255,.78)'}}>{e.phone||'Telefon yok'} · {e.email||'E-posta yok'}</Typography>
              <Typography sx={{fontSize:13.5,lineHeight:1.55,color:'rgba(255,255,255,.62)'}}>Yetkili: {e.owner_name||'Tanımlanmamış'} · Son işlem: {s.last_activity||'Yok'}</Typography>
            </Box>
          </Stack>
          <Stack direction="row" spacing={1} flexWrap="wrap" alignContent="flex-start" sx={{'& .MuiButton-root':{borderColor:'rgba(255,255,255,.28)'}}}>
            <Button variant="contained" startIcon={<AddShoppingCartIcon/>} onClick={()=>setTransactionOpen(true)} sx={{bgcolor:'#fff',color:'#0b3567','&:hover':{bgcolor:'#eaf2fb'}}}>{type==='customer'?'Satış Yap':'Alış Yap'}</Button>
            {canPayments&&<Button variant="contained" startIcon={<PaymentsIcon/>} onClick={openPayment} sx={{bgcolor:'rgba(255,255,255,.15)',color:'#fff','&:hover':{bgcolor:'rgba(255,255,255,.22)'}}}>{type==='customer'?'Tahsilat Al':'Ödeme Yap'}</Button>}
            <Button variant={headerDarkActionVariant} startIcon={<ReceiptLongIcon/>} onClick={()=>setStatementOpen(true)}>Ekstre</Button>
            <Button variant={headerDarkActionVariant} startIcon={<EditIcon/>} onClick={()=>setEditOpen(true)}>Düzenle</Button>
            {e.phone&&<Tooltip title="Ara"><IconButton component="a" href={`tel:${e.phone}`} aria-label="Telefonla ara" sx={{color:'#fff',bgcolor:'rgba(255,255,255,.1)'}}><PhoneIcon/></IconButton></Tooltip>}
            {e.phone&&<Tooltip title="WhatsApp"><IconButton component="a" target="_blank" rel="noreferrer" href={`https://wa.me/${String(e.phone).replace(/\D/g,'')}`} aria-label="WhatsApp ile iletişim kur" sx={{color:'#fff',bgcolor:'rgba(255,255,255,.1)'}}><WhatsAppIcon/></IconButton></Tooltip>}
            {e.email&&<Tooltip title="E-posta"><IconButton component="a" href={`mailto:${e.email}`} aria-label="E-posta gönder" sx={{color:'#fff',bgcolor:'rgba(255,255,255,.1)'}}><EmailIcon/></IconButton></Tooltip>}
          </Stack>
        </Stack>
      </CardContent>
    </Card>

    <Grid container spacing={1.5}>{kpis.map(k=><Grid key={k.label} size={{xs:12,sm:6,lg:3}}><Card variant="outlined" role="button" tabIndex={0} aria-label={`${k.label}: ${k.value}. ${k.description}`} onClick={()=>setTab(k.tab)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setTab(k.tab)}}} sx={{height:'100%',cursor:'pointer',borderTop:'3px solid',borderTopColor:k.color,boxShadow:'0 8px 24px rgba(28,38,32,.045)','&:hover':{boxShadow:'0 14px 34px rgba(28,38,32,.1)',transform:'translateY(-3px)'},'&:focus-visible':{outline:'2px solid',outlineColor:'primary.main',outlineOffset:2},transition:'.2s'}}><CardContent><Typography sx={{fontSize:12.5,fontWeight:800,letterSpacing:'.045em'}} color="text.secondary">{k.label.toUpperCase()}</Typography><Typography variant="h5" fontWeight={900} color={k.color} mt={.35}>{k.value}</Typography><Typography sx={{fontSize:13,mt:.5}} color="text.secondary">{k.description}</Typography>{k.label==='Risk Kullanımı'&&s.risk_limit>0&&<LinearProgress variant="determinate" value={riskPercent} color={s.risk_exceeded?'error':'warning'} sx={{mt:1,height:7,borderRadius:9}}/>}</CardContent></Card></Grid>)}</Grid>

    {/* 8 sekmeden 390px'te yalnız 3'ü görünüyordu. `scrollButtons="auto"` MUI'de
        dokunmatik kırılımda düğmeleri gizler; kaydırma yapılabildiği halde hiç
        göstergesi yoktu. `allowScrollButtonsMobile` düğmeleri dar ekranda da
        bırakır. Sekme yüksekliği yalnız xs'te 72px'ten 48px'e iner. */}
    <Card sx={{overflow:'hidden',boxShadow:'0 12px 34px rgba(28,38,32,.06)'}}><Box sx={{p:1,bgcolor:'action.hover',borderBottom:'1px solid',borderColor:'divider'}}><Tabs
      value={tab}
      onChange={(_,v)=>setTab(v)}
      variant="scrollable"
      scrollButtons
      allowScrollButtonsMobile
      aria-label="Cari detay bölümleri"
      sx={{
        minHeight:{xs:48,sm:44},
        '& .MuiTabs-indicator':{display:'none'},
        '& .MuiTab-root':{minHeight:{xs:48,sm:44},py:{xs:.5},minWidth:'auto',px:{xs:1.25,sm:1.75},fontWeight:750,borderRadius:2,color:'text.secondary'},
        '& .MuiTab-root.Mui-selected':{bgcolor:'background.paper',color:'primary.main',boxShadow:'0 4px 14px rgba(28,38,32,.08)'},
        '& .MuiTab-icon':{mr:.75},
        '& .MuiTabs-scrollButtons':{width:{xs:44}},
      }}
    >
      <Tab label="Genel"/><Tab icon={<ReceiptLongIcon/>} iconPosition="start" label={type==='customer'?'Satış':'Alış'}/><Tab icon={<PaymentsIcon/>} iconPosition="start" label={type==='customer'?'Tahsilat':'Ödeme'}/><Tab label="Geçmiş"/><Tab icon={<Inventory2Icon/>} iconPosition="start" label="Ürün"/><Tab icon={<ContactsIcon/>} iconPosition="start" label={`Kişiler (${(data.contacts||[]).length})`}/><Tab icon={<TaskAltIcon/>} iconPosition="start" label={`Görev (${(data.tasks||[]).filter((x:any)=>x.status==='open').length})`}/><Tab icon={<NotesIcon/>} iconPosition="start" label={`Not (${(data.notes||[]).filter((x:any)=>x.status==='open').length})`}/><CustomerMachinesTab allowed={type==='customer'}/><SupplierPriceHistoryTab allowed={type==='supplier'&&canViewSupplierPrices}/>
    </Tabs></Box><CardContent sx={{p:{xs:1.5,sm:2.25}}}>
      {formError&&<Alert severity="error" sx={{mb:2}}>{formError}</Alert>}
      {tab===0&&<Grid container spacing={2}>
        <Grid size={12}><EntityLedgerOverview type={type} documents={data.documents||[]} payments={data.payments||[]} chargeDocuments={data.charge_documents||[]} onOpenDocument={setDocumentId} onOpenProduct={openProduct} documentTotal={Number(data.summary?.document_count_all||0)} onShowAllDocuments={()=>setTab(1)}/></Grid>
        <Grid size={{xs:12,lg:7}}><Stack spacing={2}>
          <Card variant="outlined"><CardContent><Typography variant="h6" fontWeight={900} mb={1}>Son İşlemler</Typography>{timeline.slice(0,6).map((x:any)=><Stack key={`${x.type}-${x.id}`} role="button" tabIndex={0} direction="row" justifyContent="space-between" gap={2} sx={interactiveRowSx} onClick={()=>openTimelineItem(x)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openTimelineItem(x)}}}><Box><Typography fontSize={15} fontWeight={750}>{x.title}</Typography><Typography sx={metadataSx}>{x.date}{x.note?` · ${x.note}`:''}</Typography></Box>{x.amount!==0&&<Typography fontSize={16} fontWeight={900}>{money(Math.abs(x.amount))}</Typography>}</Stack>)}{timeline.length===0&&<Typography color="text.secondary">Henüz işlem yok.</Typography>}</CardContent></Card>
          <Card variant="outlined"><CardContent><Typography variant="h6" fontWeight={900} mb={1}>{type==='customer'?'En Çok Satılan Ürünler':'En Çok Alınan Ürünler'}</Typography>{(data.products||[]).slice(0,5).map((x:any)=><Stack key={x.product_id} role="button" tabIndex={0} direction="row" justifyContent="space-between" alignItems="center" gap={2} sx={interactiveRowSx} onClick={()=>openProduct(x.product_id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openProduct(x.product_id)}}}><Box><Typography fontSize={15} fontWeight={750}>{x.product_name}</Typography><Typography sx={{fontSize:15,fontWeight:650,color:'text.secondary'}}>{Number(x.quantity||0).toLocaleString('tr-TR')} adet</Typography></Box><Typography fontSize={16} fontWeight={900}>{money(x.total)}</Typography></Stack>)}{!(data.products||[]).length&&<Typography color="text.secondary">Ürün hareketi yok.</Typography>}</CardContent></Card>
        </Stack></Grid>
        <Grid size={{xs:12,lg:5}}><Stack spacing={2}>
          <Card variant="outlined"><CardContent><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="h6" fontWeight={900}>Açık Görevler</Typography><Button size="small" startIcon={<AddTaskIcon/>} onClick={()=>setTaskOpen(true)}>Ekle</Button></Stack>{(data.tasks||[]).filter((x:any)=>x.status==='open').slice(0,5).map((x:any)=><Stack key={x.id} direction="row" alignItems="center" spacing={1} sx={{py:1,minHeight:52}}><Checkbox checked={false} onChange={()=>setTaskStatus(x.id,'completed')} inputProps={{'aria-label':`${x.title} görevini tamamla`}}/><Box flex={1}><Typography fontSize={15} fontWeight={750}>{x.title}</Typography><Typography sx={metadataSx}>{x.due_date||'Tarih yok'} · {x.assigned_to||'Atanmamış'}</Typography></Box><Chip size="small" color={priorityColor[x.priority]||'default'} label={x.priority}/></Stack>)}{!(data.tasks||[]).some((x:any)=>x.status==='open')&&<Stack spacing={1} mt={1}><Typography color="text.secondary">Açık görev yok.</Typography><Button variant="outlined" size="small" onClick={()=>setTaskOpen(true)} sx={{alignSelf:'flex-start'}}>İlk görevi oluştur</Button></Stack>}</CardContent></Card>
          <Card variant="outlined"><CardContent><Typography variant="h6" fontWeight={900}>Müşteri Sağlığı</Typography><Typography sx={metadataSx} mt={1}>{s.risk_exceeded?'Risk limiti aşılmış. Tahsilat planı önerilir.':Number(s.overdue_amount)>0?'Vadesi geçen bakiye bulunuyor.':'Finansal risk görünmüyor.'}</Typography><Typography sx={metadataSx} mt={1}>{s.last_activity?`Son işlem ${s.last_activity} tarihinde.`:'Henüz işlem yok.'}</Typography>{canPayments&&Number(s.overdue_amount)>0&&<Button color="success" variant="outlined" size="small" startIcon={<PaymentsIcon/>} onClick={openPayment} sx={{mt:1.5}}>Tahsilat Al</Button>}</CardContent></Card>
          {type==='customer'&&<NotificationConsentPanel partyId={Number(id)} defaultRecipient={data.phone}/>}
        </Stack></Grid>
      </Grid>}
      {tab===1&&<Stack spacing={1}><EntityDocumentList entityType={type} entityId={entityId} onOpenDocument={openDocument}/>{(data.charge_documents||[]).length>0&&<><Typography variant="subtitle2" fontWeight={900} sx={{mt:1}}>Servis / Gecikme Bedelleri</Typography>{(data.charge_documents||[]).map((x:any)=><Stack key={`charge-${x.id}`} role={chargeOpenable(x)?'button':undefined} tabIndex={chargeOpenable(x)?0:undefined} aria-label={chargeOpenable(x)?`${x.document_no} servis iş emrini aç`:undefined} onClick={()=>openCharge(x)} onKeyDown={event=>{if(chargeOpenable(x)&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openCharge(x)}}} direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1} sx={{p:1.5,minHeight:58,border:'1px solid',borderColor:'divider',borderRadius:2,bgcolor:'action.hover',...(chargeOpenable(x)?{cursor:'pointer','&:hover':{bgcolor:'action.selected'},'&:focus-visible':{outline:'2px solid',outlineColor:'primary.main'}}:{})}}><Box><Stack direction="row" spacing={1} alignItems="center"><Chip size="small" color={x.charge_type==='service_fee'?'info':'warning'} label={x.charge_type==='service_fee'?'Servis':'Gecikme'}/><Typography fontSize={15} fontWeight={800}>{x.document_no}</Typography></Stack><Typography sx={metadataSx}>Vade {x.due_date}</Typography></Box><Stack direction="row" spacing={1} alignItems="center">{Number(x.applied)>0&&<Typography sx={metadataSx}>Tahsil {money(x.applied)}</Typography>}<Typography fontSize={16} fontWeight={900}>{money(x.remaining)}</Typography></Stack></Stack>)}</>}</Stack>}
      {tab===2&&<Stack spacing={1}>{(data.payments||[]).length?(data.payments||[]).map((x:any)=><Stack key={x.id} direction={{xs:'column',sm:'row'}} justifyContent="space-between" sx={{p:1.5,minHeight:58,borderBottom:'1px solid',borderColor:'divider'}}><Box><Typography fontSize={15} fontWeight={800}>{x.payment_date} · {paymentLabel[x.payment_method]||x.payment_method}</Typography><Typography sx={metadataSx}>{x.note||'Not yok'}</Typography></Box><Typography fontSize={16} fontWeight={900}>{money(x.amount)}</Typography></Stack>):<Typography color="text.secondary">Kayıt yok.</Typography>}</Stack>}
      {tab===3&&<Stack spacing={1}>{timeline.map((x:any)=><Stack key={`${x.type}-${x.id}`} role="button" tabIndex={0} direction="row" spacing={2} sx={interactiveRowSx} onClick={()=>openTimelineItem(x)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openTimelineItem(x)}}}><Typography sx={{...metadataSx,minWidth:100}}>{x.date}</Typography><Box flex={1}><Typography fontSize={15} fontWeight={800}>{x.title}</Typography>{x.note&&<Typography sx={metadataSx}>{x.note}</Typography>}</Box>{x.amount!==0&&<Typography fontSize={16} fontWeight={900}>{money(Math.abs(x.amount))}</Typography>}</Stack>)}</Stack>}
      {tab===4&&<Stack spacing={1}>{(data.products||[]).map((x:any)=><Stack key={x.product_id} role="button" tabIndex={0} direction="row" justifyContent="space-between" alignItems="center" gap={2} sx={{...interactiveRowSx,p:1.5}} onClick={()=>openProduct(x.product_id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openProduct(x.product_id)}}}><Box><Typography fontSize={15} fontWeight={800}>{x.product_name}</Typography><Typography sx={{fontSize:15,fontWeight:650,color:'text.secondary'}}>Toplam miktar: {Number(x.quantity||0).toLocaleString('tr-TR')}</Typography></Box><Typography fontSize={16} fontWeight={900}>{money(x.total)}</Typography></Stack>)}</Stack>}
      {tab===5&&<Stack spacing={1.5}><Button startIcon={<PersonAddIcon/>} variant="contained" onClick={()=>setContactOpen(true)} sx={{alignSelf:'flex-start'}}>Yetkili Ekle</Button>{(data.contacts||[]).map((c:any)=><Card key={c.id} variant="outlined"><CardContent><Stack direction="row" justifyContent="space-between"><Box><Stack direction="row" spacing={1} alignItems="center"><Typography fontWeight={900}>{c.name}</Typography>{Boolean(c.is_primary)&&<Chip size="small" color="primary" label="Birincil"/>}</Stack><Typography color="text.secondary">{c.role||'Görev belirtilmemiş'}</Typography><Typography variant="body2">{c.phone||''} {c.email?`· ${c.email}`:''}</Typography></Box><IconButton color="error" aria-label={`${c.name} yetkilisini sil`} onClick={()=>remove(`${endpoint}/contacts/${c.id}`)}><DeleteIcon/></IconButton></Stack></CardContent></Card>)}</Stack>}
      {tab===6&&<Stack spacing={1.5}><Button startIcon={<AddTaskIcon/>} variant="contained" onClick={()=>setTaskOpen(true)} sx={{alignSelf:'flex-start'}}>Görev Ekle</Button>{(data.tasks||[]).map((x:any)=><Card key={x.id} variant="outlined"><CardContent><Stack direction="row" alignItems="center" spacing={1}><Checkbox checked={x.status==='completed'} onChange={()=>setTaskStatus(x.id,x.status==='completed'?'open':'completed')} inputProps={{'aria-label':`${x.title} görev durumunu değiştir`}}/><Box flex={1}><Typography fontSize={15} fontWeight={900} sx={{textDecoration:x.status==='completed'?'line-through':'none'}}>{x.title}</Typography><Typography sx={metadataSx}>{x.due_date||'Tarih yok'} · {x.assigned_to||'Atanmamış'}</Typography></Box><Chip size="small" color={priorityColor[x.priority]||'default'} label={x.priority}/><IconButton color="error" aria-label={`${x.title} görevini sil`} onClick={()=>remove(`${endpoint}/tasks/${x.id}`)}><DeleteIcon/></IconButton></Stack></CardContent></Card>)}</Stack>}
      {tab===7&&<Stack spacing={1.5}><Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField fullWidth label="Yeni cari notu" value={newNote} onChange={x=>setNewNote(x.target.value)} multiline minRows={2}/><Button variant="contained" onClick={saveNote}>Not Ekle</Button></Stack>{(data.notes||[]).map((n:any)=><Card key={n.id} variant="outlined"><CardContent><Stack direction="row" justifyContent="space-between"><Box><Typography>{n.note}</Typography><Typography sx={metadataSx}>{n.created_at}{n.created_by?` · ${n.created_by}`:''}</Typography></Box><IconButton color="error" aria-label="Notu sil" onClick={()=>remove(`${endpoint}/notes/${n.id}`)}><DeleteIcon/></IconButton></Stack></CardContent></Card>)}</Stack>}
      {/* 8. sekme türe göre farklı: müşteride Makineler, tedarikçide Fiyat
          Geçmişi. İkisi de karşı türde null döndüğü için indeks çakışmaz. */}
      {type==='customer'&&tab===8&&<CustomerMachines customerId={entityId}/>}
      {type==='supplier'&&canViewSupplierPrices&&tab===8&&<SupplierPriceHistory supplierId={entityId}/>}
    </CardContent></Card>

    <EntityDialog open={editOpen} type={type} id={entityId} onClose={()=>setEditOpen(false)} onSaved={load}/>
    <EntityStatementDialog open={statementOpen} type={type} id={entityId} onClose={()=>setStatementOpen(false)}/>
    <TransactionDialog open={transactionOpen||documentId!==null} kind={type==='customer'?'sale':'purchase'} id={documentId} initialEntityId={entityId} onClose={()=>{setTransactionOpen(false);setDocumentId(null)}} onSaved={()=>{setTransactionOpen(false);setDocumentId(null);load()}}/>
    {canPayments&&<Dialog open={paymentOpen} onClose={()=>setPaymentOpen(false)} fullWidth maxWidth="xs"><DialogTitle>{type==='customer'?'Tahsilat Al':'Ödeme Yap'}</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}><TextField label="Tutar" type="number" value={paymentAmount} onChange={x=>setPaymentAmount(Number(x.target.value))}/><TextField label="Tarih" type="date" value={paymentDate} onChange={x=>setPaymentDate(x.target.value)} slotProps={{inputLabel:{shrink:true}}}/><TextField select label="Ödeme Yöntemi" value={paymentMethod} onChange={x=>{setPaymentMethod(x.target.value);setPaymentAccountId('')}}>{Object.entries(paymentLabel).map(([v,l])=><MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField>{accountTypeForMethod[paymentMethod]&&<TextField select label="Kasa / Banka / POS Hesabı" value={paymentAccountId} onChange={x=>setPaymentAccountId(x.target.value===''?'':Number(x.target.value))}><MenuItem value="">Varsayılan Hesap</MenuItem>{accounts.filter((a:any)=>a.account_type===accountTypeForMethod[paymentMethod]).map((a:any)=><MenuItem key={a.id} value={a.id}>{a.name}</MenuItem>)}</TextField>}<TextField label="Not" value={paymentNote} onChange={x=>setPaymentNote(x.target.value)}/></Stack></DialogContent><DialogActions><Button onClick={()=>setPaymentOpen(false)}>Vazgeç</Button><Button variant="contained" color="success" onClick={savePayment}>Kaydet</Button></DialogActions></Dialog>}
    <Dialog open={contactOpen} onClose={()=>setContactOpen(false)} fullWidth maxWidth="sm"><DialogTitle>Yetkili Kişi Ekle</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}><TextField label="Ad Soyad" value={contact.name} onChange={x=>setContact({...contact,name:x.target.value})}/><TextField label="Görev / Ünvan" value={contact.role} onChange={x=>setContact({...contact,role:x.target.value})}/><TextField label="Telefon" value={contact.phone} onChange={x=>setContact({...contact,phone:x.target.value})}/><TextField label="E-posta" value={contact.email} onChange={x=>setContact({...contact,email:x.target.value})}/><Stack direction="row" alignItems="center"><Checkbox checked={contact.is_primary} onChange={x=>setContact({...contact,is_primary:x.target.checked})}/><Typography>Birincil yetkili</Typography></Stack><TextField label="Not" multiline minRows={2} value={contact.notes} onChange={x=>setContact({...contact,notes:x.target.value})}/></Stack></DialogContent><DialogActions><Button onClick={()=>setContactOpen(false)}>Vazgeç</Button><Button variant="contained" onClick={saveContact}>Kaydet</Button></DialogActions></Dialog>
    <Dialog open={taskOpen} onClose={()=>setTaskOpen(false)} fullWidth maxWidth="sm"><DialogTitle>Görev Ekle</DialogTitle><DialogContent><Stack spacing={1.5} mt={1}><TextField label="Görev" value={task.title} onChange={x=>setTask({...task,title:x.target.value})}/><TextField label="Son Tarih" type="date" value={task.due_date} onChange={x=>setTask({...task,due_date:x.target.value})} slotProps={{inputLabel:{shrink:true}}}/><TextField select label="Öncelik" value={task.priority} onChange={x=>setTask({...task,priority:x.target.value})}><MenuItem value="low">Düşük</MenuItem><MenuItem value="normal">Normal</MenuItem><MenuItem value="high">Yüksek</MenuItem></TextField><TextField label="Atanan Kişi" value={task.assigned_to} onChange={x=>setTask({...task,assigned_to:x.target.value})}/><TextField label="Not" multiline minRows={2} value={task.notes} onChange={x=>setTask({...task,notes:x.target.value})}/></Stack></DialogContent><DialogActions><Button onClick={()=>setTaskOpen(false)}>Vazgeç</Button><Button variant="contained" onClick={saveTask}>Kaydet</Button></DialogActions></Dialog>
  </Stack>;
}
