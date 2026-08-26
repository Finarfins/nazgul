import {useCallback,useEffect,useState} from 'react';
import {Alert,Box,Chip,CircularProgress,MenuItem,Paper,Stack,Table,TableBody,TableCell,TableHead,TableRow,TextField,Typography} from '@mui/material';
import {Link as RouterLink} from 'react-router-dom';

import {api,errorDetail,money} from '../api';

type Receivable={
  id:number;customer_id:number;customer_name:string;transaction_date:string;
  document_no:string|null;due_date:string|null;total:number;paid:number;
  outstanding:number;status:'ACIK'|'VADESI_GECTI'|'ODENDI';
};
type Summary={count:number;total_outstanding:number;total_overdue:number};

// Sungur tasarım standardı token'ları (bkz. docs: sungur_tasarim_standardi).
// Merkezi theme.ts Codex tarafından kurulana kadar geçici olarak burada;
// tema geldiğinde bu sabitler silinip theme.palette'e bağlanacak.
const T={
  primary:'#2f7d32',primaryTint:'#eaf3ea',
  ink:'#1c2620',muted:'#6b7a70',
  surface:'#ffffff',line:'#e5e9e3',
  danger:'#c0392b',dangerTint:'#fbeceb',
  amber:'#b7791f',amberTint:'#fbf1de',
  shadow:'0 6px 20px rgba(28,38,32,.06)',
};
const cardSx={p:'18px',bgcolor:T.surface,border:`1px solid ${T.line}`,borderRadius:'14px',boxShadow:T.shadow};
const tabular={fontVariantNumeric:'tabular-nums'} as const;

const STATUS_LABEL:Record<string,string>={ACIK:'Açık',VADESI_GECTI:'Vadesi Geçti',ODENDI:'Ödendi'};
// Durum çipleri: tint zemin + koyu metin, 650 ağırlık (standart §4).
const STATUS_CHIP_SX:Record<string,object>={
  ACIK:{bgcolor:T.amberTint,color:T.amber},
  VADESI_GECTI:{bgcolor:T.dangerTint,color:T.danger},
  ODENDI:{bgcolor:T.primaryTint,color:T.primary},
};

// Harman vadesi (hasat sonu tahsilat) alacak ekranı: harman vadeli satışlar
// vade tarihine göre sıralı, vadesi geçen satırlar vurgulu, toplamlar üstte.
// Hatırlatmalar bu artışın kapsamı dışında (bildirim seam'ine sonra bağlanır).
export default function Receivables(){
  const [rows,setRows]=useState<Receivable[]>([]);
  const [summary,setSummary]=useState<Summary>({count:0,total_outstanding:0,total_overdue:0});
  const [status,setStatus]=useState('');
  const [dueBefore,setDueBefore]=useState('');
  const [sort,setSort]=useState('due_asc');
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');

  const load=useCallback(()=>{
    setLoading(true);setError('');
    api.get('/receivables',{params:{term:'harman',status:status||undefined,due_before:dueBefore||undefined,sort}})
      .then(response=>{setRows(response.data.receivables||[]);setSummary(response.data.summary||{count:0,total_outstanding:0,total_overdue:0})})
      .catch(err=>setError(errorDetail(err,'Alacaklar yüklenemedi.')))
      .finally(()=>setLoading(false));
  },[status,dueBefore,sort]);
  useEffect(()=>{load()},[load]);

  return <Box>
    <Typography sx={{fontSize:22,fontWeight:700,color:T.ink}} mb={.5}>Harman Vadesi / Alacaklar</Typography>
    <Typography sx={{color:T.muted}} mb={2}>Harman vadeli satışlardan doğan alacaklar, vade tarihine göre sıralı.</Typography>

    <Stack direction={{xs:'column',sm:'row'}} spacing={2} mb={2}>
      <Paper elevation={0} sx={{...cardSx,flex:1}}>
        <Typography sx={{fontSize:13,color:T.muted}}>Toplam Açık Alacak</Typography>
        <Typography sx={{fontSize:26,fontWeight:800,color:T.ink,...tabular}}>{money(summary.total_outstanding)}</Typography>
      </Paper>
      <Paper elevation={0} sx={{...cardSx,flex:1,...(Number(summary.total_overdue)>0?{borderColor:T.danger,bgcolor:T.dangerTint}:{})}}>
        <Typography sx={{fontSize:13,color:T.muted}}>Vadesi Geçen</Typography>
        <Typography sx={{fontSize:26,fontWeight:800,color:Number(summary.total_overdue)>0?T.danger:T.ink,...tabular}}>{money(summary.total_overdue)}</Typography>
      </Paper>
    </Stack>

    <Stack direction={{xs:'column',sm:'row'}} spacing={1.2} mb={2}>
      <TextField select label="Durum" value={status} onChange={e=>setStatus(e.target.value)} size="small" sx={{minWidth:170}}>
        <MenuItem value="">Tümü</MenuItem>
        <MenuItem value="ACIK">Açık</MenuItem>
        <MenuItem value="VADESI_GECTI">Vadesi Geçti</MenuItem>
        <MenuItem value="ODENDI">Ödendi</MenuItem>
      </TextField>
      <TextField label="Vade Tarihine Kadar" type="date" value={dueBefore} onChange={e=>setDueBefore(e.target.value)} size="small" slotProps={{inputLabel:{shrink:true}}}/>
      <TextField select label="Sıralama" value={sort} onChange={e=>setSort(e.target.value)} size="small" sx={{minWidth:170}}>
        <MenuItem value="due_asc">Vade (yakın → uzak)</MenuItem>
        <MenuItem value="due_desc">Vade (uzak → yakın)</MenuItem>
      </TextField>
    </Stack>

    {error&&<Alert severity="error" sx={{mb:2}}>{error}</Alert>}

    <Paper elevation={0} sx={{...cardSx,p:0,overflow:'hidden'}}>
      {loading?<Box p={4} display="grid" sx={{placeItems:'center'}}><CircularProgress sx={{color:T.primary}}/></Box>:
      <Table size="small" sx={{'& td,& th':{borderBottom:`1px solid ${T.line}`,fontSize:14,color:T.ink},'& th':{fontWeight:650,fontSize:13,color:T.muted}}}>
        <TableHead>
          <TableRow>
            <TableCell>Müşteri</TableCell>
            <TableCell>Belge</TableCell>
            <TableCell>Vade</TableCell>
            <TableCell align="right">Tutar</TableCell>
            <TableCell align="right">Ödenen</TableCell>
            <TableCell align="right">Kalan</TableCell>
            <TableCell>Durum</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.length===0?<TableRow><TableCell colSpan={7}><Typography sx={{color:T.muted}} py={2} textAlign="center">Harman vadeli alacak bulunamadı.</Typography></TableCell></TableRow>:
          rows.map(row=><TableRow key={row.id} hover sx={row.status==='VADESI_GECTI'?{bgcolor:T.dangerTint,'&:hover':{bgcolor:T.dangerTint}}:{'&:hover':{bgcolor:T.primaryTint}}}>
            <TableCell><RouterLink to={`/musteriler/${row.customer_id}`} style={{color:T.ink,fontWeight:700,textDecoration:'none'}}>{row.customer_name}</RouterLink></TableCell>
            <TableCell>{row.document_no||`#${row.id}`}</TableCell>
            <TableCell sx={row.status==='VADESI_GECTI'?{color:T.danger,fontWeight:650}:undefined}>{row.due_date||'—'}</TableCell>
            <TableCell align="right" sx={tabular}>{money(row.total)}</TableCell>
            <TableCell align="right" sx={tabular}>{money(row.paid)}</TableCell>
            <TableCell align="right" sx={{...tabular,fontWeight:800,...(row.status==='VADESI_GECTI'?{color:T.danger}:{})}}>{money(row.outstanding)}</TableCell>
            <TableCell><Chip size="small" label={STATUS_LABEL[row.status]||row.status} sx={{fontWeight:650,fontSize:12,...STATUS_CHIP_SX[row.status]}}/></TableCell>
          </TableRow>)}
        </TableBody>
      </Table>}
    </Paper>
  </Box>;
}
