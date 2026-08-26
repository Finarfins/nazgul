/**
 * Hasat Kayıtları (`/tarla/hasat`).
 *
 * Bu ekran ÇEVRİMDIŞI çalışabilen ilk tarla ekranı: hasat, kapsama alanının
 * dışında — biçerdöverin yanında — giriliyor. Kayıt önce doğrudan gönderiliyor,
 * şebeke yoksa aynı işlem kimliğiyle kuyruğa alınıyor (bkz. farmOutbox).
 *
 * KULLANICIYA NE SÖYLENDİĞİ ÖNEMLİ. "Kaydedildi" demek, kuyrukta bekleyen bir
 * kayıt için yalan olurdu; telefonu kapatıp giden kullanıcı verinin sunucuda
 * olduğunu sanır. Bu yüzden iki ayrı mesaj var: gönderildi / cihazda bekliyor.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogActions,
  DialogContent, DialogTitle, Divider, MenuItem, Stack, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import CloudOffIcon from '@mui/icons-material/CloudOff';
import GrassIcon from '@mui/icons-material/Grass';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';

import type {GridColDef} from '@mui/x-data-grid';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import ResponsiveTable from '../components/ResponsiveTable';
import {DEFAULT_FARM_POLICIES, farmErrorText, isoDate, money, qty, todayIso,
  type CropSeason, type FarmPolicySettings, type FieldSafety,
  type HarvestDecision, type Page} from '../farm/farmApi';
import {
  flushFarmOutbox, readFarmOutbox, removeFarmOperation, saveOrQueueFarmCreate,
  type FarmOutboxEntry,
} from '../farm/farmOutbox';

type Harvest = {
  id: number; season_id: number; harvested_on: string; quantity: string; unit: string;
  harvested_area_decare: string | null; quality_grade: string | null;
  moisture_percent: string | null; notes: string | null; status: string;
  sold_quantity: string | null; revenue_amount: string | null;
  safety_override_reason: string | null; safety_warning: string | null;
};

type Form = {
  season_id: string; harvested_on: string; quantity: string; unit: string;
  harvested_area_decare: string; quality_grade: string; moisture_percent: string; notes: string;
  safety_override_reason: string;
  sold_quantity: string; revenue_amount: string;
};

const bosForm = (): Form => ({
  season_id: '', harvested_on: todayIso(), quantity: '', unit: 'KG',
  harvested_area_decare: '', quality_grade: '', moisture_percent: '', notes: '',
  safety_override_reason: '', sold_quantity: '', revenue_amount: '',
});
const bosNull = (v: string) => (v.trim() === '' ? null : v.trim());
const ondalik = (v: string) => v.trim().replace(',', '.');

export default function FieldHarvests() {
  const {can, activeCompany} = useAuth();
  const yazabilir = can('farm.manage');
  const companyId = activeCompany?.id ?? 0;

  const [harvests, setHarvests] = useState<Harvest[]>([]);
  const [seasons, setSeasons] = useState<CropSeason[]>([]);
  const [kuyruk, setKuyruk] = useState<FarmOutboxEntry[]>([]);
  const [safety, setSafety] = useState<FieldSafety | null>(null);
  const [policies, setPolicies] = useState<FarmPolicySettings>(DEFAULT_FARM_POLICIES);
  const [decision, setDecision] = useState<HarvestDecision | null>(null);
  const [decisionLoading, setDecisionLoading] = useState(false);
  const [decisionError, setDecisionError] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [bilgi, setBilgi] = useState('');
  const [online, setOnline] = useState(
    () => typeof navigator === 'undefined' || navigator.onLine !== false,
  );
  const offline = !online;

  const [dialog, setDialog] = useState<{open: boolean; form: Form}>({open: false, form: bosForm()});
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  useEffect(() => {
    const markOnline = () => setOnline(true);
    const markOffline = () => setOnline(false);
    globalThis.addEventListener('online', markOnline);
    globalThis.addEventListener('offline', markOffline);
    return () => {
      globalThis.removeEventListener('online', markOnline);
      globalThis.removeEventListener('offline', markOffline);
    };
  }, []);

  const kuyrugaBak = useCallback(async () => {
    if (!companyId) return;
    setKuyruk(await readFarmOutbox(companyId));
  }, [companyId]);

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [hv, sz, gv, pv] = await Promise.all([
        api.get<Page<Harvest>>('/field-harvests', {params: {limit: 200}}),
        api.get<Page<CropSeason>>('/crop-seasons', {params: {limit: 200}}),
        api.get<FieldSafety>('/field-safety'),
        api.get<FarmPolicySettings>('/company-settings'),
      ]);
      setHarvests(hv.data.items);
      setSeasons(sz.data.items);
      setSafety(gv.data);
      setPolicies({...DEFAULT_FARM_POLICIES, ...pv.data});
    } catch (err) {
      setError(farmErrorText(err, 'Hasat kayıtları yüklenemedi.'));
    } finally {
      setLoading(false);
      await kuyrugaBak();
    }
  }, [kuyrugaBak]);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  // Şebeke geri geldiğinde kuyruğu boşalt. `online` olayı, kullanıcı hiçbir şey
  // yapmasa bile tetiklenir — sahada telefonu cebe koyup arabaya binen kişi
  // için tek gerçekçi senaryo bu.
  useEffect(() => {
    if (!companyId) return undefined;
    const bosalt = async () => {
      const sonuc = await flushFarmOutbox(companyId);
      if (sonuc.sent > 0) {
        setBilgi(`${sonuc.sent} bekleyen kayıt sunucuya gönderildi.`);
        await yukle();
      } else {
        await kuyrugaBak();
      }
    };
    void bosalt();
    globalThis.addEventListener('online', bosalt);
    return () => globalThis.removeEventListener('online', bosalt);
  }, [companyId, yukle, kuyrugaBak]);

  const sezonAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const s of seasons) map.set(s.id, `${s.crop} · ${s.season_year}`);
    return (id: number) => map.get(id) ?? `#${id}`;
  }, [seasons]);

  const seciliSezon = seasons.find(s => String(s.id) === dialog.form.season_id);

  useEffect(() => {
    if (!dialog.open || !dialog.form.season_id || !dialog.form.harvested_on) {
      setDecision(null);
      setDecisionError('');
      setDecisionLoading(false);
      return undefined;
    }
    if (offline) {
      setDecision(null);
      setDecisionError('');
      setDecisionLoading(false);
      return undefined;
    }
    let active = true;
    setDecision(null);
    setDecisionError('');
    setDecisionLoading(true);
    void api.get<HarvestDecision>('/field-harvest-decision', {params: {
      season_id: Number(dialog.form.season_id),
      harvested_on: dialog.form.harvested_on,
    }}).then(response => {
      if (active) setDecision(response.data);
    }).catch(err => {
      if (active) setDecisionError(farmErrorText(err, 'Hasat bekleme kararı alınamadı.'));
    }).finally(() => {
      if (active) setDecisionLoading(false);
    });
    return () => { active = false; };
  }, [dialog.open, dialog.form.season_id, dialog.form.harvested_on, offline]);

  const kaydet = async () => {
    setSaving(true);
    setDialogError('');
    const f = dialog.form;
    try {
      const sonuc = await saveOrQueueFarmCreate(companyId, 'harvest', {
        season_id: Number(f.season_id),
        harvested_on: f.harvested_on,
        quantity: ondalik(f.quantity),
        unit: f.unit.trim(),
        harvested_area_decare: f.harvested_area_decare.trim() === '' ? null : ondalik(f.harvested_area_decare),
        quality_grade: bosNull(f.quality_grade),
        moisture_percent: f.moisture_percent.trim() === '' ? null : ondalik(f.moisture_percent),
        notes: bosNull(f.notes),
        safety_override_reason: bosNull(f.safety_override_reason),
        // Boş bırakılırsa null gider: "gelir sıfır" değil "HENÜZ BİLİNMİYOR".
        sold_quantity: f.sold_quantity.trim() === '' ? null : ondalik(f.sold_quantity),
        revenue_amount: f.revenue_amount.trim() === '' ? null : ondalik(f.revenue_amount),
      });
      setDialog({open: false, form: bosForm()});
      if (sonuc.durum === 'sent') {
        setBilgi('Hasat kaydedildi.');
        await yukle();
      } else {
        // BİLEREK "kaydedildi" DEMİYORUZ: kayıt henüz cihazda.
        setBilgi('Şebeke yok — hasat cihaza kaydedildi, bağlantı gelince gönderilecek.');
        await kuyrugaBak();
      }
    } catch (err) {
      setDialogError(farmErrorText(err, 'Hasat kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  const sutunlar: GridColDef[] = [
    {field: 'harvested_on', headerName: 'Tarih', width: 130, valueGetter: (_v, row) => isoDate(row.harvested_on)},
    {field: 'season_id', headerName: 'Sezon', flex: 1, minWidth: 180, valueGetter: (_v, row) => sezonAdi(row.season_id)},
    {field: 'quantity', headerName: 'Miktar', width: 150, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => `${qty(row.quantity)} ${row.unit}`},
    {field: 'harvested_area_decare', headerName: 'Hasat alanı', width: 140, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => qty(row.harvested_area_decare)},
    {field: 'moisture_percent', headerName: 'Nem %', width: 110, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => qty(row.moisture_percent)},
    {field: 'quality_grade', headerName: 'Kalite', width: 120, valueGetter: (_v, row) => row.quality_grade || '—'},
    {field: 'sold_quantity', headerName: 'Satılan', width: 130, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => (row.sold_quantity === null ? 'Girilmemiş' : qty(row.sold_quantity))},
    {field: 'revenue_amount', headerName: 'Gelir', width: 140, align: 'right', headerAlign: 'right',
      valueGetter: (_v, row) => (row.revenue_amount === null ? 'Girilmemiş' : money(row.revenue_amount))},
  ];

  const bekleyen = kuyruk.filter(e => !e.rejectedReason);
  const reddedilen = kuyruk.filter(e => e.rejectedReason);

  const gecerli =
    dialog.form.season_id !== '' &&
    dialog.form.harvested_on !== '' &&
    Number(ondalik(dialog.form.quantity)) > 0 &&
    dialog.form.unit.trim() !== '';

  const phiBlocked = decision?.decision === 'blocked';
  const phiReasonRequired = decision?.decision === 'reason_required';

  /** Satılan miktar hasadı aşamaz — sunucu da reddediyor. */
  const satisAsiyor =
    dialog.form.sold_quantity.trim() !== '' &&
    dialog.form.quantity.trim() !== '' &&
    Number(ondalik(dialog.form.sold_quantity)) > Number(ondalik(dialog.form.quantity));

  /** Hasat sezon başlangıcından önce olamaz — sunucu da reddediyor. */
  const tarihErken =
    !!seciliSezon?.started_on &&
    dialog.form.harvested_on !== '' &&
    dialog.form.harvested_on < seciliSezon.started_on.slice(0, 10);

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <GrassIcon color="primary" />
            <Typography variant="h4">Hasat Kayıtları</Typography>
          </Stack>
          <Typography color="text.secondary">
            Dekar başına verim bu kayıtlardan hesaplanır. Şebeke yokken de girilebilir.
          </Typography>
        </Box>
        {yazabilir && (
          <Button variant="contained" startIcon={<AddIcon />} sx={{minHeight: {xs: 44}, alignSelf: {md: 'center'}}}
            onClick={() => {
              setDialogError('');
              setDialog({open: true, form: bosForm()});
            }}>
            Hasat gir
          </Button>
        )}
      </Stack>

      {error && <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>{error}</Alert>}
      {bilgi && <Alert severity="success" onClose={() => setBilgi('')}>{bilgi}</Alert>}

      {!!safety?.harvest_blocks.length && (
        <Alert severity="warning" icon={<WarningAmberIcon />}>
          <strong>Hasat bekleme süresi süren {safety.harvest_blocks.length} sezon var.</strong>{' '}
          {safety.harvest_blocks.map(b => `${b.crop} ${b.season_year} (${b.parcel_name}) — ${isoDate(b.safe_from)} sonrası`).join(' · ')}
        </Alert>
      )}

      {!!safety?.reentry_blocks.length && (
        <Alert severity="warning" icon={<WarningAmberIcon />}>
          <strong>Tarlaya giriş yasağı süren {safety.reentry_blocks.length} parsel var.</strong>{' '}
          {safety.reentry_blocks.map(b => `${b.parcel_name} — ${isoDate(b.safe_from)} sonrası`).join(' · ')}
          {' '}Bu uyarı hasatla ilgisi olmayan işler (sulama, gübreleme) için de geçerlidir.
        </Alert>
      )}

      {bekleyen.length > 0 && (
        <Alert
          severity="warning"
          icon={<CloudOffIcon />}
          action={
            <Button color="inherit" size="small" onClick={async () => {
              const sonuc = await flushFarmOutbox(companyId);
              if (sonuc.sent > 0) await yukle();
              else await kuyrugaBak();
            }}>
              Şimdi gönder
            </Button>
          }
        >
          Cihazda gönderilmeyi bekleyen {bekleyen.length} kayıt var. Bağlantı gelince
          kendiliğinden gönderilir; uygulamayı kapatsanız da kaybolmaz.
        </Alert>
      )}

      {reddedilen.map(e => (
        <Alert key={e.operationId} severity="error"
          action={<Button color="inherit" size="small" onClick={async () => {
            await removeFarmOperation(e.operationId);
            await kuyrugaBak();
          }}>Kuyruktan sil</Button>}>
          Bekleyen bir kayıt sunucu tarafından reddedildi: {e.rejectedReason} — veriyi
          not alıp elle tekrar girin, sonra bu satırı silin.
        </Alert>
      ))}

      {!loading && !seasons.length && (
        <Alert severity="info">Önce bir ekim sezonu açın — hasat sezona bağlanır.</Alert>
      )}

      <ResponsiveTable
        rows={harvests}
        columns={sutunlar}
        loading={loading}
        cardTitle={row => `${qty(row.quantity)} ${row.unit}`}
        cardSubtitle={row => `${sezonAdi(row.season_id)} · ${isoDate(row.harvested_on)}`}
        cardFields={[
          {label: 'Hasat alanı', value: row => (row.harvested_area_decare === null ? '—' : `${qty(row.harvested_area_decare)} dekar`)},
          {label: 'Nem', value: row => (row.moisture_percent === null ? '—' : `%${qty(row.moisture_percent)}`)},
          {label: 'Kalite', value: row => row.quality_grade || '—'},
          {label: 'Satılan', value: row => (row.sold_quantity === null ? 'Girilmemiş' : `${qty(row.sold_quantity)} ${row.unit}`)},
          {label: 'Gelir', value: row => (row.revenue_amount === null ? 'Girilmemiş' : money(row.revenue_amount))},
          {label: 'PHI', value: row => row.safety_warning || row.safety_override_reason || 'Uygun'},
        ]}
      />

      <Dialog open={dialog.open} onClose={() => !saving && setDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>Hasat gir</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField select label="Sezon" required value={dialog.form.season_id}
              onChange={e => setDialog(d => ({...d, form: {...d.form, season_id: e.target.value}}))}>
              {seasons.map(s => <MenuItem key={s.id} value={String(s.id)}>{s.crop} · {s.season_year}</MenuItem>)}
            </TextField>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Hasat tarihi" type="date" required fullWidth InputLabelProps={{shrink: true}}
                value={dialog.form.harvested_on} error={tarihErken}
                helperText={tarihErken ? `Sezon ${isoDate(seciliSezon?.started_on)} tarihinde başladı; hasat ondan önce olamaz.` : ' '}
                onChange={e => setDialog(d => ({...d, form: {...d.form, harvested_on: e.target.value}}))} />
              <TextField label="Miktar" required inputMode="decimal" fullWidth value={dialog.form.quantity}
                onChange={e => setDialog(d => ({...d, form: {...d.form, quantity: e.target.value}}))} />
              <TextField label="Birim" required sx={{minWidth: 110}} value={dialog.form.unit}
                onChange={e => setDialog(d => ({...d, form: {...d.form, unit: e.target.value}}))} />
            </Stack>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Hasat edilen alan (dekar)" inputMode="decimal" fullWidth value={dialog.form.harvested_area_decare}
                onChange={e => setDialog(d => ({...d, form: {...d.form, harvested_area_decare: e.target.value}}))} />
              <TextField label="Nem %" inputMode="decimal" fullWidth value={dialog.form.moisture_percent}
                onChange={e => setDialog(d => ({...d, form: {...d.form, moisture_percent: e.target.value}}))} />
              <TextField label="Kalite" fullWidth value={dialog.form.quality_grade}
                onChange={e => setDialog(d => ({...d, form: {...d.form, quality_grade: e.target.value}}))} />
            </Stack>
            {offline && (
              <Alert severity="warning">
                Çevrimdışı olduğunuz için seçilen tarih için canlı PHI kararı alınmadı. Kayıt cihaz kuyruğuna
                alınacak; sunucu güvenlik ve politika doğrulamasını senkronizasyon sırasında yapacaktır.
              </Alert>
            )}
            {decisionLoading && <Alert severity="info">Seçilen tarih için PHI kararı alınıyor…</Alert>}
            {decisionError && <Alert severity="error">{decisionError}</Alert>}
            {decision && (
              <>
                <Alert severity={decision.decision === 'safe' ? 'success' : decision.decision === 'warning' ? 'warning' : 'error'}
                  icon={decision.decision === 'safe' ? undefined : <WarningAmberIcon />}>
                  <strong>{isoDate(decision.harvested_on)} PHI kararı:</strong>{' '}
                  {decision.decision === 'safe'
                    ? 'Hasat bekleme süresi dolmuş.'
                    : `${decision.warning}. ${decision.decision === 'blocked'
                      ? 'Firma politikası bu tarihte hasadı engelliyor.'
                      : decision.decision === 'warning'
                        ? 'Firma politikası uyarıyla kayda izin veriyor.'
                        : 'Kaydetmek için gerekçe zorunlu.'}`}
                </Alert>
                {phiReasonRequired && (
                  <TextField label="Erken hasat gerekçesi" required
                    value={dialog.form.safety_override_reason}
                    placeholder="Örn. ilaçlama tarihi yanlış girilmişti"
                    onChange={e => setDialog(d => ({...d, form: {...d.form, safety_override_reason: e.target.value}}))} />
                )}
              </>
            )}
            <Divider><Chip size="small" label="Satış (sonradan da girilebilir)" /></Divider>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Satılan miktar" inputMode="decimal" fullWidth value={dialog.form.sold_quantity}
                error={satisAsiyor}
                helperText={satisAsiyor
                  ? 'Satılan miktar hasat miktarını aşamaz.'
                  : 'Hasadın tamamı satılmayabilir (tohumluk, yemlik, fire).'}
                onChange={e => setDialog(d => ({...d, form: {...d.form, sold_quantity: e.target.value}}))} />
              <TextField label="Elde edilen tutar (₺)" inputMode="decimal" fullWidth value={dialog.form.revenue_amount}
                helperText="Boş bırakılabilir — henüz satılmadıysa boş kalsın."
                onChange={e => setDialog(d => ({...d, form: {...d.form, revenue_amount: e.target.value}}))} />
            </Stack>
            <TextField label="Not" multiline minRows={2} value={dialog.form.notes}
              onChange={e => setDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
            <Chip
              size="small"
              variant="outlined"
              color={typeof navigator !== 'undefined' && navigator.onLine === false ? 'warning' : 'success'}
              label={typeof navigator !== 'undefined' && navigator.onLine === false
                ? 'Şebeke yok — kayıt cihazda bekletilecek'
                : 'Bağlantı var — kayıt hemen gönderilecek'}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void kaydet()}
            disabled={saving || !gecerli || tarihErken || satisAsiyor
              || (!offline && (decisionLoading || !!decisionError || !decision
                || phiBlocked
                || (phiReasonRequired && dialog.form.safety_override_reason.trim() === '')
                || (decision?.policy !== policies.farm_early_harvest_policy)))}>
            Kaydet
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
