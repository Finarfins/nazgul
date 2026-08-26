import {useCallback,useEffect,useState} from 'react';
import {Alert,Autocomplete,Box,Button,Chip,CircularProgress,IconButton,Paper,Stack,Table,TableBody,TableCell,TableHead,TableRow,TextField,Typography,useTheme} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import {Link as RouterLink} from 'react-router-dom';

import {api,errorDetail} from '../api';
import {useAuth} from '../AuthContext';

type Supersession={
  id:number;old_product_id:number;new_product_id:number;note:string|null;created_at:string;
  old_name:string;old_code:string;new_name:string;new_code:string;
};
type ProductOption={id:number;name:string;product_code:string|null};

const label=(p:ProductOption|null)=>p?`${p.product_code?p.product_code+' — ':''}${p.name}`:'';

// Parça Supersession yönetimi: OEM bir parça numarasını değiştirdiğinde
// eski -> yeni eşlemesi burada tanımlanır; aramalar zinciri takip ederek
// güncel parçaya çözülür. Renk/ölçüler merkezi temadan gelir (Sungur standardı).
export default function PartSupersessions(){
  const theme=useTheme();
  const {can}=useAuth();
  const canWrite=can('stock');
  const [rows,setRows]=useState<Supersession[]>([]);
  const [products,setProducts]=useState<ProductOption[]>([]);
  const [oldProduct,setOldProduct]=useState<ProductOption|null>(null);
  const [newProduct,setNewProduct]=useState<ProductOption|null>(null);
  const [note,setNote]=useState('');
  const [q,setQ]=useState('');
  const [loading,setLoading]=useState(false);
  const [saving,setSaving]=useState(false);
  const [error,setError]=useState('');

  const load=useCallback(()=>{
    setLoading(true);
    api.get('/part-supersessions',{params:{q:q||undefined}})
      .then(r=>setRows(r.data))
      .catch(err=>setError(errorDetail(err,'Supersession listesi yüklenemedi.')))
      .finally(()=>setLoading(false));
  },[q]);
  useEffect(()=>{load()},[load]);
  useEffect(()=>{api.get('/products',{params:{limit:2000}}).then(r=>setProducts(r.data)).catch(()=>setProducts([]));},[]);

  const add=async()=>{
    if(!oldProduct||!newProduct)return setError('Eski ve yeni parçayı seçin.');
    setSaving(true);setError('');
    try{
      await api.post('/part-supersessions',{old_product_id:oldProduct.id,new_product_id:newProduct.id,note:note.trim()||null});
      setOldProduct(null);setNewProduct(null);setNote('');load();
    }catch(err){setError(errorDetail(err,'Supersession kaydedilemedi.'))}
    finally{setSaving(false)}
  };
  const remove=async(id:number)=>{
    setError('');
    try{await api.delete(`/part-supersessions/${id}`);load()}
    catch(err){setError(errorDetail(err,'Supersession silinemedi.'))}
  };

  return <Box>
    <Typography variant="h5" fontWeight={700} mb={.5}>Parça Supersession</Typography>
    <Typography color="text.secondary" mb={2}>OEM parça numarası değişimleri: eski parça aramaları güncel parçaya yönlenir.</Typography>

    {canWrite&&<Paper variant="outlined" sx={{p:'18px',mb:2,borderRadius:`${theme.shape.borderRadius}px`}}>
      <Typography fontWeight={650} mb={1.5}>Yeni Supersession</Typography>
      <Stack direction={{xs:'column',md:'row'}} spacing={1.2} alignItems={{md:'center'}}>
        <Autocomplete sx={{flex:1,minWidth:220}} options={products} value={oldProduct}
          isOptionEqualToValue={(a,b)=>a.id===b.id} getOptionLabel={label}
          onChange={(_,v)=>setOldProduct(v)} renderInput={p=><TextField {...p} label="Eski parça" size="small"/>}/>
        <SwapHorizIcon sx={{color:'text.secondary',display:{xs:'none',md:'block'}}}/>
        <Autocomplete sx={{flex:1,minWidth:220}} options={products} value={newProduct}
          isOptionEqualToValue={(a,b)=>a.id===b.id} getOptionLabel={label}
          onChange={(_,v)=>setNewProduct(v)} renderInput={p=><TextField {...p} label="Yeni (güncel) parça" size="small"/>}/>
        <TextField label="Not" value={note} onChange={e=>setNote(e.target.value)} size="small" sx={{flex:1,minWidth:180}}/>
        <Button variant="contained" startIcon={<AddIcon/>} onClick={add} disabled={saving}>Ekle</Button>
      </Stack>
    </Paper>}

    {error&&<Alert severity="error" sx={{mb:2}} onClose={()=>setError('')}>{error}</Alert>}

    <Stack direction="row" mb={1.5}>
      <TextField label="Ara (parça adı / kodu)" value={q} onChange={e=>setQ(e.target.value)} size="small" sx={{minWidth:260}}/>
    </Stack>

    <Paper variant="outlined" sx={{overflow:'hidden',borderRadius:`${theme.shape.borderRadius}px`}}>
      {loading?<Box p={4} display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>:
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Eski Parça</TableCell>
            <TableCell/>
            <TableCell>Güncel Parça</TableCell>
            <TableCell>Not</TableCell>
            {canWrite&&<TableCell align="right">İşlem</TableCell>}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.length===0?<TableRow><TableCell colSpan={canWrite?5:4}><Typography color="text.secondary" py={2} textAlign="center">Supersession kaydı yok.</Typography></TableCell></TableRow>:
          rows.map(row=><TableRow key={row.id} hover>
            <TableCell>
              <RouterLink to={`/urunler/${row.old_product_id}`} style={{color:'inherit',fontWeight:700,textDecoration:'none'}}>{row.old_name}</RouterLink>
              {row.old_code&&<Chip label={row.old_code} size="small" variant="outlined" sx={{ml:1}}/>}
            </TableCell>
            <TableCell><SwapHorizIcon fontSize="small" sx={{color:'primary.main',verticalAlign:'middle'}}/></TableCell>
            <TableCell>
              <RouterLink to={`/urunler/${row.new_product_id}`} style={{color:'inherit',fontWeight:700,textDecoration:'none'}}>{row.new_name}</RouterLink>
              {row.new_code&&<Chip label={row.new_code} size="small" color="primary" variant="outlined" sx={{ml:1}}/>}
            </TableCell>
            <TableCell><Typography variant="body2" color="text.secondary">{row.note||'—'}</Typography></TableCell>
            {canWrite&&<TableCell align="right"><IconButton size="small" color="error" onClick={()=>remove(row.id)} aria-label="Supersession sil"><DeleteOutlineIcon fontSize="small"/></IconButton></TableCell>}
          </TableRow>)}
        </TableBody>
      </Table>}
    </Paper>
  </Box>;
}
