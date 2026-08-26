/**
 * Tarla Panosu (`/tarla`) — sezon başına gerçek maliyet, verim ve bugünün işleri.
 *
 * Panonun taşıdığı tek zor karar şu: **oran hesaplanamadığında sıfır
 * göstermiyoruz.** Sunucu alansız sezonda `yield_per_decare`/`cost_per_decare`
 * için `null` dönüyor (sıfıra bölmek yerine). Burada `0,00 ₺` yazmak
 * "dekar başına maliyet sıfır" demek olurdu ve bu, ölçülmemiş bir sezonu
 * kârlı gibi gösterirdi. Onun yerine "alan girilmemiş" diyoruz.
 */
import {useCallback, useEffect, useState} from 'react';
import {Link as RouterLink} from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Divider,
  Stack, Tooltip, Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import AgricultureIcon from '@mui/icons-material/Agriculture';
import AssignmentLateIcon from '@mui/icons-material/AssignmentLate';
import EventAvailableIcon from '@mui/icons-material/EventAvailable';
import ScheduleIcon from '@mui/icons-material/Schedule';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';

import {api} from '../api';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  farmErrorText, isoDate, money, qty, seasonStatusLabel,
  type FarmDashboardData, type FieldSafety, type FieldTask, type Page,
} from '../farm/farmApi';

/** Oranın "hesaplanamadı" hâli. Sıfır göstermek yanıltıcı olurdu. */
const HESAPLANAMADI = 'Alan girilmemiş';
/** Gelirin "henüz bilinmiyor" hâli — sıfır göstermek zarar gibi okunurdu. */
const SATILMADI = 'Satış girilmemiş';

// ParcelDetail'deki sezon tablosunun ikizi (8 sütun, ölçülen 654px / clientWidth
// 364). Aynı kusur iki yerde: ikisi de artık ortak ResponsiveTable'dan geçiyor,
// yani 390px'te kart düzenine düşüyor ve yatay taşma üretmiyor.
const seasonColumns: GridColDef[] = [
  {field: 'crop', headerName: 'Sezon', flex: 1, minWidth: 170, sortable: false,
   renderCell: params => (
     <Box>
       <Typography variant="body2" fontWeight={650}>{params.row.crop} · {params.row.season_year}</Typography>
       <Typography variant="caption" color="text.secondary">
         {params.row.parcel_name} · {seasonStatusLabel(params.row.status)}
       </Typography>
     </Box>
   )},
  {field: 'area_decare', headerName: 'Alan (dekar)', width: 125, align: 'right', headerAlign: 'right',
   valueGetter: (_v, row: any) => qty(row.area_decare)},
  {field: 'total_cost', headerName: 'Girdi maliyeti', width: 130, align: 'right', headerAlign: 'right',
   valueGetter: (_v, row: any) => money(row.total_cost)},
  {field: 'cost_per_decare', headerName: 'Dekar başına maliyet', width: 165, align: 'right', headerAlign: 'right',
   renderCell: params => params.row.cost_per_decare === null
     ? <Tooltip title="Sezona ekilen alan girilmediği için oran hesaplanamıyor."><Typography variant="body2" color="text.secondary">{HESAPLANAMADI}</Typography></Tooltip>
     : <>{money(params.row.cost_per_decare)}</>},
  {field: 'harvest_quantity', headerName: 'Hasat', width: 110, align: 'right', headerAlign: 'right',
   valueGetter: (_v, row: any) => qty(row.harvest_quantity)},
  {field: 'yield_per_decare', headerName: 'Dekar başına verim', width: 155, align: 'right', headerAlign: 'right',
   renderCell: params => params.row.yield_per_decare === null
     ? <Typography variant="body2" color="text.secondary">{HESAPLANAMADI}</Typography>
     : <>{qty(params.row.yield_per_decare)}</>},
  // Gelir girilmemişse SIFIR göstermiyoruz: ürününü henüz satmamış bir sezon
  // "zarar etti" gibi okunurdu.
  {field: 'revenue_amount', headerName: 'Gelir', width: 140, align: 'right', headerAlign: 'right',
   renderCell: params => params.row.revenue_amount === null
     ? <Typography variant="body2" color="text.secondary">{SATILMADI}</Typography>
     : <>{money(params.row.revenue_amount)}</>},
  {field: 'gross_margin', headerName: 'Brüt katkı', width: 130, align: 'right', headerAlign: 'right',
   renderHeader: () => (
     <Tooltip title="Gelir − GİRDİ maliyeti. Kâr DEĞİL: işçilik, makine ve yakıt bu hesaba girmiyor.">
       <span>Brüt katkı</span>
     </Tooltip>
   ),
   renderCell: params => params.row.gross_margin === null
     ? <Typography variant="body2" color="text.secondary">—</Typography>
     : <Typography variant="body2" fontWeight={650}
         color={Number(params.row.gross_margin) < 0 ? 'error.main' : 'success.main'}>
         {money(params.row.gross_margin)}
       </Typography>},
];

function OzetKart({icon, label, value, hint, color}: {
  icon: React.ReactNode; label: string; value: string; hint?: string;
  color?: 'primary' | 'warning' | 'error' | 'success';
}) {
  return (
    <Card sx={{flex: '1 1 210px', minWidth: 190}}>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" mb={0.5} color={color ? `${color}.main` : 'text.secondary'}>
          {icon}
          <Typography variant="body2" color="text.secondary">{label}</Typography>
        </Stack>
        <Typography variant="h5" fontWeight={750}>{value}</Typography>
        {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
      </CardContent>
    </Card>
  );
}

export default function FarmDashboard() {
  const [data, setData] = useState<FarmDashboardData | null>(null);
  const [tasks, setTasks] = useState<FieldTask[]>([]);
  const [safety, setSafety] = useState<FieldSafety | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const yukle = useCallback(() => {
    let active = true;
    setLoading(true);
    setError('');
    Promise.all([
      api.get<FarmDashboardData>('/field-dashboard'),
      // Açık görevler, en yakın tarihli önce. Panoda ilk 8'i gösteriyoruz;
      // tamamı Tarla Görevleri ekranında.
      api.get<Page<FieldTask>>('/field-tasks', {params: {status: 'OPEN', limit: 8}}),
      api.get<FieldSafety>('/field-safety'),
    ])
      .then(([pano, gorev, guvenlik]) => {
        if (!active) return;
        setData(pano.data);
        setTasks(gorev.data.items);
        setSafety(guvenlik.data);
      })
      .catch(err => {
        if (!active) return;
        setData(null);
        setError(farmErrorText(err, 'Tarla panosu yüklenemedi.'));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(yukle, [yukle]);

  if (loading && !data) {
    return <Box py={8} display="grid" sx={{placeItems: 'center'}}><CircularProgress /></Box>;
  }

  const ozet = data?.summary;
  const gorevOzet = data?.tasks;
  const bugun = new Date().toISOString().slice(0, 10);

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <AgricultureIcon color="primary" />
            <Typography variant="h4">Tarla Panosu</Typography>
          </Stack>
          <Typography color="text.secondary">
            Sürmekte olan sezonların dekar başına maliyeti, verimi ve bekleyen işler.
          </Typography>
        </Box>
        <Button component={RouterLink} to="/tarla/gorevler" variant="outlined" sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}>
          Tüm görevler
        </Button>
      </Stack>

      {error && <Alert severity="error" action={<Button color="inherit" size="small" onClick={yukle}>Yeniden dene</Button>}>{error}</Alert>}

      {/* İLAÇ GÜVENLİK KISITLARI EN ÜSTTE. Bunlar bir "bilgi" değil, işi
          durduran kurallar: süresi dolmadan hasat kalıntı riski, giriş yasağı
          süren parsele girmek ise doğrudan insan sağlığı. Panonun altına
          koymak, tarlaya gitmek üzere olan kişinin görmemesi demekti. */}
      {!!safety?.harvest_blocks.length && (
        <Alert severity="warning" icon={<WarningAmberIcon />}>
          <strong>Hasat bekleme süresi süren {safety.harvest_blocks.length} sezon:</strong>{' '}
          {safety.harvest_blocks
            .map(b => `${b.crop} ${b.season_year} (${b.parcel_name}) — ${isoDate(b.safe_from)} sonrası`)
            .join(' · ')}
        </Alert>
      )}
      {!!safety?.reentry_blocks.length && (
        <Alert severity="error" icon={<WarningAmberIcon />}>
          <strong>Tarlaya giriş yasağı süren {safety.reentry_blocks.length} parsel:</strong>{' '}
          {safety.reentry_blocks
            .map(b => `${b.parcel_name} — ${isoDate(b.safe_from)} sonrası`)
            .join(' · ')}
          {' '}Sulama ve gübreleme dâhil her iş için geçerlidir.
        </Alert>
      )}

      <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
        <OzetKart icon={<AgricultureIcon fontSize="small" />} label="Çiftlik" value={String(ozet?.farm_count ?? 0)} />
        <OzetKart
          icon={<AgricultureIcon fontSize="small" />}
          label="Parsel"
          value={String(ozet?.parcel_count ?? 0)}
          hint={`Toplam ${qty(ozet?.total_area_decare)} dekar`}
        />
        <OzetKart icon={<EventAvailableIcon fontSize="small" />} label="Süren sezon" value={String(ozet?.active_season_count ?? 0)} />
        <OzetKart
          icon={<AssignmentLateIcon fontSize="small" />}
          label="Geciken iş"
          value={String(gorevOzet?.overdue ?? 0)}
          hint={`Bugün ${gorevOzet?.due_today ?? 0} · sırada ${gorevOzet?.upcoming ?? 0}`}
          color={(gorevOzet?.overdue ?? 0) > 0 ? 'error' : 'success'}
        />
      </Stack>

      <Card>
        <CardContent sx={{pb: 1}}>
          <Typography variant="h6">Sezon maliyeti ve verimi</Typography>
          <Typography variant="body2" color="text.secondary">
            Maliyet, faaliyetlere bağlanan girdilerden (gübre, ilaç, tohum) toplanır;
            verim kaydedilmiş hasatlardan. İkisi ayrı hesaplanır.
            <strong> Brüt katkı = gelir − girdi maliyeti;</strong> işçilik, makine ve
            yakıt bu hesaba dâhil değildir, dolayısıyla gerçek kârdan yüksektir.
          </Typography>
        </CardContent>
        <Divider />
        {!data?.seasons.length ? (
          <Alert severity="info" sx={{m: 2}}>
            Henüz süren ya da hasat edilmiş sezon yok. Sezon açtığınızda maliyet ve verim burada birikir.
          </Alert>
        ) : (
          <Box data-testid="farm-dashboard-data-surface">
            <ResponsiveTable
              rows={data.seasons}
              columns={seasonColumns}
              hideFooter
              getRowId={s => s.season_id}
              cardTitle={s => `${s.crop} · ${s.season_year}`}
              cardSubtitle={s => `${s.parcel_name} · ${seasonStatusLabel(s.status)}`}
              cardFields={[
                {label: 'Alan (dekar)', value: (s: any) => qty(s.area_decare)},
                {label: 'Girdi maliyeti', value: (s: any) => money(s.total_cost)},
                {label: 'Dekar başına maliyet', value: (s: any) => s.cost_per_decare === null ? HESAPLANAMADI : money(s.cost_per_decare)},
                {label: 'Hasat', value: (s: any) => qty(s.harvest_quantity)},
                {label: 'Dekar başına verim', value: (s: any) => s.yield_per_decare === null ? HESAPLANAMADI : qty(s.yield_per_decare)},
                {label: 'Gelir', value: (s: any) => s.revenue_amount === null ? SATILMADI : money(s.revenue_amount)},
                {label: 'Brüt katkı', value: (s: any) => s.gross_margin === null ? '—' : (
                  <Typography variant="body2" fontWeight={650}
                    color={Number(s.gross_margin) < 0 ? 'error.main' : 'success.main'}>
                    {money(s.gross_margin)}
                  </Typography>
                )},
              ]}
            />
          </Box>
        )}
      </Card>

      <Card>
        <CardContent sx={{pb: 1}}>
          <Typography variant="h6">Bekleyen işler</Typography>
        </CardContent>
        <Divider />
        {!tasks.length ? (
          <Alert severity="success" sx={{m: 2}}>Açık iş yok.</Alert>
        ) : (
          <Stack divider={<Divider />}>
            {tasks.map(t => {
              const gecikti = !!t.due_date && t.due_date < bugun;
              const bugunMu = t.due_date === bugun;
              return (
                <Stack key={t.id} direction={{xs: 'column', sm: 'row'}} spacing={1} px={2} py={1.4} alignItems={{sm: 'center'}} justifyContent="space-between">
                  <Box>
                    <Typography fontWeight={650}>{t.title}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t.due_date ? `Termin: ${isoDate(t.due_date)}` : 'Termin yok'}
                    </Typography>
                  </Box>
                  <Stack direction="row" spacing={1} alignItems="center">
                    {t.priority === 'HIGH' && <Chip size="small" color="warning" label="Yüksek öncelik" />}
                    <Chip
                      size="small"
                      icon={gecikti ? <AssignmentLateIcon /> : <ScheduleIcon />}
                      color={gecikti ? 'error' : bugunMu ? 'warning' : 'default'}
                      label={gecikti ? 'Gecikti' : bugunMu ? 'Bugün' : 'Sırada'}
                    />
                  </Stack>
                </Stack>
              );
            })}
          </Stack>
        )}
      </Card>
    </Stack>
  );
}
