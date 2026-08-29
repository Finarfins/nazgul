import React,{useState} from 'react';
import {Alert,Box,Button,Card,CardContent,CircularProgress,Link,Stack,TextField,Typography} from '@mui/material';
import {Link as RouterLink,useNavigate,useSearchParams} from 'react-router-dom';
import {api,errorDetail} from '../api';
import {PASSWORD_MIN_LENGTH} from '../passwordPolicy';

export default function ResetPassword(){
 const [params]=useSearchParams();
 const navigate=useNavigate();
 const token=params.get('token')||'';
 const [password,setPassword]=useState('');
 const [confirm,setConfirm]=useState('');
 const [done,setDone]=useState(false);
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const submit=async(event:React.FormEvent)=>{
  event.preventDefault();setError('');
  if(password.length<PASSWORD_MIN_LENGTH){setError(`Şifre en az ${PASSWORD_MIN_LENGTH} karakter olmalıdır.`);return}
  if(password!==confirm){setError('Şifreler eşleşmiyor.');return}
  setBusy(true);
  try{
   await api.post('/auth/reset-password',{token,new_password:password});
   // Sıfırlama tüm oturumları düşürür ve yeni oturum açmaz; kullanıcı yeni
   // şifresiyle giriş ekranına döner.
   setDone(true);
  }catch(err:any){
   setError(errorDetail(err,'Şifre sıfırlanamadı.'));
  }finally{setBusy(false)}
 };
 // rota-govdesi: oturumsuz rotada AppShell yoktur, rota gövdesinin
 // kökü sayfa bileşeninin kendisidir (bkz. e2e/rota-envanteri.ts).
 return <Box data-testid="rota-govdesi" sx={{minHeight:'100dvh',display:'grid',placeItems:'center',p:2,background:'linear-gradient(135deg,#13253c 0%,#244e70 55%,#f28c28 140%)'}}>
  <Card sx={{width:'100%',maxWidth:430,borderRadius:3,boxShadow:'0 24px 70px rgba(0,0,0,.28)'}}><CardContent sx={{p:{xs:3,sm:5}}}>
   {done
    ?<Stack spacing={2.2}>
      <Typography variant="h5" fontWeight={900}>Şifreniz güncellendi</Typography>
      <Alert severity="success">Yeni şifrenizle giriş yapabilirsiniz. Açık olan diğer oturumlarınız kapatıldı.</Alert>
      <Button variant="contained" size="large" onClick={()=>navigate('/giris')}>Giriş Yap</Button>
     </Stack>
    :<Stack component="form" spacing={2.2} onSubmit={submit}>
      <Box>
       <Typography variant="h5" fontWeight={900}>Yeni şifre belirleyin</Typography>
       <Typography color="text.secondary" mt={.5}>En az {PASSWORD_MIN_LENGTH} karakterli, yaygın olmayan bir şifre seçin.</Typography>
      </Box>
      {!token&&<Alert severity="error">Bağlantıda token bulunamadı. Sıfırlama e-postasındaki bağlantıyı olduğu gibi açın.</Alert>}
      {error&&<Alert severity="error">{error}</Alert>}
      <TextField label="Yeni şifre" type="password" inputProps={{minLength:PASSWORD_MIN_LENGTH}} value={password} onChange={e=>setPassword(e.target.value)} autoFocus fullWidth/>
      <TextField label="Yeni şifre tekrar" type="password" inputProps={{minLength:PASSWORD_MIN_LENGTH}} value={confirm} onChange={e=>setConfirm(e.target.value)} fullWidth/>
      <Button type="submit" variant="contained" size="large" disabled={busy||!token}>{busy?<CircularProgress size={22}/>:'Şifreyi Güncelle'}</Button>
      <Typography textAlign="center"><Link component={RouterLink} to="/sifremi-unuttum">Bağlantının süresi dolduysa yeni bağlantı isteyin</Link></Typography>
     </Stack>}
  </CardContent></Card>
 </Box>;
}
