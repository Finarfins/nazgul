import React from 'react';
import {useCallback,useEffect,useMemo,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Alert,Box,Card,CardContent,Chip,CircularProgress,Grid,Link,Paper,Stack,Table,TableBody,TableCell,TableContainer,TableHead,TableRow,TextField,Tooltip,Typography} from '@mui/material';
import {Bar,BarChart,CartesianGrid,Cell,Legend,Pie,PieChart,ResponsiveContainer,Tooltip as ChartTooltip,XAxis,YAxis} from 'recharts';

import {api,money,errorDetail} from '../api';

// Satın Alma Panosu: #153 motorunun ve alış belgelerinin GÖRÜNÜR katmanı.
// Hiçbir tutar burada hesaplanmaz — trend, kırılım ve paylar sunucudan hazır
// (KDV dahil, Decimal string) gelir; ekran yalnızca biçimlendirir ve çizer.
// Grafik yüksekliği için Number()'a çevrilen değerler sadece piksel geometrisi
// içindir; gösterilen her rakam sunucunun gönderdiği string'den gelir. PARA
// KARARI Number üzerinden türetilmez: "kuyruk var mı" gibi eşik karşılaştırmaları
// sunucuda Decimal ile verilir ve has_other_* bayraklarıyla gelir.
// Yeniden sipariş bölümü kasıtlı olarak SALT OKUNURDUR: öneri bir taslaktır,
// sipariş verme akışı Tedarikçi Karşılaştırma ekranında kalır.
const CHART_COLORS=['#f28c28','#2e7d32','#0288d1','#7b1fa2','#c62828','#00838f','#5d4037','#455a64','#9e9d24','#ad1457'];
const UNCATEGORISED='Kategorisiz';

type Share={total_try:string;share_percent:string|null};
type SupplierSpend=Share&{supplier_id:number;supplier_name:string;purchase_count:number;last_purchase_date:string|null};
type ProductSpend=Share&{product_id:number|null;product_name:string;quantity:string};
type CategorySpend=Share&{category:string|null};
type MonthSpend={month:string;total_try:string;purchase_count:number};
type Spend={
 date_from:string|null;date_to:string|null;excluded_statuses:string[];vat_included:boolean;
 totals:{total_try:string;line_total_try:string;purchase_count:number;supplier_count:number;average_purchase_try:string|null};
 monthly:MonthSpend[];
 by_supplier:SupplierSpend[];other_supplier_total_try:string;has_other_suppliers:boolean;
 by_product:ProductSpend[];other_product_total_try:string;has_other_products:boolean;
 by_category:CategorySpend[];other_category_total_try:string;has_other_categories:boolean;
};
type ScoreRow={supplier_id:number;supplier_name:string|null;product_count:number;priced_product_count:number;best_count:number;worst_count:number;alternative_count:number};
type Scorecard={evaluated_product_count:number;candidate_product_count:number;truncated:boolean;comparable_product_count:number;single_source_product_count:number;unpriced_product_count:number;suppliers:ScoreRow[]};
type ReorderRow={product_id:number;product_code:string|null;product_name:string;current_stock:string;reorder_point:string;suggested_quantity:string;lead_time_days:number|null;best_supplier:null|{supplier_id:number;supplier_name:string|null;effective_price_in_try:string|null;catalog_cost_try_total:string|null}};
type ReorderSummary={below_reorder_point_count:number;unpriced_count:number;total_suggested_cost_try:string;by_supplier:{supplier_id:number;supplier_name:string|null;line_count:number;total_cost_try:string}[]};

const monthLabel=(month:string)=>{const [year,part]=month.split('-');return part?`${part}.${year}`:month};
// share_percent bir YÜZDE, para değil: burada Number yalnızca gösterim
// yuvarlaması yapar, hiçbir para eşiği veya karşılaştırma buradan türetilmez.
const percent=(value:string|null)=>value==null?'-':`%${Number(value).toFixed(1)}`;

export default function PurchaseDashboard(){
 const nav=useNavigate();
 const [dateFrom,setDateFrom]=useState('');const [dateTo,setDateTo]=useState('');
 const [spend,setSpend]=useState<Spend|null>(null);
 const [scorecard,setScorecard]=useState<Scorecard|null>(null);
 const [reorder,setReorder]=useState<ReorderRow[]|null>(null);
 const [reorderSummary,setReorderSummary]=useState<ReorderSummary|null>(null);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');
 // Pano maliyet ve tedarikçi verisi gösterir; backend bunları ``purchases``
 // iznine bağlar. Yetkisiz kullanıcı 403 alır — genel bir "yüklenemedi" yerine
 // nedenini söyle ve hiçbir veri gösterme.
 const denied=(e:unknown)=>(e as {response?:{status?:number}})?.response?.status===403;

 const load=useCallback(()=>{
  let active=true;
  setLoading(true);setError('');
  const params={date_from:dateFrom||undefined,date_to:dateTo||undefined};
  Promise.all([
   api.get<Spend>('/purchase-comparison/spend-analytics',{params}),
   api.get<Scorecard>('/purchase-comparison/supplier-scorecard'),
   api.get<{items:ReorderRow[]}>('/purchase-comparison/reorder-suggestions'),
   api.get<ReorderSummary>('/purchase-comparison/dashboard'),
  ]).then(([spendResponse,scoreResponse,reorderResponse,summaryResponse])=>{
   if(!active)return;
   setSpend(spendResponse.data);
   setScorecard(scoreResponse.data);
   setReorder(reorderResponse.data.items||[]);
   setReorderSummary(summaryResponse.data);
  }).catch((requestError:unknown)=>{
   if(!active)return;
   setSpend(null);setScorecard(null);setReorder(null);setReorderSummary(null);
   setError(denied(requestError)
    ?'Bu pano satın alma yetkisi gerektirir (maliyet ve tedarikçi verisi).'
    :errorDetail(requestError,'Satın alma panosu yüklenemedi.'));
  }).finally(()=>{if(active)setLoading(false)});
  return()=>{active=false};
 },[dateFrom,dateTo]);
 useEffect(()=>load(),[load]);

 const trend=useMemo(()=>(spend?.monthly||[]).map(row=>({
  label:monthLabel(row.month),total:Number(row.total_try),display:row.total_try,count:row.purchase_count,
 })),[spend]);
 const supplierChart=useMemo(()=>(spend?.by_supplier||[]).map(row=>({
  name:row.supplier_name,total:Number(row.total_try),display:row.total_try,
 })),[spend]);
 const categoryChart=useMemo(()=>(spend?.by_category||[]).map(row=>({
  name:row.category||UNCATEGORISED,total:Number(row.total_try),display:row.total_try,
 })),[spend]);
 const bestSupplier=scorecard?.suppliers.find(row=>row.best_count>0)||null;
 const worstSupplier=useMemo(()=>{
  const ranked=[...(scorecard?.suppliers||[])].sort((a,b)=>b.worst_count-a.worst_count);
  return ranked[0]&&ranked[0].worst_count>0?ranked[0]:null;
 },[scorecard]);

 if(error)return <Stack spacing={2}>
  <Typography variant="h4" fontWeight={900}>Satın Alma Panosu</Typography>
  <Alert severity={/yetki/i.test(error)?'warning':'error'}>{error}</Alert>
 </Stack>;
 if(loading&&!spend)return <Box py={6} display="grid" sx={{placeItems:'center'}}><CircularProgress aria-label="Satın alma panosu yükleniyor"/></Box>;
 if(!spend)return null;

 const totals=spend.totals;
 const stats=[
  {label:'Toplam Alış (KDV dahil)',value:money(totals.total_try)},
  {label:'Alış Belgesi',value:String(totals.purchase_count)},
  {label:'Tedarikçi',value:String(totals.supplier_count)},
  {label:'Ortalama Belge',value:totals.average_purchase_try==null?'-':money(totals.average_purchase_try)},
  {label:'Sipariş Noktası Altı',value:String(reorderSummary?.below_reorder_point_count??0)},
 ];

 return <Stack spacing={2}>
  <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} gap={1}>
   <Typography variant="h4" fontWeight={900}>Satın Alma Panosu</Typography>
   <Link sx={{cursor:'pointer'}} onClick={()=>nav('/raporlar/tedarikci-karsilastirma')}>Tedarikçi Fiyat Karşılaştırma →</Link>
  </Stack>

  <Stack direction={{xs:'column',sm:'row'}} spacing={1}>
   <TextField type="date" size="small" label="Başlangıç" value={dateFrom}
    onChange={e=>setDateFrom(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
   <TextField type="date" size="small" label="Bitiş" value={dateTo}
    onChange={e=>setDateTo(e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>
   <Chip variant="outlined" label="Taslak ve iptal belgeler hariç"/>
  </Stack>

  <Grid container spacing={1.5}>
   {stats.map(stat=><Grid size={{xs:6,md:2.4}} key={stat.label}>
    <Card><CardContent>
     <Typography color="text.secondary" variant="caption">{stat.label}</Typography>
     <Typography fontWeight={900} fontSize={20}>{stat.value}</Typography>
    </CardContent></Card>
   </Grid>)}
  </Grid>

  {totals.purchase_count===0&&<Alert severity="info">Seçilen dönemde alış belgesi bulunmuyor. Trend ve kırılım grafikleri boş görünecek.</Alert>}

  <Grid container spacing={2}>
   <Grid size={{xs:12,lg:7}}>
    <Card><CardContent>
     <Typography fontWeight={900} mb={0.5}>Aylık Alış Harcaması</Typography>
     <Typography variant="body2" color="text.secondary" mb={1.5}>Belge toplamı (KDV dahil, belge indirimi düşülmüş).</Typography>
     {trend.length===0?<Typography color="text.secondary" py={4}>Grafik için alış verisi yok.</Typography>
      :<Box height={300}><ResponsiveContainer>
       <BarChart data={trend}>
        <CartesianGrid strokeDasharray="3 3"/>
        <XAxis dataKey="label"/>
        <YAxis/>
        <ChartTooltip formatter={(_value,_name,item)=>money(item?.payload?.display)}
         labelFormatter={label=>`${label} dönemi`}/>
        <Bar dataKey="total" name="Alış" fill="#f28c28"/>
       </BarChart>
      </ResponsiveContainer></Box>}
    </CardContent></Card>
   </Grid>

   <Grid size={{xs:12,lg:5}}>
    <Card><CardContent>
     <Typography fontWeight={900} mb={0.5}>Kategori Kırılımı</Typography>
     <Typography variant="body2" color="text.secondary" mb={1.5}>Satır toplamı bazında (belge indirimi öncesi).</Typography>
     {categoryChart.length===0?<Typography color="text.secondary" py={4}>Kategori verisi yok.</Typography>
      :<Box height={300}><ResponsiveContainer>
       <PieChart>
        <Pie data={categoryChart} dataKey="total" nameKey="name" outerRadius={95} label={false}>
         {categoryChart.map((row,index)=><Cell key={row.name} fill={CHART_COLORS[index%CHART_COLORS.length]}/>)}
        </Pie>
        <Legend/>
        <ChartTooltip formatter={(_value,_name,item)=>money(item?.payload?.display)}/>
       </PieChart>
      </ResponsiveContainer></Box>}
    </CardContent></Card>
   </Grid>

   <Grid size={{xs:12,lg:7}}>
    <Card><CardContent>
     <Typography fontWeight={900} mb={0.5}>Tedarikçi Bazlı Harcama</Typography>
     <Typography variant="body2" color="text.secondary" mb={1.5}>Tedarikçi kartına gitmek için satıra tıklayın.</Typography>
     {supplierChart.length===0?<Typography color="text.secondary" py={4}>Tedarikçi harcaması yok.</Typography>
      :<><Box height={220}><ResponsiveContainer>
       <BarChart data={supplierChart} layout="vertical" margin={{left:24}}>
        <CartesianGrid strokeDasharray="3 3"/>
        <XAxis type="number"/>
        <YAxis type="category" dataKey="name" width={130}/>
        <ChartTooltip formatter={(_value,_name,item)=>money(item?.payload?.display)}/>
        <Bar dataKey="total" name="Alış" fill="#2e7d32"/>
       </BarChart>
      </ResponsiveContainer></Box>
      <TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small">
       <TableHead><TableRow>
        <TableCell sx={{fontWeight:800}}>Tedarikçi</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Tutar</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Pay</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Belge</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Son Alış</TableCell>
       </TableRow></TableHead>
       <TableBody>
        {spend.by_supplier.map(row=><TableRow key={row.supplier_id} hover>
         <TableCell><Link sx={{cursor:'pointer'}} onClick={()=>nav(`/tedarikciler/${row.supplier_id}`)}>{row.supplier_name}</Link></TableCell>
         <TableCell align="right">{money(row.total_try)}</TableCell>
         <TableCell align="right">{percent(row.share_percent)}</TableCell>
         <TableCell align="right">{row.purchase_count}</TableCell>
         <TableCell align="right">{row.last_purchase_date||'-'}</TableCell>
        </TableRow>)}
        {spend.has_other_suppliers&&<TableRow>
         <TableCell><Typography color="text.secondary">Diğer tedarikçiler</Typography></TableCell>
         <TableCell align="right">{money(spend.other_supplier_total_try)}</TableCell>
         <TableCell colSpan={3}/>
        </TableRow>}
       </TableBody>
      </Table></TableContainer></>}
    </CardContent></Card>
   </Grid>

   <Grid size={{xs:12,lg:5}}>
    <Card><CardContent>
     <Typography fontWeight={900} mb={0.5}>Fiyat Karşılaştırma Özeti</Typography>
     <Typography variant="body2" color="text.secondary" mb={1.5}>
      #153 karşılaştırma motorundan: en az iki tedarikçinin fiyat verdiği ürünlerde kim kazanıyor.
     </Typography>
     {!scorecard||scorecard.suppliers.length===0?<Typography color="text.secondary" py={4}>Karşılaştırılacak tedarikçi fiyatı yok.</Typography>
      :<Stack spacing={1.5}>
       <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        {bestSupplier&&<Chip color="success" label={`En iyi: ${bestSupplier.supplier_name||`#${bestSupplier.supplier_id}`} (${bestSupplier.best_count})`}/>}
        {worstSupplier&&<Chip color="error" variant="outlined" label={`En pahalı: ${worstSupplier.supplier_name||`#${worstSupplier.supplier_id}`} (${worstSupplier.worst_count})`}/>}
        <Chip variant="outlined" label={`${scorecard.comparable_product_count} karşılaştırılabilir ürün`}/>
        {scorecard.single_source_product_count>0&&<Tooltip title="Tek tedarikçinin fiyat verdiği ürünler karşılaştırmaya girmez.">
         <Chip variant="outlined" color="warning" label={`${scorecard.single_source_product_count} tek kaynak`}/>
        </Tooltip>}
       </Stack>
       <TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small">
        <TableHead><TableRow>
         <TableCell sx={{fontWeight:800}}>Tedarikçi</TableCell>
         <TableCell align="right" sx={{fontWeight:800}}>En İyi</TableCell>
         <TableCell align="right" sx={{fontWeight:800}}>En Pahalı</TableCell>
         <TableCell align="right" sx={{fontWeight:800}}>Fiyatlı Ürün</TableCell>
        </TableRow></TableHead>
        <TableBody>{scorecard.suppliers.map(row=><TableRow key={row.supplier_id} hover>
         <TableCell><Link sx={{cursor:'pointer'}} onClick={()=>nav(`/tedarikciler/${row.supplier_id}`)}>{row.supplier_name||`#${row.supplier_id}`}</Link></TableCell>
         <TableCell align="right">{row.best_count}</TableCell>
         <TableCell align="right">{row.worst_count}</TableCell>
         <TableCell align="right">{row.priced_product_count}</TableCell>
        </TableRow>)}</TableBody>
       </Table></TableContainer>
       {scorecard.truncated&&<Typography variant="caption" color="text.secondary">
        {scorecard.candidate_product_count} üründen ilk {scorecard.evaluated_product_count} tanesi tarandı; özet kısmi.
       </Typography>}
      </Stack>}
    </CardContent></Card>
   </Grid>

   <Grid size={{xs:12,lg:7}}>
    <Card><CardContent>
     <Typography fontWeight={900} mb={0.5}>Ürün Bazlı Harcama</Typography>
     <Typography variant="body2" color="text.secondary" mb={1.5}>Satır toplamı bazında; ürün kartını açmak için tıklayın.</Typography>
     {spend.by_product.length===0?<Typography color="text.secondary" py={4}>Ürün alışı yok.</Typography>
      :<TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small">
       <TableHead><TableRow>
        <TableCell sx={{fontWeight:800}}>Ürün</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Miktar</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Tutar</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Pay</TableCell>
       </TableRow></TableHead>
       <TableBody>
        {spend.by_product.map(row=><TableRow key={row.product_id??row.product_name} hover>
         <TableCell>{row.product_id
          ?<Link sx={{cursor:'pointer'}} onClick={()=>nav(`/urunler/${row.product_id}`)}>{row.product_name}</Link>
          :<Tooltip title="Aynı ada sahip birden fazla ürün veya serbest satır"><span>{row.product_name}</span></Tooltip>}</TableCell>
         <TableCell align="right">{Number(row.quantity)}</TableCell>
         <TableCell align="right">{money(row.total_try)}</TableCell>
         <TableCell align="right">{percent(row.share_percent)}</TableCell>
        </TableRow>)}
        {spend.has_other_products&&<TableRow>
         <TableCell><Typography color="text.secondary">Diğer ürünler</Typography></TableCell>
         <TableCell/>
         <TableCell align="right">{money(spend.other_product_total_try)}</TableCell>
         <TableCell/>
        </TableRow>}
       </TableBody>
      </Table></TableContainer>}
    </CardContent></Card>
   </Grid>

   <Grid size={{xs:12,lg:5}}>
    <Card><CardContent>
     <Stack direction="row" alignItems="center" spacing={1} mb={0.5}>
      <Typography fontWeight={900}>Yeniden Sipariş Önerileri</Typography>
      <Chip size="small" color="warning" variant="outlined" label={`${reorder?.length??0} ürün`}/>
     </Stack>
     <Typography variant="body2" color="text.secondary" mb={1.5}>
      Öneri listesidir; bu ekran sipariş oluşturmaz. Taslak sipariş için Tedarikçi Fiyat Karşılaştırma ekranını kullanın.
     </Typography>
     {reorderSummary&&reorderSummary.below_reorder_point_count>0&&<Paper variant="outlined" sx={{p:1.5,mb:1.5}}>
      <Typography variant="caption" color="text.secondary">Önerilen taslakların katalog maliyeti</Typography>
      <Typography fontWeight={900} fontSize={18}>{money(reorderSummary.total_suggested_cost_try)}</Typography>
      {reorderSummary.unpriced_count>0&&<Typography variant="caption" color="warning.main" display="block">
       {reorderSummary.unpriced_count} ürünün tedarikçi fiyatı yok; tutara dahil değil.
      </Typography>}
     </Paper>}
     {!reorder||reorder.length===0?<Typography color="text.secondary" py={2}>Sipariş noktasının altına düşen ürün yok.</Typography>
      :<TableContainer sx={{maxWidth:'100%',overflowX:'auto'}}><Table size="small">
       <TableHead><TableRow>
        <TableCell sx={{fontWeight:800}}>Ürün</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Stok / Nokta</TableCell>
        <TableCell sx={{fontWeight:800}}>Önerilen Tedarikçi</TableCell>
        <TableCell align="right" sx={{fontWeight:800}}>Miktar</TableCell>
       </TableRow></TableHead>
       <TableBody>{reorder.map(row=><TableRow key={row.product_id} hover>
        <TableCell><Link sx={{cursor:'pointer'}} onClick={()=>nav(`/urunler/${row.product_id}`)}>{row.product_name}</Link></TableCell>
        <TableCell align="right"><Typography component="span" color="error" fontWeight={700}>{Number(row.current_stock)}</Typography> / {Number(row.reorder_point)}</TableCell>
        <TableCell>{row.best_supplier
         ?`${row.best_supplier.supplier_name||`#${row.best_supplier.supplier_id}`} · ${money(row.best_supplier.effective_price_in_try)}`
         :<Typography component="span" color="text.secondary">Tedarikçi fiyatı yok</Typography>}</TableCell>
        <TableCell align="right">{Number(row.suggested_quantity)}</TableCell>
       </TableRow>)}</TableBody>
      </Table></TableContainer>}
    </CardContent></Card>
   </Grid>
  </Grid>

  <Typography variant="caption" color="text.secondary">
   Tüm tutarlar KDV dahil ve TL cinsindendir, sunucuda hesaplanır. Toplam/trend/tedarikçi kırılımı belge toplamına
   (belge indirimi sonrası), ürün/kategori kırılımı satır toplamına (belge indirimi öncesi) dayanır; bu yüzden
   iki taban birbirinden farklı olabilir. Taslak ve iptal edilmiş belgeler hariçtir.
  </Typography>
 </Stack>;
}
