/**
 * Olay Kuyruğu (`/tarla/olay-kuyrugu`) — outbox okuma yüzeyi.
 *
 * NİYE VAR: `field_integration_events` tablosunu okuyan hiçbir ekran yoktu;
 * tüketicinin yazdığı kovalar yalnız süreç günlüğünde görünüyordu
 * (`backend/app/FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md`, koşul 2). Bu ekran o
 * iki soruyu cevaplar: kuyruk ne durumda, ve hangi olaylar neden başarısız.
 *
 * EKRANIN TAŞIDIĞI TEK ZOR KARAR: **`PENDING` "sorun yok" DEMEK DEĞİLDİR.**
 * Belgenin ölçümüne göre `RECOVERY_FAILED` sınıfındaki olay veritabanında iz
 * bırakmaz — `status` `PENDING` kalır, `attempts` değişmez. Yani burada
 * "Bekliyor" görünen bir satır, hiç denenmemiş de olabilir, denenip kurtarma
 * yazımı da başarısız olmuş da. Ekran bunu SÖYLER; sessizce "kuyrukta" diye
 * göstermek kullanıcıya olmayan bir güvence verirdi.
 *
 * ALAN ADI EKRANDA SABİT DEĞİL: sunucu yanıtı `source` taşıyor ve tipler alan
 * adı içermiyor. İkinci outbox tablosu (sürü) eklendiğinde bu ekran yolu
 * parametre alarak yeniden kullanılabilir; bugün yalnız tarla yüzeyi bağlı
 * çünkü sürünün YAZICISI henüz yok — okunacak olay üretilmiyor.
 */
import {useCallback, useEffect, useState} from 'react';
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress,
  FormControlLabel, Stack, Switch, Tooltip, Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import RefreshIcon from '@mui/icons-material/Refresh';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';

import ResponsiveTable from '../components/ResponsiveTable';
import {
  FAILED_EVENT_STATUSES, eventSourceLabel, eventStatusLabel, farmErrorText,
  fetchIntegrationEventSummary, fetchIntegrationEvents, isoDateTime,
  type IntegrationEvent, type IntegrationEventSummary,
} from '../farm/farmApi';

const SAYFA = 50;

/** `PENDING` satırının okunma biçimi — bkz. dosya başlığı. */
const BEKLIYOR_UYARISI =
  'Bekleyen bir olay HİÇ denenmemiş olabilir; kurtarma yazımı başarısız olan ' +
  'olay da bu durumda kalır ve ikisi veritabanında ayırt edilemez. ' +
  'Ayrıntı: FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md';

const basarisizMi = (status: string) =>
  (FAILED_EVENT_STATUSES as readonly string[]).includes(status);

const columns: GridColDef[] = [
  {field: 'id', headerName: '#', width: 80},
  {field: 'source_type', headerName: 'Kaynak', width: 130, sortable: false,
   renderCell: params => (
     <Box>
       <Typography variant="body2">{eventSourceLabel(params.row.source_type)}</Typography>
       <Typography variant="caption" color="text.secondary">
         kayıt #{params.row.source_id}
       </Typography>
     </Box>
   )},
  {field: 'status', headerName: 'Durum', width: 210, sortable: false,
   renderCell: params => {
     const etiket = eventStatusLabel(params.row.status);
     if (params.row.status === 'PENDING') {
       return (
         <Tooltip title={BEKLIYOR_UYARISI}>
           <Chip size="small" variant="outlined" label={etiket} />
         </Tooltip>
       );
     }
     return (
       <Chip
         size="small"
         color={basarisizMi(params.row.status) ? 'warning' : 'success'}
         variant={basarisizMi(params.row.status) ? 'filled' : 'outlined'}
         label={etiket}
       />
     );
   }},
  {field: 'attempts', headerName: 'Deneme', width: 95, align: 'right', headerAlign: 'right',
   valueGetter: (_v, row: any) => (row.attempts === null ? '—' : row.attempts)},
  {field: 'last_error', headerName: 'Gerekçe', flex: 1, minWidth: 260, sortable: false,
   renderCell: params => (
     <Typography variant="body2" color={params.row.last_error ? 'text.primary' : 'text.secondary'}>
       {params.row.last_error || '—'}
     </Typography>
   )},
  {field: 'updated_at', headerName: 'Son güncelleme', width: 155,
   valueGetter: (_v, row: any) => isoDateTime(row.updated_at)},
];

export default function IntegrationEvents() {
  const [ozet, setOzet] = useState<IntegrationEventSummary | null>(null);
  const [olaylar, setOlaylar] = useState<IntegrationEvent[]>([]);
  const [toplam, setToplam] = useState(0);
  const [yalnizBasarisiz, setYalnizBasarisiz] = useState(false);
  const [yukleniyor, setYukleniyor] = useState(true);
  const [hata, setHata] = useState('');

  const yukle = useCallback(async () => {
    setYukleniyor(true);
    setHata('');
    try {
      const [o, l] = await Promise.all([
        fetchIntegrationEventSummary(),
        fetchIntegrationEvents({limit: SAYFA, failed_only: yalnizBasarisiz || undefined}),
      ]);
      setOzet(o);
      setOlaylar(l.items);
      setToplam(l.total);
    } catch (err) {
      setHata(farmErrorText(err, 'Olay kuyruğu okunamadı.'));
    } finally {
      setYukleniyor(false);
    }
  }, [yalnizBasarisiz]);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{mb: 2}} flexWrap="wrap" gap={1}>
        <Typography variant="h5">Olay Kuyruğu</Typography>
        <Button startIcon={<RefreshIcon />} onClick={() => void yukle()} disabled={yukleniyor}>
          Yenile
        </Button>
      </Stack>

      {hata && <Alert severity="error" sx={{mb: 2}}>{hata}</Alert>}

      <Stack direction="row" spacing={2} sx={{mb: 2}} flexWrap="wrap" useFlexGap>
        <Card sx={{minWidth: 150}}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Toplam olay</Typography>
            <Typography variant="h5">{ozet?.total ?? '—'}</Typography>
          </CardContent>
        </Card>
        <Card sx={{minWidth: 150}}>
          <CardContent>
            <Tooltip title={BEKLIYOR_UYARISI}>
              <Typography variant="caption" color="text.secondary">Bekleyen</Typography>
            </Tooltip>
            <Typography variant="h5">{ozet?.pending_total ?? '—'}</Typography>
          </CardContent>
        </Card>
        <Card sx={{minWidth: 150}}>
          <CardContent>
            <Typography variant="caption" color="text.secondary">Başarısız</Typography>
            <Typography variant="h5" color={ozet && ozet.failed_total > 0 ? 'warning.main' : undefined}>
              {ozet?.failed_total ?? '—'}
            </Typography>
          </CardContent>
        </Card>
      </Stack>

      {ozet && ozet.buckets.length > 0 && (
        <Card sx={{mb: 2}}>
          <CardContent>
            <Typography variant="subtitle2" sx={{mb: 1}}>Kaynak ve duruma göre kırılım</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {ozet.buckets.map(kova => (
                <Tooltip
                  key={`${kova.source_type}:${kova.status}`}
                  title={kova.oldest_created_at
                    ? `En eski: ${isoDateTime(kova.oldest_created_at)}`
                    : 'Zaman bilgisi yok'}
                >
                  <Chip
                    size="small"
                    icon={basarisizMi(kova.status) ? <WarningAmberIcon /> : undefined}
                    color={basarisizMi(kova.status) ? 'warning' : 'default'}
                    variant="outlined"
                    label={`${eventSourceLabel(kova.source_type)} · ${eventStatusLabel(kova.status)}: ${kova.count}`}
                  />
                </Tooltip>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}

      <FormControlLabel
        sx={{mb: 1}}
        control={
          <Switch
            checked={yalnizBasarisiz}
            onChange={e => setYalnizBasarisiz(e.target.checked)}
          />
        }
        label="Yalnız başarısız olaylar"
      />

      {yukleniyor ? (
        <Box sx={{display: 'flex', justifyContent: 'center', py: 4}}><CircularProgress /></Box>
      ) : hata ? null : olaylar.length === 0 ? (
        // HATA VARKEN "olay yok" YAZILMAZ. İkisi farklı şey: biri "kuyruk
        // temiz", öteki "kuyruğu okuyamadık". Aynı anda göstermek, okunamayan
        // bir kuyruğu temiz sanmaya davet ederdi — bu ekranın kapatmak için
        // var olduğu kusurun aynısı, bir kat yukarıda.
        <Alert severity="info">
          {yalnizBasarisiz ? 'Başarısız olay yok.' : 'Kuyrukta olay yok.'}
        </Alert>
      ) : (
        <>
          <ResponsiveTable
            rows={olaylar}
            columns={columns}
            getRowId={row => row.id}
            cardTitle={row => `#${row.id} · ${eventSourceLabel(row.source_type)} #${row.source_id}`}
            cardSubtitle={row => eventStatusLabel(row.status)}
            cardFields={[
              {label: 'Deneme', value: row => (row.attempts === null ? '—' : String(row.attempts))},
              {label: 'Gerekçe', value: row => row.last_error || '—'},
              {label: 'Son güncelleme', value: row => isoDateTime(row.updated_at)},
            ]}
          />
          {toplam > olaylar.length && (
            <Typography variant="caption" color="text.secondary" sx={{mt: 1, display: 'block'}}>
              {toplam} olayın ilk {olaylar.length} tanesi gösteriliyor.
            </Typography>
          )}
        </>
      )}
    </Box>
  );
}
