import {useEffect,useState} from 'react';
import {Alert,Button,Chip,Dialog,DialogActions,DialogContent,DialogTitle,MenuItem,Stack,TextField,Typography} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import {api,errorDetail} from '../api';

type Supplier={id:number;name:string};
export type SupplierPriceImportResult={
 processed_rows:number;matched:number;inserted:number;updated:number;
 unmatched_count:number;unmatched_barcodes:string[];invalid_price_count:number;
 invalid_rows:{row:number;barcode?:string;message:string}[];
 foreign_currency_rows:number;currencies_requiring_rate:string[];
};
type Props={open:boolean;onClose:()=>void;onImported:()=>void};

const safeCsvCell=(value:string)=>{
 const guarded=/^[\s]*[=+\-@]/.test(value)?`'${value}`:value;
 return `"${guarded.replace(/"/g,'""')}"`;
};

export const unmatchedBarcodeCsv=(barcodes:string[])=>
 `\uFEFF${safeCsvCell('Bulunamayan Barkod')}\r\n${barcodes.map(safeCsvCell).join('\r\n')}`;

export default function SupplierPriceImportDialog({open,onClose,onImported}:Props){
 const [suppliers,setSuppliers]=useState<Supplier[]>([]);const [supplierId,setSupplierId]=useState('');
 const [file,setFile]=useState<File|null>(null);const [result,setResult]=useState<SupplierPriceImportResult|null>(null);
 const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 useEffect(()=>{if(!open)return;setFile(null);setResult(null);setError('');api.get('/suppliers',{params:{active:'active'}}).then(r=>setSuppliers(r.data||[])).catch(e=>setError(errorDetail(e,'Tedarikçiler yüklenemedi.')))},[open]);
 const upload=async()=>{
  if(!supplierId||!file){setError('Tedarikçi ve fiyat listesi seçin.');return}
  const form=new FormData();form.append('supplier_id',supplierId);form.append('file',file);
  setBusy(true);setError('');
  try{const {data}=await api.post<SupplierPriceImportResult>('/supplier-price-lists/import',form);setResult(data);onImported()}
  catch(e){setError(errorDetail(e,'Fiyat listesi yüklenemedi.'))}finally{setBusy(false)}
 };
 const downloadUnmatched=()=>{
  if(!result?.unmatched_barcodes.length)return;
  const blob=new Blob([unmatchedBarcodeCsv(result.unmatched_barcodes)],{type:'text/csv;charset=utf-8'});
  const href=URL.createObjectURL(blob);const link=document.createElement('a');link.href=href;link.download='eslesmeyen-barkodlar.csv';link.click();URL.revokeObjectURL(href);
 };
 return <Dialog open={open} onClose={busy?undefined:onClose} fullWidth maxWidth="sm">
  <DialogTitle>Tedarikçi Fiyat Listesi Yükle</DialogTitle>
  <DialogContent><Stack spacing={2} sx={{pt:1}}>
   <Typography variant="body2" color="text.secondary">Excel veya CSV listesindeki barkodlar ürünlerle eşleştirilir; mevcut tedarikçi fiyatları güncellenir.</Typography>
   {error&&<Alert severity="error">{error}</Alert>}
   <TextField select required label="Tedarikçi" value={supplierId} onChange={e=>setSupplierId(e.target.value)} disabled={busy}>
    {suppliers.map(supplier=><MenuItem key={supplier.id} value={String(supplier.id)}>{supplier.name}</MenuItem>)}
   </TextField>
   <Button component="label" variant="outlined" startIcon={<UploadFileIcon/>} disabled={busy}>
    {file?.name||'Excel / CSV Dosyası Seç'}
    <input hidden type="file" accept=".xlsx,.xlsm,.csv" onChange={e=>{setFile(e.target.files?.[0]||null);setResult(null);setError('')}}/>
   </Button>
   {result&&<Alert severity={result.unmatched_count||result.invalid_rows.length?'warning':'success'}>
    <Stack spacing={1}>
     <Typography fontWeight={700}>{result.matched} eşleşti · {result.unmatched_count} barkod bulunamadı · {result.invalid_price_count} geçersiz fiyat</Typography>
     <Stack direction="row" useFlexGap flexWrap="wrap" gap={1}>
      <Chip size="small" label={`${result.inserted} yeni`}/><Chip size="small" label={`${result.updated} güncellendi`}/>
      {result.foreign_currency_rows>0&&<Chip size="small" color="info" label={`${result.foreign_currency_rows} dövizli satır · ${result.currencies_requiring_rate.join(', ')}`}/>}
     </Stack>
     {result.invalid_rows.length>0&&<Stack spacing={0.25}>
      {result.invalid_rows.slice(0,20).map((row,index)=><Typography key={`${row.row}:${index}`} variant="caption">Satır {row.row}{row.barcode?` · ${row.barcode}`:''}: {row.message}</Typography>)}
      {result.invalid_rows.length>20&&<Typography variant="caption" color="text.secondary">+{result.invalid_rows.length-20} hata daha</Typography>}
     </Stack>}
     {result.unmatched_count>0&&<Button size="small" startIcon={<DownloadIcon/>} onClick={downloadUnmatched}>Eşleşmeyenleri İndir</Button>}
    </Stack>
   </Alert>}
  </Stack></DialogContent>
  <DialogActions><Button onClick={onClose} disabled={busy}>Kapat</Button><Button variant="contained" onClick={()=>void upload()} disabled={busy}>{busy?'Yükleniyor…':'Listeyi Yükle'}</Button></DialogActions>
 </Dialog>;
}
