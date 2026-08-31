/**
 * BKÜ Kataloğu (`/tarla/bku-katalogu`).
 *
 * PHI kilidi (hasat bekleme) 0046/0048'den beri çalışıyor ama beslendiği gün
 * sayısı faaliyet başına ELLE giriliyordu: operatör yazmayı unuttuğunda kilit
 * sessizce hiçbir şey yapmıyordu. Bu ekran o sayının kalıcı kaydı.
 *
 * ÜÇ ŞEY BURADA BİLİNÇLİ:
 *
 * 1. **Uygulama HİÇBİR PHI RAKAMI ÖNERMEZ.** Hazır liste, varsayılan, "benzer
 *    ürün" tahmini YOK. Her değer firmanın kendi girdiği değerdir. PHI yasal
 *    bir süredir ve kaynağı BKÜ etiketidir; yazılımın uydurduğu bir rakam,
 *    yanlış olduğunda yazılımın iddiası olurdu.
 *
 * 2. **Bitki boş bırakılabilir ve boş "bütün bitkiler" demektir.** Aynı etken
 *    madde domateste ve elmada farklı bekleme süresi taşır, ama her ürün için
 *    her bitkiye satır girmeyi zorunlu kılmak kataloğu doldurulamaz yapardı.
 *    Bitkiye özel satır varsa o kullanılır, yoksa bitkiden bağımsız satıra
 *    düşülür.
 *
 * 3. **Yazma `farm.manage` iznine bağlı.** Sunucu zaten reddediyor; buradaki
 *    gizleme kullanıcıyı 403 duvarına çarpmaktan kurtarmak için. Güvenlik
 *    sunucuda — bu yalnız nezaket.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  MenuItem, Stack, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import type {GridColDef} from '@mui/x-data-grid';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  PPP_PATH, farmErrorText, saveFarmRecord,
  type Page, type PlantProtectionProduct,
} from '../farm/farmApi';

type Urun = {id: number; name: string};

type Form = {
  product_id: string; crop: string; registration_no: string;
  preharvest_interval_days: string; reentry_interval_days: string; notes: string;
};

const BOS: Form = {
  product_id: '', crop: '', registration_no: '',
  preharvest_interval_days: '', reentry_interval_days: '', notes: '',
};

const bosNull = (v: string) => {
  const t = v.trim();
  return t === '' ? null : t;
};

const sayiNull = (v: string) => {
  const t = v.trim();
  return t === '' ? null : Number(t);
};

export default function PlantProtectionProducts() {
  const {can} = useAuth();
  const yazabilir = can('farm.manage');

  const [rows, setRows] = useState<PlantProtectionProduct[]>([]);
  const [urunler, setUrunler] = useState<Urun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<
    {open: boolean; row: PlantProtectionProduct | null; form: Form}
  >({open: false, row: null, form: BOS});
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [k, u] = await Promise.all([
        api.get<Page<PlantProtectionProduct>>(PPP_PATH, {params: {limit: 200}}),
        api.get<Page<Urun>>('/products', {params: {limit: 500}}),
      ]);
      setRows(k.data.items);
      setUrunler(u.data.items);
    } catch (err) {
      setError(farmErrorText(err, 'BKÜ kataloğu yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const urunAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const u of urunler) map.set(u.id, u.name);
    return (id: number) => map.get(id) ?? `#${id}`;
  }, [urunler]);

  const ac = (row: PlantProtectionProduct | null) => {
    setDialogError('');
    setDialog({
      open: true, row,
      form: row
        ? {
            product_id: String(row.product_id),
            crop: row.crop,
            registration_no: row.registration_no ?? '',
            preharvest_interval_days: String(row.preharvest_interval_days),
            reentry_interval_days:
              row.reentry_interval_days === null ? '' : String(row.reentry_interval_days),
            notes: row.notes ?? '',
          }
        : BOS,
    });
  };

  const kaydet = async () => {
    setSaving(true);
    setDialogError('');
    const {row, form} = dialog;
    const gövde = {
      product_id: Number(form.product_id),
      // Boş dize BİLEREK korunuyor — `null` göndermek sunucuda NOT NULL
      // sütununa düşerdi ve "bütün bitkiler" anlamı kaybolurdu.
      crop: form.crop.trim(),
      registration_no: bosNull(form.registration_no),
      preharvest_interval_days: Number(form.preharvest_interval_days.trim()),
      reentry_interval_days: sayiNull(form.reentry_interval_days),
      notes: bosNull(form.notes),
    };
    try {
      if (row) {
        await saveFarmRecord('put', `${PPP_PATH}/${row.id}`, {
          ...gövde, status: row.status, expected_updated_at: row.updated_at,
        }, 'Katalog kaydı güncellenemedi.');
      } else {
        await saveFarmRecord('post', PPP_PATH, gövde, 'Katalog kaydı oluşturulamadı.');
      }
      setDialog({open: false, row: null, form: BOS});
      await yukle();
    } catch (err) {
      setDialogError(farmErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const f = dialog.form;
  const gecerli =
    f.product_id !== '' &&
    f.preharvest_interval_days.trim() !== '' &&
    Number.isInteger(Number(f.preharvest_interval_days.trim())) &&
    Number(f.preharvest_interval_days.trim()) >= 0;

  const columns: GridColDef<PlantProtectionProduct>[] = [
    {
      field: 'product_id', headerName: 'Ürün', flex: 1, minWidth: 200,
      valueGetter: (_v, row) => row.product_name ?? urunAdi(row.product_id),
    },
    {
      field: 'crop', headerName: 'Bitki', width: 160,
      // Boş dizeyi '—' göstermek YANLIŞ olurdu: '—' "veri yok" demek, oysa
      // boş bitki AÇIK bir anlam taşıyor.
      renderCell: params => params.row.crop.trim() === ''
        ? <Chip size="small" label="Bütün bitkiler" />
        : <span>{params.row.crop}</span>,
    },
    {
      field: 'preharvest_interval_days', headerName: 'Hasat bekleme', width: 150,
      valueGetter: (_v, row) => `${row.preharvest_interval_days} gün`,
    },
    {
      field: 'reentry_interval_days', headerName: 'Giriş yasağı', width: 140,
      valueGetter: (_v, row) =>
        row.reentry_interval_days === null ? '—' : `${row.reentry_interval_days} gün`,
    },
    {field: 'registration_no', headerName: 'Ruhsat no', width: 150,
      valueGetter: (_v, row) => row.registration_no ?? '—'},
    {field: 'status', headerName: 'Durum', width: 110},
  ];

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
        <Typography variant="h5">BKÜ Kataloğu</Typography>
        {yazabilir && (
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => ac(null)}>
            Yeni kayıt
          </Button>
        )}
      </Stack>

      <Alert severity="info">
        Buradaki bekleme süreleri ilaçlama kaydına <b>öneri</b> olarak gelir; operatör
        değiştirebilir ve değiştirdiğinde bu kayda geçer. Süreler ürün etiketinden
        girilir — uygulama kendiliğinden bir gün sayısı önermez.
      </Alert>

      {error && <Alert severity="error">{error}</Alert>}

      <Box>
        <ResponsiveTable
          rows={rows}
          columns={columns}
          loading={loading}
          getRowId={row => row.id}
          cardTitle={row => row.product_name ?? urunAdi(row.product_id)}
          cardSubtitle={row =>
            `${row.crop.trim() === '' ? 'Bütün bitkiler' : row.crop} · ${row.preharvest_interval_days} gün`}
          cardActions={yazabilir
            ? [{label: 'Düzenle', icon: <EditIcon />, onClick: row => ac(row)}]
            : []}
          cardActionsOnDesktop
          onRowClick={row => yazabilir && ac(row)}
        />
      </Box>

      <Dialog open={dialog.open} onClose={() => !saving && setDialog(d => ({...d, open: false}))}
              fullWidth maxWidth="sm">
        <DialogTitle>{dialog.row ? 'Katalog kaydını düzenle' : 'Yeni katalog kaydı'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField select label="Ürün" required value={f.product_id}
              onChange={e => setDialog(d => ({...d, form: {...d.form, product_id: e.target.value}}))}>
              {urunler.map(u => (
                <MenuItem key={u.id} value={String(u.id)}>{u.name}</MenuItem>
              ))}
            </TextField>
            <TextField label="Bitki" value={f.crop}
              placeholder="Boş bırakırsanız bütün bitkiler için geçerli olur"
              helperText="Bitkiye özel bir satır varsa o kullanılır; yoksa bu satıra düşülür."
              onChange={e => setDialog(d => ({...d, form: {...d.form, crop: e.target.value}}))} />
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Hasat bekleme (gün)" type="number" required fullWidth
                value={f.preharvest_interval_days}
                helperText="Etiketten okuyun."
                onChange={e => setDialog(d => ({...d, form: {...d.form, preharvest_interval_days: e.target.value}}))} />
              <TextField label="Tarlaya giriş yasağı (gün)" type="number" fullWidth
                value={f.reentry_interval_days}
                onChange={e => setDialog(d => ({...d, form: {...d.form, reentry_interval_days: e.target.value}}))} />
            </Stack>
            <TextField label="Ruhsat no" value={f.registration_no}
              onChange={e => setDialog(d => ({...d, form: {...d.form, registration_no: e.target.value}}))} />
            <TextField label="Not" multiline minRows={2} value={f.notes}
              onChange={e => setDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void kaydet()} disabled={saving || !gecerli}>
            Kaydet
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
