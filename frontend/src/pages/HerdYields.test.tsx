/**
 * Süt & Besi ekranının İKİ kuralı — ikisi de sessizce yanlış sayı üretir.
 *
 * 1. **Süt kaydı hayvana YA DA gruba gider; karşı taraf `null` GÖNDERİLİR.**
 *    Sunucu ikisi birden doluysa 422 veriyor, veritabanında da CHECK var. Form
 *    tek seçim yapıyor; yapmasaydı aynı süt iki kez sayılır ve hayvan başına
 *    verim yanlış çıkardı.
 *
 * 2. **Günlük ortalama SAĞIM YAPILAN güne bölünür.** Aralıktaki tüm günlere
 *    bölmek, kayıt girilmemiş günleri üretim yapılmamış saymak olurdu — kayıt
 *    tutmayan bir işletmenin verimi olduğundan düşük görünürdü.
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

vi.mock('../components/ResponsiveTable', () => ({
  default: ({rows, cardTitle}: any) => (
    <div data-testid="tablo">
      {rows.map((row: any, i: number) => <div key={row.id ?? i}>{cardTitle(row)}</div>)}
    </div>
  ),
}));

import HerdYields from './HerdYields';

const INEK = {
  id: 7, ear_tag: 'TR0123456789', name: 'Sarıkız', species: 'CATTLE', breed: null,
  sex: 'FEMALE', birth_date: '2022-04-01', acquisition: 'BORN', acquired_on: null,
  group_id: null, mother_id: null, father_id: null, status: 'ACTIVE', notes: null,
  updated_at: '2026-08-01T10:00:00+00:00',
};
const SURU = {
  id: 3, code: 'S1', name: 'Sağmal Sürü', species: 'CATTLE', location: null,
  head_count: 20, status: 'ACTIVE', notes: null,
  updated_at: '2026-08-01T10:00:00+00:00',
};

/** N gün öncesinin ISO tarihi — ekran varsayılan aralığı son 30 gün alıyor. */
const gun = (n = 0) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

// ÜÇ KAYIT, İKİ GÜN: bugün 12,5 + 17,5 = 30; dün 10. Toplam 40.
//
// Sayılar bilerek üçü de AYRI çıkacak şekilde seçildi, yoksa test hangi bölenin
// kullanıldığını ayırt edemezdi:
//   * gün başına (DOĞRU)      → 40 / 2 gün   = 20 litre
//   * kayıt başına (YANLIŞ)   → 40 / 3 kayıt = 13,33 litre
//   * aralık günü (YANLIŞ)    → 40 / 31 gün  = 1,29 litre
const SUT = [
  {id: 1, animal_id: 7, group_id: null, milked_on: gun(0), session: 'SABAH',
   quantity_liters: '12.5', notes: null},
  {id: 2, animal_id: 7, group_id: null, milked_on: gun(0), session: 'AKSAM',
   quantity_liters: '17.5', notes: null},
  {id: 3, animal_id: 7, group_id: null, milked_on: gun(1), session: 'SABAH',
   quantity_liters: '10', notes: null},
];

const sayfa = (items: unknown[], total = items.length, offset = 0) => (
  {data: {items, total, limit: 200, offset}}
);

beforeEach(() => {
  can.mockReturnValue(true);
  get.mockImplementation((url: string) => {
    if (url === '/animals') return Promise.resolve(sayfa([INEK]));
    if (url === '/animal-groups') return Promise.resolve(sayfa([SURU]));
    if (url === '/milk-yields') return Promise.resolve(sayfa(SUT));
    return Promise.resolve(sayfa([]));
  });
  post.mockResolvedValue({data: {id: 1}});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ciz = () => render(
  <ThemeProvider theme={createTheme()}><HerdYields /></ThemeProvider>,
);

it('günlük ortalama SAĞIM GÜNÜNE bölünür, kayıt sayısına değil', async () => {
  ciz();
  // Toplam 40 litre; iki sağım günü → gün başına 20 litre.
  await waitFor(() => expect(screen.getByText('40 litre')).toBeTruthy());
  expect(screen.getByText('20 litre')).toBeTruthy();
  // Yanlış bölenlerin üreteceği sayılar EKRANDA OLMAMALI.
  expect(screen.queryByText(/13,33 litre/)).toBeNull();
  expect(screen.queryByText(/1,29 litre/)).toBeNull();
  // Bölenin ne olduğu ekranda yazılı; kullanıcı ortalamaya ancak bunu
  // görerek güvenebilir.
  expect(screen.getByText(/2 sağım günü/)).toBeTruthy();
  expect(screen.getByText(/kayıt girilmemiş günler bölene katılmıyor/)).toBeTruthy();
});

it('süt kaydı gruba yazılınca animal_id NULL gider', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Süt kaydet/}));
  await screen.findByRole('dialog');

  // Hedefi SÜRÜ yap.
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Kayıt kime ait/}));
  const hedefler = await screen.findByRole('listbox');
  fireEvent.click(within(hedefler).getByText(/^Sürü/));

  fireEvent.mouseDown(screen.getByRole('combobox', {name: /^Sürü/}));
  const suruler = await screen.findByRole('listbox');
  fireEvent.click(within(suruler).getByText(/Sağmal Sürü/));

  fireEvent.change(screen.getByLabelText(/Miktar \(litre\)/), {target: {value: '120,5'}});
  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [url, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(url).toBe('/milk-yields');
  // ASIL İDDİA: karşı taraf null. İkisi birden dolu gitse aynı süt iki kez
  // sayılırdı (sunucu da 422 verirdi).
  expect(gövde.group_id).toBe(3);
  expect(gövde.animal_id).toBeNull();
  // Virgül noktaya çevrildi ve STRING kaldı (kayan nokta hatası olmasın).
  expect(gövde.quantity_liters).toBe('120.5');
});

it('hedef değişince önceki seçim TEMİZLENİR', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Süt kaydet/}));
  await screen.findByRole('dialog');

  // Önce hayvan seç.
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /^Hayvan/}));
  fireEvent.click(within(await screen.findByRole('listbox')).getByText(/TR0123456789/));

  // Sonra hedefi sürüye çevir: hayvan seçimi kalmamalı, yoksa iki alan da
  // dolu bir gövde oluşabilirdi.
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Kayıt kime ait/}));
  fireEvent.click(within(await screen.findByRole('listbox')).getByText(/^Sürü/));

  fireEvent.mouseDown(screen.getByRole('combobox', {name: /^Sürü/}));
  fireEvent.click(within(await screen.findByRole('listbox')).getByText(/Sağmal Sürü/));
  fireEvent.change(screen.getByLabelText(/Miktar \(litre\)/), {target: {value: '5'}});
  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(gövde.animal_id).toBeNull();
  expect(gövde.group_id).toBe(3);
});

it('ikinci sayfadaki büyük kesirli sütü toplam ve ortalamaya tam ekler', async () => {
  const ilkSayfa = Array.from({length: 200}, (_, i) => ({
    id: i + 1, animal_id: 7, group_id: null,
    milked_on: i === 0 ? gun(0) : gun(40), session: 'SABAH',
    quantity_liters: i === 0 ? '0.0001' : '1', notes: null,
  }));
  const ikinciSayfa = [{
    id: 201, animal_id: 7, group_id: null, milked_on: gun(0), session: 'AKSAM',
    quantity_liters: '9007199254740993.1234', notes: null,
  }];
  get.mockImplementation((url: string, config?: {params?: {offset?: number}}) => {
    if (url === '/animals') return Promise.resolve(sayfa([INEK]));
    if (url === '/animal-groups') return Promise.resolve(sayfa([SURU]));
    if (url === '/milk-yields') {
      const offset = config?.params?.offset ?? 0;
      return Promise.resolve(offset === 0
        ? sayfa(ilkSayfa, 201, 0)
        : sayfa(ikinciSayfa, 201, 200));
    }
    return Promise.resolve(sayfa([]));
  });

  ciz();

  const tamDeger = '9.007.199.254.740.993,1235 litre';
  await waitFor(() => expect(screen.getAllByText(tamDeger)).toHaveLength(2));
  expect(get).toHaveBeenCalledWith('/milk-yields', {params: {limit: 200, offset: 200}});
});
