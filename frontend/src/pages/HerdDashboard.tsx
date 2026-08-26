/**
 * Sürü Panosu (`/hayvancilik`) — kaç hayvan, kaç doğum, aşısı geciken kaç baş.
 *
 * Konu: mobil-erp#17, FAZ 3.
 *
 * Panoda üç şey BİLEREK ayrı gösteriliyor; birleştirilseydi her biri gerçeği
 * yanlış anlatırdı:
 *
 * 1. **Bireysel hayvan sayısı ile grup baş sayısı ayrı.** Küçükbaş sürülerinde
 *    bireysel kayıt tutulmuyor; ikisini tek sayıda toplamak "hangi hayvanın
 *    kaydı var" bilgisini yok eder. Sunucu da bu yüzden iki alan dönüyor.
 *
 * 2. **Doğum OLAYI ile CANLI YAVRU sayısı ayrı.** Bir doğumdan ikiz çıkabilir,
 *    bir doğum ölü doğum olabilir. Tek sayı, ikiz doğuran bir sürüyü de yavru
 *    kaybeden bir sürüyü de aynı gösterirdi.
 *
 * 3. **"Aşısı geciken" = PLANLANMIŞ ama yapılmamış.** Zorunlu aşı takvimi
 *    (şap, brusella) FAZ 4'te gelecek. Bugünkü sayıyı "zorunlu aşısı eksik"
 *    diye sunmak, kullanıcıya olmayan bir güvence verirdi — o yüzden ekranda
 *    da açıkça yazıyor.
 */
import {useCallback, useEffect, useState} from 'react';
import {Link as RouterLink, useNavigate} from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, CircularProgress, Divider, Stack,
  Tooltip, Typography,
} from '@mui/material';
import type {GridColDef} from '@mui/x-data-grid';
import ChildCareIcon from '@mui/icons-material/ChildCare';
import GroupsIcon from '@mui/icons-material/Groups';
import PetsIcon from '@mui/icons-material/Pets';
import PregnantWomanIcon from '@mui/icons-material/PregnantWoman';
import VaccinesIcon from '@mui/icons-material/Vaccines';

import {api} from '../api';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  animalLabel, fetchAllHerd, herdErrorText, isoDate, sexLabel, speciesLabel,
  type Animal, type HerdDashboardData, type Vaccination,
} from '../herd/herdApi';

function OzetKart({icon, label, value, hint, color, to}: {
  icon: React.ReactNode; label: string; value: string; hint?: string;
  color?: 'primary' | 'warning' | 'error' | 'success'; to?: string;
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
        {to && (
          <Box mt={0.5}>
            <Button component={RouterLink} to={to} size="small" sx={{minHeight: {xs: 44}}}>Listeye git</Button>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

export default function HerdDashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<HerdDashboardData | null>(null);
  const [gecikenler, setGecikenler] = useState<Vaccination[]>([]);
  const [hayvanlar, setHayvanlar] = useState<Animal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const yukle = useCallback(() => {
    let active = true;
    setLoading(true);
    setError('');
    Promise.all([
      api.get<HerdDashboardData>('/herd-dashboard'),
      // Aşı listesi hayvanın küpesini JOIN'lemiyor; küpeleri ayrı çekip
      // istemcide eşliyoruz (tarla ekranlarındaki ile aynı desen).
      fetchAllHerd<Vaccination>('/animal-vaccinations'),
      fetchAllHerd<Animal>('/animals'),
    ])
      .then(([pano, asi, hayvan]) => {
        if (!active) return;
        setData(pano.data);
        setGecikenler(asi);
        setHayvanlar(hayvan);
      })
      .catch(err => {
        if (!active) return;
        setData(null);
        setError(herdErrorText(err, 'Sürü panosu yüklenemedi.'));
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
  const dogum = data?.births_last_12_months;
  const bugun = data?.as_of ?? '';

  const hayvanKimlik = new Map(hayvanlar.map(a => [a.id, a]));
  /** Vadesi GEÇMİŞ aşılar, en eski gecikme önce. Panoda ilk 8. */
  const geciken = gecikenler
    .filter(v => v.next_due_on && bugun && v.next_due_on < bugun)
    .filter(v => hayvanKimlik.get(v.animal_id)?.status === 'ACTIVE')
    .sort((a, b) => (a.next_due_on ?? '').localeCompare(b.next_due_on ?? ''))
    .slice(0, 8);
  const vaccinationColumns: GridColDef[] = [
    {
      field: 'animal', headerName: 'Hayvan', flex: 1, minWidth: 170,
      renderCell: ({row}) => (
        <RouterLink to={`/hayvancilik/hayvanlar/${row.animal_id}`}>
          {animalLabel(hayvanKimlik.get(row.animal_id))}
        </RouterLink>
      ),
    },
    {field: 'vaccine', headerName: 'Aşı', flex: 1, minWidth: 170},
    {field: 'applied_on', headerName: 'Yapıldığı tarih', minWidth: 145, valueFormatter: value => isoDate(value)},
    {
      field: 'next_due_on', headerName: 'Tekrar tarihi', minWidth: 145,
      renderCell: ({row}) => (
        <Tooltip title="Planlanan tekrar tarihi geçti">
          <Typography variant="body2" color="warning.main">{isoDate(row.next_due_on)}</Typography>
        </Tooltip>
      ),
    },
  ];

  const turler = Object.entries(data?.by_species ?? {});

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <PetsIcon color="primary" />
            <Typography variant="h4">Sürü Panosu</Typography>
          </Stack>
          <Typography color="text.secondary">
            {bugun ? `${isoDate(bugun)} itibarıyla` : 'Sürünün güncel durumu'}
          </Typography>
        </Box>
        <Button component={RouterLink} to="/hayvancilik/hayvanlar" variant="outlined"
          sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}>
          Hayvan listesi
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={yukle} sx={{minHeight: {xs: 44}}}>Yeniden dene</Button>}>
          {error}
        </Alert>
      )}

      <Stack direction="row" flexWrap="wrap" gap={1.5}>
        <OzetKart
          icon={<PetsIcon fontSize="small" />}
          label="Bireysel kayıtlı hayvan"
          value={String(ozet?.individual_active ?? 0)}
          hint="Küpesiyle tek tek kayıtlı, sürüdeki hayvanlar"
          to="/hayvancilik/hayvanlar"
        />
        <OzetKart
          icon={<GroupsIcon fontSize="small" />}
          label="Grup baş sayısı"
          // AYRI SAYI: bireysel kayıtla toplanmıyor. Toplamak, aynı hayvanın
          // iki kez sayılma riskini gizlerdi.
          value={String(ozet?.group_head_count ?? 0)}
          hint="Bireysel kayıt tutulmayan sürülerde beyan edilen baş sayısı"
        />
        <OzetKart
          icon={<PregnantWomanIcon fontSize="small" />}
          label="Gebe"
          value={String(ozet?.pregnant ?? 0)}
          color="success"
          hint="Tohumlama sonucu 'gebe' işaretlenmiş hayvanlar"
        />
        <OzetKart
          icon={<VaccinesIcon fontSize="small" />}
          label="Aşı vadesi geçen"
          value={String(ozet?.vaccination_overdue ?? 0)}
          color={ozet?.vaccination_overdue ? 'warning' : undefined}
          hint="Planlanan tekrar tarihi geçmiş"
          to={ozet?.vaccination_overdue ? '/hayvancilik/saglik' : undefined}
        />
        <OzetKart
          icon={<ChildCareIcon fontSize="small" />}
          label="Son 12 ay doğum"
          value={String(dogum?.events ?? 0)}
          hint={`${dogum?.live_offspring ?? 0} canlı yavru · ${dogum?.non_live_events ?? 0} ölü doğum/atma`}
          to="/hayvancilik/doller"
        />
      </Stack>

      {/* Aşı sayısının ne ANLAMA GELDİĞİ ekranda yazıyor: kullanıcı bunu
          "zorunlu aşılarım tam" diye okumamalı. FAZ 4'te zorunlu takvim geldi
          ama bu sayı HÂLÂ onu göstermiyor — ikisi farklı sorular ve
          birleştirilmedi (biri "planladım, yapmadım", diğeri "mevzuat gereği
          yapılmalıydı"). */}
      <Alert
        severity="info"
        variant="outlined"
        action={<Button component={RouterLink} to="/hayvancilik/saglik" color="inherit" size="small" sx={{minHeight: {xs: 44}}}>Zorunlu takvim</Button>}
      >
        Yukarıdaki aşı sayısı yalnız <b>sizin girdiğiniz tekrar tarihlerine</b> bakar —
        bu sayının sıfır olması &laquo;tüm zorunlu aşılar tamam&raquo; demek değildir.
        Şap ve brusellanın yaşa göre hesaplanan zorunlu takvimi Sürü Sağlığı
        ekranındaki <b>Zorunlu takvim</b> sekmesindedir.
      </Alert>

      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>Türe göre dağılım</Typography>
          {!turler.length ? (
            <Typography color="text.secondary">
              Henüz bireysel hayvan kaydı yok. <RouterLink to="/hayvancilik/hayvanlar">Hayvan ekleyin</RouterLink>.
            </Typography>
          ) : (
            <Stack direction="row" flexWrap="wrap" gap={2} divider={<Divider orientation="vertical" flexItem />}>
              {turler.map(([tur, sayi]) => (
                <Box key={tur} minWidth={140}>
                  <Typography variant="body2" color="text.secondary">{speciesLabel(tur)}</Typography>
                  <Typography variant="h6" fontWeight={700}>{sayi.total}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {sayi.female} {sexLabel('FEMALE').toLowerCase()} · {sayi.male} {sexLabel('MALE').toLowerCase()}
                  </Typography>
                </Box>
              ))}
            </Stack>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent sx={{pb: 0}}>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="h6">Aşı tekrarı gecikenler</Typography>
            <Button component={RouterLink} to="/hayvancilik/saglik" size="small" sx={{minHeight: {xs: 44}}}>Tümü</Button>
          </Stack>
        </CardContent>
        {!geciken.length ? (
          <CardContent>
            <Typography color="text.secondary">Vadesi geçmiş aşı tekrarı yok.</Typography>
          </CardContent>
        ) : (
          <Box data-testid="herd-dashboard-data-surface" px={{xs: 1.5, md: 0}} pb={1.5}>
            <ResponsiveTable
              rows={geciken}
              columns={vaccinationColumns}
              cardTitle={row => animalLabel(hayvanKimlik.get(row.animal_id))}
              cardSubtitle={row => row.vaccine}
              cardFields={[
                {label: 'Yapıldığı tarih', value: row => isoDate(row.applied_on)},
                {label: 'Tekrar tarihi', value: row => isoDate(row.next_due_on)},
              ]}
              getRowId={row => row.id}
              onRowClick={row => navigate(`/hayvancilik/hayvanlar/${row.animal_id}`)}
              hideFooter
            />
          </Box>
        )}
      </Card>
    </Stack>
  );
}
