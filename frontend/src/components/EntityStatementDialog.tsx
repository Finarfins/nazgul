import React,{useEffect,useState} from 'react';
import {
 Alert,Box,Button,Chip,CircularProgress,Dialog,DialogActions,DialogContent,DialogTitle,
 Stack,Table,TableBody,TableCell,TableHead,TableRow,TextField,Typography
} from '@mui/material';
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf';
import {api,errorDetail,money,openAuthenticated} from '../api';

type Props={
 open:boolean;
 type:'customer'|'supplier';
 id:number;
 onClose:()=>void;
};

// Yerel tarih: toISOString() UTC'ye kaydırdığı için TR saatinde ayın ilk günü
// bir önceki aya düşebiliyor. Bu yüzden tarihler yerel olarak biçimlenir.
const isoDate=(value:Date)=>`${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;
const monthStart=()=>{const now=new Date();return isoDate(new Date(now.getFullYear(),now.getMonth(),1))};
const todayIso=()=>isoDate(new Date());
const dateTr=(value:string)=>value&&value.length>=10?`${value.slice(8,10)}.${value.slice(5,7)}.${value.slice(0,4)}`:(value||'-');

export default function EntityStatementDialog({open,type,id,onClose}:Props){
 const [query,setQuery]=useState(()=>({from:monthStart(),to:todayIso()}));
 const [dateFrom,setDateFrom]=useState(query.from);
 const [dateTo,setDateTo]=useState(query.to);
 const [data,setData]=useState<any|null>(null);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const base=type==='customer'?`/customers/${id}`:`/suppliers/${id}`;

 useEffect(()=>{
  if(!open)return;
  let cancelled=false;
  setLoading(true);setError('');
  api.get(`${base}/statement`,{params:{date_from:query.from,date_to:query.to}})
   .then(response=>{if(!cancelled)setData(response.data)})
   .catch(err=>{if(!cancelled){setData(null);setError(errorDetail(err,'Ekstre yüklenemedi.'))}})
   .finally(()=>{if(!cancelled)setLoading(false)});
  return()=>{cancelled=true};
 },[open,base,query]);

 const downloadPdf=()=>openAuthenticated(
  `${base}/statement.pdf?date_from=${query.from}&date_to=${query.to}`,
  `ekstre-${query.from}-${query.to}.pdf`,
 );

 return <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
  <DialogTitle>Cari Hesap Ekstresi</DialogTitle>
  <DialogContent>
   <Stack direction={{xs:'column',sm:'row'}} spacing={1.5} alignItems={{sm:'center'}} mt={1} mb={2}>
    <TextField label="Başlangıç" type="date" value={dateFrom} onChange={x=>setDateFrom(x.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
    <TextField label="Bitiş" type="date" value={dateTo} onChange={x=>setDateTo(x.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
    <Button variant="contained" onClick={()=>setQuery({from:dateFrom,to:dateTo})}>Getir</Button>
   </Stack>

   {error&&<Alert severity="error" sx={{mb:2}}>{error}</Alert>}
   {loading&&<Box textAlign="center" py={4}><CircularProgress/></Box>}

   {!loading&&data&&<>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={1} mb={1.5}>
     <Box>
      <Typography fontWeight={900}>{data.entity?.name}</Typography>
      <Typography variant="body2" color="text.secondary">
       {data.entity?.tax_number?`VKN/TCKN: ${data.entity.tax_number}`:'VKN/TCKN yok'}
       {' · '}{dateTr(data.date_from)} - {dateTr(data.date_to)}
      </Typography>
     </Box>
     <Stack alignItems={{sm:'flex-end'}}>
      <Typography variant="body2" color="text.secondary">Devir: {money(data.opening_balance)}</Typography>
      <Typography fontWeight={900}>Kapanış: {money(data.closing_balance)}</Typography>
     </Stack>
    </Stack>

    {data.truncated&&<Alert severity="warning" sx={{mb:1.5}}>
     Bu dönemdeki hareket sayısı listeleme sınırını aştı; satırlar kısaltıldı.
     Açılış ve kapanış bakiyeleri dönemin tamamını kapsar, tam döküm için daha
     dar bir aralık seçin.
    </Alert>}

    <Table size="small">
     <TableHead><TableRow>
      <TableCell>Tarih</TableCell><TableCell>Açıklama</TableCell><TableCell>Belge No</TableCell>
      <TableCell align="right">Borç</TableCell><TableCell align="right">Alacak</TableCell>
      <TableCell align="right">Bakiye</TableCell>
     </TableRow></TableHead>
     <TableBody>
      <TableRow>
       <TableCell>{dateTr(data.date_from)}</TableCell>
       <TableCell colSpan={4}><strong>Devir (açılış bakiyesi)</strong></TableCell>
       <TableCell align="right"><strong>{money(data.opening_balance)}</strong></TableCell>
      </TableRow>
      {(data.lines||[]).map((line:any)=><TableRow key={`${line.kind}-${line.reference_id}`}>
       <TableCell>{dateTr(line.entry_date)}</TableCell>
       <TableCell>{line.label}</TableCell>
       <TableCell>{line.document_no||'-'}</TableCell>
       <TableCell align="right">{Number(line.debit)?money(line.debit):''}</TableCell>
       <TableCell align="right">{Number(line.credit)?money(line.credit):''}</TableCell>
       <TableCell align="right">{money(line.balance)}</TableCell>
      </TableRow>)}
      {!(data.lines||[]).length&&<TableRow><TableCell colSpan={6}>
       <Typography color="text.secondary" py={1}>Bu dönemde hareket yok.</Typography>
      </TableCell></TableRow>}
      <TableRow>
       <TableCell>{dateTr(data.date_to)}</TableCell>
       <TableCell colSpan={2}><strong>Dönem toplamı / kapanış</strong></TableCell>
       <TableCell align="right"><strong>{money(data.total_debit)}</strong></TableCell>
       <TableCell align="right"><strong>{money(data.total_credit)}</strong></TableCell>
       <TableCell align="right"><strong>{money(data.closing_balance)}</strong></TableCell>
      </TableRow>
     </TableBody>
    </Table>

    <Box mt={1.5}><Chip size="small" label={`${data.line_count} hareket`}/></Box>
   </>}
  </DialogContent>
  <DialogActions>
   <Button onClick={onClose}>Kapat</Button>
   <Button variant="contained" startIcon={<PictureAsPdfIcon/>} disabled={!data} onClick={downloadPdf}>
    PDF İndir / Yazdır
   </Button>
  </DialogActions>
 </Dialog>;
}
