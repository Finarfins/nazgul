import React,{useState} from 'react';
import {Alert,Box,Button,Card,CardContent,CircularProgress,Link,Stack,TextField,Typography} from '@mui/material';
import {Link as RouterLink,useNavigate} from 'react-router-dom';
import {useAuth} from '../AuthContext';
import {errorDetail} from '../api';

export default function Login(){
 const [username,setUsername]=useState('');
 const [password,setPassword]=useState('');
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const {login}=useAuth();
 const navigate=useNavigate();
 const submit=async(event:React.FormEvent)=>{
  event.preventDefault();setBusy(true);setError('');
  try{
   const mustChange=await login(username,password);
   navigate(mustChange?'/sifre-degistir':'/');
  }catch(err:any){
   const code=err?.response?.data?.detail?.code;
   setError(code==='EMAIL_VERIFICATION_REQUIRED'
    ?'Giriş yapmadan önce e-posta adresinizi doğrulayın.'
    :errorDetail(err,'Giriş başarısız'));
  }finally{setBusy(false)}
 };
 return <Box sx={{minHeight:'100dvh',display:'grid',placeItems:'center',p:2,background:'linear-gradient(135deg,#13253c 0%,#244e70 55%,#f28c28 140%)'}}>
  <Card sx={{width:'100%',maxWidth:430,borderRadius:3,boxShadow:'0 24px 70px rgba(0,0,0,.28)'}}><CardContent sx={{p:{xs:3,sm:5}}}>
   <Stack component="form" spacing={2.2} onSubmit={submit}>
    <Box><Typography variant="h4" fontWeight={900}>HARMAN <Box component="span" color="secondary.dark">ZAMANI</Box></Typography><Typography color="text.secondary" mt={.5}>Güvenli işletme yönetim paneli</Typography></Box>
    {error&&<Alert severity="error">{error}</Alert>}
    <TextField label="Kullanıcı adı veya e-posta" value={username} onChange={e=>setUsername(e.target.value)} autoFocus fullWidth/>
    <TextField label="Şifre" type="password" value={password} onChange={e=>setPassword(e.target.value)} fullWidth/>
    <Button type="submit" variant="contained" size="large" disabled={busy}>{busy?<CircularProgress size={22}/>:'Giriş Yap'}</Button>
    <Typography textAlign="center"><Link component={RouterLink} to="/sifremi-unuttum">Şifremi unuttum</Link></Typography>
    <Typography textAlign="center">Hesabınız yok mu? <Link component={RouterLink} to="/kayit">Kayıt ol</Link></Typography>
    <Alert severity="info" variant="outlined">İlk kurulum hesabında güvenlik nedeniyle şifre değişikliği zorunludur.</Alert>
   </Stack>
  </CardContent></Card>
 </Box>;
}
