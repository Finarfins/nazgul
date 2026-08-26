/**
 * Çiftlikler & Parseller (`/tarla/ciftlikler`).
 *
 * İki şey burada bilinçli:
 *
 * 1. **Yazma düğmeleri `farm.manage` iznine bağlı.** Sunucu zaten reddediyor;
 *    buradaki gizleme kullanıcıyı 403 duvarına çarpmaktan kurtarmak için.
 *    Güvenlik sunucuda, bu yalnız nezaket — o yüzden gizlemeye GÜVENİLMEZ.
 *
 * 2. **Düzenlemede `expected_updated_at` gönderiliyor.** Sunucudan okunan
 *    sürüm damgası forma taşınır; arada başkası kaydı değiştirdiyse 409 gelir
 *    ve kullanıcıya "yenile, sonra tekrar yaz" denir — sessizce ezilmez.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogActions,
  DialogContent, DialogTitle, MenuItem, Stack, Tab, Tabs, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import AgricultureIcon from '@mui/icons-material/Agriculture';
import EditIcon from '@mui/icons-material/Edit';
import TimelineIcon from '@mui/icons-material/Timeline';
import type {GridColDef} from '@mui/x-data-grid';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {farmErrorText, qty, saveFarmRecord, type Farm, type Page, type Parcel} from '../farm/farmApi';

type FarmForm = {code: string; name: string; city: string; district: string; notes: string};
type ParcelForm = {
  farm_id: string; code: string; name: string; area_decare: string;
  parcel_no: string; block_no: string; city: string; district: string; neighborhood: string;
};

const BOS_CIFTLIK: FarmForm = {code: '', name: '', city: '', district: '', notes: ''};
const BOS_PARSEL: ParcelForm = {
  farm_id: '', code: '', name: '', area_decare: '',
  parcel_no: '', block_no: '', city: '', district: '', neighborhood: '',
};

/** Boş metin alanlarını `null`'a çevirir — sunucuya '' yollamak "boş string"
 *  olarak kaydedilir ve sonra "değer var mı?" kontrolleri yanlış cevap verir. */
const bosNull = (v: string) => {
  const t = v.trim();
  return t === '' ? null : t;
};

export default function Farms() {
  const {can} = useAuth();
  const navigate = useNavigate();
  const yazabilir = can('farm.manage');

  const [tab, setTab] = useState(0);
  const [farms, setFarms] = useState<Farm[]>([]);
  const [parcels, setParcels] = useState<Parcel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [farmDialog, setFarmDialog] = useState<{open: boolean; row: Farm | null; form: FarmForm}>({
    open: false, row: null, form: BOS_CIFTLIK,
  });
  const [parcelDialog, setParcelDialog] = useState<{open: boolean; row: Parcel | null; form: ParcelForm}>({
    open: false, row: null, form: BOS_PARSEL,
  });
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [f, p] = await Promise.all([
        api.get<Page<Farm>>('/farms', {params: {limit: 200}}),
        api.get<Page<Parcel>>('/farm-parcels', {params: {limit: 200}}),
      ]);
      setFarms(f.data.items);
      setParcels(p.data.items);
    } catch (err) {
      setError(farmErrorText(err, 'Çiftlik ve parseller yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  /** Liste uçları çiftlik adını JOIN'lemiyor; adı istemcide çözüyoruz. */
  const farmAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const f of farms) map.set(f.id, f.name);
    return (id: number) => map.get(id) ?? `#${id}`;
  }, [farms]);

  // -------------------------------------------------------------------------
  // Kaydetme
  // -------------------------------------------------------------------------

  const ciftlikKaydet = async () => {
    setSaving(true);
    setDialogError('');
    const {row, form} = farmDialog;
    const gövde = {
      code: form.code.trim(), name: form.name.trim(),
      city: bosNull(form.city), district: bosNull(form.district), notes: bosNull(form.notes),
    };
    try {
      if (row) {
        await saveFarmRecord('put', `/farms/${row.id}`, {
          ...gövde, status: row.status, expected_updated_at: row.updated_at,
        }, 'Çiftlik güncellenemedi.');
      } else {
        await saveFarmRecord('post', '/farms', gövde, 'Çiftlik oluşturulamadı.');
      }
      setFarmDialog({open: false, row: null, form: BOS_CIFTLIK});
      await yukle();
    } catch (err) {
      setDialogError(farmErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const parselKaydet = async () => {
    setSaving(true);
    setDialogError('');
    const {row, form} = parcelDialog;
    const gövde = {
      farm_id: Number(form.farm_id),
      code: form.code.trim(),
      name: form.name.trim(),
      // Alan STRING olarak gidiyor: Number()'a çevirip geri yazmak ondalık
      // basamakta kayan nokta hatası üretirdi.
      area_decare: form.area_decare.trim().replace(',', '.'),
      parcel_no: bosNull(form.parcel_no), block_no: bosNull(form.block_no),
      city: bosNull(form.city), district: bosNull(form.district),
      neighborhood: bosNull(form.neighborhood),
    };
    try {
      if (row) {
        await saveFarmRecord('put', `/farm-parcels/${row.id}`, {
          ...gövde, status: row.status, expected_updated_at: row.updated_at,
        }, 'Parsel güncellenemedi.');
      } else {
        await saveFarmRecord('post', '/farm-parcels', gövde, 'Parsel oluşturulamadı.');
      }
      setParcelDialog({open: false, row: null, form: BOS_PARSEL});
      await yukle();
    } catch (err) {
      setDialogError(farmErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  // -------------------------------------------------------------------------
  // Tablolar
  // -------------------------------------------------------------------------

  const ciftlikSutun: GridColDef[] = [
    {field: 'code', headerName: 'Kod', width: 120},
    {field: 'name', headerName: 'Çiftlik', flex: 1, minWidth: 200},
    {field: 'city', headerName: 'İl', width: 130, valueGetter: (_v, row) => row.city || '—'},
    {field: 'district', headerName: 'İlçe', width: 130, valueGetter: (_v, row) => row.district || '—'},
    {
      field: 'parcels', headerName: 'Parsel', width: 100, sortable: false,
      valueGetter: (_v, row) => parcels.filter(p => p.farm_id === row.id).length,
    },
    {
      field: 'status', headerName: 'Durum', width: 120,
      renderCell: params => <Chip size="small" color={params.value === 'ACTIVE' ? 'success' : 'default'} label={params.value === 'ACTIVE' ? 'Aktif' : 'Arşiv'} />,
    },
  ];

  const parselSutun: GridColDef[] = [
    {field: 'code', headerName: 'Kod', width: 120},
    {field: 'name', headerName: 'Parsel', flex: 1, minWidth: 180},
    {field: 'farm_id', headerName: 'Çiftlik', width: 180, valueGetter: (_v, row) => farmAdi(row.farm_id)},
    {field: 'area_decare', headerName: 'Alan (dekar)', width: 140, align: 'right', headerAlign: 'right', valueGetter: (_v, row) => qty(row.area_decare)},
    {field: 'parcel_no', headerName: 'Parsel no', width: 120, valueGetter: (_v, row) => row.parcel_no || '—'},
    {field: 'block_no', headerName: 'Ada no', width: 110, valueGetter: (_v, row) => row.block_no || '—'},
  ];

  const ciftlikDuzenle = (row: Farm) => {
    setDialogError('');
    setFarmDialog({
      open: true, row,
      form: {code: row.code, name: row.name, city: row.city || '', district: row.district || '', notes: row.notes || ''},
    });
  };
  const parselDuzenle = (row: Parcel) => {
    setDialogError('');
    setParcelDialog({
      open: true, row,
      form: {
        farm_id: String(row.farm_id), code: row.code, name: row.name,
        area_decare: String(row.area_decare), parcel_no: row.parcel_no || '',
        block_no: row.block_no || '', city: row.city || '', district: row.district || '',
        neighborhood: row.neighborhood || '',
      },
    });
  };

  const ciftlikGecerli = farmDialog.form.code.trim() !== '' && farmDialog.form.name.trim() !== '';
  const parselGecerli =
    parcelDialog.form.farm_id !== '' &&
    parcelDialog.form.code.trim() !== '' &&
    parcelDialog.form.name.trim() !== '' &&
    Number(parcelDialog.form.area_decare.replace(',', '.')) > 0;

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <AgricultureIcon color="primary" />
            <Typography variant="h4">Çiftlikler & Parseller</Typography>
          </Stack>
          <Typography color="text.secondary">
            Parseller çiftliğe bağlıdır; ekim sezonu da parsele açılır.
          </Typography>
        </Box>
        {yazabilir && (
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}
            onClick={() => {
              setDialogError('');
              if (tab === 0) setFarmDialog({open: true, row: null, form: BOS_CIFTLIK});
              else setParcelDialog({open: true, row: null, form: BOS_PARSEL});
            }}
          >
            {tab === 0 ? 'Yeni çiftlik' : 'Yeni parsel'}
          </Button>
        )}
      </Stack>

      {error && <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>{error}</Alert>}

      <Card>
        <Tabs value={tab} onChange={(_e, v) => setTab(v)} variant="scrollable" allowScrollButtonsMobile>
          <Tab label={`Çiftlikler (${farms.length})`} />
          <Tab label={`Parseller (${parcels.length})`} />
        </Tabs>
      </Card>

      {tab === 0 ? (
        <ResponsiveTable
          rows={farms}
          columns={ciftlikSutun}
          loading={loading}
          cardTitle={row => row.name}
          cardSubtitle={row => `${row.code} · ${row.city || 'İl girilmemiş'}`}
          cardFields={[
            {label: 'Parsel', value: row => parcels.filter(p => p.farm_id === row.id).length},
            {label: 'İlçe', value: row => row.district || '—'},
            {label: 'Durum', value: row => (row.status === 'ACTIVE' ? 'Aktif' : 'Arşiv')},
          ]}
          cardActions={yazabilir ? [{label: 'Düzenle', icon: <EditIcon />, onClick: ciftlikDuzenle}] : undefined}
          onRowClick={yazabilir ? ciftlikDuzenle : undefined}
        />
      ) : (
        <ResponsiveTable
          rows={parcels}
          columns={parselSutun}
          loading={loading}
          cardTitle={row => row.name}
          cardSubtitle={row => `${row.code} · ${farmAdi(row.farm_id)}`}
          cardFields={[
            {label: 'Alan', value: row => `${qty(row.area_decare)} dekar`},
            {label: 'Parsel / Ada', value: row => `${row.parcel_no || '—'} / ${row.block_no || '—'}`},
            {label: 'Konum', value: row => [row.district, row.city].filter(Boolean).join(', ') || '—'},
          ]}
          cardActions={[
            {label: 'Detay', icon: <TimelineIcon />, onClick: row => navigate(`/tarla/parseller/${row.id}`)},
            ...(yazabilir ? [{label: 'Düzenle', icon: <EditIcon />, onClick: parselDuzenle}] : []),
          ]}
          // Satıra tıklamak DETAYA gidiyor, düzenlemeye değil: parsele bakmak
          // düzenlemekten çok daha sık yapılan iş.
          onRowClick={row => navigate(`/tarla/parseller/${row.id}`)}
        />
      )}

      {/* --- Çiftlik formu --- */}
      <Dialog open={farmDialog.open} onClose={() => !saving && setFarmDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>{farmDialog.row ? 'Çiftliği düzenle' : 'Yeni çiftlik'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField label="Kod" required value={farmDialog.form.code} helperText="Büyük harfe çevrilir."
              onChange={e => setFarmDialog(d => ({...d, form: {...d.form, code: e.target.value}}))} />
            <TextField label="Çiftlik adı" required value={farmDialog.form.name}
              onChange={e => setFarmDialog(d => ({...d, form: {...d.form, name: e.target.value}}))} />
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="İl" fullWidth value={farmDialog.form.city}
                onChange={e => setFarmDialog(d => ({...d, form: {...d.form, city: e.target.value}}))} />
              <TextField label="İlçe" fullWidth value={farmDialog.form.district}
                onChange={e => setFarmDialog(d => ({...d, form: {...d.form, district: e.target.value}}))} />
            </Stack>
            <TextField label="Not" multiline minRows={2} value={farmDialog.form.notes}
              onChange={e => setFarmDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setFarmDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void ciftlikKaydet()} disabled={saving || !ciftlikGecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>

      {/* --- Parsel formu --- */}
      <Dialog open={parcelDialog.open} onClose={() => !saving && setParcelDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>{parcelDialog.row ? 'Parseli düzenle' : 'Yeni parsel'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            {!farms.length && <Alert severity="warning">Önce bir çiftlik oluşturun; parsel çiftliğe bağlanır.</Alert>}
            <TextField select label="Çiftlik" required value={parcelDialog.form.farm_id}
              onChange={e => setParcelDialog(d => ({...d, form: {...d.form, farm_id: e.target.value}}))}>
              {farms.map(f => <MenuItem key={f.id} value={String(f.id)}>{f.name} ({f.code})</MenuItem>)}
            </TextField>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Kod" required fullWidth value={parcelDialog.form.code}
                onChange={e => setParcelDialog(d => ({...d, form: {...d.form, code: e.target.value}}))} />
              <TextField label="Parsel adı" required fullWidth value={parcelDialog.form.name}
                onChange={e => setParcelDialog(d => ({...d, form: {...d.form, name: e.target.value}}))} />
            </Stack>
            <TextField label="Alan (dekar)" required inputMode="decimal" value={parcelDialog.form.area_decare}
              helperText="Sıfırdan büyük olmalı. Sezonun ekilen alanı bu değeri aşamaz."
              onChange={e => setParcelDialog(d => ({...d, form: {...d.form, area_decare: e.target.value}}))} />
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Parsel no" fullWidth value={parcelDialog.form.parcel_no}
                onChange={e => setParcelDialog(d => ({...d, form: {...d.form, parcel_no: e.target.value}}))} />
              <TextField label="Ada no" fullWidth value={parcelDialog.form.block_no}
                onChange={e => setParcelDialog(d => ({...d, form: {...d.form, block_no: e.target.value}}))} />
            </Stack>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="İl" fullWidth value={parcelDialog.form.city}
                onChange={e => setParcelDialog(d => ({...d, form: {...d.form, city: e.target.value}}))} />
              <TextField label="İlçe" fullWidth value={parcelDialog.form.district}
                onChange={e => setParcelDialog(d => ({...d, form: {...d.form, district: e.target.value}}))} />
              <TextField label="Mahalle / köy" fullWidth value={parcelDialog.form.neighborhood}
                onChange={e => setParcelDialog(d => ({...d, form: {...d.form, neighborhood: e.target.value}}))} />
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setParcelDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void parselKaydet()} disabled={saving || !parselGecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
