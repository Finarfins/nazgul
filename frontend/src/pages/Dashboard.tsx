import React from 'react';
import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {useNavigate} from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  Grid,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import AddShoppingCartIcon from '@mui/icons-material/AddShoppingCart';
import PaymentsIcon from '@mui/icons-material/Payments';
import BuildIcon from '@mui/icons-material/Build';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';

import {Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {api, errorDetail, money} from '../api';
import {useAuth} from '../AuthContext';

type DashboardData = {
  as_of: string;
  today_sales: number;
  today_collections: number;
  month_sales: number;
  month_collections: number;
  month_purchases: number;
  month_expenses: number;
  month_profit: number;
  stock_value: number;
  cash_bank_total: number;
  customer_receivables: number;
  supplier_payables: number;
  customer_count: number;
  product_count: number;
  critical_stock_count: number;
  overdue_count: number;
  overdue_total: number;
  recent_sales: any[];
  critical_products: any[];
  overdue_receivables: any[];
  finance_accounts: any[];
  sales_trend: any[];
  top_products: any[];
  recent_activity: any[];
};

type QuickAction = {
  label: string;
  icon: ReactNode;
  path: string;
  color: 'success' | 'info' | 'secondary' | 'warning' | 'primary' | 'inherit';
};

const activityLabels: Record<string, string> = {
  sale: 'Satış',
  collection: 'Tahsilat',
  payment: 'Ödeme',
  finance: 'Finans',
};

const activityColors: Record<string, 'success' | 'primary' | 'warning' | 'info'> = {
  sale: 'success',
  collection: 'primary',
  payment: 'warning',
  finance: 'info',
};

function KpiCard({
  title,
  value,
  subtitle,
  accent,
  onClick,
}: {
  title: string;
  value: string;
  subtitle?: string;
  accent: string;
  onClick?: () => void;
}) {
  return (
    <Card sx={{height: '100%', borderTop: `4px solid ${accent}`}}>
      <CardActionArea onClick={onClick} disabled={!onClick} sx={{height: '100%', textAlign: 'left'}}>
        <CardContent sx={{p: {xs: 1.5, sm: 2}}}>
          <Typography variant="caption" color="text.secondary" fontWeight={800}>
            {title}
          </Typography>
          <Typography fontWeight={950} sx={{fontSize: {xs: 19, sm: 25}, mt: 0.5, wordBreak: 'break-word'}}>
            {value}
          </Typography>
          {subtitle ? <Typography sx={{fontSize: 13.5, lineHeight: 1.45, color: 'text.secondary'}}>{subtitle}</Typography> : null}
        </CardContent>
      </CardActionArea>
    </Card>
  );
}

export default function Dashboard() {
  const {can} = useAuth();
  const canViewFinance = can('finance');
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState('');
  const [demo, setDemo] = useState<any>(null);
  const nav = useNavigate();

  useEffect(() => {
    Promise.all([api.get('/dashboard'), api.get('/demo/summary')])
      .then(([dashboardResponse, demoResponse]) => {
        setData(dashboardResponse.data);
        setDemo(demoResponse.data);
      })
      .catch((requestError) => setError(errorDetail(requestError, 'Dashboard verileri yüklenemedi.')));
  }, []);

  const dateText = useMemo(
    () => new Intl.DateTimeFormat('tr-TR', {dateStyle: 'full'}).format(new Date()),
    [],
  );

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <Box p={6} textAlign="center"><CircularProgress /></Box>;

  const quickActions: QuickAction[] = [
    {label: 'Yeni Satış', icon: <AddShoppingCartIcon />, path: '/satislar?new=1', color: 'primary'},
    {label: 'Yeni İş Emri', icon: <BuildIcon />, path: '/is-emirleri?new=1', color: 'secondary'},
    {label: 'Tahsilat Al', icon: <PaymentsIcon />, path: '/odemeler?new=customer', color: 'inherit'},
  ];

  const openRecentActivity = (item: any) => {
    if (item.type === 'sale') {
      nav('/satislar', {state: {openId: item.id}});
      return;
    }
    if ((item.type === 'collection' || item.type === 'payment') && item.entity_id) {
      nav(item.entity_type === 'supplier' ? `/tedarikciler/${item.entity_id}` : `/musteriler/${item.entity_id}`);
      return;
    }
    if (item.type === 'finance') {
      nav('/nakit-yonetimi', {state: {accountId: item.account_id || null, transactionId: item.id}});
    }
  };

  return (
    <Stack spacing={2.25}>
      {demo?.enabled ? (
        <Alert severity="info" variant="outlined">
          <Typography fontWeight={900}>{demo.label}</Typography>
          <Typography variant="body2">
            {demo.counts.customers} müşteri · {demo.counts.suppliers} tedarikçi · {demo.counts.products} ürün · {demo.counts.sales} satış · {demo.counts.purchases} alış
          </Typography>
          <Typography variant="caption">{demo.reset_hint}</Typography>
        </Alert>
      ) : null}
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems={{xs: 'flex-start', md: 'center'}}
        flexDirection={{xs: 'column', md: 'row'}}
        gap={2}
        sx={{
          position:'relative',
          overflow:'hidden',
          px:{xs:2.25,sm:3},
          py:{xs:2.5,sm:3},
          borderRadius:3.5,
          color:'#fff',
          background:'linear-gradient(125deg, #071e41 0%, #0b3567 62%, #164a8a 100%)',
          boxShadow:'0 18px 48px rgba(7,30,65,.18)',
          '&:after':{content:'""',position:'absolute',width:300,height:300,right:-85,top:-170,borderRadius:'50%',background:'rgba(255,255,255,.07)'},
        }}
      >
        <Box position="relative" zIndex={1}>
          <Typography variant="caption" fontWeight={800} letterSpacing=".12em" sx={{color:'rgba(255,255,255,.65)'}}>BAYİ OPERASYON MERKEZİ</Typography>
          <Typography variant="h4" fontWeight={950} sx={{fontSize: {xs: 25, sm: 31}}}>
            İşletme Kontrol Merkezi
          </Typography>
          <Typography sx={{color:'rgba(255,255,255,.72)'}}>{dateText} · Satış, servis ve stok görünümü</Typography>
        </Box>
        <Stack direction="row" gap={1} flexWrap="wrap" position="relative" zIndex={1}>
          {quickActions.map((action) => (
            <Button
              key={action.label}
              size="small"
              variant="contained"
              color={action.color}
              startIcon={action.icon}
              onClick={() => nav(action.path)}
              // Hızlı eylemler 34px yükseklikteydi; dokunmatikte xs'te 44px'e
              // çıkar. Kural `down('sm')` ile 600px altına kilitlenir: `{xs:44}`
              // yazmak sm karşılığı olmadan masaüstüne de sızıyordu (1280px'te 44px).
              // sm ve üzeri `size="small"` özgün 34px'inde kalır.
              sx={theme=>({[theme.breakpoints.down('sm')]:{minHeight:44}, ...(action.label==='Yeni Satış'?{bgcolor:'#fff',color:'#0b3567','&:hover':{bgcolor:'#eaf2fb'}}:action.label==='Yeni İş Emri'?{bgcolor:'secondary.main',color:'secondary.contrastText'}:{bgcolor:'rgba(255,255,255,.13)',color:'#fff','&:hover':{bgcolor:'rgba(255,255,255,.2)'}})})}
            >
              {action.label}
            </Button>
          ))}
        </Stack>
      </Box>

      <Grid container spacing={1.4}>
        <Grid size={{xs: 6, md: 3}}>
          <KpiCard title="BUGÜNKÜ SATIŞ" value={money(data.today_sales)} subtitle={`${data.recent_sales.length} son satış gösteriliyor`} accent="#164a8a" onClick={() => nav('/satislar')} />
        </Grid>
        <Grid size={{xs: 6, md: 3}}>
          <KpiCard title="BUGÜNKÜ TAHSİLAT" value={money(data.today_collections)} subtitle={`Bu ay ${money(data.month_collections)}`} accent="#27845a" onClick={() => nav('/odemeler')} />
        </Grid>
        <Grid size={{xs: 6, md: 3}}>
          <KpiCard title="BU AY KÂR TAHMİNİ" value={money(data.month_profit)} subtitle={`Ciro ${money(data.month_sales)}`} accent="#f5c400" onClick={() => nav('/raporlar')} />
        </Grid>
        <Grid size={{xs: 6, md: 3}}>
          <KpiCard title="GECİKMİŞ ALACAK" value={money(data.overdue_total)} subtitle={`${data.overdue_count} belge`} accent="#d95757" onClick={() => nav('/musteriler')} />
        </Grid>
      </Grid>

      <Grid container spacing={2}>
        <Grid size={{xs: 12, lg: 8}}>
          <Card sx={{height: '100%'}}>
            <CardContent>
              <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                <Box>
                  <Typography variant="h6" fontWeight={900}>Son 14 Gün Satış Trendi</Typography>
                  <Typography variant="caption" color="text.secondary">Günlük tamamlanan satış toplamı</Typography>
                </Box>
                <Chip icon={<TrendingUpIcon />} label={`Bu ay ${money(data.month_sales)}`} color="success" variant="outlined" />
              </Box>
              <Box height={285}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data.sales_trend} margin={{left: 0, right: 12, top: 10, bottom: 0}}>
                    <defs>
                      <linearGradient id="salesFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#164a8a" stopOpacity={0.38} />
                        <stop offset="95%" stopColor="#164a8a" stopOpacity={0.03} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="label" fontSize={13} />
                    <YAxis fontSize={13} width={70} tickFormatter={(value) => new Intl.NumberFormat('tr-TR', {notation: 'compact'}).format(value)} />
                    <Tooltip formatter={(value) => money(Number(value))} />
                    <Area type="monotone" dataKey="total" stroke="#164a8a" fill="url(#salesFill)" strokeWidth={3} />
                  </AreaChart>
                </ResponsiveContainer>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{xs: 12, lg: 4}}>
          <Card sx={{height: '100%'}}>
            <CardContent>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Box>
                  <Typography variant="h6" fontWeight={900}>Acil Aksiyonlar</Typography>
                  <Typography variant="caption" color="text.secondary">Bugün ilgilenmeniz gereken konular</Typography>
                </Box>
                <Chip label={data.critical_stock_count+data.overdue_count} color={data.critical_stock_count+data.overdue_count?'warning':'success'} />
              </Stack>
              <CardActionArea onClick={()=>nav('/urunler?critical=1')} sx={{mt:2,p:1.5,borderRadius:2,bgcolor:'warningTint'}}>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Box><Typography fontWeight={800}>Kritik / Negatif Stok</Typography><Typography variant="body2" color="text.secondary">Parça ve ürün kontrolü</Typography></Box>
                  <Typography fontSize={22} fontWeight={900} color="warning.main">{data.critical_stock_count}</Typography>
                </Stack>
              </CardActionArea>
              <CardActionArea onClick={()=>nav('/satislar?status=overdue')} sx={{mt:1,p:1.5,borderRadius:2,bgcolor:'dangerTint'}}>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Box><Typography fontWeight={800}>Vadesi Geçen Alacak</Typography><Typography variant="body2" color="text.secondary">{money(data.overdue_total)}</Typography></Box>
                  <Typography fontSize={22} fontWeight={900} color="error.main">{data.overdue_count}</Typography>
                </Stack>
              </CardActionArea>
              {!data.critical_stock_count&&!data.overdue_count?<Alert severity="success" sx={{mt:2}}>Acil aksiyon bulunmuyor.</Alert>:null}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Grid container spacing={2}>
        <Grid size={{xs: 12, lg: 7}}>
          <Card>
            <CardContent>
              <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                <Typography variant="h6" fontWeight={900}>Son Satışlar</Typography>
                <Button size="small" onClick={() => nav('/satislar')}>Tümünü Gör</Button>
              </Box>
              <Box sx={{overflowX: 'auto'}}>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Tarih</TableCell><TableCell>Müşteri</TableCell><TableCell>Belge</TableCell><TableCell align="right">Tutar</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.recent_sales.map((sale) => (
                      <TableRow key={sale.id} hover sx={{cursor: 'pointer'}} onClick={() => nav('/satislar', {state: {openId: sale.id}})}>
                        <TableCell>{sale.order_date}</TableCell>
                        <TableCell sx={{fontWeight: 750}}>{sale.customer_name}</TableCell>
                        <TableCell>{sale.document_no || `S-${sale.id}`}</TableCell>
                        <TableCell align="right" sx={{fontWeight: 850}}>{money(sale.final_total)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
              {data.recent_sales.length === 0 ? <Typography color="text.secondary" py={3} textAlign="center">Henüz satış kaydı yok.</Typography> : null}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{xs: 12, lg: 5}}>
          <Card>
            <CardContent>
              <Box display="flex" justifyContent="space-between" alignItems="center">
                <Typography variant="h6" fontWeight={900}>Vadesi Geçen Alacaklar</Typography>
                <Chip label={data.overdue_count} color={data.overdue_count ? 'error' : 'default'} size="small" />
              </Box>
              <Stack mt={1} spacing={0.4}>
                {data.overdue_receivables.map((item) => (
                  <CardActionArea key={item.id} onClick={() => nav(`/musteriler/${item.customer_id}`)} sx={{borderRadius: 1.5, p: 1}}>
                    <Box display="flex" justifyContent="space-between" gap={2}>
                      <Box>
                        <Typography fontWeight={750}>{item.customer_name}</Typography>
                        <Typography variant="caption" color="error.main">{item.days_overdue} gün gecikti · {item.document_no}</Typography>
                      </Box>
                      <Typography fontWeight={900}>{money(item.remaining)}</Typography>
                    </Box>
                  </CardActionArea>
                ))}
              </Stack>
              {data.overdue_receivables.length === 0 ? <Alert severity="success" sx={{mt: 2}}>Vadesi geçmiş açık satış bulunmuyor.</Alert> : null}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Grid container spacing={2}>
        <Grid size={{xs: 12, md: 4}}>
          <Card sx={{height: '100%'}}>
            <CardContent>
              <Box display="flex" justifyContent="space-between">
                <Typography variant="h6" fontWeight={900}>Kritik / Negatif Stok</Typography>
                <Chip icon={<WarningAmberIcon />} label={data.critical_stock_count} color={data.critical_stock_count ? 'error' : 'default'} size="small" />
              </Box>
              <Stack mt={1} spacing={1}>
                {data.critical_products.map((product) => (
                  // Tek satırlık kritik stok satırı 32px'ti; xs'te 44px'e çıkar.
                  // Kural `down('sm')` ile mobile hapsedilir: `{xs:44}` sm karşılığı
                  // olmadan masaüstüne de sızıyordu (1280px'te 44px). sm ve üzeri
                  // içerik yüksekliğinde (~30px) kalır. `display` DEĞİŞTİRİLMEZ:
                  // içteki kutu `space-between` ile hizalanıyor, flex kapsayıcıya
                  // çevirmek onu daraltırdı.
                  <CardActionArea key={product.id} onClick={() => nav(`/urunler/${product.id}`)} sx={theme => ({borderRadius: 1.5, p: 0.8, [theme.breakpoints.down('sm')]: {minHeight: 44}})}>
                    <Box display="flex" justifyContent="space-between">
                      <Typography variant="body2" fontWeight={700}>{product.name}</Typography>
                      <Typography variant="body2" color="error" fontWeight={900}>{product.stock} {product.unit}</Typography>
                    </Box>
                  </CardActionArea>
                ))}
              </Stack>
              {data.critical_products.length === 0 ? <Alert severity="success" sx={{mt: 2}}>Kritik stok bulunmuyor.</Alert> : null}
            </CardContent>
          </Card>
        </Grid>

        {canViewFinance ? <Grid size={{xs: 12, md: 4}}>
          <Card sx={{height: '100%'}}>
            <CardContent>
              <Typography variant="h6" fontWeight={900}>Kasa / Banka</Typography>
              <Stack mt={1} spacing={1}>
                {data.finance_accounts.map((account) => (
                  <CardActionArea key={account.id} onClick={() => nav('/nakit-yonetimi', {state: {accountId: account.id}})} sx={{borderRadius: 1.5, px: 0.8}}><Box display="flex" justifyContent="space-between" py={0.6} borderBottom="1px solid" borderColor="divider">
                    <Box>
                      <Typography fontWeight={750}>{account.name}</Typography>
                      <Typography variant="caption" color="text.secondary">{account.account_type} · {account.currency}</Typography>
                    </Box>
                    <Typography fontWeight={900}>{money(account.balance)}</Typography>
                  </Box></CardActionArea>
                ))}
              </Stack>
              {data.finance_accounts.length === 0 ? <Typography color="text.secondary" mt={2}>Henüz finans hesabı tanımlanmamış.</Typography> : null}
              <Button sx={{mt: 1.5}} size="small" onClick={() => nav('/nakit-yonetimi')}>Nakit Yönetimine Git</Button>
            </CardContent>
          </Card>
        </Grid> : null}

        <Grid size={{xs: 12, md: 4}}>
          <Card sx={{height: '100%'}}>
            <CardContent>
              <Typography variant="h6" fontWeight={900}>En Çok Satan Ürünler</Typography>
              <Stack mt={1} spacing={1}>
                {data.top_products.map((product, index) => (
                  <CardActionArea
                    key={product.product_id ?? product.name}
                    onClick={() => product.product_id && nav(`/urunler/${product.product_id}`)}
                    disabled={!product.product_id}
                    sx={{borderRadius: 1.5, p: 0.8, opacity: product.product_id ? 1 : 0.7}}
                    aria-label={`${product.name} — ${product.quantity} adet, ${money(product.total)}`}
                  >
                    <Box display="flex" gap={1.2} alignItems="center">
                      <Chip label={index + 1} size="small" />
                      <Box flex={1}>
                        <Typography variant="body2" fontWeight={750}>{product.name}</Typography>
                        <Typography sx={{fontSize: 13.5, color: 'text.secondary'}}>{product.quantity} adet</Typography>
                      </Box>
                      <Typography fontWeight={850}>{money(product.total)}</Typography>
                    </Box>
                  </CardActionArea>
                ))}
              </Stack>
              {data.top_products.length === 0 ? <Alert severity="info" sx={{mt: 2}}>Bu dönemde satış verisi bulunmuyor.</Alert> : null}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Card>
        <CardContent>
          <Typography variant="h6" fontWeight={900} mb={1}>Son Hareketler</Typography>
          {data.recent_activity.length === 0 ? (
            <Alert severity="info" sx={{mt: 1}}>Henüz hareket kaydı bulunmuyor.</Alert>
          ) : (
            <Grid container spacing={1}>
              {data.recent_activity.map((item, index) => (
                <Grid key={`${item.type}-${item.id}-${index}`} size={{xs: 12, sm: 6, lg: 4}}>
                  <CardActionArea
                    onClick={() => openRecentActivity(item)}
                    sx={{border: '1px solid', borderColor: 'divider', borderRadius: 2}}
                    aria-label={`${activityLabels[item.type] || item.type} detayını aç`}
                  >
                    <Box p={1.2} display="flex" justifyContent="space-between" gap={1}>
                      <Box>
                        <Chip size="small" color={activityColors[item.type] || 'info'} label={activityLabels[item.type] || item.type} />
                        <Typography mt={0.7} fontWeight={750}>{item.title}</Typography>
                        <Typography sx={{fontSize: 13.5, lineHeight: 1.45, color: 'text.secondary'}}>{item.date} · Detayı aç</Typography>
                      </Box>
                      <Typography fontWeight={900}>{money(item.amount)}</Typography>
                    </Box>
                  </CardActionArea>
                </Grid>
              ))}
            </Grid>
          )}
        </CardContent>
      </Card>
    </Stack>
  );
}
