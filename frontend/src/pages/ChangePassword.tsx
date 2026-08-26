import React from 'react';
import {useState} from 'react';
import {Alert,Box,Button,Card,CardContent,CircularProgress,Stack,TextField,Typography} from '@mui/material';
import {Navigate,useNavigate} from 'react-router-dom';
import {useAuth} from '../AuthContext';
import {errorDetail} from '../api';
import {PASSWORD_MIN_LENGTH} from '../passwordPolicy';

export default function ChangePassword(){
 const {user,changePassword}=useAuth();const nav=useNavigate();
 const [currentPassword,setCurrentPassword]=useState('');const [newPassword,setNewPassword]=useState('');const [confirm,setConfirm]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 if(!user)return <Navigate to="/giris" replace/>;
 if(!user.must_change_password)return <Navigate to="/" replace/>;
 const submit=async(e:React.FormEvent)=>{e.preventDefault();setError('');if(newPassword.length<PASSWORD_MIN_LENGTH){setError(`Yeni şifre en az ${PASSWORD_MIN_LENGTH} karakter olmalıdır.`);return}if(newPassword!==confirm){setError('Yeni şifreler eşleşmiyor.');return}setBusy(true);try{await changePassword(currentPassword,newPassword);nav('/',{replace:true})}catch(err:any){setError(errorDetail(err,'Şifre değiştirilemedi.'))}finally{setBusy(false)}};
 return <Box sx={{minHeight:'100dvh',display:'grid',placeItems:'center',p:2,background:'linear-gradient(135deg,#13253c 0%,#244e70 55%,#f28c28 140%)'}}><Card sx={{width:'100%',maxWidth:480,borderRadius:3}}><CardContent sx={{p:{xs:3,sm:5}}}><Stack component="form" spacing={2} onSubmit={submit}><Typography variant="h4" fontWeight={900}>Şifrenizi Değiştirin</Typography><Alert severity="warning">İlk kurulum şifresiyle devam edemezsiniz. En az {PASSWORD_MIN_LENGTH} karakterli, yaygın olmayan yeni bir şifre belirleyin.</Alert>{error&&<Alert severity="error">{error}</Alert>}<TextField label="Mevcut şifre" type="password" value={currentPassword} onChange={e=>setCurrentPassword(e.target.value)} autoFocus/><TextField label="Yeni şifre" type="password" inputProps={{minLength:PASSWORD_MIN_LENGTH}} value={newPassword} onChange={e=>setNewPassword(e.target.value)}/><TextField label="Yeni şifre tekrar" type="password" inputProps={{minLength:PASSWORD_MIN_LENGTH}} value={confirm} onChange={e=>setConfirm(e.target.value)}/><Button type="submit" variant="contained" size="large" disabled={busy}>{busy?<CircularProgress size={22}/>: 'Şifreyi Değiştir'}</Button></Stack></CardContent></Card></Box>
}
