/**
 * Ekim Sezonları (`/tarla/sezonlar`).
 *
 * Sezon, maliyet ve verim hesabının çapasıdır: girdiler faaliyet üzerinden,
 * hasat doğrudan sezona bağlanır. Bu yüzden **ekilen alan** alanı önemli —
 * boş bırakılırsa dekar başına maliyet/verim hesaplanamaz. Form bunu bir hata
 * olarak değil, açık bir uyarı olarak söylüyor: alan gerçekten bilinmiyorsa
 * boş bırakmak, uydurmaktan iyidir.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  Alert, Box, Button, Card, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, MenuItem, Stack, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import EventAvailableIcon from '@mui/icons-material/EventAvailable';
import type {GridColDef} from '@mui/x-data-grid';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  farmErrorText, isoDate, qty, saveFarmRecord, seasonStatusLabel, SEASON_STATUSES,
  type CropSeason, type Page, type Parcel,
} from '../farm/farmApi';

/** Ürün seçicinin listesi. `/products` düz dizi döner; `HarvestSeasonAdmin`
 *  ile aynı savunmacı okuma kullanılıyor (dizi ya da `{items}`). */
type ProductRow = {id: number; name: string; product_code?: string | null};

type Form = {
  parcel_id: string; season_year: string; crop: string; product_id: string; variety: string;
  started_on: string; ended_on: string; planted_area_decare: string;
  status: string; notes: string;
};

const bosForm = (): Form => ({
  parcel_id: '', season_year: String(new Date().getFullYear()), crop: '', product_id: '',
  variety: '',
  started_on: '', ended_on: '', planted_area_decare: '', status: 'PLANNED', notes: '',
});

const bosNull = (v: string) => (v.trim() === '' ? null : v.trim());
const durumRengi = (s: string) =>
  s === 'ACTIVE' ? 'success' : s === 'HARVESTED' ? 'info' : s === 'CANCELLED' ? 'error' : 'default';

export default function CropSeasons() {
  const {can} = useAuth();
  const yazabilir = can('farm.manage');

  const [seasons, setSeasons] = useState<CropSeason[]>([]);
  const [parcels, setParcels] = useState<Parcel[]>([]);
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [yilFiltre, setYilFiltre] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [dialog, setDialog] = useState<{open: boolean; row: CropSeason | null; form: Form}>({
    open: false, row: null, form: bosForm(),
  });
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params: Record<string, unknown> = {limit: 200};
      if (yilFiltre) params.season_year = Number(yilFiltre);
      const [s, p] = await Promise.all([
        api.get<Page<CropSeason>>('/crop-seasons', {params}),
        api.get<Page<Parcel>>('/farm-parcels', {params: {limit: 200}}),
      ]);
      setSeasons(s.data.items);
      setParcels(p.data.items);
    } catch (err) {
      setError(farmErrorText(err, 'Sezonlar yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, [yilFiltre]);

  /** Ürün listesi AYRI yükleniyor ve hatası SESSİZ: ürün seçimi opsiyonel bir
   *  alan, listesi gelmezse sezon yine kaydedilebilmeli. Sezon/parsel
   *  yüklemesiyle aynı `Promise.all`a konsaydı `/products`taki bir arıza
   *  bütün sayfayı hata ekranına düşürürdü. */
  useEffect(() => {
    void (async () => {
      try {
        const r = await api.get<ProductRow[] | {items: ProductRow[]}>(
          '/products', {params: {limit: 1000}});
        setProducts(Array.isArray(r.data) ? r.data : r.data?.items || []);
      } catch {
        setProducts([]);
      }
    })();
  }, []);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const parselBilgi = useMemo(() => {
    const map = new Map<number, Parcel>();
    for (const p of parcels) map.set(p.id, p);
    return map;
  }, [parcels]);
  const parselAdi = (id: number) => parselBilgi.get(id)?.name ?? `#${id}`;

  const urunAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const p of products) map.set(p.id, p.name);
    // Bildirilmemiş ürün "—" DEĞİL, açıkça söyleniyor: bu sezonun hasadı
    // stoğa işlenmez ve kullanıcının düzeltebilmesi için bunu görmesi gerek.
    return (id: number | null | undefined) =>
      id === null || id === undefined ? 'Bildirilmemiş' : map.get(id) ?? `#${id}`;
  }, [products]);

  /** Seçili parselin alanı — forma "en fazla bu kadar" bilgisi düşmek için. */
  const secilenParsel = parselBilgi.get(Number(dialog.form.parcel_id));

  const kaydet = async () => {
    setSaving(true);
    setDialogError('');
    const {row, form} = dialog;
    const gövde = {
      parcel_id: Number(form.parcel_id),
      season_year: Number(form.season_year),
      crop: form.crop.trim(),
      // Boş seçim NULL gider — "ürün bildirilmemiş" geçerli bir durum.
      product_id: form.product_id === '' ? null : Number(form.product_id),
      variety: bosNull(form.variety),
      started_on: bosNull(form.started_on),
      ended_on: bosNull(form.ended_on),
      planted_area_decare: form.planted_area_decare.trim() === '' ? null : form.planted_area_decare.trim().replace(',', '.'),
      notes: bosNull(form.notes),
    };
    try {
      if (row) {
        await saveFarmRecord('put', `/crop-seasons/${row.id}`, {
          ...gövde, status: form.status, expected_updated_at: row.updated_at,
        }, 'Sezon güncellenemedi.');
      } else {
        await saveFarmRecord('post', '/crop-seasons', gövde, 'Sezon oluşturulamadı.');
      }
      setDialog({open: false, row: null, form: bosForm()});
      await yukle();
    } catch (err) {
      setDialogError(farmErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const duzenle = (row: CropSeason) => {
    setDialogError('');
    setDialog({
      open: true, row,
      form: {
        parcel_id: String(row.parcel_id), season_year: String(row.season_year),
        crop: row.crop,
        product_id: row.product_id === null || row.product_id === undefined
          ? '' : String(row.product_id),
        variety: row.variety || '',
        started_on: row.started_on?.slice(0, 10) || '', ended_on: row.ended_on?.slice(0, 10) || '',
        planted_area_decare: row.planted_area_decare === null ? '' : String(row.planted_area_decare),
        status: row.status, notes: row.notes || '',
      },
    });
  };

  const sutunlar: GridColDef[] = [
    {field: 'crop', headerName: 'Ürün', flex: 1, minWidth: 160,
      valueGetter: (_v, row) => (row.variety ? `${row.crop} (${row.variety})` : row.crop)},
    {field: 'season_year', headerName: 'Yıl', width: 90},
    {field: 'parcel_id', headerName: 'Parsel', width: 180, valueGetter: (_v, row) => parselAdi(row.parcel_id)},
    {field: 'planted_area_decare', headerName: 'Ekilen (dekar)', width: 150, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => qty(row.planted_area_decare)},
    {field: 'started_on', headerName: 'Başlangıç', width: 130, valueGetter: (_v, row) => isoDate(row.started_on)},
    {field: 'status', headerName: 'Durum', width: 140,
      renderCell: p => <Chip size="small" color={durumRengi(p.value)} label={seasonStatusLabel(p.value)} />},
  ];

  const gecerli =
    dialog.form.parcel_id !== '' &&
    dialog.form.crop.trim() !== '' &&
    Number(dialog.form.season_year) >= 2000;

  /** Ekilen alan parseli aşıyor mu? Sunucu da reddediyor; burada ÖNCEDEN
   *  söylemek kullanıcının formu doldurup reddedilmesini önlüyor. */
  const alanAsiyor =
    !!secilenParsel &&
    dialog.form.planted_area_decare.trim() !== '' &&
    Number(dialog.form.planted_area_decare.replace(',', '.')) > Number(secilenParsel.area_decare);

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <EventAvailableIcon color="primary" />
            <Typography variant="h4">Ekim Sezonları</Typography>
          </Stack>
          <Typography color="text.secondary">
            Maliyet ve verim sezon üzerinden birikir; faaliyetler ve hasat sezona bağlanır.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <TextField
            select size="small" label="Yıl" value={yilFiltre} sx={{minWidth: 130}}
            onChange={e => setYilFiltre(e.target.value)}
          >
            <MenuItem value="">Tüm yıllar</MenuItem>
            {Array.from({length: 6}, (_x, i) => new Date().getFullYear() + 1 - i).map(y => (
              <MenuItem key={y} value={String(y)}>{y}</MenuItem>
            ))}
          </TextField>
          {yazabilir && (
            <Button variant="contained" startIcon={<AddIcon />} sx={{minHeight: {xs: 44}}}
              onClick={() => {
                setDialogError('');
                setDialog({open: true, row: null, form: bosForm()});
              }}>
              Yeni sezon
            </Button>
          )}
        </Stack>
      </Stack>

      {error && <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>{error}</Alert>}

      {!loading && !parcels.length && (
        <Alert severity="info">Önce bir parsel tanımlayın — sezon parsele açılır.</Alert>
      )}

      <ResponsiveTable
        rows={seasons}
        columns={sutunlar}
        loading={loading}
        cardTitle={row => (row.variety ? `${row.crop} (${row.variety})` : row.crop)}
        cardSubtitle={row => `${row.season_year} · ${parselAdi(row.parcel_id)}`}
        cardFields={[
          {label: 'Stok ürünü', value: row => urunAdi(row.product_id)},
          {label: 'Ekilen alan', value: row => (row.planted_area_decare === null ? 'Girilmemiş' : `${qty(row.planted_area_decare)} dekar`)},
          {label: 'Başlangıç', value: row => isoDate(row.started_on)},
          {label: 'Durum', value: row => <Chip size="small" color={durumRengi(row.status)} label={seasonStatusLabel(row.status)} />},
        ]}
        cardActions={yazabilir ? [{label: 'Düzenle', icon: <EditIcon />, onClick: duzenle}] : undefined}
        onRowClick={yazabilir ? duzenle : undefined}
      />

      <Dialog open={dialog.open} onClose={() => !saving && setDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>{dialog.row ? 'Sezonu düzenle' : 'Yeni ekim sezonu'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField select label="Parsel" required value={dialog.form.parcel_id}
              onChange={e => setDialog(d => ({...d, form: {...d.form, parcel_id: e.target.value}}))}>
              {parcels.map(p => (
                <MenuItem key={p.id} value={String(p.id)}>{p.name} — {qty(p.area_decare)} dekar</MenuItem>
              ))}
            </TextField>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Ürün" required fullWidth value={dialog.form.crop} placeholder="Buğday"
                onChange={e => setDialog(d => ({...d, form: {...d.form, crop: e.target.value}}))} />
              <TextField label="Çeşit" fullWidth value={dialog.form.variety}
                onChange={e => setDialog(d => ({...d, form: {...d.form, variety: e.target.value}}))} />
              <TextField label="Yıl" required type="number" sx={{minWidth: 110}} value={dialog.form.season_year}
                onChange={e => setDialog(d => ({...d, form: {...d.form, season_year: e.target.value}}))} />
            </Stack>
            <TextField
              select
              label="Stok ürünü"
              value={dialog.form.product_id}
              disabled={!products.length}
              helperText={
                products.length
                  ? 'Bu sezonun hasadı hangi stok ürününe yazılsın? Boş bırakılabilir — ama boşsa hasat stoğa işlenmez.'
                  : 'Ürün listesi yüklenemedi; sezon ürünsüz kaydedilebilir.'
              }
              onChange={e => setDialog(d => ({...d, form: {...d.form, product_id: e.target.value}}))}
            >
              <MenuItem value="">Ürün bildirilmemiş</MenuItem>
              {products.map(p => (
                <MenuItem key={p.id} value={String(p.id)}>
                  {p.name}{p.product_code ? ` — ${p.product_code}` : ''}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Ekilen alan (dekar)"
              inputMode="decimal"
              value={dialog.form.planted_area_decare}
              error={alanAsiyor}
              helperText={
                alanAsiyor
                  ? `Parselin alanı ${qty(secilenParsel?.area_decare)} dekar; ekilen alan bunu aşamaz.`
                  : 'Boş bırakılabilir — ama boşsa dekar başına maliyet ve verim hesaplanamaz.'
              }
              onChange={e => setDialog(d => ({...d, form: {...d.form, planted_area_decare: e.target.value}}))}
            />
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Başlangıç" type="date" fullWidth InputLabelProps={{shrink: true}} value={dialog.form.started_on}
                helperText="Hasat bu tarihten önce kaydedilemez."
                onChange={e => setDialog(d => ({...d, form: {...d.form, started_on: e.target.value}}))} />
              <TextField label="Bitiş" type="date" fullWidth InputLabelProps={{shrink: true}} value={dialog.form.ended_on}
                onChange={e => setDialog(d => ({...d, form: {...d.form, ended_on: e.target.value}}))} />
            </Stack>
            {dialog.row && (
              <TextField select label="Durum" value={dialog.form.status}
                onChange={e => setDialog(d => ({...d, form: {...d.form, status: e.target.value}}))}>
                {SEASON_STATUSES.map(s => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
              </TextField>
            )}
            <TextField label="Not" multiline minRows={2} value={dialog.form.notes}
              onChange={e => setDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void kaydet()} disabled={saving || !gecerli || alanAsiyor}>Kaydet</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
