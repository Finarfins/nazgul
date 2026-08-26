/**
 * Maliyet Oranları (`/tanimlar/maliyet-oranlari`).
 *
 * Konu: mobil-erp#24, FAZ 1.
 *
 * Dört şey ekranda görünür kılınıyor:
 *
 * 1. **Bu tablo GEÇMİŞİ DEĞİŞTİRMEZ.** Oran, faaliyet kaydedilirken satıra
 *    kopyalanacak (FAZ 2). Buradaki değeri sonradan değiştirmek geçen sezonun
 *    maliyetini DEĞİŞTİRMEZ — ve bu bilinçli: değiştirseydi geçen yılın kârı bu
 *    yıl başka çıkardı. Kullanıcının bunu bilmesi gerekiyor, yoksa "eski
 *    kayıtlar neden düzelmedi" diye arar.
 *
 * 2. **Bir hedefe bir aktif oran.** Sunucu ikinciyi 409 ile reddediyor; ekran
 *    da hangi hedeflerin dolu olduğunu gösterip kullanıcıyı duvara çarpmaktan
 *    kurtarıyor.
 *
 * 3. **Sıfır geçerli bir orandır.** Kendi traktörüyle çalışan işletme "makine
 *    maliyeti 0" diyebilmeli; form bunu engellemez, yalnız negatifi engeller.
 *
 * 4. **Yazma `finance` iznine bağlı.** Oran bir para tanımı; girdi giren depo
 *    rolü bu ekranı hiç görmüyor (rota izni de `finance`).
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  Alert, Box, Button, Card, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, MenuItem, Stack, Tab, Tabs, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import PaymentsIcon from '@mui/icons-material/Payments';
import type {GridColDef} from '@mui/x-data-grid';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  bosNull, herdErrorText, money, ondalik, saveHerdRecord, type Page,
} from '../herd/herdApi';

export type CostRate = {
  id: number;
  /** LABOR | MACHINE */
  kind: string;
  machine_id: number | null;
  user_id: number | null;
  label: string;
  /** Para STRING gelir; sayıya çevirip geri yazmak ondalıkta hata üretir. */
  hourly_rate: string;
  notes: string | null;
  status: string;
  updated_at: string;
};

type Machine = {id: number; name?: string | null; brand?: string | null; model?: string | null; serial_no?: string | null};
type User = {id: number; display_name?: string | null; username: string};

const KINDS = [
  {value: 'LABOR', label: 'İşçilik'},
  {value: 'MACHINE', label: 'Makine'},
] as const;

/** Hedef seçimi: firma geneli / makine / kişi. */
type Hedef = 'default' | 'machine' | 'user';

type Form = {
  kind: string; hedef: Hedef; machine_id: string; user_id: string;
  label: string; hourly_rate: string; notes: string;
};

const BOS: Form = {
  kind: 'LABOR', hedef: 'default', machine_id: '', user_id: '',
  label: '', hourly_rate: '', notes: '',
};

const kindLabel = (v: string) => KINDS.find(k => k.value === v)?.label ?? v;

export default function CostRates() {
  const {can} = useAuth();
  const yazabilir = can('finance');

  const [tab, setTab] = useState(0);
  const [rows, setRows] = useState<CostRate[]>([]);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [dialog, setDialog] = useState<{open: boolean; row: CostRate | null; form: Form}>({
    open: false, row: null, form: BOS,
  });
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [r, m, u] = await Promise.all([
        api.get<Page<CostRate>>('/cost-rates', {params: {limit: 200}}),
        // Makine ve kullanıcı listeleri YARDIMCI: gelmezse ekran yine çalışsın,
        // yalnız hedef seçimi kısıtlansın. Bu yüzden hataları yutuluyor.
        api.get<Page<Machine>>('/machines', {params: {limit: 200}}).catch(() => null),
        api.get<User[] | Page<User>>('/users').catch(() => null),
      ]);
      setRows(r.data.items);
      setMachines(m?.data?.items ?? []);
      const kullanicilar = u?.data;
      setUsers(Array.isArray(kullanicilar) ? kullanicilar : kullanicilar?.items ?? []);
    } catch (err) {
      setError(herdErrorText(err, 'Maliyet oranları yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const makineAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const m of machines) {
      map.set(m.id, [m.brand, m.model, m.serial_no].filter(Boolean).join(' ') || m.name || `#${m.id}`);
    }
    return (id: number | null) => (id === null ? null : map.get(id) ?? `#${id}`);
  }, [machines]);

  const kisiAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const u of users) map.set(u.id, u.display_name || u.username);
    return (id: number | null) => (id === null ? null : map.get(id) ?? `#${id}`);
  }, [users]);

  const gosterilen = useMemo(
    () => rows.filter(r => (tab === 0 ? r.status === 'ACTIVE' : r.status !== 'ACTIVE')),
    [rows, tab],
  );

  /** Aynı hedefe ikinci aktif oran açılamaz; formda önceden uyaralım. */
  const doluHedefler = useMemo(() => {
    const genel = new Set<string>();
    const makine = new Set<number>();
    const kisi = new Set<number>();
    for (const r of rows) {
      if (r.status !== 'ACTIVE') continue;
      if (r.machine_id !== null) makine.add(r.machine_id);
      else if (r.user_id !== null) kisi.add(r.user_id);
      else genel.add(r.kind);
    }
    return {genel, makine, kisi};
  }, [rows]);

  const kaydet = async () => {
    setSaving(true);
    setDialogError('');
    const {row, form} = dialog;
    const gövde = {
      kind: form.kind,
      label: form.label.trim(),
      // Oran STRING gidiyor: sayıya çevirip geri yazmak kuruşta kayma üretir.
      hourly_rate: ondalik(form.hourly_rate),
      machine_id: form.hedef === 'machine' ? Number(form.machine_id) : null,
      user_id: form.hedef === 'user' ? Number(form.user_id) : null,
      notes: bosNull(form.notes),
    };
    try {
      if (row) {
        await saveHerdRecord('put', `/cost-rates/${row.id}`, {
          ...gövde, status: row.status, expected_updated_at: row.updated_at,
        }, 'Oran güncellenemedi.');
      } else {
        await saveHerdRecord('post', '/cost-rates', gövde, 'Oran eklenemedi.');
      }
      setDialog({open: false, row: null, form: BOS});
      await yukle();
    } catch (err) {
      setDialogError(herdErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const arsivle = async (row: CostRate) => {
    setError('');
    try {
      await saveHerdRecord('put', `/cost-rates/${row.id}`, {
        kind: row.kind, label: row.label, hourly_rate: row.hourly_rate,
        machine_id: row.machine_id, user_id: row.user_id, notes: row.notes,
        status: row.status === 'ACTIVE' ? 'ARCHIVED' : 'ACTIVE',
        expected_updated_at: row.updated_at,
      }, 'Durum değiştirilemedi.');
      await yukle();
    } catch (err) {
      setError(herdErrorText(err, 'Durum değiştirilemedi.'));
    }
  };

  const duzenle = (row: CostRate) => {
    setDialogError('');
    setDialog({
      open: true, row,
      form: {
        kind: row.kind,
        hedef: row.machine_id !== null ? 'machine' : row.user_id !== null ? 'user' : 'default',
        machine_id: row.machine_id === null ? '' : String(row.machine_id),
        user_id: row.user_id === null ? '' : String(row.user_id),
        label: row.label, hourly_rate: String(row.hourly_rate), notes: row.notes || '',
      },
    });
  };

  const hedefMetni = (row: CostRate) =>
    row.machine_id !== null ? `Makine: ${makineAdi(row.machine_id)}`
      : row.user_id !== null ? `Kişi: ${kisiAdi(row.user_id)}`
      : 'Firma geneli (varsayılan)';

  const sutun: GridColDef[] = [
    {field: 'kind', headerName: 'Tür', width: 110, valueGetter: (_v, row) => kindLabel(row.kind)},
    {field: 'label', headerName: 'Ad', flex: 1, minWidth: 180},
    {
      field: 'machine_id', headerName: 'Hedef', width: 240,
      valueGetter: (_v, row) => hedefMetni(row as CostRate),
    },
    {
      field: 'hourly_rate', headerName: 'Saatlik maliyet', width: 160,
      align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => money(row.hourly_rate),
    },
    {
      field: 'status', headerName: 'Durum', width: 110,
      renderCell: params => (
        <Chip size="small" color={params.value === 'ACTIVE' ? 'success' : 'default'}
          label={params.value === 'ACTIVE' ? 'Aktif' : 'Arşiv'} />
      ),
    },
  ];

  const form = dialog.form;
  const hedefDolu =
    form.hedef === 'default' ? doluHedefler.genel.has(form.kind) && !dialog.row
      : form.hedef === 'machine' ? form.machine_id !== '' && doluHedefler.makine.has(Number(form.machine_id)) && !dialog.row
      : form.user_id !== '' && doluHedefler.kisi.has(Number(form.user_id)) && !dialog.row;

  const gecerli =
    form.label.trim() !== '' &&
    form.hourly_rate.trim() !== '' &&
    Number(ondalik(form.hourly_rate)) >= 0 &&
    (form.hedef !== 'machine' || form.machine_id !== '') &&
    (form.hedef !== 'user' || form.user_id !== '') &&
    !hedefDolu;

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <PaymentsIcon color="primary" />
            <Typography variant="h4">Maliyet Oranları</Typography>
          </Stack>
          <Typography color="text.secondary">
            Tarla faaliyetlerinde ve hayvancılıkta kullanılacak saatlik işçilik ve makine maliyeti.
          </Typography>
        </Box>
        {yazabilir && (
          <Button variant="contained" startIcon={<AddIcon />}
            sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}
            onClick={() => {setDialogError(''); setDialog({open: true, row: null, form: BOS});}}>
            Yeni oran
          </Button>
        )}
      </Stack>

      {error && (
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>
          {error}
        </Alert>
      )}

      {/* Kullanıcının bilmesi gereken tek şey: buradaki değişiklik GEÇMİŞE
          İŞLEMEZ. Bilmezse "eski kayıtlar neden düzelmedi" diye arar. */}
      <Alert severity="info" variant="outlined">
        Buradaki oran, bir faaliyet <b>kaydedilirken</b> o kaydın içine yazılır.
        Oranı sonradan değiştirmek <b>geçmiş kayıtların maliyetini değiştirmez</b> —
        bu bilinçli: aksi hâlde geçen sezonun kârı bu yıl başka çıkardı ve hiçbir
        rapora güvenilemezdi. Yeni oran, bundan sonraki kayıtlara uygulanır.
      </Alert>

      <Card>
        <Tabs value={tab} onChange={(_e, v) => setTab(v)} variant="scrollable" allowScrollButtonsMobile>
          <Tab label={`Aktif (${rows.filter(r => r.status === 'ACTIVE').length})`} />
          <Tab label={`Arşiv (${rows.filter(r => r.status !== 'ACTIVE').length})`} />
        </Tabs>
      </Card>

      <ResponsiveTable
        rows={gosterilen}
        columns={sutun}
        loading={loading}
        cardTitle={row => row.label}
        cardSubtitle={row => `${kindLabel(row.kind)} · ${money(row.hourly_rate)}/saat`}
        cardFields={[
          {label: 'Hedef', value: row => hedefMetni(row as CostRate)},
          {label: 'Durum', value: row => (row.status === 'ACTIVE' ? 'Aktif' : 'Arşiv')},
        ]}
        cardActions={yazabilir ? [
          {label: 'Düzenle', icon: <EditIcon />, onClick: duzenle},
          {
            label: tab === 0 ? 'Arşivle' : 'Geri al',
            icon: <EditIcon />,
            onClick: (row: CostRate) => void arsivle(row),
          },
        ] : undefined}
        onRowClick={yazabilir ? duzenle : undefined}
      />

      <Dialog open={dialog.open} onClose={() => !saving && setDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>{dialog.row ? 'Oranı düzenle' : 'Yeni maliyet oranı'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}

            <TextField select label="Tür" required value={form.kind}
              onChange={e => setDialog(d => ({
                ...d,
                form: {
                  ...d.form, kind: e.target.value,
                  // Tür değişince hedef sıfırlanır: makine türünde kişi,
                  // işçilik türünde makine seçili kalamaz (sunucu 422 verir).
                  hedef: 'default', machine_id: '', user_id: '',
                },
              }))}>
              {KINDS.map(k => <MenuItem key={k.value} value={k.value}>{k.label}</MenuItem>)}
            </TextField>

            <TextField select label="Kime uygulanacak" value={form.hedef}
              helperText="Firma geneli, o türün varsayılanıdır; makine/kişi seçimi onu ezer."
              onChange={e => setDialog(d => ({
                ...d,
                form: {...d.form, hedef: e.target.value as Hedef, machine_id: '', user_id: ''},
              }))}>
              <MenuItem value="default">Firma geneli (varsayılan)</MenuItem>
              {form.kind === 'MACHINE' && <MenuItem value="machine">Belirli bir makine</MenuItem>}
              {form.kind === 'LABOR' && <MenuItem value="user">Belirli bir kişi</MenuItem>}
            </TextField>

            {form.hedef === 'machine' && (
              <TextField select label="Makine" required value={form.machine_id}
                onChange={e => setDialog(d => ({...d, form: {...d.form, machine_id: e.target.value}}))}>
                {machines.map(m => (
                  <MenuItem key={m.id} value={String(m.id)}>
                    {[m.brand, m.model, m.serial_no].filter(Boolean).join(' ') || m.name || `#${m.id}`}
                  </MenuItem>
                ))}
              </TextField>
            )}

            {form.hedef === 'user' && (
              <TextField select label="Kişi" required value={form.user_id}
                onChange={e => setDialog(d => ({...d, form: {...d.form, user_id: e.target.value}}))}>
                {users.map(u => (
                  <MenuItem key={u.id} value={String(u.id)}>{u.display_name || u.username}</MenuItem>
                ))}
              </TextField>
            )}

            {/* Sunucu 409 veriyor; kullanıcı o duvara çarpmadan öğrensin. */}
            {hedefDolu && (
              <Alert severity="warning">
                Bu hedef için zaten aktif bir oran var. İki aktif oran, hangisinin
                uygulanacağını belirsiz kılardı — önce mevcut oranı düzenleyin ya
                da arşivleyin.
              </Alert>
            )}

            <TextField label="Ad" required value={form.label}
              helperText="Listede görünecek ad: 'Traktör 1', 'Mevsimlik işçi'…"
              onChange={e => setDialog(d => ({...d, form: {...d.form, label: e.target.value}}))} />

            <TextField label="Saatlik maliyet (₺)" required inputMode="decimal"
              value={form.hourly_rate}
              helperText="Sıfır girilebilir: kendi makinesiyle çalışan işletme için geçerli bir cevaptır."
              onChange={e => setDialog(d => ({...d, form: {...d.form, hourly_rate: e.target.value}}))} />

            <TextField label="Not" multiline minRows={2} value={form.notes}
              onChange={e => setDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void kaydet()} disabled={saving || !gecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
