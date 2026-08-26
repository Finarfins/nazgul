/**
 * Hayvanlar & Sürüler (`/hayvancilik/hayvanlar`).
 *
 * Konu: mobil-erp#17, FAZ 3.
 *
 * Üç karar burada görünür hâle getiriliyor:
 *
 * 1. **Küpe uyarısı ENGEL DEĞİL.** Sunucu alışılmadık biçimdeki küpeyi kabul
 *    edip yanıtta `warnings` döndürüyor (küpe standardı 2026'da değişti).
 *    Ekran bunu KAYIT SONRASI bilgi olarak gösteriyor; formu kırmızıya boyayıp
 *    kaydı reddetseydik geçerli bir küpeyi girmek imkânsız hâle gelirdi.
 *
 * 2. **Grupta baş sayısı, bireysel kayıt varken girilemez.** Sunucu 422
 *    veriyor; ekran da alanı kapatıp sebebini yazıyor ki kullanıcı duvara
 *    çarparak öğrenmesin. İkisi birden dolu olursa aynı hayvan iki kez sayılır.
 *
 * 3. **Yazma düğmeleri `herd.manage` iznine bağlı.** Güvenlik sunucuda;
 *    buradaki gizleme yalnız nezaket ve ona GÜVENİLMEZ.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {
  Alert, Box, Button, Card, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, MenuItem, Stack, Tab, Tabs, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import PetsIcon from '@mui/icons-material/Pets';
import TimelineIcon from '@mui/icons-material/Timeline';
import type {GridColDef} from '@mui/x-data-grid';

import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  ACQUISITION, GROUP_SPECIES, SEX, SPECIES,
  animalLabel, animalStatusLabel, bosNull, herdErrorText,
  fetchAllHerd, isoDate, saveHerdRecord, sexLabel, speciesLabel,
  type Animal, type AnimalGroup,
} from '../herd/herdApi';

type AnimalForm = {
  ear_tag: string; name: string; species: string; breed: string; sex: string;
  birth_date: string; acquisition: string; acquired_on: string; group_id: string; notes: string;
};
type GroupForm = {
  code: string; name: string; species: string; location: string;
  head_count: string; notes: string;
};

const BOS_HAYVAN: AnimalForm = {
  ear_tag: '', name: '', species: 'CATTLE', breed: '', sex: 'FEMALE',
  birth_date: '', acquisition: 'BORN', acquired_on: '', group_id: '', notes: '',
};
const BOS_GRUP: GroupForm = {code: '', name: '', species: '', location: '', head_count: '', notes: ''};

export default function Animals() {
  const {can} = useAuth();
  const navigate = useNavigate();
  const yazabilir = can('herd.manage');

  const [tab, setTab] = useState(0);
  const [animals, setAnimals] = useState<Animal[]>([]);
  const [groups, setGroups] = useState<AnimalGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [arama, setArama] = useState('');
  /** Kayıt YAPILDI ama küpe biçimi alışılmadık — bilgi, hata değil. */
  const [uyarilar, setUyarilar] = useState<string[]>([]);

  const [animalDialog, setAnimalDialog] = useState<{open: boolean; row: Animal | null; form: AnimalForm}>({
    open: false, row: null, form: BOS_HAYVAN,
  });
  const [groupDialog, setGroupDialog] = useState<{open: boolean; row: AnimalGroup | null; form: GroupForm}>({
    open: false, row: null, form: BOS_GRUP,
  });
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [a, g] = await Promise.all([
        fetchAllHerd<Animal>('/animals'),
        fetchAllHerd<AnimalGroup>('/animal-groups'),
      ]);
      setAnimals(a);
      setGroups(g);
    } catch (err) {
      setError(herdErrorText(err, 'Hayvan ve sürüler yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const grupAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const g of groups) map.set(g.id, g.name);
    return (id: number | null) => (id === null ? '—' : map.get(id) ?? `#${id}`);
  }, [groups]);

  /** Bir sürüde bireysel kayıt varsa baş sayısı GİRİLEMEZ (çifte sayım). */
  const grupBireyselSayisi = useMemo(() => {
    const sayac = new Map<number, number>();
    for (const a of animals) {
      if (a.group_id !== null) sayac.set(a.group_id, (sayac.get(a.group_id) ?? 0) + 1);
    }
    return sayac;
  }, [animals]);

  const aramaTerimi = arama.trim().toLocaleLowerCase('tr-TR');
  const arananHayvanlar = useMemo(() => {
    if (!aramaTerimi) return animals;
    return animals.filter(a => [
      a.ear_tag, a.name, a.breed, speciesLabel(a.species), sexLabel(a.sex),
      animalStatusLabel(a.status), grupAdi(a.group_id),
    ].some(v => String(v ?? '').toLocaleLowerCase('tr-TR').includes(aramaTerimi)));
  }, [animals, aramaTerimi, grupAdi]);
  const arananGruplar = useMemo(() => {
    if (!aramaTerimi) return groups;
    return groups.filter(g => [
      g.code, g.name, g.location, speciesLabel(g.species), g.status,
    ].some(v => String(v ?? '').toLocaleLowerCase('tr-TR').includes(aramaTerimi)));
  }, [groups, aramaTerimi]);

  // -------------------------------------------------------------------------
  // Kaydetme
  // -------------------------------------------------------------------------

  const hayvanKaydet = async () => {
    setSaving(true);
    setDialogError('');
    const {row, form} = animalDialog;
    const gövde = {
      ear_tag: bosNull(form.ear_tag),
      name: bosNull(form.name),
      species: form.species,
      breed: bosNull(form.breed),
      sex: form.sex,
      birth_date: bosNull(form.birth_date),
      acquisition: form.acquisition,
      acquired_on: bosNull(form.acquired_on),
      group_id: form.group_id === '' ? null : Number(form.group_id),
      notes: bosNull(form.notes),
    };
    try {
      const kayit = row
        ? await saveHerdRecord<Animal>('put', `/animals/${row.id}`, {
            ...gövde,
            // Düzenlemede mevcut durum korunuyor; durumu değiştiren yer
            // HAREKET ekranı (satış/ölüm) — orada tek işlemde yapılıyor.
            status: row.status,
            mother_id: row.mother_id, father_id: row.father_id,
            expected_updated_at: row.updated_at,
          }, 'Hayvan güncellenemedi.')
        : await saveHerdRecord<Animal>('post', '/animals', gövde, 'Hayvan eklenemedi.');
      setUyarilar(kayit.warnings ?? []);
      setAnimalDialog({open: false, row: null, form: BOS_HAYVAN});
      await yukle();
    } catch (err) {
      setDialogError(herdErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const grupKaydet = async () => {
    setSaving(true);
    setDialogError('');
    const {row, form} = groupDialog;
    const gövde = {
      code: form.code.trim(),
      name: form.name.trim(),
      species: form.species === '' ? null : form.species,
      location: bosNull(form.location),
      head_count: form.head_count.trim() === '' ? null : Number(form.head_count),
      notes: bosNull(form.notes),
    };
    try {
      if (row) {
        await saveHerdRecord('put', `/animal-groups/${row.id}`, {
          ...gövde, status: row.status, expected_updated_at: row.updated_at,
        }, 'Sürü güncellenemedi.');
      } else {
        await saveHerdRecord('post', '/animal-groups', gövde, 'Sürü oluşturulamadı.');
      }
      setGroupDialog({open: false, row: null, form: BOS_GRUP});
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

  const hayvanSutun: GridColDef[] = [
    {field: 'ear_tag', headerName: 'Küpe no', width: 150, valueGetter: (_v, row) => row.ear_tag || '—'},
    {field: 'name', headerName: 'Ad', width: 150, valueGetter: (_v, row) => row.name || '—'},
    {field: 'species', headerName: 'Tür', width: 110, valueGetter: (_v, row) => speciesLabel(row.species)},
    {field: 'sex', headerName: 'Cinsiyet', width: 100, valueGetter: (_v, row) => sexLabel(row.sex)},
    {field: 'birth_date', headerName: 'Doğum', width: 120, valueGetter: (_v, row) => isoDate(row.birth_date)},
    {field: 'group_id', headerName: 'Sürü', width: 160, valueGetter: (_v, row) => grupAdi(row.group_id)},
    {
      field: 'status', headerName: 'Durum', width: 120,
      renderCell: params => (
        <Chip size="small" color={params.value === 'ACTIVE' ? 'success' : 'default'}
          label={animalStatusLabel(params.value as string)} />
      ),
    },
  ];

  const grupSutun: GridColDef[] = [
    {field: 'code', headerName: 'Kod', width: 120},
    {field: 'name', headerName: 'Sürü', flex: 1, minWidth: 180},
    {field: 'species', headerName: 'Tür', width: 110, valueGetter: (_v, row) => speciesLabel(row.species)},
    {field: 'location', headerName: 'Yer', width: 160, valueGetter: (_v, row) => row.location || '—'},
    {
      field: 'head_count', headerName: 'Baş sayısı', width: 130, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => {
        const bireysel = grupBireyselSayisi.get(row.id) ?? 0;
        // Bireysel kayıt varsa GERÇEK sayı odur; beyan edilen baş sayısı
        // zaten boş olmak zorunda.
        return bireysel ? `${bireysel} (bireysel)` : row.head_count ?? '—';
      },
    },
  ];

  const hayvanDuzenle = (row: Animal) => {
    setDialogError('');
    setAnimalDialog({
      open: true, row,
      form: {
        ear_tag: row.ear_tag || '', name: row.name || '', species: row.species,
        breed: row.breed || '', sex: row.sex, birth_date: row.birth_date || '',
        acquisition: row.acquisition, acquired_on: row.acquired_on || '',
        group_id: row.group_id === null ? '' : String(row.group_id), notes: row.notes || '',
      },
    });
  };

  const grupDuzenle = (row: AnimalGroup) => {
    setDialogError('');
    setGroupDialog({
      open: true, row,
      form: {
        code: row.code, name: row.name, species: row.species || '',
        location: row.location || '', head_count: row.head_count === null ? '' : String(row.head_count),
        notes: row.notes || '',
      },
    });
  };

  const duzenlenenGrupBireysel = groupDialog.row ? grupBireyselSayisi.get(groupDialog.row.id) ?? 0 : 0;
  const hayvanGecerli = animalDialog.form.species !== '' && animalDialog.form.sex !== '';
  const grupGecerli = groupDialog.form.code.trim() !== '' && groupDialog.form.name.trim() !== '';

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <PetsIcon color="primary" />
            <Typography variant="h4">Hayvanlar & Sürüler</Typography>
          </Stack>
          <Typography color="text.secondary">
            Bireysel kayıt küpeyle tutulur; küçükbaş sürülerinde baş sayısı yeterlidir.
          </Typography>
        </Box>
        {yazabilir && (
          <Button variant="contained" startIcon={<AddIcon />}
            sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}
            onClick={() => {
              setDialogError('');
              if (tab === 0) setAnimalDialog({open: true, row: null, form: BOS_HAYVAN});
              else setGroupDialog({open: true, row: null, form: BOS_GRUP});
            }}
          >
            {tab === 0 ? 'Yeni hayvan' : 'Yeni sürü'}
          </Button>
        )}
      </Stack>

      {error && (
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>
          {error}
        </Alert>
      )}

      {/* KAYIT YAPILDI, ama küpe biçimi alışılmadık. `warning` değil `info`
          seviyesinde: kullanıcı bir şeyin BAŞARISIZ olduğunu sanmasın. */}
      {uyarilar.map(u => (
        <Alert key={u} severity="info" onClose={() => setUyarilar(x => x.filter(y => y !== u))}>{u}</Alert>
      ))}

      <Card>
        <Tabs value={tab} onChange={(_e, v) => {setTab(v); setArama('');}} variant="scrollable" allowScrollButtonsMobile>
          <Tab label={`Hayvanlar (${animals.length})`} />
          <Tab label={`Sürüler (${groups.length})`} />
        </Tabs>
      </Card>

      <TextField
        label={tab === 0 ? 'Hayvanlarda ara' : 'Sürülerde ara'}
        value={arama}
        onChange={e => setArama(e.target.value)}
        size="small"
        sx={{maxWidth: 420}}
      />

      {tab === 0 ? (
        <ResponsiveTable
          rows={arananHayvanlar}
          columns={hayvanSutun}
          loading={loading}
          cardTitle={row => animalLabel(row)}
          cardSubtitle={row => `${speciesLabel(row.species)} · ${sexLabel(row.sex)}`}
          cardFields={[
            {label: 'Doğum', value: row => isoDate(row.birth_date)},
            {label: 'Sürü', value: row => grupAdi(row.group_id)},
            {label: 'Durum', value: row => animalStatusLabel(row.status)},
          ]}
          cardActions={[
            {label: 'Detay', icon: <TimelineIcon />, onClick: row => navigate(`/hayvancilik/hayvanlar/${row.id}`)},
            ...(yazabilir ? [{label: 'Düzenle', icon: <EditIcon />, onClick: hayvanDuzenle}] : []),
          ]}
          // Satıra tıklamak DETAYA gider: hayvanın geçmişine bakmak,
          // kaydını düzeltmekten çok daha sık yapılan iş.
          onRowClick={row => navigate(`/hayvancilik/hayvanlar/${row.id}`)}
        />
      ) : (
        <ResponsiveTable
          rows={arananGruplar}
          columns={grupSutun}
          loading={loading}
          cardTitle={row => row.name}
          cardSubtitle={row => `${row.code} · ${speciesLabel(row.species)}`}
          cardFields={[
            {label: 'Yer', value: row => row.location || '—'},
            {
              label: 'Baş sayısı',
              value: row => {
                const bireysel = grupBireyselSayisi.get(row.id) ?? 0;
                return bireysel ? `${bireysel} (bireysel kayıt)` : String(row.head_count ?? '—');
              },
            },
            {label: 'Durum', value: row => (row.status === 'ACTIVE' ? 'Aktif' : 'Arşiv')},
          ]}
          cardActions={yazabilir ? [{label: 'Düzenle', icon: <EditIcon />, onClick: grupDuzenle}] : undefined}
          onRowClick={yazabilir ? grupDuzenle : undefined}
        />
      )}

      {/* --- Hayvan formu --- */}
      <Dialog open={animalDialog.open} onClose={() => !saving && setAnimalDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>{animalDialog.row ? 'Hayvanı düzenle' : 'Yeni hayvan'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField label="Küpe no" value={animalDialog.form.ear_tag}
              helperText="Boş bırakılabilir. Alışılmadık biçim kaydı ENGELLEMEZ, yalnız uyarı verir."
              onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, ear_tag: e.target.value}}))} />
            <TextField label="Ad / lakap" value={animalDialog.form.name}
              onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, name: e.target.value}}))} />
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField select label="Tür" required fullWidth value={animalDialog.form.species}
                onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, species: e.target.value}}))}>
                {SPECIES.map(s => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
              </TextField>
              <TextField select label="Cinsiyet" required fullWidth value={animalDialog.form.sex}
                onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, sex: e.target.value}}))}>
                {SEX.map(s => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
              </TextField>
            </Stack>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Irk" fullWidth value={animalDialog.form.breed}
                onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, breed: e.target.value}}))} />
              <TextField label="Doğum tarihi" type="date" fullWidth InputLabelProps={{shrink: true}}
                value={animalDialog.form.birth_date}
                helperText="Yaş ve aşı takvimi bu tarihten hesaplanır."
                onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, birth_date: e.target.value}}))} />
            </Stack>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField select label="Nasıl geldi" fullWidth value={animalDialog.form.acquisition}
                onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, acquisition: e.target.value}}))}>
                {ACQUISITION.map(s => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
              </TextField>
              <TextField label="Geliş tarihi" type="date" fullWidth InputLabelProps={{shrink: true}}
                value={animalDialog.form.acquired_on}
                onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, acquired_on: e.target.value}}))} />
            </Stack>
            <TextField select label="Sürü" value={animalDialog.form.group_id}
              helperText="Boş bırakılabilir."
              onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, group_id: e.target.value}}))}>
              <MenuItem value="">— Sürüye bağlı değil —</MenuItem>
              {groups.map(g => <MenuItem key={g.id} value={String(g.id)}>{g.name} ({g.code})</MenuItem>)}
            </TextField>
            <TextField label="Not" multiline minRows={2} value={animalDialog.form.notes}
              onChange={e => setAnimalDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAnimalDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void hayvanKaydet()} disabled={saving || !hayvanGecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>

      {/* --- Sürü formu --- */}
      <Dialog open={groupDialog.open} onClose={() => !saving && setGroupDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>{groupDialog.row ? 'Sürüyü düzenle' : 'Yeni sürü'}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Kod" required fullWidth value={groupDialog.form.code}
                helperText="Büyük harfe çevrilir."
                onChange={e => setGroupDialog(d => ({...d, form: {...d.form, code: e.target.value}}))} />
              <TextField label="Sürü adı" required fullWidth value={groupDialog.form.name}
                onChange={e => setGroupDialog(d => ({...d, form: {...d.form, name: e.target.value}}))} />
            </Stack>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField select label="Tür" fullWidth value={groupDialog.form.species}
                onChange={e => setGroupDialog(d => ({...d, form: {...d.form, species: e.target.value}}))}>
                <MenuItem value="">— Belirtilmedi —</MenuItem>
                {GROUP_SPECIES.map(s => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
              </TextField>
              <TextField label="Yer / ağıl" fullWidth value={groupDialog.form.location}
                onChange={e => setGroupDialog(d => ({...d, form: {...d.form, location: e.target.value}}))} />
            </Stack>
            {/* Bireysel kayıt varken baş sayısı GİRİLEMEZ. Sunucu 422 veriyor;
                alanı burada kapatmak kullanıcıyı o duvara çarpmaktan kurtarır. */}
            <TextField label="Baş sayısı" inputMode="numeric" value={groupDialog.form.head_count}
              disabled={duzenlenenGrupBireysel > 0}
              helperText={
                duzenlenenGrupBireysel > 0
                  ? `Bu sürüde ${duzenlenenGrupBireysel} hayvan bireysel kayıtlı; baş sayısı girilemez ` +
                    '(aynı hayvanlar iki kez sayılırdı).'
                  : 'Yalnız bireysel kayıt tutulmayan sürüler için.'
              }
              onChange={e => setGroupDialog(d => ({...d, form: {...d.form, head_count: e.target.value}}))} />
            <TextField label="Not" multiline minRows={2} value={groupDialog.form.notes}
              onChange={e => setGroupDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setGroupDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void grupKaydet()} disabled={saving || !grupGecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
