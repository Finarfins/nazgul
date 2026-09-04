import {useEffect,useMemo,useRef,useState} from 'react';
import {Alert,Box,Button,Card,CardContent,Checkbox,CircularProgress,FormControlLabel,FormGroup,FormLabel,Link,Stack,TextField,Typography} from '@mui/material';
import {Link as RouterLink} from 'react-router-dom';
import {api,errorDetail} from '../api';
import {PASSWORD_MIN_LENGTH,PASSWORD_RULE_LABEL} from '../passwordPolicy';

declare global{
 interface Window{turnstile?:{render:(element:HTMLElement,options:{sitekey:string;callback:(token:string)=>void;'expired-callback':()=>void})=>string;reset:(widgetId?:string)=>void}}
}

const initial={company_name:'',display_name:'',email:'',phone:'',password:'',password_confirmation:'',terms_accepted:false,turnstile_token:'',profiller:[] as string[]};
// Dört iş kolu ÇOKLU seçilir: karışmak kuraldır, istisna değil (bir işletme
// aynı anda bayilik + servis + tarla + sürü tutabilir). Sıra sahibin saydığı
// sıradır; DEPODA alfabetik sıralanarak saklanır, o normalleştirme sunucuda.
// Seçim İSTEĞE BAĞLIDIR: boş bırakmak "seçilmedi" demektir, "hiçbiri" demez.
const PROFILLER=[['ciftci','Çiftçi'],['tuccar','Tüccar'],['veteriner','Veteriner'],['pazarci','Pazarcı']] as const;
const siteKey=import.meta.env.VITE_TURNSTILE_SITE_KEY as string|undefined;

export default function Register(){
 const [form,setForm]=useState(initial);
 const [error,setError]=useState('');const [message,setMessage]=useState('');const [busy,setBusy]=useState(false);
 const turnstileHost=useRef<HTMLDivElement>(null);const turnstileWidget=useRef<string|undefined>(undefined);
 // Politika tek şarttan ibaret: minimum uzunluk. Karmaşıklık kuralları
 // bilinçli olarak kaldırıldı; backend de yalnız uzunluğu doğruluyor.
 const passwordRules=useMemo(()=>[
  [PASSWORD_RULE_LABEL,form.password.length>=PASSWORD_MIN_LENGTH],
 ] as const,[form.password]);
 useEffect(()=>{
  if(!siteKey||!turnstileHost.current)return;
  const render=()=>{turnstileWidget.current=window.turnstile?.render(turnstileHost.current!,{sitekey:siteKey,callback:token=>setForm(current=>({...current,turnstile_token:token})),'expired-callback':()=>setForm(current=>({...current,turnstile_token:''}))})};
  if(window.turnstile){render();return}
  const script=document.createElement('script');script.src='https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';script.async=true;script.onload=render;document.head.appendChild(script);
  return()=>{script.remove()};
 },[]);
 const valid=passwordRules.every(([,ok])=>ok)&&form.password===form.password_confirmation&&form.terms_accepted&&(!siteKey||Boolean(form.turnstile_token));
 const submit=async(event:React.FormEvent)=>{
  event.preventDefault();setBusy(true);setError('');setMessage('');
  try{const {data}=await api.post('/auth/register',form);setMessage(data.message)}
  catch(err){setError(errorDetail(err,'Kayıt oluşturulamadı.'))}
  // Turnstile jetonu tek kullanımlıktır: sayfa yönlenmediği için jetonu sıfırlayıp
  // yeni denemenin timeout-or-duplicate ile reddedilmesini önlüyoruz.
  finally{setBusy(false);if(siteKey){setForm(current=>({...current,turnstile_token:''}));window.turnstile?.reset(turnstileWidget.current)}}
 };
 return <Box sx={{minHeight:'100dvh',display:'grid',placeItems:'center',p:2,background:'linear-gradient(135deg,#13253c,#244e70 60%,#f28c28 150%)'}}>
  <Card sx={{width:'100%',maxWidth:560,borderRadius:3}}><CardContent sx={{p:{xs:3,sm:5}}}>
   <Stack component="form" spacing={2} onSubmit={submit}>
    <Typography variant="h4" fontWeight={900}>Firma hesabı oluştur</Typography>
    <Typography color="text.secondary">İlk kullanıcı firma yöneticisi olacaktır. Vergi numaranızı deneme süresinde girmeniz gerekmez.</Typography>
    {error&&<Alert severity="error">{error}</Alert>}{message&&<Alert severity="success">{message}</Alert>}
    <TextField required label="Ad Soyad" value={form.display_name} onChange={e=>setForm({...form,display_name:e.target.value})}/>
    <TextField required label="Şirket Ünvanı / Şahıs Adı" value={form.company_name} onChange={e=>setForm({...form,company_name:e.target.value})}/>
    <TextField required type="email" label="E-posta" value={form.email} onChange={e=>setForm({...form,email:e.target.value})}/>
    <TextField required label="Cep Telefonu" inputProps={{inputMode:'numeric',pattern:'[1-9][0-9]{9}',maxLength:10}} helperText="Başında 0 olmadan 10 hane: 5361234567" value={form.phone} onChange={e=>setForm({...form,phone:e.target.value})}/>
    <TextField required type="password" label="Şifre" value={form.password} onChange={e=>setForm({...form,password:e.target.value})}/>
    <Stack spacing={0.25}>{passwordRules.map(([label,ok])=><Typography key={label} variant="caption" color={ok?'success.main':'text.secondary'}>{ok?'✓':'○'} {label}</Typography>)}</Stack>
    <TextField required type="password" label="Şifre Tekrar" error={Boolean(form.password_confirmation&&form.password!==form.password_confirmation)} helperText={form.password_confirmation&&form.password!==form.password_confirmation?'Şifreler eşleşmiyor':''} value={form.password_confirmation} onChange={e=>setForm({...form,password_confirmation:e.target.value})}/>
    <Box>
     <FormLabel component="legend" sx={{fontSize:14}}>Ne iş yapıyorsunuz? (isteğe bağlı, birden fazla seçebilirsiniz)</FormLabel>
     <FormGroup row>{PROFILLER.map(([deger,etiket])=><FormControlLabel key={deger} control={<Checkbox checked={form.profiller.includes(deger)} onChange={e=>setForm(current=>({...current,profiller:e.target.checked?[...current.profiller,deger]:current.profiller.filter(p=>p!==deger)}))}/>} label={etiket}/>)}</FormGroup>
    </Box>
    <FormControlLabel control={<Checkbox checked={form.terms_accepted} onChange={e=>setForm({...form,terms_accepted:e.target.checked})}/>} label="Kullanım Koşullarını kabul ediyorum"/>
    {siteKey&&<Box ref={turnstileHost}/>}
    <Button type="submit" variant="contained" size="large" disabled={busy||!valid}>{busy?<CircularProgress size={22}/>:'Kayıt ol'}</Button>
    <Typography textAlign="center"><Link component={RouterLink} to="/giris">Giriş ekranına dön</Link></Typography>
   </Stack>
  </CardContent></Card>
 </Box>;
}
