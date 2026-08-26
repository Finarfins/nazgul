import {useCallback,useEffect,useState} from 'react';
import {Alert,Box,Chip,CircularProgress,Divider,LinearProgress,Paper,Stack,Table,TableBody,TableCell,TableRow,TextField,Tooltip,Typography} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';

import {api,errorDetail,money} from '../api';

// Emilim oranı (absorption rate): parça + servis brüt kârının işletme genel
// giderini ne kadar karşıladığı. Bayi kârlılığının ana göstergesi.
//   emilim % = (parça brüt kâr + servis brüt kâr) / genel gider x 100
// Artış 1: metrik + bileşen kırılımı. Genel gider ERP'de income_expenses
// tablosundan okunur; kayıt yoksa kullanıcı elle girer (kaynak ekranda yazar).
type Absorption={
  period:{date_from:string;date_to:string};
  parts:{revenue:string;cogs:string;gross_profit:string;counter_revenue:string;work_order_revenue:string;revenue_source:string;cogs_source:string};
  service:{labor_revenue:string;labor_cost:string;labor_cost_known:boolean;gross_profit_upper_bound:string;work_order_count:number;labor_cost_source:string};
  total_gross_profit_upper_bound:string;
  overhead:{amount:string;source:'user_supplied'|'income_expenses';entry_count:number};
  absorption_rate_upper_bound:string|null;
  is_upper_bound:boolean;
  rate_note:string|null;
};

const isoDay=(value:Date)=>value.toISOString().slice(0,10);
const yearStart=()=>`${new Date().getFullYear()}-01-01`;

// 100% ve üzeri: gider tamamen emiliyor (sağlıklı). 70-100 arası izlenmeli.
const rateTone=(rate:number):'success'|'warning'|'error'=>rate>=100?'success':rate>=70?'warning':'error';

export default function AbsorptionRate(){
  const [dateFrom,setDateFrom]=useState(yearStart());
  const [dateTo,setDateTo]=useState(isoDay(new Date()));
  const [overhead,setOverhead]=useState('');
  const [data,setData]=useState<Absorption|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');

  const load=useCallback(()=>{
    setLoading(true);setError('');
    api.get('/analytics/absorption-rate',{params:{date_from:dateFrom||undefined,date_to:dateTo||undefined,overhead:overhead.trim()||undefined}})
      .then(response=>setData(response.data))
      .catch(err=>{setData(null);setError(errorDetail(err,'Emilim oranı hesaplanamadı.'))})
      .finally(()=>setLoading(false));
  },[dateFrom,dateTo,overhead]);
  useEffect(()=>{const timer=setTimeout(load,250);return()=>clearTimeout(timer)},[load]);

  const rate=data?.absorption_rate_upper_bound==null?null:Number(data.absorption_rate_upper_bound);
  // Servis işçilik maliyeti takip edilmediği sürece oran bir ÜST SINIR:
  // ekranda asla kesin oranmış gibi gösterilmez (bkz. backend is_upper_bound).
  const bound=data?.is_upper_bound!==false;
  const tone=rate==null?'info':rateTone(rate);

  return <Stack spacing={2}>
    <Box>
      <Typography variant="h4" fontWeight={900}>Emilim Oranı (Absorption Rate)</Typography>
      <Typography color="text.secondary">
        Parça ve servis departmanlarının brüt kârı, işletme genel giderinin yüzde kaçını karşılıyor?
      </Typography>
    </Box>

    <Stack direction={{xs:'column',sm:'row'}} spacing={1.5} alignItems={{sm:'center'}}>
      <TextField label="Başlangıç" type="date" value={dateFrom} onChange={e=>setDateFrom(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
      <TextField label="Bitiş" type="date" value={dateTo} onChange={e=>setDateTo(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
      <TextField
        label="Genel Gider (TL)" value={overhead} onChange={e=>setOverhead(e.target.value)}
        placeholder={data?.overhead.source==='income_expenses'?'Gelir/Gider kaydından':'Elle girin'}
        helperText="Boş bırakılırsa dönem gider kayıtları kullanılır" sx={{minWidth:220}}/>
    </Stack>

    {error&&<Alert severity="error" onClose={()=>setError('')}>{error}</Alert>}

    {loading&&!data?<Box py={6} display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>:!data?null:<>
      <Paper sx={{p:3}}>
        <Stack direction={{xs:'column',sm:'row'}} spacing={2} alignItems={{sm:'flex-end'}} justifyContent="space-between">
          <Box>
            <Typography variant="caption" color="text.secondary">{bound?'EMİLİM ORANI — ÜST SINIR':'EMİLİM ORANI'}</Typography>
            <Typography variant="h2" fontWeight={900} color={rate==null?'text.secondary':`${tone}.main`} sx={{fontVariantNumeric:'tabular-nums'}}>
              {rate==null?'Tanımsız':`${bound?'≤ ':''}%${rate.toFixed(1)}`}
            </Typography>
            <Typography color="text.secondary" variant="body2">
              {data.period.date_from} – {data.period.date_to}
            </Typography>
          </Box>
          <Stack spacing={.5} alignItems={{xs:'flex-start',sm:'flex-end'}}>
            <Typography variant="body2" color="text.secondary">{bound?'Toplam Brüt Kâr (üst sınır)':'Toplam Brüt Kâr'}</Typography>
            <Typography variant="h5" fontWeight={800} sx={{fontVariantNumeric:'tabular-nums'}}>{money(data.total_gross_profit_upper_bound)}</Typography>
            <Typography variant="body2" color="text.secondary">Genel Gider {money(data.overhead.amount)}</Typography>
          </Stack>
        </Stack>
        {rate!=null&&<LinearProgress variant="determinate" color={tone} value={Math.min(rate,100)} sx={{mt:2,height:10,borderRadius:5}}/>}
        {data.rate_note&&<Alert severity={bound?'warning':'info'} sx={{mt:2}}>{data.rate_note}</Alert>}
      </Paper>

      <Stack direction={{xs:'column',md:'row'}} spacing={2}>
        <Paper sx={{p:2.5,flex:1}}>
          <Typography variant="h6" mb={1}>Parça Departmanı</Typography>
          <Table size="small">
            <TableBody sx={{'& td':{border:0,py:.6},'& td:last-of-type':{textAlign:'right',fontVariantNumeric:'tabular-nums'}}}>
              <TableRow><TableCell>Tezgâh satışı (KDV ve belge indirimi hariç)</TableCell><TableCell>{money(data.parts.counter_revenue)}</TableCell></TableRow>
              <TableRow><TableCell>İş emri parçaları</TableCell><TableCell>{money(data.parts.work_order_revenue)}</TableCell></TableRow>
              <TableRow><TableCell>Toplam ciro</TableCell><TableCell>{money(data.parts.revenue)}</TableCell></TableRow>
              <TableRow><TableCell>
                Maliyet (SMM)
                <Tooltip title={`Kaynak: ${data.parts.cogs_source} — güncel alış fiyatı yaklaşımı`}>
                  <InfoOutlinedIcon sx={{fontSize:15,ml:.5,verticalAlign:'-2px',color:'text.secondary'}}/>
                </Tooltip>
              </TableCell><TableCell>-{money(data.parts.cogs)}</TableCell></TableRow>
            </TableBody>
          </Table>
          <Divider sx={{my:1}}/>
          <Stack direction="row" justifyContent="space-between">
            <Typography fontWeight={800}>Brüt Kâr</Typography>
            <Typography fontWeight={800} sx={{fontVariantNumeric:'tabular-nums'}}>{money(data.parts.gross_profit)}</Typography>
          </Stack>
        </Paper>

        <Paper sx={{p:2.5,flex:1}}>
          <Typography variant="h6" mb={1}>Servis Departmanı</Typography>
          <Table size="small">
            <TableBody sx={{'& td':{border:0,py:.6},'& td:last-of-type':{textAlign:'right',fontVariantNumeric:'tabular-nums'}}}>
              <TableRow><TableCell>İşçilik geliri</TableCell><TableCell>{money(data.service.labor_revenue)}</TableCell></TableRow>
              <TableRow><TableCell>
                İşçilik maliyeti
                {data.service.labor_cost_source==='not_tracked'&&
                  <Tooltip title="ERP'de teknisyen maliyet oranı tutulmuyor; maliyet uydurulmaz, 0 olarak raporlanır.">
                    <InfoOutlinedIcon sx={{fontSize:15,ml:.5,verticalAlign:'-2px',color:'text.secondary'}}/>
                  </Tooltip>}
              </TableCell><TableCell>-{money(data.service.labor_cost)}</TableCell></TableRow>
              <TableRow><TableCell>Tamamlanan iş emri</TableCell><TableCell>{data.service.work_order_count}</TableCell></TableRow>
            </TableBody>
          </Table>
          <Divider sx={{my:1}}/>
          <Stack direction="row" justifyContent="space-between">
            <Typography fontWeight={800}>Brüt Kâr (üst sınır)</Typography>
            <Typography fontWeight={800} sx={{fontVariantNumeric:'tabular-nums'}}>{money(data.service.gross_profit_upper_bound)}</Typography>
          </Stack>
        </Paper>
      </Stack>

      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Chip variant="outlined" label={data.overhead.source==='user_supplied'
          ?'Genel gider: elle girildi'
          :`Genel gider: gider kayıtları (${data.overhead.entry_count} kayıt)`}/>
        {data.service.labor_cost_source==='not_tracked'&&
          <Chip variant="outlined" color="warning" label="İşçilik maliyeti takip edilmiyor — oran üst sınır"/>}
        <Chip variant="outlined" label="Parça maliyeti: ürün alış fiyatı"/>
      </Stack>
    </>}
  </Stack>;
}
