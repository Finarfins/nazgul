/**
 * Süt & Besi (`/hayvancilik/verim`) — süt sağım ve canlı ağırlık kayıtları.
 *
 * Konu: mobil-erp#17, FAZ 6. Uçlar FAZ 2'de yazıldı, ekranı bu fazda geliyor.
 *
 * Dört karar burada:
 *
 * 1. **Süt kaydı HAYVANA YA DA GRUBA, ikisine birden değil.** Sunucu 422
 *    veriyor ve veritabanında CHECK var. Form bu yüzden tek bir seçim yapıyor:
 *    ikisi birden dolu olsaydı aynı süt iki kez sayılır ve hayvan başına verim
 *    yanlış çıkardı.
 *
 * 2. **Toplamlar SEÇİLEN aralık için, "hep" için değil.** Süt üretimi mevsime
 *    ve laktasyon dönemine göre değişir; tarihsiz bir "toplam litre" hiçbir
 *    soruya cevap vermez. Ekran gün aralığı alıyor ve toplamı onunla birlikte
 *    yazıyor.
 *
 * 3. **Günlük ortalama SAĞIM YAPILAN güne bölünür, aralıktaki tüm günlere
 *    değil.** Sağım kaydı girilmemiş günleri bölene katmak, kayıt tutmayan bir
 *    işletmenin verimini olduğundan düşük gösterirdi — ve bu, düzeltilmesi
 *    kullanıcıya bırakılamayacak bir yanlış.
 *
 * 4. **Miktarlar STRING taşınır ve Decimal ile hesaplanır.** Litre ve kilogram
 *    sunucudan string geliyor (para/miktar sözleşmesi); `Number()` toplamda ve
 *    gösterimde büyük/kesirli değerleri sessizce bozardı.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Dialog, DialogActions, DialogContent,
  DialogTitle, MenuItem, Stack, Tab, Tabs, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import MonitorWeightIcon from '@mui/icons-material/MonitorWeight';
import WaterDropIcon from '@mui/icons-material/WaterDrop';
import type {GridColDef} from '@mui/x-data-grid';

import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  animalLabel, bosNull, fetchAllHerd, herdErrorText, isoDate, ondalik,
  saveHerdRecord, type Animal, type AnimalGroup, type MilkYield, type Numeric, type Weight,
} from '../herd/herdApi';
import {decimal} from '../utils/documentMoney';

/** Sağım seansı serbest metin değil ama kapalı da değil: yaygın üçü hazır. */
const SESSIONS = [
  {value: '', label: '— Belirtilmedi —'},
  {value: 'SABAH', label: 'Sabah'},
  {value: 'AKSAM', label: 'Akşam'},
  {value: 'OGLE', label: 'Öğle'},
] as const;

type MilkForm = {
  hedef: 'animal' | 'group';
  animal_id: string; group_id: string;
  milked_on: string; session: string; quantity_liters: string; notes: string;
};
type WeightForm = {animal_id: string; weighed_on: string; weight_kg: string; notes: string};

const bugunIso = () => {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

/** N gün öncesinin ISO tarihi — varsayılan aralık için. */
const gunOnce = (n: number) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const BOS_SUT: MilkForm = {
  hedef: 'animal', animal_id: '', group_id: '', milked_on: bugunIso(),
  session: '', quantity_liters: '', notes: '',
};
const BOS_TARTIM: WeightForm = {animal_id: '', weighed_on: bugunIso(), weight_kg: '', notes: ''};

/** En çok dört ondalığı, Number'a uğramadan Türkçe ayraçlarla gösterir. */
const verimQty = (value: Numeric | ReturnType<typeof decimal> | null | undefined) => {
  if (value === null || value === undefined || value === '') return '—';
  const [rawWhole, rawFraction = ''] = decimal(value).toDecimalPlaces(4).toFixed(4).split('.');
  const negative = rawWhole.startsWith('-');
  const digits = negative ? rawWhole.slice(1) : rawWhole;
  const whole = digits.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const fraction = rawFraction.replace(/0+$/, '');
  return `${negative ? '-' : ''}${whole}${fraction ? `,${fraction}` : ''}`;
};

export default function HerdYields() {
  const {can} = useAuth();
  const navigate = useNavigate();
  const yazabilir = can('herd.manage');

  const [tab, setTab] = useState(0);
  const [milk, setMilk] = useState<MilkYield[]>([]);
  const [weights, setWeights] = useState<Weight[]>([]);
  const [animals, setAnimals] = useState<Animal[]>([]);
  const [groups, setGroups] = useState<AnimalGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [arama, setArama] = useState('');

  // Varsayılan aralık son 30 gün: tarihsiz bir toplam hiçbir soruya cevap
  // vermez (bkz. dosya başlığı).
  const [baslangic, setBaslangic] = useState(gunOnce(30));
  const [bitis, setBitis] = useState(bugunIso());

  const [milkDialog, setMilkDialog] = useState(false);
  const [weightDialog, setWeightDialog] = useState(false);
  const [milkForm, setMilkForm] = useState<MilkForm>(BOS_SUT);
  const [weightForm, setWeightForm] = useState<WeightForm>(BOS_TARTIM);
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [s, t, a, g] = await Promise.all([
        fetchAllHerd<MilkYield>('/milk-yields'),
        fetchAllHerd<Weight>('/animal-weights'),
        fetchAllHerd<Animal>('/animals'),
        fetchAllHerd<AnimalGroup>('/animal-groups'),
      ]);
      setMilk(s);
      setWeights(t);
      setAnimals(a);
      setGroups(g);
    } catch (err) {
      setError(herdErrorText(err, 'Verim kayıtları yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const hayvanKimlik = useMemo(() => new Map(animals.map(a => [a.id, a])), [animals]);
  const grupAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const g of groups) map.set(g.id, g.name);
    return (id: number | null) => (id === null ? '—' : map.get(id) ?? `#${id}`);
  }, [groups]);

  const araliktaki = useMemo(
    () => milk.filter(m => m.milked_on >= baslangic && m.milked_on <= bitis),
    [milk, baslangic, bitis],
  );

  /**
   * Aralık toplamı ve günlük ortalama.
   *
   * Bölen SAĞIM YAPILAN GÜN SAYISI — aralıktaki tüm günler DEĞİL. Kayıt
   * girilmemiş günleri bölene katmak, kayıt tutmayan bir işletmenin verimini
   * olduğundan düşük gösterirdi.
   */
  const sutOzet = useMemo(() => {
    const toplam = araliktaki.reduce((acc, m) => acc.plus(decimal(m.quantity_liters)), decimal(0));
    const gunler = new Set(araliktaki.map(m => m.milked_on));
    return {
      toplam,
      kayitliGun: gunler.size,
      gunlukOrtalama: gunler.size ? toplam.div(gunler.size) : null,
    };
  }, [araliktaki]);

  const aramaTerimi = arama.trim().toLocaleLowerCase('tr-TR');
  const arananSut = useMemo(() => {
    if (!aramaTerimi) return araliktaki;
    return araliktaki.filter(row => [
      row.animal_id ? animalLabel(hayvanKimlik.get(row.animal_id)) : grupAdi(row.group_id),
      row.milked_on, row.session, row.quantity_liters, row.notes,
    ].some(v => String(v ?? '').toLocaleLowerCase('tr-TR').includes(aramaTerimi)));
  }, [araliktaki, aramaTerimi, hayvanKimlik, grupAdi]);
  const arananTartimlar = useMemo(() => {
    if (!aramaTerimi) return weights;
    return weights.filter(row => [
      animalLabel(hayvanKimlik.get(row.animal_id)), row.weighed_on, row.weight_kg, row.notes,
    ].some(v => String(v ?? '').toLocaleLowerCase('tr-TR').includes(aramaTerimi)));
  }, [weights, aramaTerimi, hayvanKimlik]);

  // -------------------------------------------------------------------------

  const sutKaydet = async () => {
    setSaving(true);
    setDialogError('');
    try {
      await saveHerdRecord('post', '/milk-yields', {
        // HAYVAN YA DA GRUP: karşı taraf her zaman null gönderiliyor. İkisini
        // birden dolu göndermek 422 alır ve dolu gönderilmesinin tek yolu
        // formu yanlış kurmaktır.
        animal_id: milkForm.hedef === 'animal' ? Number(milkForm.animal_id) : null,
        group_id: milkForm.hedef === 'group' ? Number(milkForm.group_id) : null,
        milked_on: milkForm.milked_on,
        session: bosNull(milkForm.session),
        quantity_liters: ondalik(milkForm.quantity_liters),
        notes: bosNull(milkForm.notes),
      }, 'Süt kaydı eklenemedi.');
      setMilkDialog(false);
      setMilkForm(BOS_SUT);
      await yukle();
    } catch (err) {
      setDialogError(herdErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const tartimKaydet = async () => {
    setSaving(true);
    setDialogError('');
    try {
      await saveHerdRecord('post', '/animal-weights', {
        animal_id: Number(weightForm.animal_id),
        weighed_on: weightForm.weighed_on,
        weight_kg: ondalik(weightForm.weight_kg),
        notes: bosNull(weightForm.notes),
      }, 'Tartım eklenemedi.');
      setWeightDialog(false);
      setWeightForm(BOS_TARTIM);
      await yukle();
    } catch (err) {
      setDialogError(herdErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  // -------------------------------------------------------------------------

  const sutSutun: GridColDef[] = [
    {field: 'milked_on', headerName: 'Tarih', width: 120, valueGetter: (_v, row) => isoDate(row.milked_on)},
    {
      field: 'animal_id', headerName: 'Hayvan / sürü', flex: 1, minWidth: 180,
      valueGetter: (_v, row) => (row.animal_id
        ? animalLabel(hayvanKimlik.get(row.animal_id))
        : `${grupAdi(row.group_id)} (sürü)`),
    },
    {
      field: 'session', headerName: 'Seans', width: 110,
      valueGetter: (_v, row) => SESSIONS.find(s => s.value === row.session)?.label ?? row.session ?? '—',
    },
    {
      field: 'quantity_liters', headerName: 'Litre', width: 120,
      align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => verimQty(row.quantity_liters),
    },
  ];

  const tartimSutun: GridColDef[] = [
    {field: 'weighed_on', headerName: 'Tarih', width: 120, valueGetter: (_v, row) => isoDate(row.weighed_on)},
    {
      field: 'animal_id', headerName: 'Hayvan', flex: 1, minWidth: 180,
      valueGetter: (_v, row) => animalLabel(hayvanKimlik.get(row.animal_id)),
    },
    {
      field: 'weight_kg', headerName: 'Ağırlık (kg)', width: 140,
      align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => verimQty(row.weight_kg),
    },
  ];

  const sutGecerli =
    (milkForm.hedef === 'animal' ? milkForm.animal_id !== '' : milkForm.group_id !== '') &&
    milkForm.milked_on !== '' &&
    decimal(ondalik(milkForm.quantity_liters)).greaterThan(0);
  const tartimGecerli =
    weightForm.animal_id !== '' && weightForm.weighed_on !== '' &&
    decimal(ondalik(weightForm.weight_kg)).greaterThan(0);

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <WaterDropIcon color="primary" />
            <Typography variant="h4">Süt & Besi</Typography>
          </Stack>
          <Typography color="text.secondary">
            Sağım kayıtları ve canlı ağırlık takibi.
          </Typography>
        </Box>
        {yazabilir && (
          <Button variant="contained" startIcon={<AddIcon />}
            sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}
            onClick={() => {
              setDialogError('');
              if (tab === 0) {setMilkForm(BOS_SUT); setMilkDialog(true);}
              else {setWeightForm(BOS_TARTIM); setWeightDialog(true);}
            }}>
            {tab === 0 ? 'Süt kaydet' : 'Tartım kaydet'}
          </Button>
        )}
      </Stack>

      {error && (
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>
          {error}
        </Alert>
      )}

      <Card>
        <Tabs value={tab} onChange={(_e, v) => {setTab(v); setArama('');}} variant="scrollable" allowScrollButtonsMobile>
          <Tab label={`Süt (${milk.length})`} />
          <Tab label={`Tartım (${weights.length})`} />
        </Tabs>
      </Card>

      <TextField
        label={tab === 0 ? 'Süt kayıtlarında ara' : 'Tartımlarda ara'}
        value={arama}
        onChange={e => setArama(e.target.value)}
        size="small"
        sx={{maxWidth: 420}}
      />

      {tab === 0 ? (
        <>
          {/* TOPLAM HER ZAMAN BİR ARALIKLA BİRLİKTE: tarihsiz bir "toplam
              litre" mevsim ve laktasyon dönemi karıştığı için hiçbir soruya
              cevap vermez. */}
          <Card>
            <CardContent>
              <Stack direction={{xs: 'column', sm: 'row'}} spacing={2} alignItems={{sm: 'center'}}>
                <TextField label="Başlangıç" type="date" size="small" InputLabelProps={{shrink: true}}
                  value={baslangic} onChange={e => setBaslangic(e.target.value)} />
                <TextField label="Bitiş" type="date" size="small" InputLabelProps={{shrink: true}}
                  value={bitis} onChange={e => setBitis(e.target.value)} />
                <Box>
                  <Typography variant="body2" color="text.secondary">Aralık toplamı</Typography>
                  <Typography variant="h6" fontWeight={700}>{verimQty(sutOzet.toplam)} litre</Typography>
                </Box>
                <Box>
                  <Typography variant="body2" color="text.secondary">Kayıtlı gün başına</Typography>
                  <Typography variant="h6" fontWeight={700}>
                    {sutOzet.gunlukOrtalama === null ? 'Kayıt yok' : `${verimQty(sutOzet.gunlukOrtalama)} litre`}
                  </Typography>
                  {/* Bölenin ne olduğu YAZILI: kullanıcı ortalamanın neye
                      bölündüğünü bilmeden ona güvenemez. */}
                  <Typography variant="caption" color="text.secondary">
                    {sutOzet.kayitliGun} sağım günü (kayıt girilmemiş günler bölene katılmıyor)
                  </Typography>
                </Box>
              </Stack>
            </CardContent>
          </Card>

          <ResponsiveTable
            rows={arananSut}
            columns={sutSutun}
            loading={loading}
            cardTitle={row => (row.animal_id
              ? animalLabel(hayvanKimlik.get(row.animal_id))
              : `${grupAdi(row.group_id)} (sürü)`)}
            cardSubtitle={row => `${isoDate(row.milked_on)} · ${verimQty(row.quantity_liters)} litre`}
            cardFields={[
              {
                label: 'Seans',
                value: row => SESSIONS.find(s => s.value === row.session)?.label ?? row.session ?? '—',
              },
            ]}
            // Grup kaydında gidilecek hayvan YOK; yalnız hayvan satırı tıklanır.
            onRowClick={row => row.animal_id && navigate(`/hayvancilik/hayvanlar/${row.animal_id}`)}
          />
        </>
      ) : (
        <ResponsiveTable
          rows={arananTartimlar}
          columns={tartimSutun}
          loading={loading}
          cardTitle={row => animalLabel(hayvanKimlik.get(row.animal_id))}
          cardSubtitle={row => `${isoDate(row.weighed_on)} · ${verimQty(row.weight_kg)} kg`}
          cardFields={[{label: 'Not', value: row => row.notes || '—'}]}
          onRowClick={row => navigate(`/hayvancilik/hayvanlar/${row.animal_id}`)}
        />
      )}

      {/* --- Süt formu --- */}
      <Dialog open={milkDialog} onClose={() => !saving && setMilkDialog(false)} fullWidth maxWidth="sm">
        <DialogTitle>Süt kaydet</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            {/* TEK SEÇİM: hayvan ya da sürü. İkisi birden dolu olsaydı aynı süt
                iki kez sayılır ve hayvan başına verim yanlış çıkardı. */}
            <TextField select label="Kayıt kime ait" value={milkForm.hedef}
              helperText="Süt ya bir hayvana ya bir sürüye yazılır; ikisine birden yazılamaz (aynı süt iki kez sayılırdı)."
              onChange={e => setMilkForm(f => ({
                ...f, hedef: e.target.value as 'animal' | 'group',
                animal_id: '', group_id: '',
              }))}>
              <MenuItem value="animal">Tek hayvan</MenuItem>
              <MenuItem value="group">Sürü (bireysel kayıt tutulmuyor)</MenuItem>
            </TextField>

            {milkForm.hedef === 'animal' ? (
              <TextField select label="Hayvan" required value={milkForm.animal_id}
                onChange={e => setMilkForm(f => ({...f, animal_id: e.target.value}))}>
                {animals.filter(a => a.status === 'ACTIVE' && a.sex === 'FEMALE').map(a => (
                  <MenuItem key={a.id} value={String(a.id)}>{animalLabel(a)}</MenuItem>
                ))}
              </TextField>
            ) : (
              <TextField select label="Sürü" required value={milkForm.group_id}
                onChange={e => setMilkForm(f => ({...f, group_id: e.target.value}))}>
                {groups.filter(g => g.status === 'ACTIVE').map(g => (
                  <MenuItem key={g.id} value={String(g.id)}>{g.name} ({g.code})</MenuItem>
                ))}
              </TextField>
            )}

            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Sağım tarihi" type="date" required fullWidth InputLabelProps={{shrink: true}}
                value={milkForm.milked_on}
                onChange={e => setMilkForm(f => ({...f, milked_on: e.target.value}))} />
              <TextField select label="Seans" fullWidth value={milkForm.session}
                onChange={e => setMilkForm(f => ({...f, session: e.target.value}))}>
                {SESSIONS.map(s => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
              </TextField>
            </Stack>
            <TextField label="Miktar (litre)" required inputMode="decimal"
              value={milkForm.quantity_liters}
              onChange={e => setMilkForm(f => ({...f, quantity_liters: e.target.value}))} />
            <TextField label="Not" multiline minRows={2} value={milkForm.notes}
              onChange={e => setMilkForm(f => ({...f, notes: e.target.value}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMilkDialog(false)} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void sutKaydet()} disabled={saving || !sutGecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>

      {/* --- Tartım formu --- */}
      <Dialog open={weightDialog} onClose={() => !saving && setWeightDialog(false)} fullWidth maxWidth="xs">
        <DialogTitle>
          <Stack direction="row" spacing={1} alignItems="center">
            <MonitorWeightIcon fontSize="small" />
            <span>Tartım kaydet</span>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField select label="Hayvan" required value={weightForm.animal_id}
              onChange={e => setWeightForm(f => ({...f, animal_id: e.target.value}))}>
              {animals.filter(a => a.status === 'ACTIVE').map(a => (
                <MenuItem key={a.id} value={String(a.id)}>{animalLabel(a)}</MenuItem>
              ))}
            </TextField>
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
          <Button variant="contained" onClick={() => void tartimKaydet()} disabled={saving || !tartimGecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
