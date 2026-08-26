import {useCallback,useEffect,useState} from 'react';
import {
 Alert,Box,Button,Chip,CircularProgress,Paper,Stack,Switch,TextField,
 Tooltip,Typography,
} from '@mui/material';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';

type ConsentRow={
 id:number;company_id:number;party_type:string;party_id:number;channel:string;
 status:string;granted_at:string|null;revoked_at:string|null;source:string;
 source_ref:string|null;recipient_snapshot:string|null;version:number;
 created_by:number|null;created_at:string;updated_at:string;
};

const CHANNELS=[
 {value:'SMS',label:'SMS'},
 {value:'WHATSAPP',label:'WhatsApp'},
 {value:'EMAIL',label:'E-posta'},
] as const;

const formatMoment=(value:string|null)=>{
 if(!value)return '—';
 const parsed=new Date(value);
 return Number.isNaN(parsed.getTime())?value:parsed.toLocaleString('tr-TR');
};

/**
 * Müşteri kartındaki izin (opt-in) bölümü — dükkânda rıza toplama akışının
 * girişi.
 *
 * İzin YOKSA o kanaldan hiçbir bildirim gönderilemez: kayıt olmaması "izin
 * verilmedi" demektir, "bilinmiyor, gönderelim" değil. Her değişiklik
 * denetim kaydına düşer ve rıza sürümü artar.
 */
export default function NotificationConsentPanel({
 partyType='CUSTOMER',partyId,defaultRecipient,
}:{partyType?:'CUSTOMER'|'SUPPLIER';partyId:number;defaultRecipient?:string|null}){
 const {can}=useAuth();
 const canManage=can('notifications_admin');

 const [rows,setRows]=useState<ConsentRow[]>([]);
 const [loading,setLoading]=useState(false);
 const [busy,setBusy]=useState('');
 const [error,setError]=useState('');
 const [recipient,setRecipient]=useState(defaultRecipient||'');

 const load=useCallback(async()=>{
  if(!partyId)return;
  setLoading(true);setError('');
  try{
   const {data}=await api.get<ConsentRow[]>('/notifications/consents',{
    params:{party_type:partyType,party_id:partyId},
   });
   setRows(data||[]);
  }catch(err){setError(errorDetail(err,'İzin kayıtları yüklenemedi'));}
  finally{setLoading(false);}
 },[partyId,partyType]);

 useEffect(()=>{void load();},[load]);
 useEffect(()=>{setRecipient(defaultRecipient||'');},[defaultRecipient]);

 const current=(channel:string)=>rows.find(row=>row.channel===channel);

 const toggle=async(channel:string,granted:boolean)=>{
  setBusy(channel);setError('');
  try{
   await api.put('/notifications/consents',{
    party_type:partyType,party_id:partyId,channel,granted,
    source:granted?'FORM':'PHONE',
    recipient:granted?recipient:undefined,
   });
   await load();
  }catch(err){setError(errorDetail(err,'İzin güncellenemedi'));}
  finally{setBusy('');}
 };

 return <Paper variant="outlined" sx={{p:2}}>
  <Typography variant="subtitle1" sx={{mb:0.5}}>Bildirim izinleri (KVKK)</Typography>
  <Typography variant="body2" color="text.secondary" sx={{mb:2}}>
   İzin verilmeyen kanaldan mesaj gönderilmez. İzin, verildiği andaki telefon
   veya e-posta için geçerlidir; bilgi değişirse izin yeniden alınmalıdır.
  </Typography>

  {error&&<Alert severity="error" sx={{mb:2}} onClose={()=>setError('')}>{error}</Alert>}

  {canManage&&<TextField size="small" fullWidth sx={{mb:2}}
   label="İzin kapsamındaki telefon / e-posta" value={recipient}
   onChange={event=>setRecipient(event.target.value)}
   helperText="İzin verilirken bu bilgi kaydedilir ve gönderim anında karşılaştırılır"/>}

  {loading?<Box sx={{py:2,textAlign:'center'}}><CircularProgress size={22}/></Box>:
  <Stack spacing={1.5}>
   {CHANNELS.map(channel=>{
    const row=current(channel.value);
    const granted=row?.status==='GRANTED';
    return <Stack key={channel.value} direction="row" spacing={2} alignItems="center"
      justifyContent="space-between">
     <Box>
      <Stack direction="row" spacing={1} alignItems="center">
       <Typography variant="body2">{channel.label}</Typography>
       <Chip size="small" color={granted?'success':'default'}
        label={granted?'İzin var':'İzin yok'}/>
       {row&&<Tooltip title={`Sürüm ${row.version}`}>
        <Chip size="small" variant="outlined" label={`v${row.version}`}/>
       </Tooltip>}
      </Stack>
      {row&&<Typography variant="caption" color="text.secondary">
       {granted
        ?`İzin: ${formatMoment(row.granted_at)} · ${row.recipient_snapshot||'—'}`
        :`Geri çekildi: ${formatMoment(row.revoked_at)}`}
      </Typography>}
     </Box>
     <Switch checked={granted} disabled={!canManage||busy===channel.value}
      onChange={event=>void toggle(channel.value,event.target.checked)}
      inputProps={{'aria-label':`${channel.label} izni`}}/>
    </Stack>;
   })}
  </Stack>}
 </Paper>;
}
