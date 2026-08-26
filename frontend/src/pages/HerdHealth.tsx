/**
 * Sürü Sağlığı (`/hayvancilik/saglik`) — zorunlu aşı takvimi ve aşı kayıtları.
 *
 * Konu: mobil-erp#17, FAZ 3 + FAZ 4.
 *
 * Dört karar burada:
 *
 * 1. **Aşı yazma AYRI izne bağlı (`herd.health`).** Veteriner aşı girebilmeli
 *    ama hayvan alıp satamamalı. Tek bir `herd` izni bu ikisini ayıramazdı;
 *    ekran da bu ayrımı koruyor (asıl karar backend'de).
 *
 * 2. **İKİ AYRI LİSTE, bilinçli olarak birleştirilmedi (FAZ 4).**
 *    *Zorunlu takvim* doğum tarihinden ve dozlardan TÜRETİLİR: "mevzuat gereği
 *    yapılmalıydı". *Elle plan* ise kullanıcının girdiği tekrar tarihine bakar:
 *    "planladım, yapmadım". Tek listede toplamak, hiç plan girmemiş bir
 *    işletmeye "aşınız tamam" demek olurdu.
 *
 * 3. **Pencere ARALIK olarak gösterilir.** Şap tekrarı 4–6 ay, brusella ikinci
 *    doz 4–12 ay. Tek tarih göstermek ya erken uyarır (kullanıcı uyarılara
 *    güvenmeyi bırakır) ya zorunlu pencereyi kaçırtır.
 *
 * 4. **Hesabın KAPSAMADIĞI şeyler ekranda yazılı.** Kodsuz aşı kaydı ve doğum
 *    tarihi girilmemiş hayvan takvime girmiyor; bu sayılar gizlenirse "eksiği
 *    yok" cevabı yanlış bir güvence olur.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {
  Alert, Box, Button, Card, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, MenuItem, Stack, Tab, Tabs, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import VaccinesIcon from '@mui/icons-material/Vaccines';
import type {GridColDef} from '@mui/x-data-grid';

import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  CALENDAR_STATES, VACCINE_CODES,
  animalLabel, bosNull, fetchAllHerd, fetchCalendar, herdErrorText, isoDate,
  saveHerdRecord, type Animal, type CalendarRow, type Vaccination,
  type VaccinationCalendar,
} from '../herd/herdApi';

type Form = {
  animal_id: string; vaccine: string; vaccine_code: string; applied_on: string;
  dose_no: string; next_due_on: string; veterinarian: string; batch_no: string;
  notes: string;
};

type VaccinationCalendarTableRow = CalendarRow & {id: string};

const bugunIso = () => {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

const BOS: Form = {
  animal_id: '', vaccine: '', vaccine_code: '', applied_on: bugunIso(),
  dose_no: '', next_due_on: '', veterinarian: '', batch_no: '', notes: '',
};

/** Zorunlu aşı seçilince adı da otomatik dolsun — kod ile ad ayrışmasın. */
const KOD_ADI: Record<string, string> = {FMD: 'Şap', BRUCELLA: 'Brusella'};

const calendarAnimalLabel = (row: VaccinationCalendarTableRow) => animalLabel({
  animal_id: row.animal_id,
  ear_tag: row.ear_tag,
  name: row.name,
});

export default function HerdHealth() {
  const {can} = useAuth();
  const navigate = useNavigate();
  const yazabilir = can('herd.health');

  const [tab, setTab] = useState(0);
  const [rows, setRows] = useState<Vaccination[]>([]);
  const [animals, setAnimals] = useState<Animal[]>([]);
  const [calendar, setCalendar] = useState<VaccinationCalendar | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [arama, setArama] = useState('');

  const [dialog, setDialog] = useState(false);
  const [form, setForm] = useState<Form>(BOS);
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [v, a, t] = await Promise.all([
        fetchAllHerd<Vaccination>('/animal-vaccinations'),
        fetchAllHerd<Animal>('/animals'),
        // Takvim SUNUCUDA hesaplanıyor; süzgeçsiz çekip sekmelerde ayırıyoruz
        // ki özet sayılar süzerken kaybolmasın.
        fetchCalendar(),
      ]);
      setRows(v);
      setAnimals(a);
      setCalendar(t);
    } catch (err) {
      setError(herdErrorText(err, 'Aşı kayıtları yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const hayvanKimlik = useMemo(() => new Map(animals.map(a => [a.id, a])), [animals]);
  const bugun = bugunIso();

  /** Vadesi geçmiş VE hayvanı hâlâ sürüde olan kayıtlar. Satılmış bir hayvanın
   *  aşı gecikmesini göstermek, kullanıcıyı yapamayacağı bir işe yönlendirir. */
  const gecikmis = useMemo(
    () => rows.filter(v =>
      v.next_due_on && v.next_due_on < bugun &&
      hayvanKimlik.get(v.animal_id)?.status === 'ACTIVE'),
    [rows, bugun, hayvanKimlik],
  );

  const kaydet = async () => {
    setSaving(true);
    setDialogError('');
    try {
      await saveHerdRecord('post', '/animal-vaccinations', {
        animal_id: Number(form.animal_id),
        vaccine: form.vaccine.trim(),
        // Kod GÖNDERİLMEZSE kayıt takvime girmez — bu bilinçli (serbest
        // metinden tahmin sahte gecikme üretirdi), ama kullanıcıya formda
        // söyleniyor.
        vaccine_code: bosNull(form.vaccine_code),
        applied_on: form.applied_on,
        dose_no: form.dose_no.trim() === '' ? null : Number(form.dose_no),
        next_due_on: bosNull(form.next_due_on),
        veterinarian: bosNull(form.veterinarian),
        batch_no: bosNull(form.batch_no),
        notes: bosNull(form.notes),
      }, 'Aşı kaydedilemedi.');
      setDialog(false);
      setForm(BOS);
      await yukle();
    } catch (err) {
      setDialogError(herdErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const sutun: GridColDef[] = [
    {
      field: 'animal_id', headerName: 'Hayvan', width: 160,
      valueGetter: (_v, row) => animalLabel(hayvanKimlik.get(row.animal_id)),
    },
    {field: 'vaccine', headerName: 'Aşı', flex: 1, minWidth: 160},
    {field: 'applied_on', headerName: 'Yapıldı', width: 120, valueGetter: (_v, row) => isoDate(row.applied_on)},
    {field: 'dose_no', headerName: 'Doz', width: 80, valueGetter: (_v, row) => row.dose_no ?? '—'},
    {
      field: 'next_due_on', headerName: 'Tekrar', width: 140,
      renderCell: params => {
        const v = params.row as Vaccination;
        if (!v.next_due_on) return <Typography variant="body2" color="text.secondary">—</Typography>;
        const gecti = v.next_due_on < bugun;
        return (
          <Chip size="small" color={gecti ? 'warning' : 'default'}
            variant={gecti ? 'filled' : 'outlined'} label={isoDate(v.next_due_on)} />
        );
      },
    },
    {field: 'veterinarian', headerName: 'Veteriner', width: 160, valueGetter: (_v, row) => row.veterinarian || '—'},
  ];

  /** Takvim satırları: gecikmişler zaten sunucuda en üstte sıralı. */
  const takvimSutun: GridColDef[] = [
    {
      field: 'ear_tag', headerName: 'Hayvan', width: 160,
      valueGetter: (_v, row) => calendarAnimalLabel(row),
    },
    {field: 'vaccine_name', headerName: 'Aşı', width: 120},
    {
      field: 'state', headerName: 'Durum', width: 150,
      renderCell: params => {
        const d = CALENDAR_STATES[params.value as string] ?? {label: params.value as string, color: 'default' as const};
        return <Chip size="small" color={d.color} label={d.label} />;
      },
    },
    {field: 'dose_no', headerName: 'Doz', width: 80, valueGetter: (_v, row) => row.dose_no ?? '—'},
    {
      // PENCERE ARALIK OLARAK gösteriliyor; tek tarihe indirmek ya erken
      // uyarır ya zorunlu pencereyi kaçırtır.
      field: 'due_from', headerName: 'Beklenen aralık', width: 210,
      valueGetter: (_v, row) =>
        row.due_from ? `${isoDate(row.due_from)} – ${isoDate(row.due_to)}` : '—',
    },
    {
      field: 'overdue_days', headerName: 'Gecikme', width: 110,
      valueGetter: (_v, row) => (row.overdue_days ? `${row.overdue_days} gün` : '—'),
    },
    {
      field: 'last_applied_on', headerName: 'Son yapılan', width: 130,
      valueGetter: (_v, row) => isoDate(row.last_applied_on),
    },
  ];

  const takvimSatirlari: VaccinationCalendarTableRow[] = (calendar?.items ?? []).map(r => ({
    ...r, id: `${r.animal_id}-${r.vaccine_code}`,
  }));
  const ozet = calendar?.summary;
  const gosterilen = tab === 0 ? rows : gecikmis;
  const aramaTerimi = arama.trim().toLocaleLowerCase('tr-TR');
  const arananKayitlar = !aramaTerimi ? gosterilen : gosterilen.filter(row => [
    animalLabel(hayvanKimlik.get(row.animal_id)), row.vaccine, row.veterinarian,
    row.batch_no, row.applied_on, row.next_due_on,
  ].some(v => String(v ?? '').toLocaleLowerCase('tr-TR').includes(aramaTerimi)));
  const arananTakvim = !aramaTerimi ? takvimSatirlari : takvimSatirlari.filter(row => [
    calendarAnimalLabel(row), row.vaccine_name, CALENDAR_STATES[row.state]?.label,
    row.basis, row.due_from, row.due_to,
  ].some(v => String(v ?? '').toLocaleLowerCase('tr-TR').includes(aramaTerimi)));
  const gecerli = form.animal_id !== '' && form.vaccine.trim() !== '' && form.applied_on !== '';

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <VaccinesIcon color="primary" />
            <Typography variant="h4">Sürü Sağlığı</Typography>
          </Stack>
          <Typography color="text.secondary">
            Yaşa göre hesaplanan zorunlu aşı takvimi ve girilmiş aşı kayıtları.
          </Typography>
        </Box>
        {yazabilir && (
          <Button variant="contained" startIcon={<AddIcon />}
            sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}
            onClick={() => {setDialogError(''); setForm(BOS); setDialog(true);}}>
            Aşı kaydet
          </Button>
        )}
      </Stack>

      {error && (
        <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>
          {error}
        </Alert>
      )}

      {/* Takvimin NEYİ KAPSAMADIĞI ekranda yazılı: gizlenirse "eksiği yok"
          cevabı yanlış bir güvence olur. */}
      {ozet && (ozet.uncoded_vaccinations > 0 || ozet.unknown_birth_date > 0) && (
        <Alert severity="warning" variant="outlined">
          Takvim hesabına <b>girmeyen</b> kayıtlar var:
          {ozet.uncoded_vaccinations > 0 && (
            <> {ozet.uncoded_vaccinations} aşı kaydında zorunlu aşı kodu seçilmemiş
            (kod olmadan hangi aşı olduğu makinece bilinemez, tahmin etmek yanlış
            gecikme üretirdi).</>
          )}
          {ozet.unknown_birth_date > 0 && (
            <> {ozet.unknown_birth_date} hayvanın doğum tarihi girilmemiş; yaş
            bilinmeden pencere hesaplanamaz — bu hayvanlar &laquo;aşısı tam&raquo;
            sayılmıyor, &laquo;hesaplanamadı&raquo; olarak işaretleniyor.</>
          )}
        </Alert>
      )}

      {ozet && (
        <Stack direction="row" flexWrap="wrap" gap={1}>
          {(['OVERDUE', 'DUE', 'UPCOMING', 'UNKNOWN'] as const).map(k => {
            const adet = k === 'OVERDUE' ? ozet.overdue
              : k === 'DUE' ? ozet.due
              : k === 'UPCOMING' ? ozet.upcoming : ozet.unknown;
            const d = CALENDAR_STATES[k];
            return (
              <Chip key={k} color={adet ? d.color : 'default'}
                variant={adet ? 'filled' : 'outlined'}
                label={`${d.label}: ${adet}`} />
            );
          })}
        </Stack>
      )}

      <Card>
        <Tabs value={tab} onChange={(_e, v) => {setTab(v); setArama('');}} variant="scrollable" allowScrollButtonsMobile>
          <Tab label={`Tüm kayıtlar (${rows.length})`} />
          <Tab label={`Elle plan geçenler (${gecikmis.length})`} />
          <Tab label={`Zorunlu takvim (${takvimSatirlari.length})`} />
        </Tabs>
      </Card>

      <TextField
        label={tab === 2 ? 'Zorunlu takvimde ara' : 'Aşı kayıtlarında ara'}
        value={arama}
        onChange={e => setArama(e.target.value)}
        size="small"
        sx={{maxWidth: 420}}
      />

      {tab === 2 ? (
        <>
          {/* İKİ LİSTE AYRI: biri türetilmiş zorunluluk, diğeri elle plan. */}
          <Alert severity="info" variant="outlined">
            Bu liste doğum tarihinden ve kaydedilmiş dozlardan <b>hesaplanır</b> —
            şap ve brusella için Bakanlıkça zorunlu takvim. &laquo;Elle plan
            geçenler&raquo; sekmesi ise yalnız sizin girdiğiniz tekrar tarihlerine
            bakar; ikisi farklı sorulara cevap verir ve birleştirilmedi.
          </Alert>
          <ResponsiveTable
            rows={arananTakvim}
            columns={takvimSutun}
            loading={loading}
            cardTitle={row => calendarAnimalLabel(row)}
            cardSubtitle={row => `${row.vaccine_name} · ${CALENDAR_STATES[row.state]?.label ?? row.state}`}
            cardFields={[
              {
                label: 'Beklenen aralık',
                value: row => (row.due_from ? `${isoDate(row.due_from)} – ${isoDate(row.due_to)}` : '—'),
              },
              {label: 'Son yapılan', value: row => isoDate(row.last_applied_on)},
              // GEREKÇE karttan da görünüyor: kullanıcı sayıya değil sebebe güvenir.
              {label: 'Dayanak', value: row => row.basis},
            ]}
            onRowClick={row => navigate(`/hayvancilik/hayvanlar/${row.animal_id}`)}
          />
        </>
      ) : (
        <ResponsiveTable
          rows={arananKayitlar}
          columns={sutun}
          loading={loading}
          cardTitle={row => animalLabel(hayvanKimlik.get(row.animal_id))}
          cardSubtitle={row => `${row.vaccine} · ${isoDate(row.applied_on)}`}
          cardFields={[
            {label: 'Doz', value: row => row.dose_no ?? '—'},
            {label: 'Tekrar', value: row => isoDate(row.next_due_on)},
            {label: 'Veteriner', value: row => row.veterinarian || '—'},
          ]}
          // Aşı kaydı DÜZENLENMİYOR (uç yok); satır hayvanın geçmişine götürür.
          onRowClick={row => navigate(`/hayvancilik/hayvanlar/${row.animal_id}`)}
        />
      )}

      <Dialog open={dialog} onClose={() => !saving && setDialog(false)} fullWidth maxWidth="sm">
        <DialogTitle>Aşı kaydet</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            {!animals.length && <Alert severity="warning">Önce hayvan kaydı oluşturun.</Alert>}
            <TextField select label="Hayvan" required value={form.animal_id}
              onChange={e => setForm(f => ({...f, animal_id: e.target.value}))}>
              {animals.filter(a => a.status === 'ACTIVE').map(a => (
                <MenuItem key={a.id} value={String(a.id)}>{animalLabel(a)}</MenuItem>
              ))}
            </TextField>
            {/* KOD, takvimin eşleşme anahtarı. Serbest metinden tahmin
                ('Şap' / 'FMD' / 'şap aşısı') sessizce yanlış cevap üretirdi;
                bu yüzden zorunlu aşılarda kod SEÇİLİR. Kapalı liste değil:
                boş bırakılırsa kayıt yine yapılır, yalnız takvime girmez. */}
            <TextField select label="Zorunlu aşı kodu" value={form.vaccine_code}
              helperText="Yalnız şap ve brusellanın yaşa bağlı takvimi var. Boş bırakılırsa kayıt yapılır ama zorunlu aşı takvimine girmez."
              onChange={e => {
                const kod = e.target.value;
                setForm(f => ({
                  ...f, vaccine_code: kod,
                  // Ad boşsa koddan doldur: kod ile ad ayrışırsa kullanıcı
                  // listede 'Şap' görüp takvimde göremez.
                  vaccine: f.vaccine.trim() === '' && KOD_ADI[kod] ? KOD_ADI[kod] : f.vaccine,
                }));
              }}>
              <MenuItem value="">— Zorunlu aşı değil / takvime girmesin —</MenuItem>
              {VACCINE_CODES.map(k => <MenuItem key={k.value} value={k.value}>{k.label}</MenuItem>)}
            </TextField>
            <TextField label="Aşı" required value={form.vaccine}
              helperText="Serbest metin: şap, brusella, septisemi, IBR…"
              onChange={e => setForm(f => ({...f, vaccine: e.target.value}))} />
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Yapıldığı tarih" type="date" required fullWidth InputLabelProps={{shrink: true}}
                value={form.applied_on}
                onChange={e => setForm(f => ({...f, applied_on: e.target.value}))} />
              <TextField label="Tekrar tarihi" type="date" fullWidth InputLabelProps={{shrink: true}}
                value={form.next_due_on}
                helperText="Girilirse gecikme takibine girer."
                onChange={e => setForm(f => ({...f, next_due_on: e.target.value}))} />
            </Stack>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Doz no" inputMode="numeric" fullWidth value={form.dose_no}
                onChange={e => setForm(f => ({...f, dose_no: e.target.value}))} />
              <TextField label="Seri / parti no" fullWidth value={form.batch_no}
                onChange={e => setForm(f => ({...f, batch_no: e.target.value}))} />
            </Stack>
            <TextField label="Veteriner" value={form.veterinarian}
              onChange={e => setForm(f => ({...f, veterinarian: e.target.value}))} />
            <TextField label="Not" multiline minRows={2} value={form.notes}
              onChange={e => setForm(f => ({...f, notes: e.target.value}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(false)} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void kaydet()} disabled={saving || !gecerli}>Kaydet</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
