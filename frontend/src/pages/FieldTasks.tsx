/**
 * Tarla Görevleri (`/tarla/gorevler`) — "bugün ne yapılacak" görünümü.
 *
 * Liste TARİHE göre kümelenir, duruma göre değil: sahadaki kişi "hangi işler
 * açık" diye değil "bugün ne yapacağım" diye bakıyor. Sunucu zaten
 * `due_date IS NULL, due_date, id` sırasıyla döndürüyor; buradaki kümeleme o
 * sıralamayı bozmadan başlık ekler.
 *
 * "Tamamlandı" işaretlemek de bir güncellemedir ve `expected_updated_at`
 * gönderir: iki kişi aynı görevi aynı anda kapatmaya çalışırsa ikincisi
 * sessizce birincinin notunu ezmez, 409 alır.
 */
import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  Alert, Box, Button, Card, CardContent, Checkbox, Chip, Dialog, DialogActions,
  DialogContent, DialogTitle, Divider, MenuItem, Stack, TextField, ToggleButton,
  ToggleButtonGroup, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import AssignmentIcon from '@mui/icons-material/Assignment';

import {api} from '../api';
import {useAuth} from '../AuthContext';
import {
  farmErrorText, isoDate, saveFarmRecord, taskPriorityLabel, TASK_PRIORITIES,
  todayIso, type CropSeason, type FieldTask, type Page,
} from '../farm/farmApi';

type Form = {title: string; season_id: string; due_date: string; priority: string; notes: string};

const bosForm = (): Form => ({title: '', season_id: '', due_date: todayIso(), priority: 'NORMAL', notes: ''});
const bosNull = (v: string) => (v.trim() === '' ? null : v.trim());

/** Görevi tarihe göre kovaya at. Sunucudan gelen sıra korunur. */
type Kova = 'gecikti' | 'bugun' | 'yakinda' | 'terminsiz';
const KOVA_BASLIK: Record<Kova, string> = {
  gecikti: 'Gecikenler',
  bugun: 'Bugün',
  yakinda: 'Sırada',
  terminsiz: 'Termin verilmemiş',
};
const KOVA_SIRA: Kova[] = ['gecikti', 'bugun', 'yakinda', 'terminsiz'];

function kova(task: FieldTask, bugun: string): Kova {
  if (!task.due_date) return 'terminsiz';
  if (task.due_date < bugun) return 'gecikti';
  if (task.due_date === bugun) return 'bugun';
  return 'yakinda';
}

export default function FieldTasks() {
  const {can} = useAuth();
  const yazabilir = can('farm.manage');

  const [tasks, setTasks] = useState<FieldTask[]>([]);
  const [seasons, setSeasons] = useState<CropSeason[]>([]);
  const [durum, setDurum] = useState('OPEN');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);

  const [dialog, setDialog] = useState<{open: boolean; form: Form}>({open: false, form: bosForm()});
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState('');

  const bugun = todayIso();

  const yukle = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [t, s] = await Promise.all([
        api.get<Page<FieldTask>>('/field-tasks', {params: {limit: 200, ...(durum ? {status: durum} : {})}}),
        api.get<Page<CropSeason>>('/crop-seasons', {params: {limit: 200}}),
      ]);
      setTasks(t.data.items);
      setSeasons(s.data.items);
    } catch (err) {
      setError(farmErrorText(err, 'Görevler yüklenemedi.'));
    } finally {
      setLoading(false);
    }
  }, [durum]);

  useEffect(() => {
    void yukle();
  }, [yukle]);

  const sezonAdi = useMemo(() => {
    const map = new Map<number, string>();
    for (const s of seasons) map.set(s.id, `${s.crop} · ${s.season_year}`);
    return (id: number | null) => (id === null ? null : map.get(id) ?? `#${id}`);
  }, [seasons]);

  const kovalar = useMemo(() => {
    const gruplar = new Map<Kova, FieldTask[]>();
    for (const t of tasks) {
      const k = kova(t, bugun);
      const liste = gruplar.get(k);
      if (liste) liste.push(t);
      else gruplar.set(k, [t]);
    }
    return KOVA_SIRA.filter(k => gruplar.has(k)).map(k => ({kova: k, items: gruplar.get(k)!}));
  }, [tasks, bugun]);

  const durumDegistir = async (task: FieldTask, tamam: boolean) => {
    setBusyId(task.id);
    setError('');
    try {
      await saveFarmRecord('put', `/field-tasks/${task.id}`, {
        title: task.title,
        season_id: task.season_id,
        parcel_id: task.parcel_id,
        due_date: task.due_date,
        priority: task.priority,
        assigned_user_id: task.assigned_user_id,
        notes: task.notes,
        status: tamam ? 'DONE' : 'OPEN',
        expected_updated_at: task.updated_at,
      }, 'Görev güncellenemedi.');
      await yukle();
    } catch (err) {
      setError(farmErrorText(err, 'Görev güncellenemedi.'));
    } finally {
      setBusyId(null);
    }
  };

  const kaydet = async () => {
    setSaving(true);
    setDialogError('');
    const f = dialog.form;
    try {
      await saveFarmRecord('post', '/field-tasks', {
        title: f.title.trim(),
        season_id: f.season_id === '' ? null : Number(f.season_id),
        due_date: bosNull(f.due_date),
        priority: f.priority,
        notes: bosNull(f.notes),
      }, 'Görev oluşturulamadı.');
      setDialog({open: false, form: bosForm()});
      await yukle();
    } catch (err) {
      setDialogError(farmErrorText(err, 'Kaydedilemedi.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Stack spacing={2}>
      <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" gap={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <AssignmentIcon color="primary" />
            <Typography variant="h4">Tarla Görevleri</Typography>
          </Stack>
          <Typography color="text.secondary">Termin tarihine göre sıralı; geciken işler en üstte.</Typography>
        </Box>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <ToggleButtonGroup exclusive size="small" value={durum} onChange={(_e, v) => v !== null && setDurum(v)}>
            <ToggleButton value="OPEN">Açık</ToggleButton>
            <ToggleButton value="DONE">Tamamlanan</ToggleButton>
            <ToggleButton value="">Tümü</ToggleButton>
          </ToggleButtonGroup>
          {yazabilir && (
            <Button variant="contained" startIcon={<AddIcon />} sx={{minHeight: {xs: 44}}}
              onClick={() => {
                setDialogError('');
                setDialog({open: true, form: bosForm()});
              }}>
              Yeni görev
            </Button>
          )}
        </Stack>
      </Stack>

      {error && <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void yukle()}>Yeniden dene</Button>}>{error}</Alert>}

      {loading && !tasks.length && <Card><CardContent><Typography color="text.secondary">Yükleniyor…</Typography></CardContent></Card>}

      {!loading && !tasks.length && (
        <Alert severity="info">
          {durum === 'OPEN' ? 'Açık görev yok.' : durum === 'DONE' ? 'Tamamlanmış görev yok.' : 'Henüz görev yok.'}
        </Alert>
      )}

      {kovalar.map(({kova: k, items}) => (
        <Card key={k}>
          <CardContent sx={{pb: 1}}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="h6">{KOVA_BASLIK[k]}</Typography>
              <Chip size="small" color={k === 'gecikti' ? 'error' : k === 'bugun' ? 'warning' : 'default'} label={items.length} />
            </Stack>
          </CardContent>
          <Divider />
          <Stack divider={<Divider />}>
            {items.map(t => {
              const sezon = sezonAdi(t.season_id);
              return (
                <Stack key={t.id} direction="row" spacing={1.5} px={2} py={1.2} alignItems="center">
                  <Checkbox
                    checked={t.status === 'DONE'}
                    disabled={!yazabilir || busyId === t.id}
                    onChange={e => void durumDegistir(t, e.target.checked)}
                    inputProps={{'aria-label': `${t.title} — tamamlandı olarak işaretle`}}
                    sx={{p: 1.2}}
                  />
                  <Box flex={1} minWidth={0}>
                    <Typography fontWeight={650} sx={{textDecoration: t.status === 'DONE' ? 'line-through' : 'none'}}>
                      {t.title}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {[t.due_date ? isoDate(t.due_date) : 'Termin yok', sezon].filter(Boolean).join(' · ')}
                    </Typography>
                  </Box>
                  {t.priority !== 'NORMAL' && (
                    <Chip size="small" color={t.priority === 'HIGH' ? 'warning' : 'default'} label={taskPriorityLabel(t.priority)} />
                  )}
                </Stack>
              );
            })}
          </Stack>
        </Card>
      ))}

      <Dialog open={dialog.open} onClose={() => !saving && setDialog(d => ({...d, open: false}))} fullWidth maxWidth="sm">
        <DialogTitle>Yeni görev</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={0.5}>
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
            <TextField label="Başlık" required value={dialog.form.title} placeholder="Gübre at"
              onChange={e => setDialog(d => ({...d, form: {...d.form, title: e.target.value}}))} />
            <TextField select label="Sezon" value={dialog.form.season_id}
              helperText="Boş bırakılabilir — sezona bağlı olmayan işler için."
              onChange={e => setDialog(d => ({...d, form: {...d.form, season_id: e.target.value}}))}>
              <MenuItem value="">Sezona bağlı değil</MenuItem>
              {seasons.map(s => <MenuItem key={s.id} value={String(s.id)}>{s.crop} · {s.season_year}</MenuItem>)}
            </TextField>
            <Stack direction={{xs: 'column', sm: 'row'}} spacing={2}>
              <TextField label="Termin" type="date" fullWidth InputLabelProps={{shrink: true}} value={dialog.form.due_date}
                onChange={e => setDialog(d => ({...d, form: {...d.form, due_date: e.target.value}}))} />
              <TextField select label="Öncelik" fullWidth value={dialog.form.priority}
                onChange={e => setDialog(d => ({...d, form: {...d.form, priority: e.target.value}}))}>
                {TASK_PRIORITIES.map(p => <MenuItem key={p.value} value={p.value}>{p.label}</MenuItem>)}
              </TextField>
            </Stack>
            <TextField label="Not" multiline minRows={2} value={dialog.form.notes}
              onChange={e => setDialog(d => ({...d, form: {...d.form, notes: e.target.value}}))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(d => ({...d, open: false}))} disabled={saving}>Vazgeç</Button>
          <Button variant="contained" onClick={() => void kaydet()} disabled={saving || dialog.form.title.trim() === ''}>Kaydet</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
