/**
 * BKÜ kataloğunun faaliyet formuna ÖNERİ olarak gelmesi (göç 20260901_0063).
 *
 * Üç iddia, üçü de sessizce bozulabilecek türden:
 *
 * 1. **Ürün seçilince hasat bekleme alanı DOLAR.** Kazanç bu: operatör süreyi
 *    hatırlamak zorunda değil.
 * 2. **Alan DÜZENLENEBİLİR KALIR ve temizlenince SESSİZCE GERİ DOLMAZ.** Bu
 *    ikincisi asıl kırılgan olan: doldurmayı bir efekte bağlayan bir sürüm her
 *    render'da değeri geri yazar ve kullanıcı alanı SİLEMEZ. O hâlde "katalog
 *    önerir, operatör karar verir" kuralı ekranda yalan olurdu.
 * 3. **Ürün seçildiyse girdi satırı İSTEKLE BİRLİKTE gider.** Yalnız süreyi
 *    doldurup ürünü göndermeseydik sunucu değeri OPERATOR kökenli sayardı;
 *    ekran "katalogdan geldi" derken kayıt "operatör yazdı" gösterirdi.
 */
import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
const post = vi.fn();
const saveOrQueue = vi.fn();
const readOutbox = vi.fn();
const flushOutbox = vi.fn();
vi.mock('../api', () => ({
  api: {get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a), put: vi.fn()},
  errorDetail: (_e: unknown, fallback: string) => fallback,
}));

const can = vi.fn();
vi.mock('../AuthContext', () => ({useAuth: () => ({can, activeCompany: {id: 1, name: 'A'}})}));

vi.mock('../farm/farmOutbox', () => ({
  saveOrQueueFarmCreate: (...args: unknown[]) => saveOrQueue(...args),
  readFarmOutbox: (...args: unknown[]) => readOutbox(...args),
  flushFarmOutbox: (...args: unknown[]) => flushOutbox(...args),
}));

vi.mock('../components/ResponsiveTable', () => ({
  default: () => <div />,
}));

import FieldActivities from './FieldActivities';

const SEZON = {
  id: 5, parcel_id: 2, season_year: 2026, crop: 'Domates', variety: null,
  status: 'ACTIVE', started_on: '2026-03-01', ended_on: null,
  planted_area_decare: '45.0000', notes: null, updated_at: '2026-08-01T10:00:00+00:00',
};

/** Aynı ürünün İKİ satırı: bitkiye özel olan, bitkiden bağımsız olanı yenmeli. */
const KATALOG = [
  {id: 1, product_id: 77, product_name: 'ORNEK BKU', crop: '', registration_no: null,
   preharvest_interval_days: 14, reentry_interval_days: 2, notes: null,
   status: 'ACTIVE', updated_at: '2026-08-01T10:00:00+00:00'},
  {id: 2, product_id: 77, product_name: 'ORNEK BKU', crop: 'Domates', registration_no: null,
   preharvest_interval_days: 21, reentry_interval_days: 3, notes: null,
   status: 'ACTIVE', updated_at: '2026-08-01T10:00:00+00:00'},
];

const POLICIES = {
  farm_area_override_policy: 'require_reason',
  farm_early_harvest_policy: 'require_reason',
  farm_spraying_dose_required: true,
};

function cevapla() {
  get.mockImplementation((url: string) => {
    if (url === '/field-activities') {
      return Promise.resolve({data: {items: [], total: 0, limit: 200, offset: 0}});
    }
    if (url === '/crop-seasons') {
      return Promise.resolve({data: {items: [SEZON], total: 1, limit: 200, offset: 0}});
    }
    if (url === '/company-settings') return Promise.resolve({data: POLICIES});
    if (url === '/plant-protection-products') {
      return Promise.resolve({data: {items: KATALOG, total: 2, limit: 200, offset: 0}});
    }
    return Promise.resolve({data: {items: [], total: 0, limit: 200, offset: 0}});
  });
}

const ciz = () =>
  render(
    <ThemeProvider theme={createTheme()}>
      <FieldActivities />
    </ThemeProvider>,
  );

/** MUI `select` gerçek bir <select> değil; listeyi açıp seçeneğe tıklıyoruz. */
async function secimYap(label: string | RegExp, secenek: string) {
  fireEvent.mouseDown(screen.getByRole('combobox', {name: label}));
  const liste = await screen.findByRole('listbox');
  fireEvent.click(within(liste).getByText(secenek));
  await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
}

const hasatBeklemeAlani = () =>
  screen.getByLabelText(/^Hasat bekleme/) as HTMLInputElement;

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(navigator, 'onLine', {configurable: true, value: true});
  readOutbox.mockImplementation(async () => []);
  flushOutbox.mockImplementation(async () => ({sent: 0, rejected: 0, pending: 0}));
  saveOrQueue.mockImplementation(async (
    _companyId: number, _kind: string, payload: Record<string, unknown>,
  ) => {
    await post('/field-activities', payload);
    return {durum: 'sent', kayit: {}};
  });
  can.mockReturnValue(true);
  post.mockResolvedValue({data: {id: 1}});
  cevapla();
});

afterEach(cleanup);

async function formuAc() {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Yeni faaliyet/i}));
  await secimYap('Sezon', 'Domates · 2026');
}

it('ürün seçilince hasat bekleme alanı katalogdan dolar', async () => {
  await formuAc();
  expect(hasatBeklemeAlani().value).toBe('');

  await secimYap(/BKÜ ürünü/, 'ORNEK BKU');

  // Bitkiye ÖZEL satır (21) kazanmalı, bitkiden bağımsız olan (14) değil.
  await waitFor(() => expect(hasatBeklemeAlani().value).toBe('21'));
  expect((screen.getByLabelText(/^Tarlaya giriş yasağı/) as HTMLInputElement).value)
    .toBe('3');
});

it('katalogdan dolan alan düzenlenebilir kalır ve temizlenince geri dolmaz', async () => {
  await formuAc();
  await secimYap(/BKÜ ürünü/, 'ORNEK BKU');
  await waitFor(() => expect(hasatBeklemeAlani().value).toBe('21'));

  // ÜSTÜNE YAZ: operatörün değeri kazanmalı.
  fireEvent.change(hasatBeklemeAlani(), {target: {value: '7'}});
  expect(hasatBeklemeAlani().value).toBe('7');

  // TEMİZLE: bir efekt eşitleseydi burada '21' geri gelirdi ve alan
  // silinemez olurdu. Bu satır tam olarak o kırılmayı yakalıyor.
  fireEvent.change(hasatBeklemeAlani(), {target: {value: ''}});
  expect(hasatBeklemeAlani().value).toBe('');

  // Başka bir alana dokunmak da geri doldurmamalı (render tetiklenir).
  fireEvent.change(screen.getByLabelText(/^Miktar /), {target: {value: '10'}});
  await waitFor(() => expect(hasatBeklemeAlani().value).toBe(''));
});

it('ürün seçildiyse girdi satırı istekle BİRLİKTE gider', async () => {
  await formuAc();
  await secimYap(/BKÜ ürünü/, 'ORNEK BKU');
  await waitFor(() => expect(hasatBeklemeAlani().value).toBe('21'));

  fireEvent.change(screen.getByLabelText(/Uygulanan alan/), {target: {value: '40'}});
  fireEvent.change(screen.getByLabelText(/^Miktar /), {target: {value: '10'}});
  fireEvent.change(screen.getByLabelText(/^Birim /), {target: {value: 'LT'}});
  fireEvent.change(screen.getByLabelText(/^Doz \*/), {target: {value: '2'}});
  fireEvent.change(screen.getByLabelText(/^Doz birimi /), {target: {value: 'LT/DA'}});

  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const govde = post.mock.calls[0][1] as Record<string, unknown>;
  expect(govde.preharvest_interval_days).toBe(21);
  expect(govde.inputs).toEqual([{
    product_id: 77, input_name: 'ORNEK BKU', quantity: '10', unit: 'LT',
    dose: '2', dose_unit: 'LT/DA',
  }]);
});

it('ürün seçilmediyse inputs alanı HİÇ gönderilmez', async () => {
  await formuAc();
  fireEvent.change(screen.getByLabelText(/Uygulanan alan/), {target: {value: '40'}});
  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const govde = post.mock.calls[0][1] as Record<string, unknown>;
  // Boş bir dizi göndermek "girdisiz faaliyet" demekle aynı değil; alan hiç
  // olmamalı.
  expect('inputs' in govde).toBe(false);
});
