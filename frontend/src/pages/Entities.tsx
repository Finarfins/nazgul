import React, {useEffect, useMemo, useRef, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {
  Alert,
  alpha,
  Box,
  Button,
  Chip,
  IconButton,
  InputAdornment,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import BlockIcon from '@mui/icons-material/Block';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import SearchIcon from '@mui/icons-material/Search';
import PeopleAltRoundedIcon from '@mui/icons-material/PeopleAltRounded';
import AccountBalanceWalletRoundedIcon from '@mui/icons-material/AccountBalanceWalletRounded';
import ScheduleRoundedIcon from '@mui/icons-material/ScheduleRounded';
import ShieldOutlinedIcon from '@mui/icons-material/ShieldOutlined';
import {GridColDef} from '@mui/x-data-grid';

import ExcelImportDialog from '../components/ExcelImportDialog';
import ResponsiveTable from '../components/ResponsiveTable';
import EntityDialog from '../components/EntityDialog';
import EntityQuickActions from '../components/EntityQuickActions';
import {api, money} from '../api';

type EntityType = 'customer' | 'supplier';

function SummaryCard({
  label,
  value,
  detail,
  icon,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  icon: React.ReactNode;
  tone: string;
}) {
  const theme = useTheme();
  return (
    <Paper
      variant="outlined"
      sx={{
        p: {xs: 1.5, sm: 2},
        minWidth: 0,
        flex: '1 1 180px',
        borderRadius: 3,
        boxShadow: '0 8px 28px rgba(28,38,32,.045)',
        transition: 'transform .18s ease, box-shadow .18s ease',
        '&:hover': {transform: 'translateY(-2px)', boxShadow: '0 12px 32px rgba(28,38,32,.09)'},
      }}
    >
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <Box
          sx={{
            width: {xs: 38, sm: 42},
            height: {xs: 38, sm: 42},
            flex: '0 0 auto',
            display: 'grid',
            placeItems: 'center',
            borderRadius: 2.25,
            color: tone,
            bgcolor: alpha(tone, theme.palette.mode === 'dark' ? 0.2 : 0.1),
          }}
        >
          {icon}
        </Box>
        <Box minWidth={0}>
          <Typography variant="caption" color="text.secondary" fontWeight={800} letterSpacing=".035em">
            {label}
          </Typography>
          <Typography fontSize={{xs: 17, sm: 19}} lineHeight={1.2} fontWeight={800} mt={0.25} noWrap title={value}>
            {value}
          </Typography>
          <Typography variant="caption" color="text.secondary">{detail}</Typography>
        </Box>
      </Stack>
    </Paper>
  );
}

export default function Entities({type}: {type: EntityType}) {
  const theme = useTheme();
  const navigate = useNavigate();
  const path = type === 'customer' ? '/customers' : '/suppliers';
  const singular = type === 'customer' ? 'Müşteri' : 'Tedarikçi';
  const plural = type === 'customer' ? 'Müşteriler' : 'Tedarikçiler';
  const [rows, setRows] = useState<any[]>([]);
  const [q, setQ] = useState('');
  const [sort, setSort] = useState('balance_desc');
  const [active, setActive] = useState('active');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [quickRow, setQuickRow] = useState<any | null>(null);
  const requestSeq = useRef(0);

  const load = () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError('');
    api.get(path, {params: {q, sort, active}})
      .then(response => {
        if (seq === requestSeq.current) setRows(response.data);
      })
      .catch(requestError => {
        if (seq === requestSeq.current) {
          setError(requestError.response?.data?.detail || 'Cari listesi yüklenemedi.');
        }
      })
      .finally(() => {
        if (seq === requestSeq.current) setLoading(false);
      });
  };

  useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [q, sort, active, type]);

  const totals = useMemo(() => ({
    balance: rows.reduce((sum, row) => sum + Number(row.current_balance || 0), 0),
    overdue: rows.reduce((sum, row) => sum + Number(row.overdue_amount || 0), 0),
    overdueCount: rows.filter(row => Number(row.overdue_amount) > 0).length,
    riskCount: rows.filter(row => row.risk_exceeded).length,
  }), [rows]);

  const toggleActive = async (row: any) => {
    try {
      await api.patch(`${path}/${row.id}/active`, null, {params: {active: !row.is_active}});
      load();
    } catch (requestError: any) {
      setError(requestError.response?.data?.detail || requestError.message);
    }
  };

  const columns = useMemo<GridColDef[]>(() => [
    {field: 'id', headerName: 'ID', width: 70},
    {
      field: 'name',
      headerName: 'İsim / Ünvan',
      flex: 1,
      minWidth: 260,
      renderCell: params => (
        <Stack justifyContent="center" height="100%" minWidth={0}>
          <Typography fontWeight={750} noWrap>{params.value}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap>
            {params.row.email || params.row.phone || 'İletişim bilgisi yok'}
          </Typography>
        </Stack>
      ),
    },
    {field: 'phone', headerName: 'Telefon', width: 145},
    {field: 'order_count', headerName: type === 'customer' ? 'Satış' : 'Alış', width: 90},
    {field: 'current_balance', headerName: 'Mevcut Bakiye', width: 155, valueFormatter: value => money(value)},
    {
      field: 'overdue_amount',
      headerName: 'Vadesi Geçen',
      width: 145,
      valueFormatter: value => money(value),
      cellClassName: params => Number(params.value) > 0 ? 'overdue-cell' : '',
    },
    {field: 'last_activity', headerName: 'Son İşlem', width: 120},
    {
      field: 'risk',
      headerName: 'Risk',
      width: 105,
      sortable: false,
      renderCell: params => Number(params.row.risk_limit || 0) > 0
        ? <Chip size="small" color={params.row.risk_exceeded ? 'error' : 'success'} label={`%${Math.round(Number(params.row.risk_usage_percent || 0))}`} />
        : <Chip size="small" variant="outlined" label="Limitsiz" />,
    },
    {
      field: 'actions',
      headerName: '',
      width: 170,
      sortable: false,
      renderCell: params => (
        <>
          <Tooltip title="Hızlı işlemler">
            <IconButton size="small" color="primary" onClick={event => {event.stopPropagation(); setQuickRow(params.row);}}>
              <MoreVertIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Düzenle">
            <IconButton size="small" onClick={event => {event.stopPropagation(); setEditId(params.row.id); setOpen(true);}}>
              <EditIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={params.row.is_active ? 'Pasife al' : 'Aktif et'}>
            <IconButton size="small" color={params.row.is_active ? 'warning' : 'success'} onClick={event => {event.stopPropagation(); toggleActive(params.row);}}>
              {params.row.is_active ? <BlockIcon fontSize="small" /> : <CheckCircleIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
          <Tooltip title="Kalıcı sil">
            <IconButton
              size="small"
              color="error"
              onClick={async event => {
                event.stopPropagation();
                try {
                  await api.delete(`${path}/${params.row.id}`);
                  load();
                } catch (requestError: any) {
                  setError(requestError.response?.data?.detail || requestError.message);
                }
              }}
            >
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </>
      ),
    },
  ], [path, type]);

  return (
    <Stack spacing={{xs: 1.5, sm: 2.25}}>
      <Paper
        sx={{
          position: 'relative',
          overflow: 'hidden',
          px: {xs: 2.25, sm: 3},
          py: {xs: 2, sm: 3},
          color: '#fff',
          border: 0,
          borderRadius: 3.5,
          background: `linear-gradient(125deg, ${theme.palette.sidebar} 0%, #0b3567 62%, #164a8a 100%)`,
          boxShadow: '0 18px 48px rgba(20,38,26,.18)',
          '&:after': {
            content: '""',
            position: 'absolute',
            width: 280,
            height: 280,
            right: -75,
            top: -130,
            borderRadius: '50%',
            background: 'rgba(255,255,255,.075)',
          },
        }}
      >
        <Stack direction={{xs: 'column', md: 'row'}} justifyContent="space-between" alignItems={{md: 'center'}} gap={2}>
          <Box position="relative" zIndex={1}>
            <Typography variant="caption" sx={{color: 'rgba(255,255,255,.68)', fontWeight: 800, letterSpacing: '.12em'}}>
              CARİ YÖNETİMİ
            </Typography>
            <Typography component="h1" fontSize={{xs: 22, sm: 29}} fontWeight={800} letterSpacing="-.025em" mt={0.35}>
              {plural}
            </Typography>
            <Typography sx={{color: 'rgba(255,255,255,.72)', mt: 0.5, maxWidth: 580}}>
              {type === 'customer'
                ? 'Müşteri ilişkilerini, bakiyeleri ve tahsilat risklerini tek merkezden yönetin.'
                : 'Tedarikçi hesaplarını, satın alma hacmini ve ödeme risklerini tek merkezden yönetin.'}
            </Typography>
          </Box>
          <Stack direction={{xs: 'column', sm: 'row'}} spacing={1} position="relative" zIndex={1}>
            <Button
              variant="outlined"
              startIcon={<UploadFileIcon />}
              onClick={() => setImportOpen(true)}
              sx={{color: '#fff', borderColor: 'rgba(255,255,255,.35)', bgcolor: 'rgba(255,255,255,.06)', '&:hover': {borderColor: '#fff', bgcolor: 'rgba(255,255,255,.12)'}}}
            >
              Excel&apos;den Aktar
            </Button>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => {setEditId(null); setOpen(true);}}
              sx={{bgcolor: '#fff', color: '#0b3567', '&:hover': {bgcolor: '#eaf2fb'}}}
            >
              Yeni {singular}
            </Button>
          </Stack>
        </Stack>
      </Paper>

      {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}

      <Stack direction="row" gap={{xs: 1, sm: 1.4}} flexWrap="wrap">
        <SummaryCard label="TOPLAM KAYIT" value={String(rows.length)} detail={active === 'active' ? 'Aktif hesaplar' : 'Filtrelenen hesaplar'} icon={<PeopleAltRoundedIcon />} tone="#164a8a" />
        <SummaryCard label="TOPLAM BAKİYE" value={money(totals.balance)} detail="Görüntülenen cari toplamı" icon={<AccountBalanceWalletRoundedIcon />} tone="#3f708f" />
        <SummaryCard label="VADESİ GEÇEN" value={money(totals.overdue)} detail={`${totals.overdueCount} hesapta gecikme`} icon={<ScheduleRoundedIcon />} tone="#b7791f" />
        <SummaryCard label="RİSK LİMİTİ" value={String(totals.riskCount)} detail="Limiti aşan hesap" icon={<ShieldOutlinedIcon />} tone="#c0392b" />
      </Stack>

      <Paper variant="outlined" sx={{p: {xs: 1.5, sm: 2}, borderRadius: 3}}>
        <Stack direction={{xs: 'column', md: 'row'}} spacing={1.25}>
          <TextField
            fullWidth
            placeholder="İsim, telefon, e-posta veya vergi no ara"
            value={q}
            onChange={event => setQ(event.target.value)}
            inputProps={{'aria-label': `${singular} ara`}}
            InputProps={{startAdornment: <InputAdornment position="start"><SearchIcon color="action" /></InputAdornment>}}
          />
          <TextField select label="Durum" value={active} onChange={event => setActive(event.target.value)} sx={{minWidth: {md: 160}}}>
            <MenuItem value="active">Aktif hesaplar</MenuItem>
            <MenuItem value="inactive">Pasif hesaplar</MenuItem>
            <MenuItem value="all">Tüm hesaplar</MenuItem>
          </TextField>
          <TextField select label="Sıralama" value={sort} onChange={event => setSort(event.target.value)} sx={{minWidth: {md: 235}}}>
            <MenuItem value="balance_desc">Bakiye yüksek → düşük</MenuItem>
            <MenuItem value="balance_asc">Bakiye düşük → yüksek</MenuItem>
            <MenuItem value="overdue_desc">Vadesi geçen yüksek → düşük</MenuItem>
            <MenuItem value="activity_desc">Son işlem yeni → eski</MenuItem>
            <MenuItem value="name_asc">İsim A → Z</MenuItem>
            <MenuItem value="name_desc">İsim Z → A</MenuItem>
          </TextField>
        </Stack>
      </Paper>

      <ResponsiveTable
        rows={rows}
        columns={columns}
        loading={loading}
        onRowClick={(row: any) => navigate(type === 'customer' ? `/musteriler/${row.id}` : `/tedarikciler/${row.id}`)}
        cardTitle={row => row.name}
        cardSubtitle={row => row.phone || 'Telefon yok'}
        cardFields={[
          {label: type === 'customer' ? 'Satış' : 'Alış', value: row => row.order_count || 0},
          {label: 'Mevcut Bakiye', value: row => money(row.current_balance)},
          {label: 'Vadesi Geçen', value: row => money(row.overdue_amount)},
          {label: 'Son İşlem', value: row => row.last_activity || '-'},
        ]}
      />

      <EntityQuickActions open={Boolean(quickRow)} type={type} entity={quickRow} onClose={() => setQuickRow(null)} onChanged={load} />
      <ExcelImportDialog open={importOpen} kind={type === 'customer' ? 'customers' : 'suppliers'} onClose={() => setImportOpen(false)} onDone={load} />
      <EntityDialog open={open} type={type} id={editId} onClose={() => setOpen(false)} onSaved={load} />
    </Stack>
  );
}
