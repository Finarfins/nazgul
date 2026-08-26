import {useEffect,useState} from 'react';
import {Alert,Box,Button,Card,CardContent,CircularProgress,Stack,Typography} from '@mui/material';
import {useNavigate,useSearchParams} from 'react-router-dom';
import {api,errorDetail} from '../api';

export default function VerifyEmail(){
 const [params]=useSearchParams();
 const navigate=useNavigate();
 const [message,setMessage]=useState('');
 const [error,setError]=useState('');
 useEffect(()=>{
  const token=params.get('token');
  if(!token){setError('Doğrulama bağlantısında token bulunamadı.');return}
  api.get('/auth/verify-email',{params:{token}})
   .then(({data})=>setMessage(data.message))
   .catch(err=>setError(errorDetail(err,'E-posta doğrulanamadı.')));
 },[params]);
 return <Box sx={{minHeight:'100dvh',display:'grid',placeItems:'center',p:2}}>
  <Card sx={{width:'100%',maxWidth:480}}><CardContent sx={{p:4}}>
   <Stack spacing={2}>
    <Typography variant="h4" fontWeight={800}>E-posta doğrulama</Typography>
    {!message&&!error&&<Box textAlign="center"><CircularProgress/></Box>}
    {message&&<Alert severity="success">{message}</Alert>}
    {error&&<Alert severity="error">{error}</Alert>}
    <Button variant="contained" onClick={()=>navigate('/giris')}>Giriş ekranına git</Button>
   </Stack>
  </CardContent></Card>
 </Box>;
}
