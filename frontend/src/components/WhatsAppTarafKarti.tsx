import {useCallback,useEffect,useState} from 'react';
import {
 Alert,Box,Button,Chip,CircularProgress,Dialog,DialogActions,DialogContent,
 DialogContentText,DialogTitle,Paper,Stack,TextField,Typography,
} from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';
import {yerelGun} from '../utils/tarih';

type PartyType='CUSTOMER'|'SUPPLIER';

/** `GET /api/whatsapp/party-links` satırı. Ham numara cevapta YOKTUR. */
type BaglantiSatiri={
 id:number;party_type:PartyType;party_id:number;phone_masked:string;
 is_active:boolean;consent_at:string|null;created_at:string|null;
};
/** Bekleyen kod — DÜZ KOD BURADA TUTULMAZ, yalnız iptal için gereken kimlik. */
type BekleyenKod={kod_id:number;telefon:string;expires_at:string};
/** Tek seferlik gösterim. Pencere kapanınca state'ten silinir. */
type UretilenKod=BekleyenKod&{kod_gosterim:string};

// K4 (`app/auth.py`): izin gövdedeki `party_type`tan türer. Liste GET'i de
// aynı izni ister; izinsiz rolde kart HİÇ çizilmez ve hiçbir uç çağrılmaz —
// 403 alacak bir istek ya da ölü bir düğme göstermenin anlamı yok.
const IZIN:Record<PartyType,string>={CUSTOMER:'sales',SUPPLIER:'purchases'};

// SEC-3b: rolü maskeli kullanıcıda cari telefonu yıldızlı gelir; yıldızlı
// değer kod hedefi olarak ön dolguya GİRMEZ (EntityDetail'deki `maskeli`).
const maskeli=(deger:unknown)=>typeof deger==='string'&&deger.includes('*');

const kalanMetni=(expiresAt:string,simdi:number)=>{
 const bitis=new Date(expiresAt).getTime();
 if(Number.isNaN(bitis))return `Son geçerlilik: ${expiresAt}`;
 const dakika=Math.ceil((bitis-simdi)/60000);
 if(dakika<=0)return 'Süresi doldu';
 return `~${dakika} dk içinde geçersiz olur`;
};

/**
 * F10-1d — cari kartında çiftçi WhatsApp bağlantısı.
 *
 * Bağlantı "bu numara kim" sorusunu cevaplar; RIZA ayrı bir sorudur ve
 * personel tarafından VERİLMEZ: çiftçinin ilk WhatsApp mesajında alınır
 * (keşif §5.4). Buradaki "Rıza var/yok" rozeti yalnız görüntüdür —
 * `consent_at` dolu mu; defter kararı sunucunundur.
 *
 * Bekleyen kodların listesini veren bir uç YOK; bu yüzden bekleyen kod
 * bölümü yalnız BU oturumda üretilen kodu gösterir. Yeni kod, aynı carinin
 * bekleyen kodunu sunucuda iptal eder — liste bu yüzden tek elemanlıdır.
 */
export default function WhatsAppTarafKarti({partyType,partyId,defaultPhone}:{
 partyType:PartyType;partyId:number;defaultPhone?:string|null;
}){
 const {can}=useAuth();
 const yetkili=can(IZIN[partyType]);

 const [baglantilar,setBaglantilar]=useState<BaglantiSatiri[]>([]);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const [telefon,setTelefon]=useState('');
 const [bekleyen,setBekleyen]=useState<BekleyenKod[]>([]);
 const [uretilen,setUretilen]=useState<UretilenKod|null>(null);
 const [kapatilacak,setKapatilacak]=useState<BaglantiSatiri|null>(null);
 const [kopyalandi,setKopyalandi]=useState(false);
 const [simdi,setSimdi]=useState(()=>Date.now());

 const load=useCallback(async()=>{
  if(!yetkili||!partyId)return;
  setLoading(true);setError('');
  try{
   const {data}=await api.get<BaglantiSatiri[]>('/whatsapp/party-links',{
    params:{party_type:partyType,party_id:partyId},
   });
   setBaglantilar(data||[]);
  }catch(err){setError(errorDetail(err,'WhatsApp bağlantıları yüklenemedi.'));}
  finally{setLoading(false);}
 },[yetkili,partyId,partyType]);

 useEffect(()=>{void load();},[load]);
 useEffect(()=>{setTelefon(defaultPhone&&!maskeli(defaultPhone)?defaultPhone:'');},[defaultPhone]);
 useEffect(()=>{
  if(!bekleyen.length)return undefined;
  const zamanlayici=window.setInterval(()=>setSimdi(Date.now()),30000);
  return ()=>window.clearInterval(zamanlayici);
 },[bekleyen.length]);

 if(!yetkili)return null;

 const aktifler=baglantilar.filter(b=>b.is_active);

 const kodUret=async()=>{
  setBusy(true);setError('');
  try{
   const {data}=await api.post<UretilenKod&{kod?:string}>('/whatsapp/party-pairing-codes',{
    party_type:partyType,party_id:partyId,phone:telefon.trim(),
   });
   const kayit={kod_id:data.kod_id,telefon:data.telefon,expires_at:data.expires_at};
   setBekleyen([kayit]);
   setSimdi(Date.now());
   setKopyalandi(false);
   setUretilen({...kayit,kod_gosterim:data.kod_gosterim||data.kod||''});
  }catch(err){setError(errorDetail(err,'Eşleştirme kodu üretilemedi.'));}
  finally{setBusy(false);}
 };

 const kodIptal=async(kodId:number)=>{
  setBusy(true);setError('');
  try{
   await api.delete(`/whatsapp/party-pairing-codes/${kodId}`);
   setBekleyen(liste=>liste.filter(k=>k.kod_id!==kodId));
  }catch(err){
   setError(errorDetail(err,'Kod iptal edilemedi.'));
   // 404: kod zaten kullanılmış/süresi dolmuş/iptal — listede tutmanın anlamı yok.
   if((err as {response?:{status?:number}})?.response?.status===404)
    setBekleyen(liste=>liste.filter(k=>k.kod_id!==kodId));
  }finally{setBusy(false);}
 };

 const baglantiKapat=async()=>{
  if(!kapatilacak)return;
  setBusy(true);setError('');
  try{
   await api.delete(`/whatsapp/party-links/${kapatilacak.id}`);
   setKapatilacak(null);
   await load();
  }catch(err){setKapatilacak(null);setError(errorDetail(err,'Bağlantı kapatılamadı.'));}
  finally{setBusy(false);}
 };

 const kopyala=async()=>{
  if(!uretilen)return;
  try{await navigator.clipboard.writeText(uretilen.kod_gosterim);setKopyalandi(true);}
  catch{setKopyalandi(false);}
 };

 return <Paper variant="outlined" sx={{p:2}}>
  <Typography variant="subtitle1" sx={{mb:0.5}}>WhatsApp bağlantısı</Typography>
  <Typography variant="body2" color="text.secondary" sx={{mb:2}}>
   {'Çiftçi, üretilen kodu kendi numarasından WhatsApp\'a "BAĞLA <KOD>" yazarak bağlanır. '}
   Rıza çiftçinin ilk mesajıyla alınır; kod üretmek rıza vermek değildir.
  </Typography>

  {error&&<Alert severity="error" sx={{mb:2}} onClose={()=>setError('')}>{error}</Alert>}

  {loading?<Box sx={{py:2,textAlign:'center'}}><CircularProgress size={22}/></Box>:
  <Stack spacing={1.5}>
   <Box>
    <Typography variant="body2" fontWeight={700} sx={{mb:.5}}>Aktif bağlantı</Typography>
    {aktifler.length===0&&<Typography variant="body2" color="text.secondary">Bağlı numara yok.</Typography>}
    {aktifler.map(b=><Stack key={b.id} data-testid={`wa-baglanti-${b.id}`} direction="row" spacing={1}
      alignItems="center" justifyContent="space-between" sx={{py:.5}}>
     <Box>
      <Stack direction="row" spacing={1} alignItems="center">
       <Typography variant="body2">{b.phone_masked}</Typography>
       <Chip size="small" color={b.consent_at?'success':'default'}
        label={b.consent_at?'Rıza var':'Rıza yok'}/>
      </Stack>
      <Typography variant="caption" color="text.secondary">
       {b.consent_at?`Rıza: ${yerelGun(b.consent_at)}`:'Rıza çiftçinin ilk mesajıyla alınır'}
      </Typography>
     </Box>
     <Button size="small" color="error" disabled={busy} onClick={()=>setKapatilacak(b)}
      aria-label={`${b.phone_masked} bağlantısını kapat`}>Bağlantıyı kapat</Button>
    </Stack>)}
   </Box>

   {bekleyen.length>0&&<Box>
    <Typography variant="body2" fontWeight={700} sx={{mb:.5}}>Bekleyen kod</Typography>
    {bekleyen.map(k=><Stack key={k.kod_id} data-testid={`wa-kod-${k.kod_id}`} direction="row" spacing={1}
      alignItems="center" justifyContent="space-between" sx={{py:.5}}>
     <Box>
      <Typography variant="body2">{k.telefon}</Typography>
      <Typography variant="caption" color="text.secondary">{kalanMetni(k.expires_at,simdi)}</Typography>
     </Box>
     <Button size="small" disabled={busy} onClick={()=>void kodIptal(k.kod_id)}
      aria-label={`${k.telefon} kodunu iptal et`}>İptal</Button>
    </Stack>)}
   </Box>}

   <Stack direction={{xs:'column',sm:'row'}} spacing={1} alignItems={{sm:'flex-start'}}>
    <TextField size="small" fullWidth label="Çiftçinin cep telefonu" value={telefon}
     onChange={event=>setTelefon(event.target.value)}
     helperText="Kod yalnız bu numaradan kullanılabilir"/>
    <Button variant="outlined" disabled={busy||!telefon.trim()} onClick={()=>void kodUret()}
     sx={{whiteSpace:'nowrap',flexShrink:0}}>Eşleştirme kodu üret</Button>
   </Stack>
  </Stack>}

  <Dialog open={uretilen!==null} onClose={()=>setUretilen(null)} fullWidth maxWidth="xs">
   <DialogTitle>Eşleştirme kodu</DialogTitle>
   {uretilen&&<DialogContent>
    <DialogContentText sx={{mb:1.5}}>
     {`Bu kod YALNIZ BİR KEZ gösterilir. Çiftçi ${uretilen.telefon} numarasından WhatsApp'a "BAĞLA ${uretilen.kod_gosterim}" yazmalı. ${kalanMetni(uretilen.expires_at,simdi)}.`}
    </DialogContentText>
    <Stack direction="row" spacing={1} alignItems="center"
     sx={{p:1.5,border:'1px dashed',borderColor:'divider',borderRadius:1}}>
     <Typography data-testid="wa-duz-kod" sx={{fontFamily:'monospace',fontSize:22,fontWeight:800,flex:1,letterSpacing:'.08em'}}>
      {uretilen.kod_gosterim}
     </Typography>
     <Button size="small" startIcon={<ContentCopyIcon/>} onClick={()=>void kopyala()}>
      {kopyalandi?'Kopyalandı':'Kopyala'}
     </Button>
    </Stack>
    <Alert severity="info" sx={{mt:1.5}}>KVKK: Rıza çiftçinin ilk mesajıyla alınır.</Alert>
   </DialogContent>}
   <DialogActions><Button variant="contained" onClick={()=>setUretilen(null)}>Kapat</Button></DialogActions>
  </Dialog>

  <Dialog open={kapatilacak!==null} onClose={()=>setKapatilacak(null)} fullWidth maxWidth="xs">
   <DialogTitle>Bağlantı kapatılsın mı?</DialogTitle>
   <DialogContent>
    <DialogContentText>
     {kapatilacak?.phone_masked} numarası bu cariden ayrılır; çiftçi yeniden bağlanmak
     için yeni bir kod ister. Kayıt silinmez, geçmişte kalır.
    </DialogContentText>
   </DialogContent>
   <DialogActions>
    <Button onClick={()=>setKapatilacak(null)}>Vazgeç</Button>
    <Button color="error" variant="contained" disabled={busy} onClick={()=>void baglantiKapat()}>Kapat</Button>
   </DialogActions>
  </Dialog>
 </Paper>;
}
