import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Grid,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useNavigate } from 'react-router-dom';
import { api, errorDetail, money } from '../api';

type MonthlySale = {
  month: string;
  total: number;
};

type TopCustomer = {
  customer_id?: number;
  name: string;
  total: number;
};

type TopProduct = {
  product_id?: number;
  product_name: string;
  quantity: number;
  total: number;
};

type ReportData = {
  sales_total: number;
  purchases_total: number;
  collections: number;
  payments: number;
  gross_difference: number;
  monthly_sales: MonthlySale[];
  top_customers: TopCustomer[];
  top_products: TopProduct[];
};

type Stat = {
  label: string;
  value: string;
};

export default function Reports() {
  const navigate = useNavigate();
  const [data, setData] = useState<ReportData | null>(null);
  const [error, setError] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  useEffect(() => {
    let active = true;

    setError('');
    api
      .get<ReportData>('/reports/summary', {
        params: {
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
        },
      })
      .then((response) => {
        if (active) setData(response.data);
      })
      .catch((requestError: unknown) => {
        if (active) setError(errorDetail(requestError, 'Rapor verileri yüklenemedi.'));
      });

    return () => {
      active = false;
    };
  }, [dateFrom, dateTo]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) {
    return (
      <Box p={6} textAlign="center">
        <CircularProgress aria-label="Raporlar yükleniyor" />
      </Box>
    );
  }

  const stats: Stat[] = [
    { label: 'Satış', value: money(data.sales_total) },
    { label: 'Alış', value: money(data.purchases_total) },
    { label: 'Tahsilat', value: money(data.collections) },
    { label: 'Ödeme', value: money(data.payments) },
    { label: 'Brüt Fark', value: money(data.gross_difference) },
  ];

  return (
    <Stack spacing={2}>
      <Typography variant="h4" fontWeight={900}>
        Raporlar
      </Typography>
      <Box>
        <Button variant="outlined" onClick={() => navigate('/raporlar/alacak-yaslandirma')}>
          Alacak Yaşlandırma
        </Button>
      </Box>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
        <TextField
          type="date"
          label="Başlangıç"
          value={dateFrom}
          onChange={(event) => setDateFrom(event.target.value)}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <TextField
          type="date"
          label="Bitiş"
          value={dateTo}
          onChange={(event) => setDateTo(event.target.value)}
          slotProps={{ inputLabel: { shrink: true } }}
        />
      </Stack>

      <Grid container spacing={1.5}>
        {stats.map((stat) => (
          <Grid size={{ xs: 6, md: 2.4 }} key={stat.label}>
            <Card>
              <CardContent>
                <Typography color="text.secondary" variant="caption">
                  {stat.label}
                </Typography>
                <Typography fontWeight={900} fontSize={20}>
                  {stat.value}
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, lg: 7 }}>
          <Card>
            <CardContent>
              <Typography fontWeight={900} mb={2}>
                Aylık Satış
              </Typography>
              <Box height={300}>
                <ResponsiveContainer>
                  <BarChart data={data.monthly_sales}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="month" />
                    <YAxis />
                    <Tooltip formatter={(value) => money(Number(value))} />
                    <Bar dataKey="total" fill="#f28c28" />
                  </BarChart>
                </ResponsiveContainer>
              </Box>
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, lg: 5 }}>
          <Card>
            <CardContent>
              <Typography fontWeight={900} mb={1}>
                En Yüksek Ciro Müşteriler
              </Typography>
              <Typography variant="body2" color="text.secondary" mb={1.5}>
                Müşteri detayını ve satın aldığı ürünleri görmek için satıra tıklayın.
              </Typography>
              {data.top_customers.length === 0 ? (
                <Typography color="text.secondary">Bu dönemde müşteri verisi bulunmuyor.</Typography>
              ) : (
                data.top_customers.map((customer) => {
                  const customerId = customer.customer_id;
                  return (
                    <Box
                    key={customerId ?? customer.name}
                    role={customerId ? 'button' : undefined}
                    tabIndex={customerId ? 0 : undefined}
                    onClick={() => customerId && navigate(`/musteriler/${customerId}`)}
                    onKeyDown={(event) => {
                      if (customerId && (event.key === 'Enter' || event.key === ' ')) {
                        event.preventDefault();
                        navigate(`/musteriler/${customerId}`);
                      }
                    }}
                    display="flex"
                    justifyContent="space-between"
                    alignItems="center"
                    gap={2}
                    py={1}
                    px={1}
                    borderBottom="1px solid"
                    borderColor="divider"
                    sx={{
                      borderRadius: 1,
                      cursor: customerId ? 'pointer' : 'default',
                      '&:hover': customerId ? { bgcolor: 'action.hover' } : undefined,
                      '&:focus-visible': customerId
                        ? { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: 2 }
                        : undefined,
                    }}
                  >
                    <Typography>{customer.name}</Typography>
                    <Typography fontWeight={800}>{money(customer.total)}</Typography>
                  </Box>
                  );
                })
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12 }}>
          <Card>
            <CardContent>
              <Typography fontWeight={900} mb={1}>En Çok Satan Ürünler</Typography>
              <Typography variant="body2" color="text.secondary" mb={1.5}>
                Ürün kartını açmak için satıra tıklayın. Aynı ada sahip birden fazla ürün varsa satır bağlantısız kalır.
              </Typography>
              {data.top_products.map((product) => {
                const productId = product.product_id;
                return (
                  <Box
                    key={productId ?? product.product_name}
                    role={productId ? 'button' : undefined}
                    tabIndex={productId ? 0 : undefined}
                    onClick={() => productId && navigate(`/urunler/${productId}`)}
                    onKeyDown={(event) => {
                      if (productId && (event.key === 'Enter' || event.key === ' ')) {
                        event.preventDefault();
                        navigate(`/urunler/${productId}`);
                      }
                    }}
                    display="flex"
                    justifyContent="space-between"
                    alignItems="center"
                    gap={2}
                    py={1.25}
                    px={1}
                    borderBottom="1px solid"
                    borderColor="divider"
                    sx={{
                      borderRadius: 1,
                      cursor: productId ? 'pointer' : 'default',
                      opacity: productId ? 1 : 0.75,
                      '&:hover': productId ? { bgcolor: 'action.hover' } : undefined,
                      '&:focus-visible': productId
                        ? { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: 2 }
                        : undefined,
                    }}
                  >
                    <Typography fontWeight={750}>{product.product_name}</Typography>
                    <Stack direction="row" spacing={2} alignItems="center">
                      <Typography color="text.secondary">{product.quantity} adet</Typography>
                      <Typography fontWeight={900}>{money(product.total)}</Typography>
                    </Stack>
                  </Box>
                );
              })}
              {data.top_products.length === 0 ? (
                <Typography color="text.secondary" py={2}>Bu dönemde ürün satışı bulunmuyor.</Typography>
              ) : null}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Stack>
  );
}
