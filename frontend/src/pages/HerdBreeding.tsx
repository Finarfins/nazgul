/**
 * Döl Verimi (`/hayvancilik/doller`) — tohumlama ve doğum kayıtları.
 *
 * Konu: mobil-erp#17, FAZ 3.
 *
 * Üç şey ekranda görünür kılınıyor:
 *
 * 1. **Doğum kaydı YENİ HAYVAN oluşturur.** Sunucu doğumu ve yavruları AYNI
 *    işlemde yazıyor. Kullanıcı bunu formda önceden görüyor; yoksa "doğum
 *    girdim, buzağı listede yok" ya da tersine "iki kere buzağı eklendi"
 *    yaşanır.
 *
 * 2. **Ölü doğumda yavru satırı GİRİLEMEZ.** Sunucu 422 veriyor; form bu
 *    durumda yavru bölümünü kapatıyor. Açık kalsaydı sürüde olmayan bir hayvan
 *    kaydı oluşur ve sayım şişerdi.
 *
 * 3. **Tohumlama sonucu SONRADAN güncellenir.** Tohumlama anında gebelik
 *    bilinmiyor; sonuç "bekleniyor" başlıyor ve muayeneden sonra iyimser
 *    kilitle (409) güncelleniyor.
 *
 * 4. **Göstergeler sekmesi ORTALAMAYI ÖRNEK SAYISIYLA gösterir (FAZ 5).**
 *    Tek ineğin tek aralığı "sürünün buzağılama aralığı" değildir; örnek sayısı
 *    olmadan ortalama göstermek üç ölçümü sürü gerçeği gibi sunmak olurdu.
 *    Hiç doğurmamış hayvan ve tohumlamaya bağlanmamış doğum sayıları da ayrıca
 *    yazılı — hesabın KAPSAMADIĞI kısım gizlenirse gösterge tam sanılır.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {
  Alert, Box, Button, Card, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, Divider, IconButton, MenuItem, Stack, Tab, Tabs, TextField,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ChildCareIcon from '@mui/icons-material/ChildCare';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditIcon from '@mui/icons-material/Edit';
import type {GridColDef} from '@mui/x-data-grid';

import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  BIRTH_OUTCOME, BREEDING_METHOD, BREEDING_RESULT, SEX,
  animalLabel, birthOutcomeLabel, bosNull, breedingMethodLabel,
  breedingResultLabel, dayLabel, fetchFertility, herdErrorText, isoDate,
  listHerd, saveHerdRecord,
  type Animal, type Birth, type Breeding, type FertilityRow, type HerdFertility,
} from '../herd/herdApi';

type BreedingForm = {
  animal_id: string; bred_on: string; method: string; sire_code: string;
  technician: string; notes: string;
  // Yalnız DÜZENLEMEDE anlamlı: tohumlama anında sonuç bilinmiyor.
  result: string; checked_on: string; expected_birth_on: string;
};
type Yavru = {ear_tag: string; name: string; sex: string};
type BirthForm = {
  mother_id: string; breeding_id: string; birth_date: string; outcome: string;
  difficulty: string; offspring_count: string; notes: string; offspring: Yavru[];
};

const bugunIso = () => {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

const BOS_TOHUM: BreedingForm = {
  animal_id: '', bred_on: bugunIso(), method: 'AI', sire_code: '',
  technician: '', notes: '', result: 'UNKNOWN', checked_on: '', expected_birth_on: '',
};
const BOS_DOGUM: BirthForm = {
  mother_id: '', breeding_id: '', birth_date: bugunIso(), outcome: 'LIVE',
  difficulty: '', offspring_count: '', notes: '', offspring: [],
};

export default function HerdBreeding() {
  const {can} = useAuth();
  const navigate = useNavigate();
  const yazabilir = can('herd.manage');

  const [tab, setTab] = useState(0);
  const [breedings, setBreedings] = useState<Breeding[]>([]);
  const [births, setBirths] = useState<Birth[]>([]);
  const [animals, setAnimals] = useState<Animal[]>([]);
  const [fertility, setFertility] = useState<HerdFertility | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [uyarilar, setUyarilar] = useState<string[]>([]);

  const [breedingDialog, setBreedingDialog] = useState<{open: boolean; row: Breeding | null; form: BreedingForm}>({
    open: false, row: null, form: BOS_TOHUM,
  });
  const [birthDialog, setBirthDialog] = useState(false);
  const [birthForm, setBirthForm] = useState<BirthForm>(BOS_DOGUM);
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [t, d, a, f] = await Promise.all([
        listHerd<Breeding>('/animal-breedings'),
        listHerd<Birth>('/animal-births'),
        listHerd<Animal>('/animals'),
        // Göstergeler SUNUCUDA hesaplanıyor: istemcide hesaplamak, aynı sayının
        // iki yerde iki farklı biçimde üretilmesi demek olurdu.
        fetchFertility(),
      ]);
      setBreedings(t.items);
      setBirths(d.items);
      setAnimals(a.items);
      setFertility(f);
    } catch (err) {
      setError(herdErrorText(err, 'Tohumlama ve doğum kayıtları yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const hayvanKimlik = useMemo(() => new Map(animals.map(a => [a.id, a])), [animals]);
  /** Doğum ve tohumlama YALNIZ dişide olur; sunucu da 422 veriyor. */
  const disiler = useMemo(
    () => animals.filter(a => a.sex === 'FEMALE' && a.status === 'ACTIVE'),
    [animals],
  );
  /** Seçilen annenin tohumlamaları — doğum, tohumlamaya bağlanabilir. */
  const anneninTohumlamalari = useMemo(
    () => breedings.filter(b => String(b.animal_id) === birthForm.mother_id),
    [breedings, birthForm.mother_id],
  );

  // -------------------------------------------------------------------------
  // Kaydetme
  // -------------------------------------------------------------------------

  const tohumlamaKaydet = async () => {
    setSaving(true);
    setDialogError('');
    const {row, form} = breedingDialog;
    const gövde = {
      animal_id: Number(form.animal_id),
      bred_on: form.bred_on,
      method: form.method,
      sire_code: bosNull(form.sire_code),
      sire_animal_id: row?.sire_animal_id ?? null,
      technician: bosNull(form.technician),
      notes: bosNull(form.notes),
    };
    try {
      if (row) {
        await saveHerdRecord('put', `/animal-breedings/${row.id}`, {
          ...gövde,
          result: form.result,
          checked_on: bosNull(form.checked_on),
          expected_birth_on: bosNull(form.expected_birth_on),
          expected_updated_at: row.updated_at,
        }, 'Tohumlama güncellenemedi.');
      } else {
        await saveHerdRecord('post', '/animal-breedings', gövde, 'Tohumlama kaydedilemedi.');
      }
      setBreedingDialog({open: false, row: null, form: BOS_TOHUM});
      await yukle();
    } catch (err) {
      setDialogError(herdErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const dogumKaydet = async () => {
    setSaving(true);
    setDialogError('');
    const canli = birthForm.outcome === 'LIVE';
    try {
      const kayit = await saveHerdRecord<Birth & {warnings?: string[]; offspring_ids?: number[]}>(
        'post', '/animal-births', {
          mother_id: Number(birthForm.mother_id),
          breeding_id: birthForm.breeding_id === '' ? null : Number(birthForm.breeding_id),
          birth_date: birthForm.birth_date,
          outcome: birthForm.outcome,
          difficulty: birthForm.difficulty === '' ? null : Number(birthForm.difficulty),
          // Yavru satırı verildiyse sayı ondan çıkar; verilmediyse alandan.
          // İkisini birden göndermek sunucuda sayıyı yavru satırından alır.
          offspring: canli && birthForm.offspring.length
            ? birthForm.offspring.map(y => ({
                ear_tag: bosNull(y.ear_tag), name: bosNull(y.name), sex: y.sex, notes: null,
              }))
            : null,
          offspring_count: birthForm.offspring_count === '' ? null : Number(birthForm.offspring_count),
          notes: bosNull(birthForm.notes),
        }, 'Doğum kaydedilemedi.');
      setUyarilar(kayit.warnings ?? []);
      setBirthDialog(false);
      setBirthForm(BOS_DOGUM);
      await yukle();
    } catch (err) {
      setDialogError(herdErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  // -------------------------------------------------------------------------
  // Tablolar
  // -------------------------------------------------------------------------

  const tohumSutun: GridColDef[] = [
    {
      field: 'animal_id', headerName: 'Hayvan', width: 160,
      valueGetter: (_v, row) => animalLabel(hayvanKimlik.get(row.animal_id)),
    },
    {field: 'bred_on', headerName: 'Tohumlama', width: 120, valueGetter: (_v, row) => isoDate(row.bred_on)},
    {field: 'method', headerName: 'Yöntem', width: 150, valueGetter: (_v, row) => breedingMethodLabel(row.method)},
    {
      field: 'result', headerName: 'Sonuç', width: 150,
      renderCell: params => {
        const v = params.value as string;
        const renk = v === 'PREGNANT' ? 'success' : v === 'UNKNOWN' ? 'default' : 'warning';
        return <Chip size="small" color={renk} label={breedingResultLabel(v)} />;
      },
    },
    {
      field: 'expected_birth_on', headerName: 'Tahmini doğum', width: 140,
      valueGetter: (_v, row) => isoDate(row.expected_birth_on),
    },
    {field: 'sire_code', headerName: 'Boğa / koç', width: 140, valueGetter: (_v, row) => row.sire_code || '—'},
  ];

  const dogumSutun: GridColDef[] = [
    {
      field: 'mother_id', headerName: 'Anne', width: 160,
      valueGetter: (_v, row) => animalLabel(hayvanKimlik.get(row.mother_id)),
    },
    {field: 'birth_date', headerName: 'Doğum', width: 120, valueGetter: (_v, row) => isoDate(row.birth_date)},
    {
      field: 'outcome', headerName: 'Sonuç', width: 150,
      renderCell: params => (
        <Chip size="small" color={params.value === 'LIVE' ? 'success' : 'warning'}
          label={birthOutcomeLabel(params.value as string)} />
      ),
    },
    {
      field: 'offspring_count', headerName: 'Yavru', width: 100, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => row.offspring_count ?? '—',
    },
    {
      field: 'difficulty', headerName: 'Güçlük', width: 110,
      valueGetter: (_v, row) => (row.difficulty ? `${row.difficulty}/4` : '—'),
    },
  ];

  const tohumlamaDuzenle = (row: Breeding) => {
    setDialogError('');
    setBreedingDialog({
      open: true, row,
      form: {
        animal_id: String(row.animal_id), bred_on: row.bred_on, method: row.method,
        sire_code: row.sire_code || '', technician: row.technician || '',
        notes: row.notes || '', result: row.result,
        checked_on: row.checked_on || '', expected_birth_on: row.expected_birth_on || '',
      },
    });
  };

  /** Gösterge sütunları. Ortalama SUNUCUDAN geliyor; burada yalnız gösterim. */
  const gostergeSutun: GridColDef[] = [
    {
      field: 'ear_tag', headerName: 'Hayvan', width: 160,
      valueGetter: (_v, row) => animalLabel(row as FertilityRow),
    },
    {field: 'calving_count', headerName: 'Doğum', width: 90, align: 'right', headerAlign: 'right'},
    {
      field: 'last_calving_interval_days', headerName: 'Son aralık', width: 170,
      renderCell: params => {
        const r = params.row as FertilityRow;
        if (r.last_calving_interval_days === null) {
          // Tek doğumlu hayvan: aralık YOK. "0" göstermek onu mükemmel
          // gösterirdi.
          return <Typography variant="body2" color="text.secondary">Tek doğum</Typography>;
        }
        return (
          <Chip size="small" color={r.interval_is_problem ? 'error' : 'success'}
            variant={r.interval_is_problem ? 'filled' : 'outlined'}
            label={dayLabel(r.last_calving_interval_days)} />
        );
      },
    },
    {
      field: 'avg_calving_interval_days', headerName: 'Ortalama aralık', width: 170,
      valueGetter: (_v, row) => dayLabel(row.avg_calving_interval_days),
    },
    {
      field: 'service_period_days', headerName: 'Servis periyodu', width: 160,
      valueGetter: (_v, row) => dayLabel(row.service_period_days),
    },
    {
      field: 'services_per_conception', headerName: 'Gebelik/tohumlama', width: 160,
      valueGetter: (_v, row) => row.services_per_conception ?? '—',
    },
    {
      field: 'age_at_first_calving_days', headerName: 'İlkine yaş', width: 160,
      valueGetter: (_v, row) => dayLabel(row.age_at_first_calving_days),
    },
    {field: 'last_calving_on', headerName: 'Son doğum', width: 130, valueGetter: (_v, row) => isoDate(row.last_calving_on)},
  ];

  const gostergeSatirlari = (fertility?.items ?? []).map(r => ({...r, id: r.animal_id}));
  const dol = fertility?.summary;
  const esik = fertility?.thresholds;

  /** Ortalamayı ÖRNEK SAYISIYLA gösterir; örnek yoksa ortalama YAZILMAZ. */
  const ortalama = (deger: number | null | undefined, ornek: number | undefined) =>
    !ornek || deger === null || deger === undefined
      ? 'Ölçüm yok'
      : `${dayLabel(deger)} (${ornek} ölçüm)`;

  const canliDogum = birthForm.outcome === 'LIVE';
  const tohumGecerli = breedingDialog.form.animal_id !== '' && breedingDialog.form.bred_on !== '';
  const dogumGecerli = birthForm.mother_id !== '' && birthForm.birth_date !== '' &&
    (!canliDogum || birthForm.offspring.every(y => y.sex !== ''));

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <ChildCareIcon color="primary" />
            <Typography variant="h4">Döl Verimi</Typography>
          </Stack>
          <Typography color="text.secondary">
            Tohumlama ve doğum kayıtları. Doğum, yavruyu da sürüye ekler.
          </Typography>
        </Box>
        {yazabilir && (
          <Button variant="contained" startIcon={<AddIcon />}
            sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}
            onClick={() => {
              setDialogError('');
              if (tab === 0) setBreedingDialog({open: true, row: null, form: BOS_TOHUM});
              else {setBirthForm(BOS_DOGUM); setBirthDialog(true);}
            }}>
            {tab === 0 ? 'Yeni tohumlama' : 'Yeni doğum'}
          </Button>
        )}
      </Stack>

      {error && (
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>
          {error}
        </Alert>
      )}

      {uyarilar.map(u => (
        <Alert key={u} severity="info" onClose={() => setUyarilar(x => x.filter(y => y !== u))}>{u}</Alert>
      ))}

      <Card>
        <Tabs value={tab} onChange={(_e, v) => setTab(v)} variant="scrollable" allowScrollButtonsMobile>
          <Tab label={`Tohumlamalar (${breedings.length})`} />
          <Tab label={`Doğumlar (${births.length})`} />
          <Tab label={`Göstergeler (${gostergeSatirlari.length})`} />
        </Tabs>
      </Card>

      {tab === 2 ? (
        <>
          {/* "KAÇ DOĞUM" TEK BAŞINA GÖSTERGE DEĞİL: yılda 40 doğum 40 inekten
              geliyorsa iyi, 20 inekten geliyorsa yarısı boş geçmiş demektir. */}
          <Alert severity="info" variant="outlined">
            Sürü yönetiminde ölçüt doğum <b>sayısı</b> değil, doğumlar arası
            <b> süre</b>. Hedef buzağılama aralığı ~{Math.round((esik?.target_calving_interval_days ?? 360) / 30)} ay;
            {' '}{Math.round((esik?.problem_calving_interval_days ?? 390) / 30)} aydan uzunu sorun sayılır.
            Servis periyodu (doğumdan yeniden gebe kalmaya) normalde
            {' '}{esik?.service_period_low_days ?? 70}–{esik?.service_period_high_days ?? 90} gündür.
          </Alert>

          {/* Kapsam DIŞI kalanlar açıkça yazılı: gizlenirse gösterge tam
              sanılır. */}
          {dol && (dol.never_calved > 0 || dol.births_without_breeding_link > 0) && (
            <Alert severity="warning" variant="outlined">
              {dol.never_calved > 0 && (
                <> {dol.never_calved} dişi hiç doğurmamış; ortalamalara
                <b> katılmıyor</b> (0 sayılsaydı ortalama düzelmesi imkânsız
                biçimde bozulurdu).</>
              )}
              {dol.births_without_breeding_link > 0 && (
                <> {dol.births_without_breeding_link} doğum bir tohumlama kaydına
                bağlı değil; servis periyodu ve gebelik başına tohumlama yalnız
                <b> bağlı</b> kayıtlardan hesaplanıyor. Doğum girerken
                &laquo;İlgili tohumlama&raquo; alanını doldurmak bu göstergeleri
                tamamlar.</>
              )}
            </Alert>
          )}

          {dol && (
            <Stack direction="row" flexWrap="wrap" gap={1.5}>
              <Card sx={{flex: '1 1 240px', minWidth: 210, p: 2}}>
                <Typography variant="body2" color="text.secondary">Ortalama buzağılama aralığı</Typography>
                <Typography variant="h6" fontWeight={700}>
                  {ortalama(dol.avg_calving_interval_days, dol.calving_interval_sample)}
                </Typography>
                <Typography variant="caption" color={dol.problem_interval_count ? 'error.main' : 'text.secondary'}>
                  {dol.problem_interval_count} hayvanda son aralık sorunlu
                </Typography>
              </Card>
              <Card sx={{flex: '1 1 240px', minWidth: 210, p: 2}}>
                <Typography variant="body2" color="text.secondary">Ortalama servis periyodu</Typography>
                <Typography variant="h6" fontWeight={700}>
                  {ortalama(dol.avg_service_period_days, dol.service_period_sample)}
                </Typography>
                <Typography variant="caption" color="text.secondary">Yalnız tohumlamaya bağlı doğumlardan</Typography>
              </Card>
              <Card sx={{flex: '1 1 240px', minWidth: 210, p: 2}}>
                <Typography variant="body2" color="text.secondary">Ortalama ilkine buzağılama yaşı</Typography>
                <Typography variant="h6" fontWeight={700}>
                  {ortalama(dol.avg_age_at_first_calving_days, dol.age_at_first_calving_sample)}
                </Typography>
              </Card>
            </Stack>
          )}

          <ResponsiveTable
            rows={gostergeSatirlari}
            columns={gostergeSutun}
            loading={loading}
            cardTitle={row => animalLabel(row as FertilityRow)}
            cardSubtitle={row => `${row.calving_count} doğum · son ${isoDate(row.last_calving_on)}`}
            cardFields={[
              {
                label: 'Son aralık',
                value: row => (row.last_calving_interval_days === null
                  ? 'Tek doğum' : dayLabel(row.last_calving_interval_days)),
              },
              {label: 'Servis periyodu', value: row => dayLabel(row.service_period_days)},
              {label: 'İlkine yaş', value: row => dayLabel(row.age_at_first_calving_days)},
            ]}
            onRowClick={row => navigate(`/hayvancilik/hayvanlar/${row.animal_id}`)}
          />
        </>
      ) : tab === 0 ? (
        <ResponsiveTable
          rows={breedings}
          columns={tohumSutun}
          loading={loading}
          cardTitle={row => animalLabel(hayvanKimlik.get(row.animal_id))}
          cardSubtitle={row => `${isoDate(row.bred_on)} · ${breedingMethodLabel(row.method)}`}
          cardFields={[
            {label: 'Sonuç', value: row => breedingResultLabel(row.result)},
            {label: 'Tahmini doğum', value: row => isoDate(row.expected_birth_on)},
            {label: 'Boğa / koç', value: row => row.sire_code || '—'},
          ]}
          cardActions={yazabilir ? [{label: 'Sonucu güncelle', icon: <EditIcon />, onClick: tohumlamaDuzenle}] : undefined}
          onRowClick={yazabilir ? tohumlamaDuzenle : undefined}
        />
      ) : (
        <ResponsiveTable
          rows={births}
          columns={dogumSutun}
          loading={loading}
          cardTitle={row => animalLabel(hayvanKimlik.get(row.mother_id))}
          cardSubtitle={row => `${isoDate(row.birth_date)} · ${birthOutcomeLabel(row.outcome)}`}
          cardFields={[
            {label: 'Yavru', value: row => row.offspring_count ?? '—'},
            {label: 'Güçlük', value: row => (row.difficulty ? `${row.difficulty}/4` : '—')},
          ]}
          // Doğum kaydı DÜZENLENMİYOR (uç yok); satır anneye götürür.
          onRowClick={row => navigate(`/hayvancilik/hayvanlar/${row.mother_id}`)}
        />
      )}

      {/* --- Tohumlama formu --- */}
      <Dialog open={breedingDialog.open} onClose={() => !saving && setBreedingDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>{breedingDialog.row ? 'Tohumlama sonucunu güncelle' : 'Yeni tohumlama'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            {!disiler.length && !breedingDialog.row && (
              <Alert severity="warning">Sürüde kayıtlı dişi hayvan yok.</Alert>
            )}
            <TextField select label="Hayvan (dişi)" required value={breedingDialog.form.animal_id}
              disabled={!!breedingDialog.row}
              onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, animal_id: e.target.value}}))}>
              {(breedingDialog.row
                ? animals.filter(a => a.id === breedingDialog.row?.animal_id)
                : disiler
              ).map(a => <MenuItem key={a.id} value={String(a.id)}>{animalLabel(a)}</MenuItem>)}
            </TextField>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Tohumlama tarihi" type="date" required fullWidth InputLabelProps={{shrink: true}}
                value={breedingDialog.form.bred_on}
                onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, bred_on: e.target.value}}))} />
              <TextField select label="Yöntem" fullWidth value={breedingDialog.form.method}
                onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, method: e.target.value}}))}>
                {BREEDING_METHOD.map(m => <MenuItem key={m.value} value={m.value}>{m.label}</MenuItem>)}
              </TextField>
            </Stack>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Boğa / koç kodu" fullWidth value={breedingDialog.form.sire_code}
                onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, sire_code: e.target.value}}))} />
              <TextField label="Teknisyen" fullWidth value={breedingDialog.form.technician}
                onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, technician: e.target.value}}))} />
            </Stack>

            {/* SONUÇ yalnız düzenlemede: tohumlama anında gebelik bilinmiyor. */}
            {breedingDialog.row && (
              <>
                <Divider />
                <TextField select label="Gebelik sonucu" value={breedingDialog.form.result}
                  onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, result: e.target.value}}))}>
                  {BREEDING_RESULT.map(r => <MenuItem key={r.value} value={r.value}>{r.label}</MenuItem>)}
                </TextField>
                <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
                  <TextField label="Muayene tarihi" type="date" fullWidth InputLabelProps={{shrink: true}}
                    value={breedingDialog.form.checked_on}
                    onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, checked_on: e.target.value}}))} />
                  <TextField label="Tahmini doğum" type="date" fullWidth InputLabelProps={{shrink: true}}
                    value={breedingDialog.form.expected_birth_on}
                    onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, expected_birth_on: e.target.value}}))} />
                </Stack>
              </>
            )}

            <TextField label="Not" multiline minRows={2} value={breedingDialog.form.notes}
              onChange={e => setBreedingDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBreedingDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void tohumlamaKaydet()} disabled={saving || !tohumGecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>

      {/* --- Doğum formu --- */}
      <Dialog open={birthDialog} onClose={() => !saving && setBirthDialog(false)} fullWidth maxWidth="sm">
        <DialogTitle>Yeni doğum</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            {!disiler.length && <Alert severity="warning">Sürüde kayıtlı dişi hayvan yok.</Alert>}
            <TextField select label="Anne" required value={birthForm.mother_id}
              onChange={e => setBirthForm(f => ({...f, mother_id: e.target.value, breeding_id: ''}))}>
              {disiler.map(a => <MenuItem key={a.id} value={String(a.id)}>{animalLabel(a)}</MenuItem>)}
            </TextField>
            {anneninTohumlamalari.length > 0 && (
              <TextField select label="İlgili tohumlama" value={birthForm.breeding_id}
                helperText="Bağlanırsa döl verimi hesapları bu doğumu tohumlamayla eşler."
                onChange={e => setBirthForm(f => ({...f, breeding_id: e.target.value}))}>
                <MenuItem value="">— Bağlama —</MenuItem>
                {anneninTohumlamalari.map(b => (
                  <MenuItem key={b.id} value={String(b.id)}>
                    {isoDate(b.bred_on)} · {breedingMethodLabel(b.method)}
                  </MenuItem>
                ))}
              </TextField>
            )}
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Doğum tarihi" type="date" required fullWidth InputLabelProps={{shrink: true}}
                value={birthForm.birth_date}
                onChange={e => setBirthForm(f => ({...f, birth_date: e.target.value}))} />
              <TextField select label="Sonuç" fullWidth value={birthForm.outcome}
                onChange={e => setBirthForm(f => ({...f, outcome: e.target.value}))}>
                {BIRTH_OUTCOME.map(o => <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>)}
              </TextField>
            </Stack>
            <TextField select label="Doğum güçlüğü" value={birthForm.difficulty}
              helperText="1 = kolay, 4 = veteriner müdahalesi."
              onChange={e => setBirthForm(f => ({...f, difficulty: e.target.value}))}>
              <MenuItem value="">— Girilmedi —</MenuItem>
              {[1, 2, 3, 4].map(n => <MenuItem key={n} value={String(n)}>{n}</MenuItem>)}
            </TextField>

            <Divider />

            {canliDogum ? (
              <>
                {/* Kullanıcı ÖNCEDEN görüyor: bu satırlar yeni hayvan kaydı olur. */}
                <Alert severity="info">
                  Buraya eklenen her yavru, doğumla <b>aynı işlemde</b> yeni bir hayvan
                  kaydı olarak sürüye eklenir. Türü ve sürüsü anneden alınır.
                </Alert>
                {birthForm.offspring.map((y, i) => (
                  <Stack key={i} direction="row" spacing={1} alignItems="flex-start">
                    <TextField label="Küpe no" fullWidth value={y.ear_tag}
                      onChange={e => setBirthForm(f => ({
                        ...f,
                        offspring: f.offspring.map((o, j) => (j === i ? {...o, ear_tag: e.target.value} : o)),
                      }))} />
                    <TextField label="Ad" fullWidth value={y.name}
                      onChange={e => setBirthForm(f => ({
                        ...f,
                        offspring: f.offspring.map((o, j) => (j === i ? {...o, name: e.target.value} : o)),
                      }))} />
                    <TextField select label="Cinsiyet" required sx={{minWidth: 120}} value={y.sex}
                      onChange={e => setBirthForm(f => ({
                        ...f,
                        offspring: f.offspring.map((o, j) => (j === i ? {...o, sex: e.target.value} : o)),
                      }))}>
                      {SEX.map(s => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
                    </TextField>
                    <IconButton aria-label="Yavruyu çıkar" sx={{mt: 1}}
                      onClick={() => setBirthForm(f => ({...f, offspring: f.offspring.filter((_o, j) => j !== i)}))}>
                      <DeleteOutlineIcon />
                    </IconButton>
                  </Stack>
                ))}
                <Box>
                  <Button startIcon={<AddIcon />} sx={{minHeight: {xs: 44}}}
                    onClick={() => setBirthForm(f => ({
                      ...f, offspring: [...f.offspring, {ear_tag: '', name: '', sex: 'FEMALE'}],
                    }))}>
                    Yavru ekle
                  </Button>
                </Box>
                {!birthForm.offspring.length && (
                  <TextField label="Yavru sayısı" inputMode="numeric" value={birthForm.offspring_count}
                    helperText="Bireysel yavru kaydı tutulmuyorsa yalnız sayıyı girin (küçükbaş sürüleri)."
                    onChange={e => setBirthForm(f => ({...f, offspring_count: e.target.value}))} />
                )}
              </>
            ) : (
              // Ölü doğumda yavru satırı GİRİLEMEZ: sunucu 422 veriyor ve
              // girilseydi sürüde olmayan bir hayvan kaydı oluşurdu.
              <Alert severity="warning">
                {birthOutcomeLabel(birthForm.outcome)} kaydında yavru hayvan kaydı
                oluşturulmaz — sürüde olmayan bir hayvan görünürdü. Olay yine de
                annenin geçmişine ve doğum sayılarına yazılır.
              </Alert>
            )}

            <TextField label="Not" multiline minRows={2} value={birthForm.notes}
              onChange={e => setBirthForm(f => ({...f, notes: e.target.value}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBirthDialog(false)} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void dogumKaydet()} disabled={saving || !dogumGecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
