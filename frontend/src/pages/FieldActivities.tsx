/**
 * Faaliyetler & Girdiler (`/tarla/faaliyetler`).
 *
 * Ekranın taşıdığı iki kural doğrudan kullanıcıya görünür:
 *
 * 1. **Toplam maliyet yazılamaz, hesaplanır.** Girdi formunda "toplam" diye
 *    bir kutu YOK; miktar × birim fiyat sunucuda çarpılır. Formda gösterilen
 *    tutar yalnız bir ÖNİZLEMEDİR ve öyle etiketlenmiştir — kaydedilen değer
 *    sunucunun hesabıdır.
 *
 * 2. **Alan aşımı reddedilmez, gerekçe istenir.** Bir parsele ikinci geçiş
 *    yapmak gerçek bir durum; sessizce geçmesi ise dekar başına maliyeti
 *    bozar. Form gerekçe kutusunu ancak aşım varsa açar.
 *
 * Girdi bağlama ayrı bir izne (`farm.inputs`) bağlıdır: depo rolü girdi
 * girebilir ama faaliyet açamaz.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogActions,
  DialogContent, DialogTitle, Divider, MenuItem, Stack, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import CloudOffIcon from '@mui/icons-material/CloudOff';
import ScienceIcon from '@mui/icons-material/Science';
import SpaIcon from '@mui/icons-material/Spa';
import type {GridColDef} from '@mui/x-data-grid';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {
  activityTypeLabel, ACTIVITY_TYPES, catalogueMatch, DEFAULT_FARM_POLICIES,
  farmErrorText, fetchPlantProtectionProducts, isoDateTime, money, qty, todayIso,
  type ActivityInput, type CropSeason, type FarmPolicySettings,
  type FieldActivity, type Page, type PlantProtectionProduct,
} from '../farm/farmApi';
import {flushFarmOutbox, readFarmOutbox, saveOrQueueFarmCreate} from '../farm/farmOutbox';
import {decimal, moneyDecimal} from '../utils/documentMoney';
const TOUCH_TARGET_STACK_SX = {
  '& button': {minHeight: 44, minWidth: 44},
  '& [role="button"]': {minHeight: 44, minWidth: 44},
  '& a[href]': {display: 'inline-flex', alignItems: 'center', minHeight: 44, minWidth: 44},
  '& input': {minHeight: 44},
  '& .MuiInputBase-root': {minHeight: 44},
  '& .MuiInputBase-input': {minHeight: 44},
  '& .MuiInputBase-inputMultiline': {minHeight: 44},
};
const TOUCH_TARGET_DIALOG_SX = {
  '& button': {minHeight: 44, minWidth: 44},
  '& [role="button"]': {minHeight: 44, minWidth: 44},
  '& a[href]': {display: 'inline-flex', alignItems: 'center', minHeight: 44, minWidth: 44},
  '& input': {minHeight: 44},
  '& .MuiInputBase-root': {minHeight: 44},
  '& .MuiInputBase-input': {minHeight: 44},
  '& .MuiInputBase-inputMultiline': {minHeight: 44},
};

type ActivityForm = {
  season_id: string; activity_type: string; performed_at: string;
  applied_area_decare: string; area_override_reason: string;
  reentry_interval_days: string; preharvest_interval_days: string;
  reentry_override_reason: string; notes: string;
  // BKÜ kataloğundan seçilen ürün ve ona bağlı girdi satırı. Ürün seçilince
  // bekleme süreleri ÖNERİ olarak dolar; kullanıcı üstüne yazabilir.
  //
  // Girdi satırı BİRLİKTE gidiyor ve bu bilinçli: yalnız süreyi doldurup ürünü
  // göndermeseydik sunucu değeri OPERATOR kökenli sayardı ve ekranda
  // "katalogdan geldi" yazarken kayıtta "operatör yazdı" görünürdü.
  ppp_product_id: string;
  input_name: string; input_quantity: string; input_unit: string;
  input_dose: string; input_dose_unit: string;
};

const activityInputTotal = (inputs: ActivityInput[]) => {
  if (inputs.some(input => input.total_cost === null)) return null;
  return moneyDecimal(
    inputs.reduce((total, input) => total.plus(decimal(input.total_cost)), decimal(0)),
  ).toFixed(2);
};
type InputForm = {input_name: string; quantity: string; unit: string; unit_cost: string; dose: string; dose_unit: string};

const bosFaaliyet = (): ActivityForm => ({
  season_id: '', activity_type: 'SPRAYING', performed_at: `${todayIso()}T08:00`,
  applied_area_decare: '', area_override_reason: '',
  reentry_interval_days: '', preharvest_interval_days: '', reentry_override_reason: '', notes: '',
  ppp_product_id: '', input_name: '', input_quantity: '', input_unit: '',
  input_dose: '', input_dose_unit: '',
});
const BOS_GIRDI: InputForm = {input_name: '', quantity: '', unit: '', unit_cost: '', dose: '', dose_unit: ''};

const bosNull = (v: string) => (v.trim() === '' ? null : v.trim());
const sayiNull = (v: string) => (v.trim() === '' ? null : Number(v));
const ondalik = (v: string) => v.trim().replace(',', '.');

export default function FieldActivities() {
  const {can, activeCompany} = useAuth();
  const companyId = activeCompany?.id ?? 0;
  const faaliyetYazabilir = can('farm.manage');
  const girdiYazabilir = can('farm.inputs');

  const [activities, setActivities] = useState<FieldActivity[]>([]);
  const [seasons, setSeasons] = useState<CropSeason[]>([]);
  const [policies, setPolicies] = useState<FarmPolicySettings>(DEFAULT_FARM_POLICIES);
  const [katalog, setKatalog] = useState<PlantProtectionProduct[]>([]);
  const [turFiltre, setTurFiltre] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [dialog, setDialog] = useState<{open: boolean; form: ActivityForm}>({open: false, form: bosFaaliyet()});
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  /** Seçili faaliyetin detayı + girdileri. */
  const [detay, setDetay] = useState<{activity: FieldActivity; inputs: ActivityInput[]} | null>(null);
  const [girdiForm, setGirdiForm] = useState<InputForm>(BOS_GIRDI);
  const [girdiHata, setGirdiHata] = useState('');
  /** Cihazda gönderilmeyi bekleyen kayıt sayısı. */
  const [bekleyen, setBekleyen] = useState(0);

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params: Record<string, unknown> = {limit: 200};
      if (turFiltre) params.activity_type = turFiltre;
      const [a, s, p, k] = await Promise.all([
        api.get<Page<FieldActivity>>('/field-activities', {params}),
        api.get<Page<CropSeason>>('/crop-seasons', {params: {limit: 200}}),
        api.get<FarmPolicySettings>('/company-settings'),
        // Katalog BOŞ GELEBİLİR ve bu normal: firma henüz doldurmamıştır.
        // Boş katalog seçiciyi gizler, ekranı bozmaz.
        fetchPlantProtectionProducts({limit: 200, status: 'ACTIVE'}),
      ]);
      setActivities(a.data.items);
      setSeasons(s.data.items);
      setPolicies({...DEFAULT_FARM_POLICIES, ...p.data});
      setKatalog(k.items);
    } catch (err) {
      setError(farmErrorText(err, 'Faaliyetler yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, [turFiltre, companyId]);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  // Şebeke geri geldiğinde kuyruğu boşalt. Kullanıcı hiçbir şey yapmasa bile
  // tetiklenir — sahadan çıkıp kapsama alanına giren kişi için tek gerçekçi yol.
  useEffect(() => {
    if (!companyId) return undefined;
    const bosalt = async () => {
      const sonuc = await flushFarmOutbox(companyId);
      setBekleyen((await readFarmOutbox(companyId)).filter(e => !e.rejectedReason).length);
      if (sonuc.sent > 0) await yukle();
    };
    void bosalt();
    globalThis.addEventListener('online', bosalt);
    return () => globalThis.removeEventListener('online', bosalt);
  }, [companyId, yukle]);

  const sezonBilgi = useMemo(() => {
    const map = new Map<number, CropSeason>();
    for (const s of seasons) map.set(s.id, s);
    return map;
  }, [seasons]);
  const sezonAdi = (id: number) => {
    const s = sezonBilgi.get(id);
    return s ? `${s.crop} · ${s.season_year}` : `#${id}`;
  };

  const detayAc = async (row: FieldActivity) => {
    setGirdiHata('');
    setGirdiForm(BOS_GIRDI);
    try {
      const {data} = await api.get<FieldActivity & {inputs: ActivityInput[]}>(`/field-activities/${row.id}`);
      const {inputs, ...activity} = data;
      setDetay({activity: activity as FieldActivity, inputs: inputs || []});
    } catch (err) {
      setError(farmErrorText(err, 'Faaliyet detayı açılamadı.'));
    }
  };

  const faaliyetKaydet = async () => {
    setSaving(true);
    setDialogError('');
    const f = dialog.form;
    try {
      const sonuc = await saveOrQueueFarmCreate(companyId, 'activity', {
        season_id: Number(f.season_id),
        activity_type: f.activity_type,
        // `datetime-local` saat dilimi taşımaz; sunucu ISO bekliyor. Tarayıcının
        // yerel saatini UTC'ye çevirerek gönderiyoruz, yoksa kayıt saat farkı
        // kadar kayardı.
        performed_at: new Date(f.performed_at).toISOString(),
        applied_area_decare: f.applied_area_decare.trim() === '' ? null : ondalik(f.applied_area_decare),
        area_override_reason: bosNull(f.area_override_reason),
        reentry_interval_days: sayiNull(f.reentry_interval_days),
        preharvest_interval_days: sayiNull(f.preharvest_interval_days),
        reentry_override_reason: bosNull(f.reentry_override_reason),
        notes: bosNull(f.notes),
        // Ürün seçildiyse girdi satırı AYNI istekte gidiyor; sunucu kökeni
        // ondan çözüyor. Seçilmediyse alan hiç gönderilmiyor — boş bir
        // `inputs` dizisi göndermek "girdisiz faaliyet" demekle aynı değil.
        ...(f.ppp_product_id === '' ? {} : {inputs: [{
          product_id: Number(f.ppp_product_id),
          input_name: f.input_name.trim(),
          quantity: ondalik(f.input_quantity),
          unit: f.input_unit.trim(),
          dose: f.input_dose.trim() === '' ? null : ondalik(f.input_dose),
          dose_unit: bosNull(f.input_dose_unit),
        }]}),
      });
      setDialog({open: false, form: bosFaaliyet()});
      if (sonuc.durum === 'queued') {
        // BİLEREK "kaydedildi" DEMİYORUZ: kayıt henüz cihazda.
        setBekleyen(b => b + 1);
        setDialogError('');
      }
      if (sonuc.durum === 'sent') await yukle();
    } catch (err) {
      setDialogError(farmErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const girdiEkle = async () => {
    if (!detay) return;
    setSaving(true);
    setGirdiHata('');
    try {
      // DİKKAT: `total_cost` GÖNDERİLMİYOR. Şema onu kabul etmiyor; sunucu
      // miktar × birim fiyat ile kendisi hesaplıyor.
      const sonuc = await saveOrQueueFarmCreate(companyId, 'activity_input', {
        input_name: girdiForm.input_name.trim(),
        quantity: ondalik(girdiForm.quantity),
        unit: girdiForm.unit.trim(),
        unit_cost: girdiForm.unit_cost.trim() === '' ? null : ondalik(girdiForm.unit_cost),
        dose: girdiForm.dose.trim() === '' ? null : ondalik(girdiForm.dose),
        dose_unit: bosNull(girdiForm.dose_unit),
      }, detay.activity.id);
      setGirdiForm(BOS_GIRDI);
      if (sonuc.durum === 'queued') setBekleyen(b => b + 1);
      else await detayAc(detay.activity);
    } catch (err) {
      setGirdiHata(farmErrorText(err, 'Girdi eklenemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const sutunlar: GridColDef[] = [
    {field: 'performed_at', headerName: 'Zaman', width: 160, valueGetter: (_v, row) => isoDateTime(row.performed_at)},
    {field: 'activity_type', headerName: 'Faaliyet', width: 150, valueGetter: (_v, row) => activityTypeLabel(row.activity_type)},
    {field: 'season_id', headerName: 'Sezon', flex: 1, minWidth: 170, valueGetter: (_v, row) => sezonAdi(row.season_id)},
    {field: 'applied_area_decare', headerName: 'Alan (dekar)', width: 140, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => qty(row.applied_area_decare)},
    {field: 'preharvest_interval_days', headerName: 'Hasat bekleme', width: 150,
      valueGetter: (_v, row) => (row.preharvest_interval_days === null ? '—' : `${row.preharvest_interval_days} gün`)},
  ];
  const girdiKolonlari: GridColDef[] = [
    {field: 'input_name', headerName: 'Girdi', flex: 1, minWidth: 140},
    {field: 'quantity', headerName: 'Miktar', width: 130, align: 'right', headerAlign: 'right', valueGetter: (_v, row) => `${qty(row.quantity)} ${row.unit}`},
    {field: 'unit_cost', headerName: 'Birim fiyat', width: 130, align: 'right', headerAlign: 'right', valueGetter: (_v, row) => money(row.unit_cost)},
    {field: 'total_cost', headerName: 'Toplam', width: 130, align: 'right', headerAlign: 'right', valueGetter: (_v, row) => money(row.total_cost)},
    {field: 'dose', headerName: 'Doz', width: 130, valueGetter: (_v, row) => row.dose === null ? '—' : `${qty(row.dose)} ${row.dose_unit || ''}`},
  ];

  const seciliSezon = sezonBilgi.get(Number(dialog.form.season_id));
  /** Sezonun ekilen alanı biliniyorsa aşımı ÖNCEDEN yakala. Bilinmiyorsa
   *  sunucu parselin alanına bakar; burada sessiz kalmak doğru. */
  const alanAsiyor =
    !!seciliSezon?.planted_area_decare &&
    dialog.form.applied_area_decare.trim() !== '' &&
    Number(ondalik(dialog.form.applied_area_decare)) > Number(seciliSezon.planted_area_decare);
  const alanBloklu = alanAsiyor && policies.farm_area_override_policy === 'block';
  const alanGerekceIstiyor = alanAsiyor && policies.farm_area_override_policy === 'require_reason';

  /** İlaçlamada uygulanan alan ZORUNLU (konu #2 değişmezi). Sunucu da
   *  reddediyor; burada önden söylemek kullanıcıyı 422 duvarından kurtarır.
   *  Diğer türlerde alan opsiyonel kalıyor. */
  const ilaclama = dialog.form.activity_type === 'SPRAYING';
  const alanEksik = ilaclama && dialog.form.applied_area_decare.trim() === '';

  /** Katalogda satırı olan ürünler, ürün başına tek kere. */
  const katalogUrunleri = useMemo(() => {
    const map = new Map<number, string>();
    for (const k of katalog) {
      if (!map.has(k.product_id)) map.set(k.product_id, k.product_name ?? `#${k.product_id}`);
    }
    return [...map.entries()].map(([id, ad]) => ({id, ad}));
  }, [katalog]);

  /**
   * Ürün (ya da sezon) değişince bekleme sürelerini ÖNERİ olarak doldurur.
   *
   * SADECE BU OLAYDA doluyor. Bir efekt her render'da eşitleseydi, kullanıcının
   * ELLE SİLDİĞİ değer bir sonraki render'da geri gelir ve alan silinemez
   * görünürdü — testin "temizleyince sessizce geri dolmaz" iddiası tam olarak
   * bunu çiviliyor.
   */
  const katalogUygula = (urunId: string, sezonId: string) => {
    setDialog(d => {
      const temel = {...d.form, ppp_product_id: urunId, season_id: sezonId};
      if (urunId === '') {
        // Ürün seçimi KALDIRILDI: girdi alanları temizleniyor ama bekleme
        // süreleri OLDUĞU GİBİ kalıyor — kullanıcı onları elle düzenlemiş
        // olabilir ve sessizce silmek onun işini geri alırdı.
        return {...d, form: {...temel, input_name: '', input_quantity: '',
                             input_unit: '', input_dose: '', input_dose_unit: ''}};
      }
      const bitki = sezonBilgi.get(Number(sezonId))?.crop ?? '';
      const satirlar = katalog.filter(k => k.product_id === Number(urunId));
      const eslesen = catalogueMatch(satirlar, bitki);
      const ad = katalogUrunleri.find(u => u.id === Number(urunId))?.ad ?? '';
      return {...d, form: {
        ...temel,
        input_name: ad,
        preharvest_interval_days: eslesen
          ? String(eslesen.preharvest_interval_days)
          : temel.preharvest_interval_days,
        reentry_interval_days: eslesen && eslesen.reentry_interval_days !== null
          ? String(eslesen.reentry_interval_days)
          : temel.reentry_interval_days,
      }};
    });
  };

  const seciliKatalog = dialog.form.ppp_product_id === '' ? null : catalogueMatch(
    katalog.filter(k => k.product_id === Number(dialog.form.ppp_product_id)),
    seciliSezon?.crop ?? '',
  );
  const urunSecildi = dialog.form.ppp_product_id !== '';
  const girdiDozZorunlu = urunSecildi && ilaclama && policies.farm_spraying_dose_required;

  const faaliyetGecerli =
    dialog.form.season_id !== '' &&
    dialog.form.performed_at !== '' &&
    !alanEksik &&
    !alanBloklu &&
    (!alanGerekceIstiyor || dialog.form.area_override_reason.trim() !== '') &&
    (!urunSecildi || (
      dialog.form.input_name.trim() !== '' &&
      dialog.form.input_quantity.trim() !== '' &&
      dialog.form.input_unit.trim() !== '' &&
      (!girdiDozZorunlu ||
        (dialog.form.input_dose.trim() !== '' && dialog.form.input_dose_unit.trim() !== ''))
    ));

  /** İlaçlama faaliyetine bağlanan girdide doz ve doz birimi ZORUNLU.
   *  Miktar tek başına dekar başına kullanımı vermez. */
  const girdiIlaclama = detay?.activity.activity_type === 'SPRAYING';
  const dozZorunlu = !!girdiIlaclama && policies.farm_spraying_dose_required;
  const girdiGecerli =
    girdiForm.input_name.trim() !== '' &&
    Number(ondalik(girdiForm.quantity)) > 0 &&
    girdiForm.unit.trim() !== '' &&
    (!dozZorunlu || (girdiForm.dose.trim() !== '' && girdiForm.dose_unit.trim() !== ''));

  /** Yalnız ÖNİZLEME — kaydedilen değer sunucunun hesabıdır. */
  const onizlemeTutar =
    girdiForm.unit_cost.trim() === '' || girdiForm.quantity.trim() === ''
      ? null
      : Number(ondalik(girdiForm.quantity)) * Number(ondalik(girdiForm.unit_cost));
  const detayToplam = detay ? activityInputTotal(detay.inputs) : null;

  return (
    <Stack spacing={2} sx={TOUCH_TARGET_STACK_SX}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <SpaIcon color="primary" />
            <Typography variant="h4">Faaliyetler & Girdiler</Typography>
          </Stack>
          <Typography color="text.secondary">
            Gübreleme, ilaçlama, sulama kayıtları ve kullanılan girdiler. Sezon maliyeti buradan birikir.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <TextField select size="small" label="Tür" value={turFiltre} sx={{minWidth: 160}}
            onChange={e => setTurFiltre(e.target.value)}>
            <MenuItem value="">Tümü</MenuItem>
            {ACTIVITY_TYPES.map(t => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
          </TextField>
          {faaliyetYazabilir && (
            <Button variant="contained" startIcon={<AddIcon />} sx={{minHeight: {xs: 44}}}
              onClick={() => {
                setDialogError('');
                setDialog({open: true, form: bosFaaliyet()});
              }}>
              Yeni faaliyet
            </Button>
          )}
        </Stack>
      </Stack>

      {error && <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()} sx={{minHeight:{xs:44}}}>Yeniden dene</Button>}>{error}</Alert>}

      {bekleyen > 0 && (
        <Alert severity="warning" icon={<CloudOffIcon />}
          action={<Button color="inherit" size="small" onClick={async () => {
            const sonuc = await flushFarmOutbox(companyId);
            setBekleyen((await readFarmOutbox(companyId)).filter(e => !e.rejectedReason).length);
            if (sonuc.sent > 0) await yukle();
          }} sx={{minHeight:{xs:44}}}>Şimdi gönder</Button>}>
          Cihazda gönderilmeyi bekleyen {bekleyen} kayıt var. Bağlantı gelince
          kendiliğinden gönderilir; uygulamayı kapatsanız da kaybolmaz.
        </Alert>
      )}

      {!loading && !seasons.length && (
        <Alert severity="info">Önce bir ekim sezonu açın — faaliyet sezona bağlanır.</Alert>
      )}

      <Box data-testid="field-activities-data-surface">
      <ResponsiveTable
        rows={activities}
        columns={sutunlar}
        loading={loading}
        cardTitle={row => activityTypeLabel(row.activity_type)}
        cardSubtitle={row => `${sezonAdi(row.season_id)} · ${isoDateTime(row.performed_at)}`}
        cardFields={[
          {label: 'Alan', value: row => (row.applied_area_decare === null ? '—' : `${qty(row.applied_area_decare)} dekar`)},
          {label: 'Hasat bekleme', value: row => (row.preharvest_interval_days === null ? '—' : `${row.preharvest_interval_days} gün`)},
        ]}
        cardActions={[{label: 'Girdiler', icon: <ScienceIcon />, onClick: row => void detayAc(row)}]}
        cardActionsOnDesktop
        onRowClick={row => void detayAc(row)}
      />
      </Box>

      {/* --- Yeni faaliyet --- */}
    <Dialog open={dialog.open} onClose={() => !saving && setDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm" PaperProps={{sx: TOUCH_TARGET_DIALOG_SX}}>
        <DialogTitle>Yeni faaliyet</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField select label="Sezon" required value={dialog.form.season_id}
              onChange={e => katalogUygula(dialog.form.ppp_product_id, e.target.value)}>
              {seasons.map(s => (
                <MenuItem key={s.id} value={String(s.id)}>{s.crop} · {s.season_year}</MenuItem>
              ))}
            </TextField>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField select label="Tür" required fullWidth value={dialog.form.activity_type}
                onChange={e => setDialog(d => ({...d, form: {...d.form, activity_type: e.target.value}}))}>
                {ACTIVITY_TYPES.map(t => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
              </TextField>
              <TextField label="Zaman" type="datetime-local" required fullWidth InputLabelProps={{shrink: true}}
                value={dialog.form.performed_at}
                onChange={e => setDialog(d => ({...d, form: {...d.form, performed_at: e.target.value}}))} />
            </Stack>
            <TextField
              label={ilaclama ? 'Uygulanan alan (dekar)' : 'Uygulanan alan (dekar) — opsiyonel'}
              required={ilaclama}
              error={alanEksik}
              helperText={alanEksik
                ? 'İlaçlamada zorunlu: doz ve kalıntı hesabı buna dayanır.'
                : ' '}
              inputMode="decimal" value={dialog.form.applied_area_decare}
              onChange={e => setDialog(d => ({...d, form: {...d.form, applied_area_decare: e.target.value}}))} />
            {alanAsiyor && (
              <>
                <Alert severity={alanBloklu ? 'error' : 'warning'}>
                  Uygulanan alan sezonun ekilen alanını ({qty(seciliSezon?.planted_area_decare)} dekar) aşıyor.
                  {alanBloklu
                    ? ' Firma politikası alan aşımına izin vermiyor.'
                    : policies.farm_area_override_policy === 'allow'
                      ? ' Firma politikası bu aşımı gerekçesiz kabul ediyor.'
                      : ' Bu bir hata olmayabilir, ancak gerekçesi kayda geçmeli.'}
                </Alert>
                {alanGerekceIstiyor && (
                  <TextField label="Aşım gerekçesi" required value={dialog.form.area_override_reason}
                    placeholder="Örn. ikinci geçiş yapıldı"
                    onChange={e => setDialog(d => ({...d, form: {...d.form, area_override_reason: e.target.value}}))} />
                )}
              </>
            )}
            {katalogUrunleri.length > 0 && (
              <>
                <TextField select label="BKÜ ürünü (kataloğdan)" value={dialog.form.ppp_product_id}
                  helperText="Seçerseniz bekleme süreleri kataloğdan önerilir; üstüne yazabilirsiniz."
                  onChange={e => katalogUygula(e.target.value, dialog.form.season_id)}>
                  <MenuItem value="">Seçilmedi</MenuItem>
                  {katalogUrunleri.map(u => (
                    <MenuItem key={u.id} value={String(u.id)}>{u.ad}</MenuItem>
                  ))}
                </TextField>
                {urunSecildi && (
                  <>
                    {seciliKatalog ? (
                      <Alert severity="info">
                        Katalog {seciliKatalog.crop.trim() === ''
                          ? 'bütün bitkiler için'
                          : `"${seciliKatalog.crop}" için`} {seciliKatalog.preharvest_interval_days} gün
                        hasat bekleme söylüyor. Aşağıdaki alanı değiştirirseniz sizin
                        değeriniz geçerli olur ve bu değişiklik kayda geçer.
                      </Alert>
                    ) : (
                      <Alert severity="warning">
                        Bu ürünün bu bitki için kataloğda kaydı yok; bekleme süresini
                        elle girin.
                      </Alert>
                    )}
                    <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
                      <TextField label="Miktar" required inputMode="decimal" fullWidth
                        value={dialog.form.input_quantity}
                        onChange={e => setDialog(d => ({...d, form: {...d.form, input_quantity: e.target.value}}))} />
                      <TextField label="Birim" required fullWidth value={dialog.form.input_unit}
                        onChange={e => setDialog(d => ({...d, form: {...d.form, input_unit: e.target.value}}))} />
                    </Stack>
                    {girdiDozZorunlu && (
                      <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
                        <TextField label="Doz" required inputMode="decimal" fullWidth
                          value={dialog.form.input_dose}
                          onChange={e => setDialog(d => ({...d, form: {...d.form, input_dose: e.target.value}}))} />
                        <TextField label="Doz birimi" required fullWidth
                          value={dialog.form.input_dose_unit}
                          onChange={e => setDialog(d => ({...d, form: {...d.form, input_dose_unit: e.target.value}}))} />
                      </Stack>
                    )}
                  </>
                )}
              </>
            )}
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Tarlaya giriş yasağı (gün)" type="number" fullWidth value={dialog.form.reentry_interval_days}
                onChange={e => setDialog(d => ({...d, form: {...d.form, reentry_interval_days: e.target.value}}))} />
              <TextField label="Hasat bekleme (gün)" type="number" fullWidth value={dialog.form.preharvest_interval_days}
                inputProps={{'aria-label': 'Hasat bekleme (gün)'}}
                onChange={e => setDialog(d => ({...d, form: {...d.form, preharvest_interval_days: e.target.value}}))} />
            </Stack>
            <TextField label="Giriş yasağı gerekçesi" value={dialog.form.reentry_override_reason}
              helperText="Yasak sürerken faaliyet gerekçesiz geçmez. Boş bırakılabilir."
              onChange={e => setDialog(d => ({...d, form: {...d.form, reentry_override_reason: e.target.value}}))} />
            <TextField label="Not" multiline minRows={2} value={dialog.form.notes}
              onChange={e => setDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(d => ({...d, open: false}))} disabled={saving} sx={{minHeight:{xs:44}}}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void faaliyetKaydet()} disabled={saving || !faaliyetGecerli} sx={{minHeight:{xs:44}}}>Kaydet</Button>
        </DialogActions>
      </Dialog>

      {/* --- Girdiler --- */}
      <Dialog open={!!detay} onClose={() => !saving && setDetay(null)} fullWidth maxWidth="md" PaperProps={{sx: TOUCH_TARGET_DIALOG_SX}}>
        <DialogTitle>
          {detay && activityTypeLabel(detay.activity.activity_type)}
          <Typography variant="body2" color="text.secondary">
            {detay && `${sezonAdi(detay.activity.season_id)} · ${isoDateTime(detay.activity.performed_at)}`}
          </Typography>
        </DialogTitle>
        <DialogContent>
          {detay?.activity.area_override_reason && (
            <Alert severity="info" sx={{mb: 2}}>
              Alan aşımı gerekçesi: {detay.activity.area_override_reason}
            </Alert>
          )}
          <Card variant="outlined" sx={{mb: 2}}>
            {!detay?.inputs.length ? (
              <CardContent><Typography color="text.secondary">Bu faaliyete henüz girdi bağlanmamış.</Typography></CardContent>
            ) : (
              <>
              <Box data-testid="field-activity-detail-data-surface">
              <ResponsiveTable
                rows={detay.inputs}
                columns={girdiKolonlari}
                cardTitle={row => row.input_name}
                cardSubtitle={row => `${row.unit_cost ? `Birim ${money(row.unit_cost)}` : ''}`.trim()}
                cardFields={[
                  {label: 'Miktar', value: row => `${qty(row.quantity)} ${row.unit}`},
                  {label: 'Birim fiyat', value: row => money(row.unit_cost)},
                  {label: 'Toplam', value: row => <strong>{money(row.total_cost)}</strong>},
                  {label: 'Doz', value: row => row.dose === null ? '—' : `${qty(row.dose)} ${row.dose_unit || ''}`},
                ]}
              />
              </Box>
              <Stack
                data-testid="activity-total-row"
                direction="row"
                justifyContent="flex-end"
                alignItems="center"
                gap={2}
                px={2}
                py={1.5}
                borderTop="1px solid"
                borderColor="divider"
              >
                <Typography fontWeight={700}>Faaliyet toplamı</Typography>
                <Typography fontWeight={800}>
                  {detayToplam === null ? '—' : money(detayToplam)}
                </Typography>
              </Stack>
              </>
            )}
          </Card>

          {girdiYazabilir && (
            <>
              <Divider sx={{mb: 2}}><Chip size="small" label="Girdi ekle" /></Divider>
              {dozZorunlu && (
                <Alert severity="info" sx={{mb: 2}}>
                  İlaçlama girdisinde <strong>doz ve doz birimi zorunludur</strong> (örn. 100 ML/DEKAR).
                  Miktar tek başına dekar başına kullanımı vermez.
                </Alert>
              )}
              {girdiHata && <Alert severity="error" sx={{mb: 2}}>{girdiHata}</Alert>}
              <Stack spacing={2}>
                <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
                  <TextField label="Girdi adı" required fullWidth value={girdiForm.input_name}
                    placeholder="Herbisit" onChange={e => setGirdiForm(f => ({...f, input_name: e.target.value}))} />
                  <TextField label="Miktar" required inputMode="decimal" sx={{minWidth: 120}} value={girdiForm.quantity}
                    onChange={e => setGirdiForm(f => ({...f, quantity: e.target.value}))} />
                  <TextField label="Birim" required sx={{minWidth: 110}} value={girdiForm.unit} placeholder="LT"
                    onChange={e => setGirdiForm(f => ({...f, unit: e.target.value}))} />
                </Stack>
                <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
                  <TextField label="Birim fiyat" inputMode="decimal" fullWidth value={girdiForm.unit_cost}
                    onChange={e => setGirdiForm(f => ({...f, unit_cost: e.target.value}))} />
                  <TextField label="Doz" required={dozZorunlu} inputMode="decimal" fullWidth value={girdiForm.dose}
                    error={dozZorunlu && girdiForm.dose.trim() === ''}
                    onChange={e => setGirdiForm(f => ({...f, dose: e.target.value}))} />
                  <TextField label="Doz birimi" required={dozZorunlu} fullWidth value={girdiForm.dose_unit} placeholder="ML/DEKAR"
                    error={dozZorunlu && girdiForm.dose_unit.trim() === ''}
                    onChange={e => setGirdiForm(f => ({...f, dose_unit: e.target.value}))} />
                </Stack>
                {/* Toplam kutusu BİLEREK yok: sunucu hesaplıyor. Aşağıdaki satır
                    bir önizlemedir, gönderilen veriye dahil değildir. */}
                <Typography variant="caption" color="text.secondary">
                  {onizlemeTutar === null
                    ? 'Toplam tutar, birim fiyat girilirse sunucuda hesaplanır.'
                    : `Önizleme — sunucunun hesaplayacağı toplam: ${money(onizlemeTutar)}`}
                </Typography>
                <Box>
                  <Button variant="contained" startIcon={<AddIcon />} disabled={saving || !girdiGecerli}
                    onClick={() => void girdiEkle()} sx={{minHeight: {xs: 44}}}>
                    Girdiyi ekle
                  </Button>
                </Box>
              </Stack>
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDetay(null)} disabled={saving} sx={{minHeight:{xs:44}}}>Kapat</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
