/**
 * BKÜ katalog içe aktarma diyaloğu (göç 20260901_0064).
 *
 * BU DOSYANIN ANA İDDİASI TEK CÜMLE: **reddedilen satırlar SAYI DEĞİL LİSTE
 * olarak gösterilir ve HİÇBİRİ kısaltılmaz.**
 *
 * Neden bir testi hak ediyor: "3 satır atlandı" yazan bir ekran, çalışıyor
 * GÖRÜNÜR. Yeşil bir yükleme özeti, gözle bakan birine doğru gelir; yanlış
 * olan şey kullanıcının o üç satırı BULAMAMASIdır ve bu, ancak 200 satırlık
 * gerçek bir dosyayla uğraşırken anlaşılır. Buradaki test o sessiz bozulmayı
 * geliştirme anında yakalıyor: 25 reddin 25'i de ekranda ARANIYOR — 20'de
 * kesip "+5 tane daha" diyen bir sürüm (uygulamanın diğer içe aktarma
 * diyaloğunun yaptığı budur) bu testi DÜŞÜRÜR.
 *
 * İkinci iddia: **çakışma kuralı yükleme ÖNCESİNDE yazıyor.** Kullanıcı
 * "üzerine yazılmaz"ı dosyayı yükledikten SONRA öğrenirse, listesinin
 * tamamının yazıldığını sanmış olarak devam eder.
 */
import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const post = vi.fn();
vi.mock('../api', () => ({
  api: {get: vi.fn(), post: (...a: unknown[]) => post(...a), put: vi.fn()},
  errorDetail: (_e: unknown, fallback: string) => fallback,
}));

import PlantProtectionImportDialog, {
  guvenliCsvHucresi, redlerCsv, sablonCsv,
} from './PlantProtectionImportDialog';

const ciz = (onImported = vi.fn()) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <PlantProtectionImportDialog open onClose={vi.fn()} onImported={onImported} />
    </ThemeProvider>,
  );

const dosyaSec = () => {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(['Ürün Kodu\n'], 'bku-listesi.csv', {type: 'text/csv'});
  Object.defineProperty(input, 'files', {value: [file]});
  fireEvent.change(input);
};

const yukleyeBas = () => fireEvent.click(screen.getByText('Yükle'));

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

it('reddedilen satırların HEPSİ gösterilir — hiçbiri kısaltılmaz', async () => {
  // YİRMİ BEŞ ret: 20'de kesen bir sürüm burada düşer. Sayı bilerek, diğer
  // içe aktarma diyaloğunun `slice(0, 20)` eşiğinin ÜSTÜNDE seçildi.
  const rejected = Array.from({length: 25}, (_, i) => ({
    row: i + 2,
    message: `Hasat bekleme günü tam sayı değil: 'x${i}'.`,
    product: `BKU-${i}`,
  }));
  post.mockResolvedValue({
    data: {filename: 'bku-listesi.csv', total_rows: 30, imported: 5, rejected},
  });
  ciz();
  dosyaSec();
  yukleyeBas();

  await waitFor(() => expect(screen.getByText(/30 satır okundu/)).toBeTruthy());
  // Satır numarası TEK TEK aranıyor; "25 satır reddedildi" yazan ama
  // satırları göstermeyen bir sürüm bu döngüde düşer.
  for (const r of rejected) {
    expect(screen.getByText(`Satır ${r.row}`)).toBeTruthy();
  }
  expect(screen.getByText(/x24/)).toBeTruthy();
  // Ve "daha fazlası var" kısaltması EKRANDA OLMAMALI.
  expect(screen.queryByText(/tane daha/)).toBeNull();
  expect(screen.queryByText(/hata daha/)).toBeNull();
});

it('bir bozuk satır dosyayı düşürmez: yazılan sayı da reddedilenler de görünür', async () => {
  // Kullanıcının görmesi gereken iki şey aynı anda: neyin YAZILDIĞI ve neyin
  // yazılmadığı. Yalnız biri gösterilseydi kullanıcı ya listesinin tamamının
  // yazıldığını sanır ya da hiçbirinin.
  post.mockResolvedValue({
    data: {
      filename: 'bku-listesi.csv', total_rows: 6, imported: 5,
      rejected: [{row: 4, message: "'BKU-YOK' kodlu ürün bulunamadı.", product: 'BKU-YOK'}],
    },
  });
  const onImported = vi.fn();
  ciz(onImported);
  dosyaSec();
  yukleyeBas();

  await waitFor(() => expect(screen.getByText(/5 kayıt yazıldı/)).toBeTruthy());
  expect(screen.getByText('Satır 4')).toBeTruthy();
  expect(screen.getByText(/kodlu ürün bulunamadı/)).toBeTruthy();
  // Bir satır bile yazıldıysa arkadaki liste tazelenmeli.
  expect(onImported).toHaveBeenCalled();
});

it('çakışmanın ÜZERİNE YAZILMAYACAĞI dosya yüklenmeden ÖNCE yazıyor', () => {
  // Uygulamanın diğer içe aktarmaları eşleşeni GÜNCELLER; buradaki kural
  // tersi ve sürpriz olmamalı.
  ciz();
  expect(screen.getByText(/reddedilir, üzerine yazılmaz/)).toBeTruthy();
  // Ve hiçbir istek atılmadan görünüyor.
  expect(post).not.toHaveBeenCalled();
});

it('şablon hiçbir ÖRNEK gün sayısı içermez', () => {
  // 0063'ün duruşu: uygulama hiçbir bekleme süresi ÖNERMEZ, örnek olarak
  // bile. Örnek bir gün sayısı, kopyalanan bir gün sayısıdır.
  const csv = sablonCsv();
  expect(csv).toContain('Hasat Bekleme (Gün)');
  // Başlık satırından BAŞKA satır yok, yani örnek veri de yok.
  expect(csv.trim().split('\r\n')).toHaveLength(1);
  expect(/\d/.test(csv)).toBe(false);
});

it('indirilen CSV formül enjeksiyonuna karşı kaçışlı', () => {
  // Reddedilen satırın "ürün" alanı KULLANICININ DOSYASINDAN geliyor ve
  // sunucudan geri dönüyor; `=cmd|...` ile başlayan bir hücre Excel'de
  // FORMÜL olarak çalışırdı.
  expect(guvenliCsvHucresi('=1+1')).toBe(`"'=1+1"`);
  expect(guvenliCsvHucresi('@SUM(A1)')).toBe(`"'@SUM(A1)"`);
  expect(guvenliCsvHucresi('a"b')).toBe('"a""b"');

  const csv = redlerCsv({
    filename: 'x.csv', total_rows: 1, imported: 0,
    rejected: [{row: 2, message: 'Hasat bekleme günü boş.', product: '=HYPERLINK("x")'}],
  });
  expect(csv).toContain(`"'=HYPERLINK(""x"")"`);
  expect(csv).not.toContain(',=HYPERLINK');
});
