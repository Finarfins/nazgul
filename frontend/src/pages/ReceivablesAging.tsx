import {useEffect,useState} from 'react';
import {
  Alert,Box,CircularProgress,Paper,Stack,TextField,Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import {Link as RouterLink} from 'react-router-dom';

import {api,errorDetail,money} from '../api';
import ResponsiveTable from '../components/ResponsiveTable';

type AgingDocument={
  id:number;document_no:string|null;due_date:string;remaining:string;
};
type AgingCustomer={
  customer_id:number;customer_name:string;not_due:string;days_1_30:string;
  days_31_60:string;days_61_90:string;days_90_plus:string;total:string;
  documents:AgingDocument[];
};
type AgingTotals=Omit<AgingCustomer,'customer_id'|'customer_name'|'documents'>;
type AgingReport={as_of:string;customers:AgingCustomer[];totals:AgingTotals};

const COLUMNS=[
  ['not_due','Vadesi Gelmemiş'],
  ['days_1_30','1–30 Gün'],
  ['days_31_60','31–60 Gün'],
  ['days_61_90','61–90 Gün'],
  ['days_90_plus','90+ Gün'],
] as const;

// İŞ TARİHİ TARAYICI SAATİNDEN TÜRETİLMEZ. Burada eskiden `localToday()`
// vardı: `as_of` varsayılanını tarayıcının saat diliminden hesaplıyordu. Arka
// uç ise bu tarihi Europe/Istanbul'da tanımlıyor (`app/business_time.py`,
// `business_today()`). Tarayıcısı başka bir dilimde olan her kullanıcı — ve
// UTC koşan CI — yanlış varsayılan tarih görüyordu: 21:00–23:59 UTC arasında
// istenen gün bir gün geride kalıyor ve o güne düşen alacak rapora hiç
// girmiyordu. Ölçüldü: 20:47 UTC koşusu geçti, 21:18 UTC koşusu düştü.
//
// Doğru kaynak arka uçtur: ilk istekte `as_of` GÖNDERİLMEZ, arka uç kendi iş
// gününü uygular ve yanıttaki `as_of` alanı tarih kutusuna yazılır. Kullanıcı
// tarihi elle değiştirirse bundan sonra onun seçimi gönderilir.

export function formatDate(value:string|null|undefined){
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}(?:$|T)/.test(value))return value;
  const [year,month,day]=value.slice(0,10).split('-');
  return `${day}.${month}.${year}`;
}

export default function ReceivablesAging(){
  const [asOf,setAsOf]=useState('');
  const [data,setData]=useState<AgingReport|null>(null);
  const [expanded,setExpanded]=useState<number|null>(null);
  const [error,setError]=useState('');

  useEffect(()=>{
    let active=true;
    setError('');
    api.get<AgingReport>('/reports/receivables-aging',asOf?{params:{as_of:asOf}}:undefined)
      .then(response=>{
        if(!active)return;
        setData(response.data);
        // Arka ucun iş günü tek kaynaktır; kutu ilk yüklemede ondan dolar.
        if(!asOf&&response.data.as_of)setAsOf(response.data.as_of);
      })
      .catch(requestError=>{
        if(active)setError(errorDetail(requestError,'Alacak yaşlandırma raporu yüklenemedi.'));
      });
    return()=>{active=false};
  },[asOf]);

  const columns:GridColDef<AgingCustomer>[]=[
    {field:'customer_name',headerName:'Müşteri',flex:1,minWidth:190},
    ...COLUMNS.map(([key,label])=>({
      field:key,headerName:label,width:140,align:'right' as const,headerAlign:'right' as const,
      renderCell:(params:{row:AgingCustomer})=>money(params.row[key]),
    })),
    {field:'total',headerName:'Toplam',width:140,align:'right',headerAlign:'right',
      renderCell:(params)=>money(params.row.total)},
  ];
  const expandedCustomer=data?.customers.find(customer=>customer.customer_id===expanded)??null;

  return <Stack spacing={2}>
    <Box>
      <Typography variant="h4" fontWeight={900}>Alacak Yaşlandırma</Typography>
      <Typography color="text.secondary">
        Açık harman vadeli alacaklar, seçilen tarihe göre müşteri ve gecikme süresi bazında gruplanır.
      </Typography>
    </Box>
    <TextField
      label="Rapor Tarihi"
      type="date"
      value={asOf}
      onChange={event=>setAsOf(event.target.value)}
      slotProps={{inputLabel:{shrink:true}}}
      sx={{maxWidth:240,'& input':{minHeight:{xs:44,md:39},boxSizing:'border-box'}}}
    />
    {error&&<Alert severity="error">{error}</Alert>}
    {!data&&!error?<Box p={5} textAlign="center"><CircularProgress aria-label="Yaşlandırma raporu yükleniyor"/></Box>:null}
    {data&&data.customers.length>0?<Box data-testid="receivables-aging-data-surface">
      <ResponsiveTable
        rows={data.customers}
        columns={columns}
        getRowId={customer=>customer.customer_id}
        cardTitle={customer=>customer.customer_name}
        cardSubtitle={customer=>`Toplam: ${money(customer.total)}`}
        cardFields={COLUMNS.map(([key,label])=>({label,value:(customer:AgingCustomer)=>money(customer[key])}))}
        onRowClick={customer=>setExpanded(current=>current===customer.customer_id?null:customer.customer_id)}
      />
    </Box>:null}
    {data&&data.customers.length===0?<Paper sx={{p:3,textAlign:'center'}}>
      <Typography color="text.secondary">Bu tarih için açık alacak bulunmuyor.</Typography>
    </Paper>:null}
    {expandedCustomer?<Paper variant="outlined" sx={{p:2,bgcolor:'action.hover'}}>
      <Stack spacing={1}>
        <Box component={RouterLink} to={`/musteriler/${expandedCustomer.customer_id}`}
          sx={{display:'inline-flex',alignItems:'center',minHeight:{xs:44,md:24},width:'fit-content'}}>
          Müşteri kartını aç
        </Box>
        {expandedCustomer.documents.map(document=><Stack
          key={document.id}
          direction={{xs:'column',sm:'row'}}
          justifyContent="space-between"
          gap={1}
        >
          <Typography>{document.document_no||`#${document.id}`}</Typography>
          <Typography color="text.secondary">Vade: {formatDate(document.due_date)}</Typography>
          <Typography fontWeight={800}>Kalan: {money(document.remaining)}</Typography>
        </Stack>)}
      </Stack>
    </Paper>:null}
    {data?<Paper variant="outlined" sx={{p:2}}>
      <Typography fontWeight={900} mb={1}>Genel Toplam</Typography>
      <Stack direction="row" flexWrap="wrap" useFlexGap gap={2}>
        {COLUMNS.map(([key,label])=><Box key={key} minWidth={130}>
          <Typography variant="caption" color="text.secondary">{label}</Typography>
          <Typography fontWeight={800}>{money(data.totals[key])}</Typography>
        </Box>)}
        <Box minWidth={130}>
          <Typography variant="caption" color="text.secondary">Toplam</Typography>
          <Typography fontWeight={900}>{money(data.totals.total)}</Typography>
        </Box>
      </Stack>
    </Paper>:null}
  </Stack>;
}
