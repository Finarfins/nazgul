/**
 * Hayvan Detayı (`/hayvancilik/hayvanlar/:id`) — bir hayvanın tüm geçmişi.
 *
 * Konu: mobil-erp#17, FAZ 3.
 *
 * Bu ekran, "bu hayvana ne oldu" sorusunun tek cevap yeri. Üç şey bilinçli:
 *
 * 1. **Yaş sunucudan geliyor, burada HESAPLANMIYOR.** `GET /animals/{id}`
 *    `age_days` döndürüyor. Cihaz saatinden hesaplasaydık, saati yanlış bir
 *    telefonda yanlış yaş — ve FAZ 4'te yanlış aşı zamanı — çıkardı.
 *
 * 2. **Zaman çizelgesi tek listede birleştiriliyor.** Aşı, tohumlama, doğum,
 *    tartım, süt ve hareket ayrı ayrı sekmelerde olsaydı "geçen ay ne oldu"
 *    sorusu altı ayrı yere bakmayı gerektirirdi.
 *
 * 3. **Hareket kaydı hayvanın DURUMUNU değiştirir.** Sunucu bunu tek işlemde
 *    yapıyor (satış → 'Satıldı'). Ekran kullanıcıyı ÖNCEDEN uyarıyor; sürpriz
 *    olarak yaşanması "satış girdim, hayvan listeden kayboldu" demek.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {Link as RouterLink, useParams} from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, Divider, MenuItem, Stack,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import PetsIcon from '@mui/icons-material/Pets';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import {
  MOVEMENT_KIND, MOVEMENT_REMOVES_FROM_HERD,
  acquisitionLabel, ageLabel, animalLabel, animalStatusLabel, birthOutcomeLabel,
  bosNull, breedingMethodLabel, breedingResultLabel, herdErrorText, isoDate,
  listHerd, money, movementKindLabel, ondalik, qty, saveHerdRecord, sexLabel,
  speciesLabel,
  type Animal, type Birth, type Breeding, type MilkYield, type Movement,
  type Vaccination, type Weight,
} from '../herd/herdApi';

/** Zaman çizelgesinin tek satırı — kaynağı ne olursa olsun aynı biçim. */
type Olay = {key: string; date: string; kind: string; detail: string};

type VaccinationForm = {
  vaccine: string; applied_on: string; dose_no: string; next_due_on: string;
  veterinarian: string; batch_no: string; notes: string;
};
type WeightForm = {weighed_on: string; weight_kg: string; notes: string};
type MovementForm = {
  kind: string; moved_on: string; amount: string; counterparty: string;
  reason: string; notes: string;
};

const bugunIso = () => {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

export default function AnimalDetail() {
  const {id} = useParams<{id: string}>();
  const animalId = Number(id);
  const {can} = useAuth();
  const yazabilir = can('herd.manage');
  const saglikYazabilir = can('herd.health');

  const [animal, setAnimal] = useState<Animal | null>(null);
  const [vaccinations, setVaccinations] = useState<Vaccination[]>([]);
  const [breedings, setBreedings] = useState<Breeding[]>([]);
  const [births, setBirths] = useState<Birth[]>([]);
  const [weights, setWeights] = useState<Weight[]>([]);
  const [milk, setMilk] = useState<MilkYield[]>([]);
  const [movements, setMovements] = useState<Movement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [vaccineDialog, setVaccineDialog] = useState(false);
  const [weightDialog, setWeightDialog] = useState(false);
  const [movementDialog, setMovementDialog] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const [vaccineForm, setVaccineForm] = useState<VaccinationForm>({
    vaccine: '', applied_on: bugunIso(), dose_no: '', next_due_on: '',
    veterinarian: '', batch_no: '', notes: '',
  });
  const [weightForm, setWeightForm] = useState<WeightForm>({
    weighed_on: bugunIso(), weight_kg: '', notes: '',
  });
  const [movementForm, setMovementForm] = useState<MovementForm>({
    kind: 'SALE', moved_on: bugunIso(), amount: '', counterparty: '', reason: '', notes: '',
  });

  const yukle = useCallback(async () => {
    if (!Number.isFinite(animalId)) return;
    setLoading(true);
    setError('');
    try {
      const [a, v, b, d, t, s, h] = await Promise.all([
        api.get<Animal>(`/animals/${animalId}`),
        listHerd<Vaccination>('/animal-vaccinations', {animal_id: animalId}),
        listHerd<Breeding>('/animal-breedings', {animal_id: animalId}),
        listHerd<Birth>('/animal-births', {mother_id: animalId}),
        listHerd<Weight>('/animal-weights', {animal_id: animalId}),
        listHerd<MilkYield>('/milk-yields', {animal_id: animalId}),
        listHerd<Movement>('/animal-movements', {animal_id: animalId}),
      ]);
      setAnimal(a.data);
      setVaccinations(v.items);
      setBreedings(b.items);
      setBirths(d.items);
      setWeights(t.items);
      setMilk(s.items);
      setMovements(h.items);
    } catch (err) {
      setAnimal(null);
      setError(herdErrorText(err, 'Hayvan kaydı yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, [animalId]);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  /** Altı kaynak tek çizelgede, EN YENİ ÜSTTE. */
  const olaylar = useMemo<Olay[]>(() => {
    const liste: Olay[] = [
      ...vaccinations.map(v => ({
        key: `v${v.id}`, date: v.applied_on, kind: 'Aşı',
        detail: [v.vaccine, v.dose_no ? `${v.dose_no}. doz` : null,
          v.next_due_on ? `tekrar ${isoDate(v.next_due_on)}` : null,
          v.veterinarian].filter(Boolean).join(' · '),
      })),
      ...breedings.map(b => ({
        key: `b${b.id}`, date: b.bred_on, kind: 'Tohumlama',
        detail: [breedingMethodLabel(b.method), breedingResultLabel(b.result),
          b.expected_birth_on ? `tahmini doğum ${isoDate(b.expected_birth_on)}` : null,
          b.sire_code].filter(Boolean).join(' · '),
      })),
      ...births.map(d => ({
        key: `d${d.id}`, date: d.birth_date, kind: 'Doğum',
        detail: [birthOutcomeLabel(d.outcome),
          d.offspring_count !== null ? `${d.offspring_count} yavru` : null,
          d.difficulty ? `güçlük ${d.difficulty}/4` : null].filter(Boolean).join(' · '),
      })),
      ...weights.map(t => ({
        key: `t${t.id}`, date: t.weighed_on, kind: 'Tartım',
        detail: `${qty(t.weight_kg)} kg`,
      })),
      ...milk.map(s => ({
        key: `s${s.id}`, date: s.milked_on, kind: 'Süt',
        detail: [`${qty(s.quantity_liters)} litre`, s.session].filter(Boolean).join(' · '),
      })),
      ...movements.map(h => ({
        key: `h${h.id}`, date: h.moved_on, kind: 'Hareket',
        detail: [movementKindLabel(h.kind), h.counterparty,
          h.amount ? money(h.amount) : null].filter(Boolean).join(' · '),
      })),
    ];
    return liste.sort((a, b) => b.date.localeCompare(a.date));
  }, [vaccinations, breedings, births, weights, milk, movements]);

  const sonTartim = weights.length
    ? [...weights].sort((a, b) => b.weighed_on.localeCompare(a.weighed_on))[0]
    : null;

  // -------------------------------------------------------------------------
  // Kaydetme
  // -------------------------------------------------------------------------

  const kaydet = async (fn: () => Promise<unknown>, kapat: () => void, hata: string) => {
    setSaving(true);
    setDialogError('');
    try {
      await fn();
      kapat();
      await yukle();
    } catch (err) {
      setDialogError(herdErrorText(err, hata));
    } finally {
      setSaving(false);
    }
  };

  const asiKaydet = () => kaydet(
    () => saveHerdRecord('post', '/animal-vaccinations', {
      animal_id: animalId,
      vaccine: vaccineForm.vaccine.trim(),
      applied_on: vaccineForm.applied_on,
      dose_no: vaccineForm.dose_no.trim() === '' ? null : Number(vaccineForm.dose_no),
      next_due_on: bosNull(vaccineForm.next_due_on),
      veterinarian: bosNull(vaccineForm.veterinarian),
      batch_no: bosNull(vaccineForm.batch_no),
      notes: bosNull(vaccineForm.notes),
    }, 'Aşı kaydedilemedi.'),
    () => {
      setVaccineDialog(false);
      setVaccineForm({vaccine: '', applied_on: bugunIso(), dose_no: '', next_due_on: '',
        veterinarian: '', batch_no: '', notes: ''});
    },
    'Aşı kaydedilemedi.',
  );

  const tartimKaydet = () => kaydet(
    () => saveHerdRecord('post', '/animal-weights', {
      animal_id: animalId,
      weighed_on: weightForm.weighed_on,
      // Ağırlık STRING gidiyor: sayıya çevirip geri yazmak ondalıkta kayan
      // nokta hatası üretirdi.
      weight_kg: ondalik(weightForm.weight_kg),
      notes: bosNull(weightForm.notes),
    }, 'Tartım kaydedilemedi.'),
    () => {
      setWeightDialog(false);
      setWeightForm({weighed_on: bugunIso(), weight_kg: '', notes: ''});
    },
    'Tartım kaydedilemedi.',
  );

  const hareketKaydet = () => kaydet(
    () => saveHerdRecord('post', '/animal-movements', {
      animal_id: animalId,
      kind: movementForm.kind,
      moved_on: movementForm.moved_on,
      amount: movementForm.amount.trim() === '' ? null : ondalik(movementForm.amount),
      counterparty: bosNull(movementForm.counterparty),
      reason: bosNull(movementForm.reason),
      notes: bosNull(movementForm.notes),
    }, 'Hareket kaydedilemedi.'),
    () => {
      setMovementDialog(false);
      setMovementForm({kind: 'SALE', moved_on: bugunIso(), amount: '', counterparty: '',
        reason: '', notes: ''});
    },
    'Hareket kaydedilemedi.',
  );

  // -------------------------------------------------------------------------

  if (loading && !animal) {
    return <Box py={8} display="grid" sx={{placeItems: 'center'}}><CircularProgress /></Box>;
  }

  if (!animal) {
    return (
      <Stack spacing={2}>
        <Alert severity="error">{error || 'Hayvan bulunamadı.'}</Alert>
        <Box><Button component={RouterLink} to="/hayvancilik/hayvanlar">Hayvan listesine dön</Button></Box>
      </Stack>
    );
  }

  const cikaracak = MOVEMENT_REMOVES_FROM_HERD[movementForm.kind];

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <PetsIcon color="primary" />
            <Typography variant="h4">{animalLabel(animal)}</Typography>
            <Chip size="small" color={animal.status === 'ACTIVE' ? 'success' : 'default'}
              label={animalStatusLabel(animal.status)} />
          </Stack>
          <Typography color="text.secondary">
            {speciesLabel(animal.species)} · {sexLabel(animal.sex)}
            {animal.breed ? ` · ${animal.breed}` : ''}
          </Typography>
        </Box>
        <Button component={RouterLink} to="/hayvancilik/hayvanlar" variant="outlined"
          sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}>
          Listeye dön
        </Button>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      <Card>
        <CardContent>
          <Stack direction="row" flexWrap="wrap" gap={3} divider={<Divider orientation="vertical" flexItem />}>
            <Box minWidth={130}>
              <Typography variant="body2" color="text.secondary">Doğum tarihi</Typography>
              <Typography fontWeight={650}>{isoDate(animal.birth_date)}</Typography>
            </Box>
            <Box minWidth={130}>
              <Typography variant="body2" color="text.secondary">Yaş</Typography>
              {/* SUNUCUDAN gelen `age_days`; istemcide hesaplanmıyor. */}
              <Typography fontWeight={650}>{ageLabel(animal.age_days)}</Typography>
            </Box>
            <Box minWidth={130}>
              <Typography variant="body2" color="text.secondary">Nasıl geldi</Typography>
              <Typography fontWeight={650}>
                {acquisitionLabel(animal.acquisition)}
                {animal.acquired_on ? ` · ${isoDate(animal.acquired_on)}` : ''}
              </Typography>
            </Box>
            <Box minWidth={130}>
              <Typography variant="body2" color="text.secondary">Son tartım</Typography>
              <Typography fontWeight={650}>
                {sonTartim ? `${qty(sonTartim.weight_kg)} kg · ${isoDate(sonTartim.weighed_on)}` : '—'}
              </Typography>
            </Box>
            <Box minWidth={130}>
              <Typography variant="body2" color="text.secondary">Anne / baba</Typography>
              <Typography fontWeight={650}>
                {animal.mother_id ? (
                  <RouterLink to={`/hayvancilik/hayvanlar/${animal.mother_id}`}>#{animal.mother_id}</RouterLink>
                ) : '—'}
                {' / '}
                {animal.father_id ? (
                  <RouterLink to={`/hayvancilik/hayvanlar/${animal.father_id}`}>#{animal.father_id}</RouterLink>
                ) : '—'}
              </Typography>
            </Box>
          </Stack>
          {animal.notes && (
            <Typography variant="body2" color="text.secondary" mt={2}>{animal.notes}</Typography>
          )}
        </CardContent>
      </Card>

      <Stack direction="row" flexWrap="wrap" gap={1}>
        {saglikYazabilir && (
          <Button variant="contained" startIcon={<AddIcon />} sx={{minHeight: {xs: 44}}}
            onClick={() => {setDialogError(''); setVaccineDialog(true);}}>
            Aşı kaydet
          </Button>
        )}
        {yazabilir && (
          <>
            <Button variant="outlined" startIcon={<AddIcon />} sx={{minHeight: {xs: 44}}}
              onClick={() => {setDialogError(''); setWeightDialog(true);}}>
              Tartım kaydet
            </Button>
            <Button variant="outlined" startIcon={<AddIcon />} sx={{minHeight: {xs: 44}}}
              onClick={() => {setDialogError(''); setMovementDialog(true);}}>
              Hareket kaydet
            </Button>
          </>
        )}
      </Stack>

      <Card>
        <CardContent sx={{pb: 0}}>
          <Typography variant="h6">Geçmiş ({olaylar.length})</Typography>
          <Typography variant="body2" color="text.secondary">
            Aşı, tohumlama, doğum, tartım, süt ve hareket kayıtları tek çizelgede.
          </Typography>
        </CardContent>
        {!olaylar.length ? (
          <CardContent><Typography color="text.secondary">Bu hayvan için henüz kayıt yok.</Typography></CardContent>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Tarih</TableCell>
                  <TableCell>Tür</TableCell>
                  <TableCell>Ayrıntı</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {olaylar.map(o => (
                  <TableRow key={o.key} hover>
                    <TableCell>{isoDate(o.date)}</TableCell>
                    <TableCell><Chip size="small" variant="outlined" label={o.kind} /></TableCell>
                    <TableCell>{o.detail || '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Card>

      {/* --- Aşı formu --- */}
      <Dialog open={vaccineDialog} onClose={() => !saving && setVaccineDialog(false)} fullWidth maxWidth="sm">
        <DialogTitle>Aşı kaydet — {animalLabel(animal)}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField label="Aşı" required value={vaccineForm.vaccine}
              helperText="Serbest metin: şap, brusella, septisemi, IBR…"
              onChange={e => setVaccineForm(f => ({...f, vaccine: e.target.value}))} />
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Yapıldığı tarih" type="date" required fullWidth InputLabelProps={{shrink: true}}
                value={vaccineForm.applied_on}
                onChange={e => setVaccineForm(f => ({...f, applied_on: e.target.value}))} />
              <TextField label="Tekrar tarihi" type="date" fullWidth InputLabelProps={{shrink: true}}
                value={vaccineForm.next_due_on}
                helperText="Girilirse pano vadesi geçenleri gösterir."
                onChange={e => setVaccineForm(f => ({...f, next_due_on: e.target.value}))} />
            </Stack>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Doz no" inputMode="numeric" fullWidth value={vaccineForm.dose_no}
                onChange={e => setVaccineForm(f => ({...f, dose_no: e.target.value}))} />
              <TextField label="Seri / parti no" fullWidth value={vaccineForm.batch_no}
                onChange={e => setVaccineForm(f => ({...f, batch_no: e.target.value}))} />
            </Stack>
            <TextField label="Veteriner" value={vaccineForm.veterinarian}
              onChange={e => setVaccineForm(f => ({...f, veterinarian: e.target.value}))} />
            <TextField label="Not" multiline minRows={2} value={vaccineForm.notes}
              onChange={e => setVaccineForm(f => ({...f, notes: e.target.value}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setVaccineDialog(false)} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void asiKaydet()}
            disabled={saving || vaccineForm.vaccine.trim() === '' || !vaccineForm.applied_on}>
            Kaydet
          </Button>
        </DialogActions>
      </Dialog>

      {/* --- Tartım formu --- */}
      <Dialog open={weightDialog} onClose={() => !saving && setWeightDialog(false)} fullWidth maxWidth="xs">
        <DialogTitle>Tartım kaydet — {animalLabel(animal)}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField label="Tartım tarihi" type="date" required InputLabelProps={{shrink: true}}
              value={weightForm.weighed_on}
              onChange={e => setWeightForm(f => ({...f, weighed_on: e.target.value}))} />
            <TextField label="Ağırlık (kg)" required inputMode="decimal" value={weightForm.weight_kg}
              onChange={e => setWeightForm(f => ({...f, weight_kg: e.target.value}))} />
            <TextField label="Not" multiline minRows={2} value={weightForm.notes}
              onChange={e => setWeightForm(f => ({...f, notes: e.target.value}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setWeightDialog(false)} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void tartimKaydet()}
            disabled={saving || Number(ondalik(weightForm.weight_kg)) <= 0 || !weightForm.weighed_on}>
            Kaydet
          </Button>
        </DialogActions>
      </Dialog>

      {/* --- Hareket formu --- */}
      <Dialog open={movementDialog} onClose={() => !saving && setMovementDialog(false)} fullWidth maxWidth="sm">
        <DialogTitle>Hareket kaydet — {animalLabel(animal)}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField select label="Hareket türü" required value={movementForm.kind}
              onChange={e => setMovementForm(f => ({...f, kind: e.target.value}))}>
              {MOVEMENT_KIND.map(k => <MenuItem key={k.value} value={k.value}>{k.label}</MenuItem>)}
            </TextField>
            {/* Kullanıcı ÖNCEDEN biliyor: bu kayıt hayvanın durumunu da
                değiştirecek ve hayvan sürü listesinden düşecek. */}
            {cikaracak && (
              <Alert severity="warning">
                Bu kayıt hayvanın durumunu <b>{cikaracak}</b> olarak değiştirir ve
                hayvan sürü sayısından düşer. Aynı işlemde yapılır.
              </Alert>
            )}
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Tarih" type="date" required fullWidth InputLabelProps={{shrink: true}}
                value={movementForm.moved_on}
                onChange={e => setMovementForm(f => ({...f, moved_on: e.target.value}))} />
              <TextField label="Tutar" inputMode="decimal" fullWidth value={movementForm.amount}
                helperText="Alış/satışta bedel; ölüm ve kesimde boş bırakılır."
                onChange={e => setMovementForm(f => ({...f, amount: e.target.value}))} />
            </Stack>
            <TextField label="Karşı taraf" value={movementForm.counterparty}
              helperText="Alıcı, satıcı ya da nakil edilen işletme."
              onChange={e => setMovementForm(f => ({...f, counterparty: e.target.value}))} />
            <TextField label="Sebep" value={movementForm.reason}
              onChange={e => setMovementForm(f => ({...f, reason: e.target.value}))} />
            <TextField label="Not" multiline minRows={2} value={movementForm.notes}
              onChange={e => setMovementForm(f => ({...f, notes: e.target.value}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMovementDialog(false)} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void hareketKaydet()}
            disabled={saving || !movementForm.moved_on}>
            Kaydet
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
