/**
 * BKÜ kataloğunu firmanın KENDİ dosyasından doldurma (göç 20260901_0064).
 *
 * ÜÇ ŞEY BURADA BİLİNÇLİ VE ÜÇÜ DE EKRANIN TAMAMINI BELİRLİYOR:
 *
 * 1. **REDDEDİLENLER SAYI DEĞİL, LİSTE.** Bu ekranın en önemli kararı.
 *    "3 satır atlandı" kullanıcıya hangisini düzelteceğini SÖYLEMEZ; 200
 *    satırlık bir listede o cümle, dosyayı baştan sona gözle taramak demektir.
 *    Her ret kendi satır numarasıyla ve gerekçesiyle yazılıyor, HİÇBİRİ
 *    "+N tane daha" diye kısaltılmıyor — kısaltılan satır, düzeltilmeyen
 *    satırdır. Uzun liste kaydırılabilir bir kutuda duruyor ve CSV olarak
 *    indirilebiliyor, çünkü 30 hatayı ekrandan not almak gerçekçi değil.
 *
 * 2. **ÇAKIŞMA GÜNCELLEME DEĞİL REDDİR ve bunu kullanıcı ÖNCEDEN bilmeli.**
 *    Uygulamanın diğer içe aktarmaları (müşteri, ürün) eşleşeni günceller.
 *    Buradaki kural TERSİ ve sürpriz olmamalı: yükleme başlamadan önce
 *    yazıyor. Aksi hâlde kullanıcı "listem yüklendi" sanır, oysa katalogdaki
 *    eski değerler yerinde durmaktadır.
 *
 * 3. **ŞABLON SUNUCUDAN DEĞİL, BURADAN.** Sunucuya bir şablon ucu eklemek
 *    yeni bir rota ve yeni bir izin yüzeyi demekti; şablon ise sabit bir
 *    başlık satırından ibaret. Tarayıcıda üretiliyor. Şablon ÖRNEK PHI DEĞERİ
 *    İÇERMEZ — 0063'ün duruşu: uygulama hiçbir bekleme süresi ÖNERMEZ, örnek
 *    olarak bile. Örnek bir gün sayısı, kopyalanan bir gün sayısıdır.
 */
import {useState} from 'react';
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  Divider, Stack, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import UploadFileIcon from '@mui/icons-material/UploadFile';

import {api} from '../api';
import {
  PPP_PATH, farmErrorText,
  type PlantProtectionImportResult,
} from '../farm/farmApi';

type Props = {open: boolean; onClose: () => void; onImported: () => void};

/**
 * Şablonun başlıkları — sunucunun tanıdığı adların BİRİNCİ yazımı
 * (`_ICE_AKTARMA_BASLIKLARI`). Sunucu eş adları da kabul ediyor; şablon
 * kullanıcıyı en açık olana yönlendiriyor.
 */
export const SABLON_BASLIKLARI = [
  'Ürün Kodu', 'Ürün Adı', 'Bitki', 'Ruhsat No',
  'Hasat Bekleme (Gün)', 'Giriş Yasağı (Gün)', 'Not',
];

/**
 * CSV hücre kaçışı — formül enjeksiyonuna karşı.
 *
 * `=`, `+`, `-`, `@` ile başlayan bir hücre Excel'de FORMÜL olarak çalışır.
 * İndirilen dosya kullanıcının kendi verisini taşıyor ve o veri sunucudan
 * geri geliyor; aynı koruma `SupplierPriceImportDialog`ta da var ve iki yerin
 * ayrışmaması için birebir aynı kural uygulanıyor.
 */
export const guvenliCsvHucresi = (deger: string) => {
  const korunmus = /^[\s]*[=+\-@]/.test(deger) ? `'${deger}` : deger;
  return `"${korunmus.replace(/"/g, '""')}"`;
};

export const sablonCsv = () =>
  `\uFEFF${SABLON_BASLIKLARI.map(guvenliCsvHucresi).join(',')}\r\n`;

/** Reddedilen satırların CSV'si: 30 hatayı ekrandan not almak gerçekçi değil. */
export const redlerCsv = (result: PlantProtectionImportResult) =>
  `\uFEFF${['Satır', 'Ürün', 'Gerekçe'].map(guvenliCsvHucresi).join(',')}\r\n${
    result.rejected
      .map(r => [String(r.row), r.product, r.message].map(guvenliCsvHucresi).join(','))
      .join('\r\n')
  }\r\n`;

const indir = (icerik: string, ad: string) => {
  const blob = new Blob([icerik], {type: 'text/csv;charset=utf-8'});
  const href = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = ad;
  link.click();
  URL.revokeObjectURL(href);
};

export default function PlantProtectionImportDialog({open, onClose, onImported}: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<PlantProtectionImportResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const kapat = () => {
    setFile(null);
    setResult(null);
    setError('');
    onClose();
  };

  const yukle = async () => {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    setBusy(true);
    setError('');
    try {
      const {data} = await api.post<PlantProtectionImportResult>(
        `${PPP_PATH}/import`, form,
      );
      setResult(data);
      // Bir satır bile yazıldıysa liste tazelenmeli; reddedilenler olsa da.
      onImported();
    } catch (err) {
      setError(farmErrorText(err, 'Dosya yüklenemedi.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : kapat} fullWidth maxWidth="md">
      <DialogTitle>Katalog dosyası yükle</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{pt: 1}}>
          <Alert severity="info">
            Dosyadaki bekleme süreleri <b>sizin</b> girdiğiniz değerlerdir; uygulama
            hiçbir gün sayısı önermez ve hazır bir BKÜ listesi getirmez. Süreleri ürün
            etiketinden okuyun.
          </Alert>
          {/* Kural yükleme ÖNCESİNDE yazıyor: sonradan söylenirse kullanıcı
              listesinin tamamının yazıldığını sanmış olur. */}
          <Alert severity="warning">
            Katalogda <b>aynı ürün ve bitki</b> için kayıt varsa o satır{' '}
            <b>reddedilir, üzerine yazılmaz</b>. Mevcut değerler bir dosya yüzünden
            sessizce değişmesin diye: hangisinin doğru olduğuna siz karar verip
            kaydı ekrandan düzeltin.
          </Alert>

          {error && <Alert severity="error">{error}</Alert>}

          <Stack direction={{xs: 'column', sm: 'row'}} spacing={1}>
            <Button component="label" variant="outlined" startIcon={<UploadFileIcon />}
                    disabled={busy} fullWidth>
              {file?.name || 'CSV / Excel dosyası seç'}
              <input hidden type="file" accept=".csv,.xlsx,.xlsm"
                     onChange={e => {
                       setFile(e.target.files?.[0] || null);
                       setResult(null);
                       setError('');
                     }} />
            </Button>
            <Button startIcon={<DownloadIcon />} disabled={busy}
                    onClick={() => indir(sablonCsv(), 'bku-katalog-sablonu.csv')}>
              Şablon indir
            </Button>
          </Stack>

          <Typography variant="caption" color="text.secondary">
            Sütunlar: {SABLON_BASLIKLARI.join(' · ')}. Ürün kodu ya da ürün adından
            en az biri ve hasat bekleme günü zorunlu. Bitki boş bırakılırsa satır
            bütün bitkiler için geçerli olur.
          </Typography>

          {result && (
            <>
              <Divider />
              <Alert severity={result.rejected.length ? 'warning' : 'success'}>
                <Typography fontWeight={700}>
                  {result.total_rows} satır okundu · {result.imported} kayıt yazıldı
                  {result.rejected.length > 0
                    && ` · ${result.rejected.length} satır reddedildi`}
                </Typography>
              </Alert>

              {result.rejected.length > 0 && (
                <Stack spacing={1}>
                  <Stack direction="row" justifyContent="space-between"
                         alignItems="center" flexWrap="wrap" gap={1}>
                    <Typography variant="subtitle2">
                      Reddedilen satırlar — düzeltip yeniden yükleyin
                    </Typography>
                    <Button size="small" startIcon={<DownloadIcon />}
                            onClick={() => indir(redlerCsv(result), 'reddedilen-satirlar.csv')}>
                      Listeyi indir
                    </Button>
                  </Stack>
                  {/* HİÇBİRİ KISALTILMIYOR. Kısaltılan satır, düzeltilmeyen
                      satırdır; uzun liste kaydırılır, gizlenmez. */}
                  <Box sx={{maxHeight: 260, overflowY: 'auto', pr: 1}}>
                    <Stack spacing={0.5}>
                      {result.rejected.map((r, i) => (
                        <Typography key={`${r.row}:${i}`} variant="body2">
                          <b>Satır {r.row}</b>
                          {r.product ? ` · ${r.product}` : ''}: {r.message}
                        </Typography>
                      ))}
                    </Stack>
                  </Box>
                </Stack>
              )}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={kapat} disabled={busy}>Kapat</Button>
        <Button variant="contained" onClick={() => void yukle()} disabled={busy || !file}>
          {busy ? 'Yükleniyor…' : 'Yükle'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
