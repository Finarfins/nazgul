/**
 * Parsel Detayı (`/tarla/parseller/:id`) — sezonlar ve zaman çizelgesi.
 *
 * Konu #2'nin "Ekranlar" başlığındaki *parsel detay* ve *sezon zaman
 * çizelgesi* maddeleri.
 *
 * Ekranın taşıdığı iki karar:
 *
 * 1. **Faaliyet ve hasat TEK bir çizelgede birleşiyor.** İkisini ayrı listede
 *    göstermek "ilaçlamadan kaç gün sonra hasat edildi" sorusunu okunmaz hâle
 *    getirirdi; oysa parsel geçmişine bakmanın başlıca sebebi bu.
 *
 * 2. **Hesaplanamayan oran sıfır DEĞİL.** Sunucu ekilen alanı girilmemiş
 *    sezonda `null` dönüyor ve parselin alanına düşmüyor. Ekran da "—" yazıp
 *    sebebini söylüyor: ölçülmemiş bir sezonu ölçülmüş gibi göstermek, dekar
 *    başına maliyeti uydurmak olurdu.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {Link as RouterLink, useParams} from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Divider,
  MenuItem, Stack, TextField, Tooltip, Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import AgricultureIcon from '@mui/icons-material/Agriculture';
import GrassIcon from '@mui/icons-material/Grass';
import ScienceIcon from '@mui/icons-material/Science';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';

import {api} from '../api';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  activityTypeLabel, farmErrorText, isoDate, isoDateTime, money, qty,
  seasonStatusLabel, type FieldSafety, type Numeric, type Parcel,
} from '../farm/farmApi';

type TimelineSeason = {
  id: number; season_year: number; crop: string; variety: string | null;
  status: string; started_on: string | null; ended_on: string | null;
  planted_area_decare: Numeric | null; notes: string | null;
  total_cost: Numeric; harvest_quantity: Numeric;
  cost_per_decare: Numeric | null; yield_per_decare: Numeric | null;
  revenue_amount: Numeric | null; gross_margin: Numeric | null;
};
type TimelineActivity = {
  id: number; season_id: number; activity_type: string; performed_at: string;
  applied_area_decare: Numeric | null; area_override_reason: string | null;
  notes: string | null; reentry_interval_days: number | null;
  preharvest_interval_days: number | null; input_cost: Numeric | null;
};
type TimelineHarvest = {
  id: number; season_id: number; harvested_on: string; quantity: Numeric;
  unit: string; harvested_area_decare: Numeric | null; quality_grade: string | null;
  moisture_percent: Numeric | null; safety_override_reason: string | null; notes: string | null;
  /** İkisi de `null` olabilir: hasat kaydedildiğinde satış henüz yapılmamış
   *  olur. Boş "sıfır" değil "henüz bilinmiyor" demektir. */
  sold_quantity: Numeric | null; revenue_amount: Numeric | null;
};
type TimelineData = {
  parcel: Parcel;
  farm: {id: number; code: string; name: string};
  seasons: TimelineSeason[];
  activities: TimelineActivity[];
  harvests: TimelineHarvest[];
};

/** Oranın "hesaplanamadı" hâli — sıfır göstermek yanıltıcı olurdu. */
const ORANSIZ = 'Ekilen alan girilmemiş';

// Masaüstü sütunları. 390px'te ResponsiveTable zaten kart düzenine geçtiği için
// bu 9 sütunluk tablo mobilde hiç çizilmez; ölçülen 749px'lik yatay taşma
// (clientWidth 364) tam olarak buradan geliyordu.
const seasonColumns: GridColDef[] = [
  {field: 'crop', headerName: 'Sezon', flex: 1, minWidth: 170, sortable: false,
   renderCell: params => (
     <Box>
       <Typography variant="body2" fontWeight={650}>
         {params.row.variety ? `${params.row.crop} (${params.row.variety})` : params.row.crop}
       </Typography>
       <Typography variant="caption" color="text.secondary">
         {params.row.season_year}{params.row.started_on ? ` · ${isoDate(params.row.started_on)}` : ''}
       </Typography>
     </Box>
   )},
  {field: 'planted_area_decare', headerName: 'Ekilen (dekar)', width: 130, align: 'right', headerAlign: 'right',
   valueGetter: (_v, row: any) => qty(row.planted_area_decare)},
  {field: 'total_cost', headerName: 'Girdi maliyeti', width: 130, align: 'right', headerAlign: 'right',
   valueGetter: (_v, row: any) => money(row.total_cost)},
  {field: 'cost_per_decare', headerName: 'Dekar başına maliyet', width: 165, align: 'right', headerAlign: 'right',
   renderCell: params => params.row.cost_per_decare === null
     ? <Tooltip title="Sezona ekilen alan girilmediği için oran hesaplanamıyor. Parselin alanına düşmüyoruz: ölçülmemiş bir sezonu ölçülmüş gibi göstermek yanıltıcı olurdu."><Typography variant="body2" color="text.secondary">{ORANSIZ}</Typography></Tooltip>
     : <>{money(params.row.cost_per_decare)}</>},
  {field: 'harvest_quantity', headerName: 'Hasat', width: 110, align: 'right', headerAlign: 'right',
   valueGetter: (_v, row: any) => qty(row.harvest_quantity)},
  {field: 'yield_per_decare', headerName: 'Dekar başına verim', width: 155, align: 'right', headerAlign: 'right',
   renderCell: params => params.row.yield_per_decare === null
     ? <Typography variant="body2" color="text.secondary">—</Typography>
     : <>{qty(params.row.yield_per_decare)}</>},
  {field: 'revenue_amount', headerName: 'Gelir', width: 140, align: 'right', headerAlign: 'right',
   renderCell: params => params.row.revenue_amount === null
     ? <Typography variant="body2" color="text.secondary">Satış girilmemiş</Typography>
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
  {field: 'status', headerName: 'Durum', width: 130, sortable: false,
   renderCell: params => <Chip size="small" label={seasonStatusLabel(params.row.status)} />},
];

type Olay =
  | {tur: 'activity'; gun: string; kayit: TimelineActivity}
  | {tur: 'harvest'; gun: string; kayit: TimelineHarvest};

export default function ParcelDetail() {
  const {id} = useParams();
  const [data, setData] = useState<TimelineData | null>(null);
  const [safety, setSafety] = useState<FieldSafety | null>(null);
  const [sezonFiltre, setSezonFiltre] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const yukle = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError('');
    try {
      const [t, g] = await Promise.all([
        api.get<TimelineData>(`/farm-parcels/${id}/timeline`),
        api.get<FieldSafety>('/field-safety'),
      ]);
      setData(t.data);
      setSafety(g.data);
    } catch (err) {
      setData(null);
      setError(farmErrorText(err, 'Parsel detayı yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const sezonAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const s of data?.seasons ?? []) map.set(s.id, `${s.crop} · ${s.season_year}`);
    return (sid: number) => map.get(sid) ?? `#${sid}`;
  }, [data]);

  /** Faaliyet + hasat, tek çizelgede, yeniden eskiye. */
  const olaylar = useMemo<Olay[]>(() => {
    if (!data) return [];
    const secili = sezonFiltre === '' ? null : Number(sezonFiltre);
    const hepsi: Olay[] = [
      ...data.activities
        .filter(a => secili === null || a.season_id === secili)
        .map(a => ({tur: 'activity' as const, gun: a.performed_at.slice(0, 10), kayit: a})),
      ...data.harvests
        .filter(h => secili === null || h.season_id === secili)
        .map(h => ({tur: 'harvest' as const, gun: h.harvested_on.slice(0, 10), kayit: h})),
    ];
    // Metin karşılaştırması yeterli: ikisi de YYYY-AA-GG ile başlıyor ve
    // `new Date()` kullanmak zaman damgasını yerel güne kaydırırdı.
    return hepsi.sort((a, b) => (a.gun < b.gun ? 1 : a.gun > b.gun ? -1 : 0));
  }, [data, sezonFiltre]);

  /** Bu parsele ait yürürlükteki giriş yasağı. */
  const girisYasagi = safety?.reentry_blocks.filter(b => String(b.parcel_id) === id) ?? [];

  if (loading && !data) {
    return <Box py={8} display="grid" sx={{placeItems: 'center'}}><CircularProgress /></Box>;
  }
  if (error) {
    return (
      <Stack spacing={2}>
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>{error}</Alert>
        <Box><Button component={RouterLink} to="/tarla/ciftlikler">Parsel listesine dön</Button></Box>
      </Stack>
    );
  }
  if (!data) return null;

  const p = data.parcel;

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <AgricultureIcon color="primary" />
            <Typography variant="h4">{p.name}</Typography>
            <Chip size="small" label={p.code} />
          </Stack>
          <Typography color="text.secondary">
            {data.farm.name} · {qty(p.area_decare)} dekar
            {p.parcel_no || p.block_no ? ` · Ada ${p.block_no || '—'} / Parsel ${p.parcel_no || '—'}` : ''}
            {[p.district, p.city].filter(Boolean).length ? ` · ${[p.district, p.city].filter(Boolean).join(', ')}` : ''}
          </Typography>
        </Box>
        <Button component={RouterLink} to="/tarla/ciftlikler" variant="outlined" sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}>
          Parsel listesi
        </Button>
      </Stack>

      {girisYasagi.length > 0 && (
        <Alert severity="error" icon={<WarningAmberIcon />}>
          <strong>Bu parselde tarlaya giriş yasağı sürüyor.</strong>{' '}
          {girisYasagi.map(b => `${isoDate(b.performed_on)} tarihli işlem, ${isoDate(b.safe_from)} sonrası güvenli`).join(' · ')}
          {' '}Sulama ve gübreleme dâhil her iş için geçerlidir.
        </Alert>
      )}

      <Card>
        <CardContent sx={{pb: 1}}>
          <Typography variant="h6">Sezonlar</Typography>
          <Typography variant="body2" color="text.secondary">
            Maliyet faaliyetlere bağlanan girdilerden, verim kaydedilmiş hasatlardan gelir.
            <strong> Brüt katkı = gelir − girdi maliyeti;</strong> işçilik, makine ve yakıt
            dâhil değildir, dolayısıyla gerçek kârdan yüksektir.
          </Typography>
        </CardContent>
        <Divider />
        {!data.seasons.length ? (
          <Alert severity="info" sx={{m: 2}}>Bu parselde henüz sezon açılmamış.</Alert>
        ) : (
          <Box data-testid="parcel-detail-data-surface">
            <ResponsiveTable
              rows={data.seasons}
              columns={seasonColumns}
              hideFooter
              cardTitle={s => (s.variety ? `${s.crop} (${s.variety})` : s.crop)}
              cardSubtitle={s => `${s.season_year}${s.started_on ? ` · ${isoDate(s.started_on)}` : ''} · ${seasonStatusLabel(s.status)}`}
              cardFields={[
                {label: 'Ekilen (dekar)', value: (s: TimelineSeason) => qty(s.planted_area_decare)},
                {label: 'Girdi maliyeti', value: (s: TimelineSeason) => money(s.total_cost)},
                {label: 'Dekar başına maliyet', value: (s: TimelineSeason) => s.cost_per_decare === null ? ORANSIZ : money(s.cost_per_decare)},
                {label: 'Hasat', value: (s: TimelineSeason) => qty(s.harvest_quantity)},
                {label: 'Dekar başına verim', value: (s: TimelineSeason) => s.yield_per_decare === null ? '—' : qty(s.yield_per_decare)},
                {label: 'Gelir', value: (s: TimelineSeason) => s.revenue_amount === null ? 'Satış girilmemiş' : money(s.revenue_amount)},
                {label: 'Brüt katkı', value: (s: TimelineSeason) => s.gross_margin === null ? '—' : (
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
          <Stack direction={{xs: 'column', sm: 'row'}} justifyContent="space-between" gap={1.5} alignItems={{sm: 'center'}}>
            <Box>
              <Typography variant="h6">Zaman çizelgesi</Typography>
              <Typography variant="body2" color="text.secondary">
                Faaliyetler ve hasatlar birlikte, yeniden eskiye.
              </Typography>
            </Box>
            {data.seasons.length > 1 && (
              <TextField select size="small" label="Sezon" value={sezonFiltre} sx={{minWidth: 200}}
                onChange={e => setSezonFiltre(e.target.value)}>
                <MenuItem value="">Tüm sezonlar</MenuItem>
                {data.seasons.map(s => (
                  <MenuItem key={s.id} value={String(s.id)}>{s.crop} · {s.season_year}</MenuItem>
                ))}
              </TextField>
            )}
          </Stack>
        </CardContent>
        <Divider />
        {!olaylar.length ? (
          <Alert severity="info" sx={{m: 2}}>Bu parselde kayıtlı faaliyet ya da hasat yok.</Alert>
        ) : (
          <Stack divider={<Divider />}>
            {olaylar.map(olay => (
              <Stack
                key={`${olay.tur}-${olay.kayit.id}`}
                direction={{xs: 'column', sm: 'row'}}
                spacing={1.5}
                px={2} py={1.4}
                alignItems={{sm: 'flex-start'}}
                justifyContent="space-between"
              >
                <Stack direction="row" spacing={1.5} alignItems="flex-start" flex={1} minWidth={0}>
                  {olay.tur === 'harvest' ? <GrassIcon color="success" /> : <ScienceIcon color="action" />}
                  <Box minWidth={0}>
                    {olay.tur === 'activity' ? (
                      <>
                        <Typography fontWeight={650}>{activityTypeLabel(olay.kayit.activity_type)}</Typography>
                        <Typography variant="caption" color="text.secondary" display="block">
                          {isoDateTime(olay.kayit.performed_at)} · {sezonAdi(olay.kayit.season_id)}
                          {olay.kayit.applied_area_decare !== null && ` · ${qty(olay.kayit.applied_area_decare)} dekar`}
                        </Typography>
                        {olay.kayit.area_override_reason && (
                          <Typography variant="caption" color="warning.main" display="block">
                            Alan aşımı gerekçesi: {olay.kayit.area_override_reason}
                          </Typography>
                        )}
                        {olay.kayit.preharvest_interval_days !== null && (
                          <Typography variant="caption" color="text.secondary" display="block">
                            Hasat bekleme: {olay.kayit.preharvest_interval_days} gün
                            {olay.kayit.reentry_interval_days !== null && ` · Giriş yasağı: ${olay.kayit.reentry_interval_days} gün`}
                          </Typography>
                        )}
                      </>
                    ) : (
                      <>
                        <Typography fontWeight={650}>
                          Hasat — {qty(olay.kayit.quantity)} {olay.kayit.unit}
                        </Typography>
                        <Typography variant="caption" color="text.secondary" display="block">
                          {isoDate(olay.kayit.harvested_on)} · {sezonAdi(olay.kayit.season_id)}
                          {olay.kayit.moisture_percent !== null && ` · Nem %${qty(olay.kayit.moisture_percent)}`}
                          {olay.kayit.quality_grade && ` · ${olay.kayit.quality_grade}`}
                          {olay.kayit.sold_quantity !== null && ` · Satılan ${qty(olay.kayit.sold_quantity)} ${olay.kayit.unit}`}
                        </Typography>
                        {olay.kayit.safety_override_reason && (
                          <Typography variant="caption" color="error.main" display="block">
                            Erken hasat gerekçesi: {olay.kayit.safety_override_reason}
                          </Typography>
                        )}
                      </>
                    )}
                  </Box>
                </Stack>
                {olay.tur === 'activity' && Number(olay.kayit.input_cost || 0) > 0 && (
                  <Chip size="small" variant="outlined" label={money(olay.kayit.input_cost)} />
                )}
                {olay.tur === 'harvest' && olay.kayit.revenue_amount !== null && (
                  <Chip size="small" color="success" variant="outlined" label={money(olay.kayit.revenue_amount)} />
                )}
              </Stack>
            ))}
          </Stack>
        )}
      </Card>
    </Stack>
  );
}
