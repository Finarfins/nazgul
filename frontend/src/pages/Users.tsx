import React, {useEffect,useMemo,useState} from 'react';
import {Alert,Box,Button,Chip,Dialog,DialogActions,DialogContent,DialogTitle,FormControl,InputLabel,MenuItem,Select,Stack,TextField,Typography} from '@mui/material';
import {DataGrid,type GridColDef} from '@mui/x-data-grid';
import {api} from '../api';
import {useAuth} from '../AuthContext';
import {PASSWORD_MIN_LENGTH,PASSWORD_RULE_LABEL} from '../passwordPolicy';

type Row={id:number;username:string;display_name:string;role:string;is_active:boolean;last_login_at?:string};
const roleRank:Record<string,number>={admin:100,yonetici:80,muhasebe:60,satis:40,depo:40,rapor:20};
const allRoles=['admin','yonetici','muhasebe','satis','depo','rapor'];

export default function Users(){
 const {user}=useAuth();
 const [rows,setRows]=useState<Row[]>([]);
 const [open,setOpen]=useState(false);
 const [form,setForm]=useState({username:'',display_name:'',password:'',role:'rapor'});
 const [error,setError]=useState('');
 const assignableRoles=useMemo(()=>user?.role==='admin'?allRoles:allRoles.filter(role=>(roleRank[role]||0)<(roleRank[user?.role||'']||0)),[user?.role]);
 const load=()=>api.get('/users').then(r=>setRows(r.data));
 useEffect(()=>{void load()},[]);
 const save=async()=>{setError('');try{await api.post('/users',form);setOpen(false);setForm({username:'',display_name:'',password:'',role:'rapor'});await load()}catch(e:any){setError(e?.response?.data?.detail||'Kayıt başarısız')}};
 const canManage=(row:Row)=>Boolean(user&&(user.role==='admin'||(roleRank[row.role]||0)<(roleRank[user.role]||0))&&row.id!==user.id);
 const toggle=async(row:Row)=>{setError('');try{await api.patch(`/users/${row.id}/status`,null,{params:{is_active:!row.is_active}});await load()}catch(e:any){setError(e?.response?.data?.detail||'Durum değiştirilemedi')}};
 const cols:GridColDef[]=[
  {field:'username',headerName:'Kullanıcı',flex:1,minWidth:130},
  {field:'display_name',headerName:'Ad Soyad',flex:1.2,minWidth:180},
  {field:'role',headerName:'Rol',width:130,renderCell:p=><Chip size="small" label={p.value}/>},
  {field:'is_active',headerName:'Durum',width:110,renderCell:p=><Chip size="small" color={p.value?'success':'default'} label={p.value?'Aktif':'Pasif'}/>},
  {field:'last_login_at',headerName:'Son Giriş',width:190,valueFormatter:v=>v?new Date(v).toLocaleString('tr-TR'):'-'},
  {field:'action',headerName:'',width:130,sortable:false,renderCell:p=>canManage(p.row)?<Button size="small" color={p.row.is_active?'error':'success'} onClick={()=>toggle(p.row)}>{p.row.is_active?'Pasife Al':'Aktifleştir'}</Button>:null},
 ];
 return <Stack spacing={2}>
  <Box display="flex" justifyContent="space-between" alignItems="center"><Typography variant="h5" fontWeight={900}>Kullanıcılar ve Yetkiler</Typography><Button variant="contained" onClick={()=>setOpen(true)}>Yeni Kullanıcı</Button></Box>
  {error&&<Alert severity="error">{error}</Alert>}
  <Box sx={{height:560,bgcolor:'background.paper',borderRadius:2}}><DataGrid rows={rows} columns={cols} disableRowSelectionOnClick/></Box>
  <Dialog open={open} onClose={()=>setOpen(false)} fullWidth maxWidth="sm"><DialogTitle>Yeni Kullanıcı</DialogTitle><DialogContent><Stack spacing={2} mt={1}><TextField label="Kullanıcı adı" value={form.username} onChange={e=>setForm({...form,username:e.target.value})}/><TextField label="Ad soyad" value={form.display_name} onChange={e=>setForm({...form,display_name:e.target.value})}/><TextField label="Şifre" type="password" inputProps={{minLength:PASSWORD_MIN_LENGTH}} helperText={PASSWORD_RULE_LABEL} value={form.password} onChange={e=>setForm({...form,password:e.target.value})}/><FormControl><InputLabel>Rol</InputLabel><Select label="Rol" value={form.role} onChange={e=>setForm({...form,role:e.target.value})}>{assignableRoles.map(r=><MenuItem key={r} value={r}>{r}</MenuItem>)}</Select></FormControl></Stack></DialogContent><DialogActions><Button onClick={()=>setOpen(false)}>Vazgeç</Button><Button variant="contained" onClick={save}>Kaydet</Button></DialogActions></Dialog>
 </Stack>;
}
