/**
 * Döl verimi ekranının ÜÇ kuralı — üçü de sessizce bozulabilir ve üçünün
 * bozulması gerçek veri hatası üretir.
 *
 * 1. **Ölü doğumda yavru satırı GÖNDERİLMEZ.** Sunucu 422 veriyor. Ekran ölü
 *    doğum seçilince yavru bölümünü kaldırıyor; kaldırmayı unutan bir düzenleme
 *    sürüde olmayan bir hayvan kaydı oluşturur ve sayım şişer. Test istek
 *    gövdesini doğrudan denetliyor.
 *
 * 2. **Yavru satırı ile `offspring_count` AYNI ANDA gönderilmez.** İkisi de
 *    dolu gelseydi sunucu sayıyı satırlardan alır, kullanıcı elle yazdığı sayıyı
 *    kaybederdi. Ekran satır varken sayı alanını hiç göstermiyor.
 *
 * 3. **Tohumlama SONUCU yalnız düzenlemede var.** Yeni kayıtta gebelik henüz
 *    bilinmiyor; POST gövdesine `result` girmesi "sonuç belli" demek olurdu.
 *
 * 4. **Ortalama ÖRNEK SAYISIYLA gösterilir, tek doğumlu hayvan 0 aralık
 *    göstermez (FAZ 5).** Örnek sayısı olmadan ortalama, üç ölçümü sürü
 *    gerçeği gibi sunar; "0 gün aralık" ise o hayvanı mükemmel gösterir.
 */
import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
const post = vi.fn();
vi.mock('../api', () => ({
  api: {get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a), put: vi.fn()},
  errorDetail: (_e: unknown, fallback: string) => fallback,
}));

const can = vi.fn();
vi.mock('../AuthContext', () => ({useAuth: () => ({can})}));

vi.mock('react-router-dom', () => ({useNavigate: () => vi.fn()}));

// ResponsiveTable yerine sade liste: jsdom'da `useMediaQuery` daima false
// döndüğü için gerçek bileşen DataGrid çiziyor. Bu testin konusu tablo değil.
vi.mock('../components/ResponsiveTable', () => ({
  default: ({rows, cardTitle, cardFields}: any) => (
    <div data-testid="tablo">
      {rows.map((row: any, i: number) => (
        <div key={row.id ?? i}>
          <span>{cardTitle(row)}</span>
          {cardFields?.map((f: any) => (
            <span key={f.label}>{f.label}: {String(f.value(row))}</span>
          ))}
        </div>
      ))}
    </div>
  ),
}));

import HerdBreeding from './HerdBreeding';

const INEK = {
  id: 7, ear_tag: 'TR0123456789', name: 'Sarıkız', species: 'CATTLE', breed: null,
  sex: 'FEMALE', birth_date: '2022-04-01', acquisition: 'BORN', acquired_on: null,
  group_id: null, mother_id: null, father_id: null, status: 'ACTIVE', notes: null,
  updated_at: '2026-08-01T10:00:00+00:00',
};

const DOL = {
  as_of: '2026-08-08',
  thresholds: {
    target_calving_interval_days: 360, problem_calving_interval_days: 390,
    service_period_low_days: 70, service_period_high_days: 90,
  },
  summary: {
    calving_interval_sample: 1, avg_calving_interval_days: 430,
    problem_interval_count: 1, age_at_first_calving_sample: 1,
    avg_age_at_first_calving_days: 800, service_period_sample: 0,
    avg_service_period_days: null, never_calved: 4,
    births_without_breeding_link: 2,
  },
  items: [
    {
      animal_id: 7, ear_tag: 'TR0123456789', name: 'Sarıkız', species: 'CATTLE',
      calving_count: 2, avg_calving_interval_days: 430,
      last_calving_interval_days: 430, age_at_first_calving_days: 800,
      service_period_days: null, services_per_conception: null,
      interval_is_problem: true, last_calving_on: '2026-03-01',
    },
    {
      animal_id: 8, ear_tag: 'TR0123456791', name: 'Benekli', species: 'CATTLE',
      calving_count: 1, avg_calving_interval_days: null,
      last_calving_interval_days: null, age_at_first_calving_days: null,
      service_period_days: null, services_per_conception: null,
      interval_is_problem: false, last_calving_on: '2026-01-15',
    },
  ],
};

const sayfa = (items: unknown[]) => ({data: {items, total: items.length, limit: 200, offset: 0}});

beforeEach(() => {
  can.mockReturnValue(true);
  get.mockImplementation((url: string) => {
    if (url === '/animals') return Promise.resolve(sayfa([INEK]));
    if (url === '/herd-fertility') return Promise.resolve({data: DOL});
    return Promise.resolve(sayfa([]));
  });
  post.mockResolvedValue({data: {id: 1, warnings: []}});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ciz = () => render(
  <ThemeProvider theme={createTheme()}><HerdBreeding /></ThemeProvider>,
);

/** MUI `select`'i açıp verilen etiketli seçeneği tıklar. */
const secim = async (label: RegExp, secenek: string | RegExp) => {
  fireEvent.mouseDown(screen.getByRole('combobox', {name: label}));
  const liste = await screen.findByRole('listbox');
  fireEvent.click(within(liste).getByText(secenek));
};

const dogumFormunuAc = async () => {
  fireEvent.click(await screen.findByRole('tab', {name: /Doğumlar/}));
  fireEvent.click(screen.getByRole('button', {name: /Yeni doğum/}));
  await screen.findByRole('dialog');
  await secim(/^Anne/, /TR0123456789/);
};

it('ölü doğumda yavru satırı gönderilmez ve form yavru bölümünü kapatır', async () => {
  ciz();
  await dogumFormunuAc();

  // Önce CANLI doğumda yavru eklenebiliyor olmalı — testin ikinci yarısının
  // anlamlı olması için bu kapının açık olduğunu görmemiz gerekiyor.
  fireEvent.click(screen.getByRole('button', {name: /Yavru ekle/}));
  expect(screen.getByLabelText(/Küpe no/)).toBeTruthy();

  await secim(/^Sonuç/, 'Ölü doğum');

  // Yavru bölümü KAPANDI: satır da, "yavru ekle" düğmesi de yok.
  expect(screen.queryByRole('button', {name: /Yavru ekle/})).toBeNull();
  expect(screen.queryByLabelText(/Küpe no/)).toBeNull();

  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [url, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(url).toBe('/animal-births');
  expect(gövde.outcome).toBe('STILLBORN');
  // ASIL İDDİA: yavru satırı gitmiyor. Gitseydi sürüde olmayan bir hayvan
  // kaydı oluşurdu.
  expect(gövde.offspring).toBeNull();
});

it('yavru satırı varken offspring_count alanı hiç gösterilmez', async () => {
  ciz();
  await dogumFormunuAc();

  expect(screen.getByLabelText(/Yavru sayısı/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: /Yavru ekle/}));
  // Satır girildiği anda sayı alanı kayboluyor: ikisi birden dolduğunda
  // sunucu sayıyı satırlardan alır ve elle yazılan sayı sessizce kaybolurdu.
  expect(screen.queryByLabelText(/Yavru sayısı/)).toBeNull();

  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));
  await waitFor(() => expect(post).toHaveBeenCalled());
  const [, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(gövde.offspring_count).toBeNull();
  expect(gövde.offspring).toHaveLength(1);
});

it('yeni tohumlamada gebelik sonucu sorulmaz ve gövdeye girmez', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Yeni tohumlama/}));
  await screen.findByRole('dialog');

  // Tohumlama ANINDA gebelik bilinmiyor; alan yok.
  expect(screen.queryByLabelText(/Gebelik sonucu/)).toBeNull();

  await secim(/Hayvan/, /TR0123456789/);
  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [url, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(url).toBe('/animal-breedings');
  expect('result' in gövde).toBe(false);
});

it('ortalama ÖRNEK SAYISIYLA gösterilir ve ölçüm yoksa sayı yazılmaz', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('tab', {name: /Göstergeler/}));

  // Aralık ortalaması TEK ölçümden çıkıyor ve bu görünür — örnek sayısı
  // olmadan 430 günü sürü gerçeği gibi sunmak yanlış karara götürür.
  await waitFor(() => expect(screen.getByText(/430 gün \(~14 ay\) \(1 ölçüm\)/)).toBeTruthy());
  // Servis periyodu için HİÇ ölçüm yok → ortalama yazılmıyor, "0 gün" değil.
  expect(screen.getByText('Ölçüm yok')).toBeTruthy();
});

it('tek doğumlu hayvan 0 aralık göstermez ve kapsam dışı sayılar yazılı', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('tab', {name: /Göstergeler/}));
  const tablo = await screen.findByTestId('tablo');

  // Tek doğumlu hayvan "Tek doğum" diyor; 0 gün DEMİYOR (0 onu mükemmel
  // gösterirdi).
  expect(within(tablo).getByText(/Son aralık: Tek doğum/)).toBeTruthy();
  expect(within(tablo).queryByText(/Son aralık: 0 gün/)).toBeNull();

  // Hiç doğurmamışlar ve bağsız doğumlar gizlenmiyor.
  const uyari = screen.getByText(/hiç doğurmamış/).closest('.MuiAlert-message');
  expect(uyari?.textContent).toContain('4 dişi');
  expect(uyari?.textContent).toContain('2 doğum');
});
