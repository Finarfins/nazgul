/**
 * Olay Kuyruğu ekranının sözleşmesi.
 *
 * Dört şey ölçülüyor ve hepsi MUTLU YOLDAN FAZLASI:
 *
 * 1. Özet kartları ve kırılım, sunucunun DÖNDÜRDÜĞÜ sayıları gösterir.
 * 2. Başarısız olayın GEREKÇE METNİ ekrana taşınır — kova adı tek başına
 *    hangi kaydın düzeltileceğini söylemez.
 * 3. "Yalnız başarısız" anahtarı sunucuya GERÇEKTEN `failed_only` gönderir;
 *    istemci tarafında süzmek, sayfalama yüzünden eksik liste gösterirdi.
 * 4. `PENDING` "sorun yok" diye okunamasın diye uyarı METNİ ekranda durur.
 *    Belgenin ölçtüğü `RECOVERY_FAILED` sınıfının tek savunması bu.
 */
import React from 'react';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
vi.mock('../api', () => ({
  api: {get: (...args: unknown[]) => get(...args)},
  errorDetail: (_error: unknown, fallback: string) => fallback,
}));
// Vekil YALNIZ KAPSAYICIDIR — GÖSTERİMİ SAYFANIN KENDİ KODU YAPAR.
//
// Vekil eskiden `row.last_error`ı KENDİ basıyordu; o hâlde 2 numaralı test
// vekilin markup'ını ölçüyordu, sayfanın gösterimini değil. ÖLÇÜLDÜ: sayfanın
// HER İKİ gerçek gösterim yolu da ('Gerekçe' sütununun `renderCell`i ve kart
// görünümünün `cardFields` değeri) kırıldığında beş test de YEŞİL kalıyor,
// gerçek tarayıcıda ise ekranda '—' yazıyordu.
//
// Bu yüzden vekil artık kendi metnini basmaz: sayfanın GEÇTİĞİ `columns`
// ve `cardFields` fonksiyonlarını ÇAĞIRIR. Izgarayı (DataGrid) hâlâ kurmuyoruz
// — jsdom'da sanallaştırma yüzünden ölçülemez —, ama içine koyduğu gösterim
// kodu artık GERÇEKTEN koşuyor. İki yoldan biri bozulursa test kırılır.
vi.mock('../components/ResponsiveTable', () => ({
  default: ({rows, columns, cardFields}: {
    rows: Array<Record<string, unknown>>;
    columns: Array<{field: string; renderCell?: (p: {row: any}) => React.ReactNode}>;
    cardFields: Array<{label: string; value: (row: any) => React.ReactNode}>;
  }) => (
    <div data-testid="tablo">
      {rows.map(row => (
        <div key={String(row.id)} data-testid={`satir-${row.id}`}>
          {String(row.status)}
          <span data-testid={`masaustu-${row.id}`}>
            {columns.filter(s => s.renderCell).map(s => (
              <span key={s.field}>{s.renderCell!({row})}</span>
            ))}
          </span>
          <span data-testid={`kart-${row.id}`}>
            {cardFields.map(alan => (
              <span key={alan.label}>{alan.value(row)}</span>
            ))}
          </span>
        </div>
      ))}
    </div>
  ),
}));

import IntegrationEvents from './IntegrationEvents';

const OZET = {
  source: 'field',
  buckets: [
    {source_type: 'field_harvest', status: 'SKIPPED_NO_PRODUCT', count: 2,
     oldest_created_at: '2026-08-20T08:00:00Z'},
    {source_type: 'field_activity', status: 'PENDING', count: 1,
     oldest_created_at: '2026-08-26T08:00:00Z'},
  ],
  total: 3,
  pending_total: 1,
  failed_total: 2,
};

const GEREKCE =
  'sezonun ürünü bildirilmemiş; hasat stok taşıyamaz ' +
  '(field_harvests -> crop_seasons.product_id NULL)';

const OLAYLAR = [
  {id: 7, source_type: 'field_harvest', source_id: 3, target: 'stock',
   status: 'SKIPPED_NO_PRODUCT', attempts: 1, last_error: GEREKCE,
   created_at: '2026-08-20T08:00:00Z', updated_at: '2026-08-20T08:00:05Z',
   processed_at: '2026-08-20T08:00:05Z'},
  {id: 9, source_type: 'field_activity', source_id: 4, target: 'stock',
   status: 'PENDING', attempts: 0, last_error: null,
   created_at: '2026-08-26T08:00:00Z', updated_at: '2026-08-26T08:00:00Z',
   processed_at: null},
];

let sonListeParams: Record<string, unknown> | undefined;

beforeEach(() => {
  vi.clearAllMocks();
  sonListeParams = undefined;
  get.mockImplementation((url: string, config?: {params?: Record<string, unknown>}) => {
    if (url === '/field-integration-events/summary') return Promise.resolve({data: OZET});
    if (url === '/field-integration-events') {
      sonListeParams = config?.params;
      const yalniz = config?.params?.failed_only === true;
      const items = yalniz ? OLAYLAR.filter(o => o.status !== 'PENDING') : OLAYLAR;
      return Promise.resolve({data: {source: 'field', items, total: items.length,
                                     limit: 50, offset: 0}});
    }
    throw new Error(url);
  });
});

afterEach(cleanup);

const goster = () =>
  render(
    <ThemeProvider theme={createTheme()}>
      <IntegrationEvents />
    </ThemeProvider>,
  );

it('özet sayıları ve kırılım sunucudan geldiği gibi görünür', async () => {
  goster();
  await waitFor(() => expect(screen.getByTestId('tablo')).toBeTruthy());

  // Kırılım kovaları kaynak ve durum ADIYLA, sayısıyla birlikte.
  expect(screen.getByText(/Hasat · Ürün bağı yok: 2/)).toBeTruthy();
  expect(screen.getByText(/Faaliyet · Bekliyor: 1/)).toBeTruthy();
});

it('başarısız olayın GEREKÇE METNİ ekranda görünür', async () => {
  goster();
  await waitFor(() => expect(screen.getByTestId('satir-7')).toBeTruthy());

  // Kova ADI değil, DÜZELTİLECEK KAYDI söyleyen metin. Bu metin artık
  // vekilin markup'ından DEĞİL, sayfanın kendi `renderCell`/`cardFields`
  // gösteriminden geliyor (bkz. vekil tanımındaki ölçüm notu).
  // HER İKİ GÖSTERİM YOLU AYRI AYRI. Tek bir birleşik metinde ölçülseydi,
  // yollardan biri bozulup öteki sağlam kaldığında test YEŞİL kalırdı
  // (ölçüldü: tam olarak öyle oluyordu).
  expect(screen.getByTestId('masaustu-7').textContent).toContain('crop_seasons.product_id');
  expect(screen.getByTestId('kart-7').textContent).toContain('crop_seasons.product_id');
});

it('PENDING "sorun yok" diye okunmasın: uyarı metni ekranda', async () => {
  goster();
  await waitFor(() => expect(screen.getByTestId('tablo')).toBeTruthy());

  // `RECOVERY_FAILED` izi bırakmaz; bekleyen satır hiç denenmemiş de olabilir.
  expect(document.body.innerHTML).toContain('denenmemiş olabilir');
});

it('"yalnız başarısız" SUNUCUYA gider, istemcide süzülmez', async () => {
  goster();
  await waitFor(() => expect(screen.getByTestId('satir-9')).toBeTruthy());
  // Başlangıçta filtre YOK.
  expect(sonListeParams?.failed_only).toBeUndefined();

  await userEvent.click(screen.getByLabelText('Yalnız başarısız olaylar'));

  await waitFor(() => expect(sonListeParams?.failed_only).toBe(true));
  // Sunucu süzdüğü için PENDING satırı listeden düşer.
  await waitFor(() => expect(screen.queryByTestId('satir-9')).toBeNull());
  expect(screen.getByTestId('satir-7')).toBeTruthy();
});

it('sunucu hatası sessizce boş liste gibi görünmez', async () => {
  // Axios biciminde bir hata: `errorDetail` yedek metni dondursun.
  get.mockImplementation(() => Promise.reject({response: {status: 500}}));
  goster();
  await waitFor(() => expect(screen.getByText('Olay kuyruğu okunamadı.')).toBeTruthy());
  // Hata varken "Kuyrukta olay yok" YAZILMAMALI: ikisi farklı şey.
  expect(screen.queryByText('Kuyrukta olay yok.')).toBeNull();
});
