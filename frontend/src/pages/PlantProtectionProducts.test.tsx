/**
 * BKÜ katalog ekranının ÜRÜN LİSTESİ SÖZLEŞMESİ.
 *
 * BU TEST BİR ÇÖKMEDEN SONRA YAZILDI ve yazılma sebebi tam olarak şu:
 * `/products` İKİ AYRI BİÇİM döndürüyor — `include_meta` verilmedikçe ÇIPLAK
 * DİZİ, verilirse `{items, has_more}` (bkz. `backend/app/routers/products.py`,
 * `list_products` sonundaki iki `return`). Ekranın ilk sürümü koşulsuz
 * `.items` okudu, çıplak dizi geldiğinde `undefined` aldı ve ürün adı
 * sözlüğünü kuran `for...of` döngüsü `TypeError: m is not iterable` ile
 * sayfayı komple çökertti.
 *
 * Bunu ne tip denetimi ne de birim testleri yakaladı — `e2e`nin
 * konsol-temizlik kapısı yakaladı (`rota-render-kapisi.spec.ts`), çünkü hata
 * yalnız ÇALIŞMA ANINDA ve yalnız gerçek yanıt biçimiyle ortaya çıkıyor.
 *
 * İki biçim de burada çivilendi: sözleşmenin hangi ucu değişirse değişsin
 * ekran çökmemeli.
 */
import React from 'react';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
vi.mock('../api', () => ({
  api: {get: (...a: unknown[]) => get(...a), post: vi.fn(), put: vi.fn()},
  errorDetail: (_e: unknown, fallback: string) => fallback,
}));

const can = vi.fn();
vi.mock('../AuthContext', () => ({useAuth: () => ({can, activeCompany: {id: 1, name: 'A'}})}));

// Kart alanlarını GERÇEK bileşenle çizdirmiyoruz; buradaki konu tablo değil,
// ekranın yanıt biçimine dayanıklılığı. Ama `cardFields` ZORUNLU bir özellik
// olduğu için sahtesi onu okuyarak verildiğini de kanıtlıyor.
vi.mock('../components/ResponsiveTable', () => ({
  default: ({rows, cardFields, cardTitle}: any) => (
    <div data-testid="tablo">
      <span data-testid="alan-sayisi">{cardFields.length}</span>
      {rows.map((row: any) => (
        <div key={row.id}>
          <span>{cardTitle(row)}</span>
          {cardFields.map((f: any) => (
            <span key={f.label}>{`${f.label}: ${f.value(row)}`}</span>
          ))}
        </div>
      ))}
    </div>
  ),
}));

import PlantProtectionProducts from './PlantProtectionProducts';

const KATALOG_SATIRI = {
  id: 1, product_id: 77, product_name: 'ORNEK BKU', crop: 'Domates',
  registration_no: 'RUHSAT-1', preharvest_interval_days: 21,
  reentry_interval_days: 3, notes: null, status: 'ACTIVE',
  updated_at: '2026-08-01T10:00:00+00:00',
};

const URUNLER = [{id: 77, name: 'ORNEK BKU'}];

/** `urunYaniti` BİLEREK parametre: iki sözleşme biçimi de sınanıyor. */
function cevapla(urunYaniti: unknown) {
  get.mockImplementation((url: string) => {
    if (url === '/plant-protection-products') {
      return Promise.resolve({data: {items: [KATALOG_SATIRI], total: 1, limit: 200, offset: 0}});
    }
    if (url === '/products') return Promise.resolve({data: urunYaniti});
    return Promise.resolve({data: {items: [], total: 0, limit: 200, offset: 0}});
  });
}

const ciz = () =>
  render(
    <ThemeProvider theme={createTheme()}>
      <PlantProtectionProducts />
    </ThemeProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  can.mockReturnValue(true);
});

afterEach(cleanup);

it('`/products` ÇIPLAK DİZİ döndürdüğünde ekran çökmez', async () => {
  // Gerçek varsayılan biçim. Regresyon burada yaşandı.
  cevapla(URUNLER);
  ciz();

  await waitFor(() => expect(screen.getByTestId('tablo')).toBeTruthy());
  expect(screen.getByText('BKÜ Kataloğu')).toBeTruthy();
  expect(screen.getByText('ORNEK BKU')).toBeTruthy();
  // Yükleme hatası şeridi ÇIKMAMALI: dizi geçerli bir yanıttır.
  expect(screen.queryByText(/BKÜ kataloğu yüklenemedi/)).toBeNull();
});

it('`/products` {items} sarmalayıcısı döndürdüğünde de çalışır', async () => {
  cevapla({items: URUNLER, has_more: false});
  ciz();

  await waitFor(() => expect(screen.getByTestId('tablo')).toBeTruthy());
  expect(screen.getByText('ORNEK BKU')).toBeTruthy();
  expect(screen.queryByText(/BKÜ kataloğu yüklenemedi/)).toBeNull();
});

it('ResponsiveTable ZORUNLU `cardFields` özelliğini alır', async () => {
  // Bu özelliğin eksikliği derlemeyi kırmıştı (TS2741) ve `frontend`,
  // `container`, `e2e` işlerinin üçünü birden düşürmüştü; mobil kart gövdesi
  // doğrudan bu diziyi geziyor.
  cevapla(URUNLER);
  ciz();

  await waitFor(() => expect(screen.getByTestId('alan-sayisi')).toBeTruthy());
  expect(Number(screen.getByTestId('alan-sayisi').textContent)).toBeGreaterThan(0);
  expect(screen.getByText('Hasat bekleme: 21 gün')).toBeTruthy();
  expect(screen.getByText('Giriş yasağı: 3 gün')).toBeTruthy();
});

it('bitkisi boş satır kartta "Bütün bitkiler" olarak görünür', async () => {
  // Boş dize "veri yok" DEĞİL, açık bir anlam taşıyor; '—' göstermek yanlış
  // olurdu.
  get.mockImplementation((url: string) => {
    if (url === '/plant-protection-products') {
      return Promise.resolve({data: {
        items: [{...KATALOG_SATIRI, crop: '', reentry_interval_days: null}],
        total: 1, limit: 200, offset: 0,
      }});
    }
    if (url === '/products') return Promise.resolve({data: URUNLER});
    return Promise.resolve({data: {items: [], total: 0, limit: 200, offset: 0}});
  });
  ciz();

  await waitFor(() => expect(screen.getByTestId('tablo')).toBeTruthy());
  expect(screen.getByText('Bitki: Bütün bitkiler')).toBeTruthy();
  expect(screen.getByText('Giriş yasağı: —')).toBeTruthy();
});
