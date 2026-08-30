import React,{useState} from 'react';
import {Alert,Box,Button,Card,CardContent,CircularProgress,Link,Stack,TextField,Typography} from '@mui/material';
import {Link as RouterLink} from 'react-router-dom';
import {api,errorDetail} from '../api';

export default function ForgotPassword(){
 const [email,setEmail]=useState('');
 const [message,setMessage]=useState('');
 const [error,setError]=useState('');
 const [busy,setBusy]=useState(false);
 const submit=async(event:React.FormEvent)=>{
  event.preventDefault();setBusy(true);setError('');setMessage('');
  try{
   // Sunucu adresin kayıtlı olup olmadığını sızdırmaz; buradaki mesaj da bu
   // yüzden her durumda aynıdır. "Böyle bir kullanıcı yok" demek, giriş
   // ekranını hesap sorgulama aracına çevirirdi.
   const {data}=await api.post('/auth/forgot-password',{email});
   setMessage(data.message);
  }catch(err:any){
   setError(errorDetail(err,'İstek gönderilemedi.'));
  }finally{setBusy(false)}
 };
 // rota-govdesi: oturumsuz rotada AppShell yoktur, rota gövdesinin
 // kökü sayfa bileşeninin kendisidir (bkz. e2e/rota-envanteri.ts).
 return <Box data-testid="rota-govdesi" sx={{minHeight:'100dvh',display:'grid',placeItems:'center',p:2,background:'linear-gradient(135deg,#13253c 0%,#244e70 55%,#f28c28 140%)'}}>
  <Card sx={{width:'100%',maxWidth:430,borderRadius:3,boxShadow:'0 24px 70px rgba(0,0,0,.28)'}}><CardContent sx={{p:{xs:3,sm:5}}}>
   <Stack component="form" spacing={2.2} onSubmit={submit}>
    <Box>
     <Typography variant="h5" fontWeight={900}>Şifremi unuttum</Typography>
     <Typography color="text.secondary" mt={.5}>Hesabınızın e-posta adresini yazın; sıfırlama bağlantısını gönderelim.</Typography>
    </Box>
    {error&&<Alert severity="error">{error}</Alert>}
    {message&&<Alert severity="success">{message}</Alert>}
    <TextField label="E-posta" type="email" value={email} onChange={e=>setEmail(e.target.value)} autoFocus fullWidth required/>
    <Button type="submit" variant="contained" size="large" disabled={busy||!email.trim()}>{busy?<CircularProgress size={22}/>:'Sıfırlama Bağlantısı Gönder'}</Button>
    <Typography textAlign="center"><Link component={RouterLink} to="/giris">Giriş ekranına dön</Link></Typography>
   </Stack>
  </CardContent></Card>
 </Box>;
}
