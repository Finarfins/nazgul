/**
 * Hızlı Faaliyet Girişi (`/tarla/hizli-giris`).
 *
 * Konu #2'nin "Ekranlar" başlığındaki *yeni faaliyet hızlı girişi* ve
 * *ilaçlama/gübreleme özel formu* maddeleri — tek ekranda.
 *
 * NEDEN AYRI EKRAN. Faaliyetler sayfasındaki form faaliyeti açıyor, girdiyi
 * sonra ayrı bir diyalogda istiyordu: iki adım, iki istek. Sahada — traktörün
 * yanında, tek elle — bu çok fazla. Burada ilaç/gübre bilgisi faaliyetle
 * BİRLİKTE giriliyor ve sunucuya TEK istek gidiyor.
 *
 * Tek istek olması sadece hız değil DOĞRULUK meselesi: iki ayrı istek iki ayrı
 * işlemdi ve arada kesilme girdisiz bir faaliyet bırakıyordu — sezon maliyeti
 * sessizce eksik çıkıyordu. Sahada şebeke kesintisi istisna değil, kural.
 *
 * Ekran ÇEVRİMDIŞI çalışır: şebeke yoksa kayıt aynı işlem kimliğiyle kuyruğa
 * alınır (bkz. farmOutbox). Kuyruğa girdiği için de tek istek şart — iki
 * parçalı bir kayıt kuyrukta sıralama sorunu yaratırdı (girdinin bağlanacağı
 * faaliyetin sunucu kimliği henüz yok).
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Chip, Divider, MenuItem, Stack,
  TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material';
import BoltIcon from '@mui/icons-material/Bolt';
import CloudOffIcon from '@mui/icons-material/CloudOff';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AddIcon from '@mui/icons-material/Add';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import {
  DEFAULT_FARM_POLICIES, farmErrorText, money, qty, todayIso,
  type CropSeason, type FarmPolicySettings, type Page,
} from '../farm/farmApi';
import {
  cacheFarmMetadata,
  readFarmMetadata,
  type FarmSeasonSelector,
} from '../farm/farmMetadataCache';
import {flushFarmOutbox, readFarmOutbox, saveOrQueueFarmCreate} from '../farm/farmOutbox';

type Girdi = {
  input_name: string; quantity: string; unit: string;
  unit_cost: string; dose: string; dose_unit: string;
};

const bosGirdi = (): Girdi => ({input_name: '', quantity: '', unit: '', unit_cost: '', dose: '', dose_unit: ''});
const ondalik = (v: string) => v.trim().replace(',', '.');
const bosNull = (v: string) => (v.trim() === '' ? null : v.trim());

/** Bu ekran YALNIZ girdili iki türü hedefliyor; diğerleri Faaliyetler sayfasında. */
const TURLER = [
  {value: 'SPRAYING', label: 'İlaçlama'},
  {value: 'FERTILIZING', label: 'Gübreleme'},
] as const;

export default function QuickActivity() {
  const {can, activeCompany, user, loading: authLoading} = useAuth();
  const navigate = useNavigate();
  const companyId = activeCompany?.id ?? 0;
  const yazabilir = can('farm.manage');
  const metadataIdentity = useMemo(
    () => !authLoading && user && companyId && yazabilir
      ? {companyId, userId: user.id}
      : null,
    [authLoading, user, companyId, yazabilir],
  );

  const [seasons, setSeasons] = useState<FarmSeasonSelector[]>([]);
  const [policies, setPolicies] = useState<FarmPolicySettings>(DEFAULT_FARM_POLICIES);
  const [tur, setTur] = useState<'SPRAYING' | 'FERTILIZING'>('SPRAYING');
  const [seasonId, setSeasonId] = useState('');
  const [tarih, setTarih] = useState(`${todayIso()}T08:00`);
  const [alan, setAlan] = useState('');
  const [gerekce, setGerekce] = useState('');
  const [reentry, setReentry] = useState('');
  const [preharvest, setPreharvest] = useState('');
  const [reentryGerekce, setReentryGerekce] = useState('');
  const [not, setNot] = useState('');
  const [girdiler, setGirdiler] = useState<Girdi[]>([bosGirdi()]);

  const [bekleyen, setBekleyen] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [bilgi, setBilgi] = useState('');
  const [saving, setSaving] = useState(false);

  const yukle = useCallback(async () => {
    if (authLoading) return;
    if (!metadataIdentity) {
      setSeasons([]);
      setPolicies(DEFAULT_FARM_POLICIES);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    let cached: ReturnType<typeof readFarmMetadata> = null;
    try {
      cached = readFarmMetadata(metadataIdentity);
    } catch {
      // Önbellek isteğe bağlıdır; depolama kapalıysa çevrimiçi API'ye devam et.
    }
    if (cached) {
      setSeasons(cached.seasons);
      setPolicies(cached.policies);
    }
    try {
      const [seasonResponse, policyResponse] = await Promise.all([
        api.get<Page<CropSeason>>('/crop-seasons', {params: {limit: 200}}),
        api.get<FarmPolicySettings>('/company-settings'),
      ]);
      const nextPolicies = {...DEFAULT_FARM_POLICIES, ...policyResponse.data};
      setSeasons(seasonResponse.data.items);
      setPolicies(nextPolicies);
      try {
        cacheFarmMetadata(metadataIdentity, seasonResponse.data.items, nextPolicies);
      } catch {
        // Sunucu yanıtı geçerlidir; yalnız önbelleğe yazma başarısız oldu.
      }
    } catch (err) {
      if (cached) setBilgi('Şebeke yok — firma sezonları cihaz önbelleğinden açıldı.');
      else setError(farmErrorText(err, 'Sezonlar ve firma politikaları yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, [authLoading, metadataIdentity]);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  useEffect(() => {
    if (!companyId) return undefined;
    const bosalt = async () => {
      await flushFarmOutbox(companyId);
      setBekleyen((await readFarmOutbox(companyId)).filter(e => !e.rejectedReason).length);
    };
    void bosalt();
    globalThis.addEventListener('online', bosalt);
    return () => globalThis.removeEventListener('online', bosalt);
  }, [companyId]);

  const seciliSezon = useMemo(
    () => seasons.find(s => String(s.id) === seasonId),
    [seasons, seasonId],
  );

  const ilaclama = tur === 'SPRAYING';

  /** İlaçlamada uygulanan alan zorunlu (konu #2 değişmezi). */
  const alanEksik = ilaclama && alan.trim() === '';

  /** Sezonun ekilen alanı biliniyorsa aşımı önden yakala. */
  const alanAsiyor =
    !!seciliSezon?.planted_area_decare &&
    alan.trim() !== '' &&
    Number(ondalik(alan)) > Number(seciliSezon.planted_area_decare);
  const alanBloklu = alanAsiyor && policies.farm_area_override_policy === 'block';
  const alanGerekceIstiyor = alanAsiyor && policies.farm_area_override_policy === 'require_reason';

  /** Dolu sayılan girdiler: hiçbir alanı doldurulmamış satır yok sayılır ki
   *  kullanıcı boş satır yüzünden kilitlenmesin. */
  const doluGirdiler = girdiler.filter(g => Object.values(g).some(v => v.trim() !== ''));

  const girdiGecerli = (g: Girdi) =>
    g.input_name.trim() !== '' &&
    Number(ondalik(g.quantity)) > 0 &&
    g.unit.trim() !== '' &&
    (!ilaclama || !policies.farm_spraying_dose_required ||
      (g.dose.trim() !== '' && g.dose_unit.trim() !== ''));

  const gecerli =
    yazabilir &&
    seasonId !== '' &&
    tarih !== '' &&
    !alanEksik &&
    !alanBloklu &&
    (!alanGerekceIstiyor || gerekce.trim() !== '') &&
    doluGirdiler.length > 0 &&
    doluGirdiler.every(girdiGecerli);

  /** Yalnız ÖNİZLEME — kaydedilen tutar sunucunun hesabı. */
  const onizleme = doluGirdiler.reduce((t, g) => {
    const q = Number(ondalik(g.quantity));
    const c = Number(ondalik(g.unit_cost));
    return t + (Number.isFinite(q) && Number.isFinite(c) && g.unit_cost.trim() !== '' ? q * c : 0);
  }, 0);

  const kaydet = async () => {
    setSaving(true);
    setError('');
    setBilgi('');
    try {
      const sonuc = await saveOrQueueFarmCreate(companyId, 'activity', {
        season_id: Number(seasonId),
        activity_type: tur,
        performed_at: new Date(tarih).toISOString(),
        applied_area_decare: alan.trim() === '' ? null : ondalik(alan),
        area_override_reason: bosNull(gerekce),
        reentry_interval_days: reentry.trim() === '' ? null : Number(reentry),
        preharvest_interval_days: preharvest.trim() === '' ? null : Number(preharvest),
        reentry_override_reason: bosNull(reentryGerekce),
        notes: bosNull(not),
        // TEK İSTEK: girdiler faaliyetle birlikte gidiyor ve sunucuda AYNI
        // işlemde yazılıyor. `total_cost` GÖNDERİLMİYOR — sunucu hesaplıyor.
        inputs: doluGirdiler.map(g => ({
          input_name: g.input_name.trim(),
          quantity: ondalik(g.quantity),
          unit: g.unit.trim(),
          unit_cost: g.unit_cost.trim() === '' ? null : ondalik(g.unit_cost),
          dose: g.dose.trim() === '' ? null : ondalik(g.dose),
          dose_unit: bosNull(g.dose_unit),
        })),
      });
      // Formu sıfırla ama SEZON ve TÜR kalsın: sahada arka arkaya birkaç
      // parsele aynı işi girmek yaygın.
      setAlan(''); setGerekce(''); setReentryGerekce(''); setNot(''); setGirdiler([bosGirdi()]);
      if (sonuc.durum === 'sent') {
        setBilgi('Faaliyet ve girdileri kaydedildi.');
      } else {
        setBilgi('Şebeke yok — kayıt cihaza alındı, bağlantı gelince gönderilecek.');
        setBekleyen(b => b + 1);
      }
    } catch (err) {
      setError(farmErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <BoltIcon color="primary" />
            <Typography variant="h4">Hızlı Faaliyet Girişi</Typography>
          </Stack>
          <Typography color="text.secondary">
            İlaç/gübre bilgisi faaliyetle birlikte, tek adımda. Şebeke yokken de çalışır.
          </Typography>
        </Box>
        <Button variant="outlined" onClick={() => navigate('/tarla/faaliyetler')}
          sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}>
          Faaliyet listesi
        </Button>
      </Stack>

      {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}
      {bilgi && <Alert severity="success" onClose={() => setBilgi('')}>{bilgi}</Alert>}
      {bekleyen > 0 && (
        <Alert severity="warning" icon={<CloudOffIcon />}>
          Cihazda gönderilmeyi bekleyen {bekleyen} kayıt var; bağlantı gelince kendiliğinden gönderilir.
        </Alert>
      )}
      {!yazabilir && <Alert severity="info">Faaliyet kaydetme yetkiniz yok; bu ekran salt görüntülemedir.</Alert>}
      {!loading && !seasons.length && (
        <Alert severity="info">Önce bir ekim sezonu açın — faaliyet sezona bağlanır.</Alert>
      )}

      <Card>
        <CardContent>
          <Stack spacing={2}>
            <ToggleButtonGroup exclusive value={tur} onChange={(_e, v) => v && setTur(v)} fullWidth>
              {TURLER.map(t => (
                <ToggleButton key={t.value} value={t.value} sx={{minHeight: 48}}>{t.label}</ToggleButton>
              ))}
            </ToggleButtonGroup>

            <TextField select label="Sezon" required value={seasonId} disabled={!yazabilir}
              onChange={e => setSeasonId(e.target.value)}>
              {seasons.map(s => (
                <MenuItem key={s.id} value={String(s.id)}>{s.crop} · {s.season_year}</MenuItem>
              ))}
            </TextField>

            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Zaman" type="datetime-local" required fullWidth InputLabelProps={{shrink: true}}
                value={tarih} disabled={!yazabilir} onChange={e => setTarih(e.target.value)} />
              <TextField
                label={ilaclama ? 'Uygulanan alan (dekar)' : 'Uygulanan alan (dekar) — opsiyonel'}
                required={ilaclama} error={alanEksik} inputMode="decimal" fullWidth value={alan}
                disabled={!yazabilir}
                helperText={alanEksik ? 'İlaçlamada zorunlu: doz ve kalıntı hesabı buna dayanır.' : ' '}
                onChange={e => setAlan(e.target.value)} />
            </Stack>

            {alanAsiyor && (
              <>
                <Alert severity={alanBloklu ? 'error' : 'warning'}>
                  Uygulanan alan sezonun ekilen alanını ({qty(seciliSezon?.planted_area_decare)} dekar)
                  aşıyor. {alanBloklu
                    ? 'Firma politikası alan aşımına izin vermiyor.'
                    : policies.farm_area_override_policy === 'allow'
                      ? 'Firma politikası gerekçesiz kayda izin veriyor.'
                      : 'Kaydetmek için gerekçe zorunlu.'}
                </Alert>
                {alanGerekceIstiyor && (
                  <TextField label="Aşım gerekçesi" required value={gerekce} disabled={!yazabilir}
                    onChange={e => setGerekce(e.target.value)} />
                )}
              </>
            )}

            {ilaclama && (
              <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
                <TextField label="Tarlaya giriş yasağı (gün)" type="number" fullWidth value={reentry}
                  disabled={!yazabilir} helperText="Boş bırakılabilir; uydurmayın."
                  onChange={e => setReentry(e.target.value)} />
                <TextField label="Hasat bekleme (gün)" type="number" fullWidth value={preharvest}
                  disabled={!yazabilir} helperText="Girilirse erken hasat uyarısı çalışır."
                  onChange={e => setPreharvest(e.target.value)} />
              </Stack>
            )}
            <TextField label="Giriş yasağı gerekçesi" value={reentryGerekce} disabled={!yazabilir}
              helperText="Yasak sürerken faaliyet gerekçesiz geçmez. Boş bırakılabilir."
              onChange={e => setReentryGerekce(e.target.value)} />
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1.5}>
            <Typography variant="h6">{ilaclama ? 'Kullanılan ilaçlar' : 'Kullanılan gübreler'}</Typography>
            <Button size="small" startIcon={<AddIcon />} disabled={!yazabilir}
              onClick={() => setGirdiler(g => [...g, bosGirdi()])}>Satır ekle</Button>
          </Stack>
          {ilaclama && policies.farm_spraying_dose_required && (
            <Alert severity="info" sx={{mb: 2}}>
              İlaçlamada <strong>doz ve doz birimi zorunludur</strong> (örn. 100 ML/DEKAR).
              Miktar tek başına dekar başına kullanımı vermez.
            </Alert>
          )}
          <Stack spacing={2} divider={<Divider />}>
            {girdiler.map((g, i) => (
              <Stack key={i} spacing={1.5}>
                <Stack direction={{xs: 'column', sm: 'row'}} spacing={1.5}>
                  <TextField label="Ad" required fullWidth value={g.input_name} disabled={!yazabilir}
                    placeholder={ilaclama ? 'Herbisit' : 'Üre'}
                    onChange={e => setGirdiler(list => list.map((x, j) => j === i ? {...x, input_name: e.target.value} : x))} />
                  <TextField label="Miktar" required inputMode="decimal" sx={{minWidth: 110}} value={g.quantity}
                    disabled={!yazabilir}
                    onChange={e => setGirdiler(list => list.map((x, j) => j === i ? {...x, quantity: e.target.value} : x))} />
                  <TextField label="Birim" required sx={{minWidth: 100}} value={g.unit} disabled={!yazabilir}
                    placeholder={ilaclama ? 'LT' : 'KG'}
                    onChange={e => setGirdiler(list => list.map((x, j) => j === i ? {...x, unit: e.target.value} : x))} />
                </Stack>
                <Stack direction={{xs: 'column', sm: 'row'}} spacing={1.5} alignItems="center">
                  <TextField label="Doz" required={ilaclama && policies.farm_spraying_dose_required} inputMode="decimal" fullWidth value={g.dose}
                    disabled={!yazabilir} error={ilaclama && policies.farm_spraying_dose_required && doluGirdiler.includes(g) && g.dose.trim() === ''}
                    onChange={e => setGirdiler(list => list.map((x, j) => j === i ? {...x, dose: e.target.value} : x))} />
                  <TextField label="Doz birimi" required={ilaclama && policies.farm_spraying_dose_required} fullWidth value={g.dose_unit}
                    disabled={!yazabilir} placeholder="ML/DEKAR"
                    error={ilaclama && policies.farm_spraying_dose_required && doluGirdiler.includes(g) && g.dose_unit.trim() === ''}
                    onChange={e => setGirdiler(list => list.map((x, j) => j === i ? {...x, dose_unit: e.target.value} : x))} />
                  <TextField label="Birim fiyat" inputMode="decimal" fullWidth value={g.unit_cost}
                    disabled={!yazabilir}
                    onChange={e => setGirdiler(list => list.map((x, j) => j === i ? {...x, unit_cost: e.target.value} : x))} />
                  {girdiler.length > 1 && (
                    <Button color="error" startIcon={<DeleteOutlineIcon />} disabled={!yazabilir}
                      onClick={() => setGirdiler(list => list.filter((_x, j) => j !== i))}
                      sx={{minHeight: {xs: 44}}}>Sil</Button>
                  )}
                </Stack>
              </Stack>
            ))}
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Stack direction={{xs: 'column', sm: 'row'}} justifyContent="space-between" alignItems={{sm: 'center'}} gap={1.5}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <Chip size="small" variant="outlined"
                color={typeof navigator !== 'undefined' && navigator.onLine === false ? 'warning' : 'success'}
                label={typeof navigator !== 'undefined' && navigator.onLine === false
                  ? 'Şebeke yok — cihazda bekletilecek'
                  : 'Bağlantı var — hemen gönderilecek'} />
              {/* Toplam kutusu YOK: sunucu hesaplıyor. Bu bir önizleme. */}
              <Typography variant="caption" color="text.secondary">
                {onizleme > 0 ? `Önizleme — sunucunun hesaplayacağı toplam: ${money(onizleme)}` : 'Birim fiyat girilirse toplam sunucuda hesaplanır.'}
              </Typography>
            </Stack>
            <Button variant="contained" size="large" disabled={!gecerli || saving}
              onClick={() => void kaydet()} sx={{minHeight: 48, minWidth: 200}}>
              {saving ? 'Kaydediliyor…' : 'Kaydet'}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Stack>
  );
}
