import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
const post = vi.fn();
const saveOrQueue = vi.fn();
vi.mock('../api', () => ({
  api: {get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args)},
  errorDetail: (_error: unknown, fallback: string) => fallback,
}));
vi.mock('../AuthContext', () => ({useAuth: () => ({
  can: () => true, activeCompany: {id: 1, name: 'A'},
})}));
vi.mock('../components/ResponsiveTable', () => ({default: () => <div />}));
vi.mock('../farm/farmOutbox', () => ({
  flushFarmOutbox: vi.fn().mockResolvedValue({sent: 0, rejected: 0, pending: 0}),
  readFarmOutbox: vi.fn().mockResolvedValue([]),
  removeFarmOperation: vi.fn(),
  saveOrQueueFarmCreate: (...args: unknown[]) => saveOrQueue(...args),
}));

import FieldHarvests from './FieldHarvests';

const SEASON = {id: 5, parcel_id: 2, season_year: 2026, crop: 'Wheat', variety: null,
  status: 'ACTIVE', started_on: '2026-03-01', ended_on: null,
  planted_area_decare: '40.0000', notes: null, updated_at: '2026-08-08T10:00:00Z'};
let policy: 'warn' | 'require_reason' | 'block' = 'require_reason';
let decisionAvailable = true;

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(navigator, 'onLine', {configurable: true, value: true});
  policy = 'require_reason';
  decisionAvailable = true;
  saveOrQueue.mockResolvedValue({durum: 'sent', kayit: {id: 1}});
  get.mockImplementation((url: string, config?: {params?: Record<string, unknown>}) => {
    if (url === '/field-harvests') return Promise.resolve({data: {items: [], total: 0, limit: 200, offset: 0}});
    if (url === '/crop-seasons') return Promise.resolve({data: {items: [SEASON], total: 1, limit: 200, offset: 0}});
    if (url === '/field-safety') return Promise.resolve({data: {as_of: '2026-08-08', harvest_blocks: [], reentry_blocks: []}});
    if (url === '/company-settings') return Promise.resolve({data: {
      farm_area_override_policy: 'require_reason', farm_early_harvest_policy: policy,
      farm_spraying_dose_required: true,
    }});
    if (url === '/field-harvest-decision') {
      if (!decisionAvailable) return Promise.reject({response: {status: 503}});
      return Promise.resolve({data: {
        season_id: 5, harvested_on: config?.params?.harvested_on, policy,
        decision: policy === 'warn' ? 'warning' : policy === 'block' ? 'blocked' : 'reason_required',
        safe_from: '2026-05-31', warning: '2026-05-01 tarihli işlem için güvenli tarih 2026-05-31', blocking: [],
      }});
    }
    throw new Error(url);
  });
});
afterEach(cleanup);

const renderPage = () => render(<ThemeProvider theme={createTheme()}><FieldHarvests /></ThemeProvider>);

async function openAndSelect(date: string) {
  renderPage();
  fireEvent.click(await screen.findByRole('button', {name: /Hasat gir/}));
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Sezon/}));
  fireEvent.click(await screen.findByRole('option', {name: /Wheat/}));
  fireEvent.change(screen.getByLabelText(/Hasat tarihi/), {target: {value: date}});
}

it('selected historical date is sent to the server and its PHI decision is displayed', async () => {
  await openAndSelect('2026-05-15');
  await waitFor(() => expect(get).toHaveBeenCalledWith('/field-harvest-decision', {params: {
    season_id: 5, harvested_on: '2026-05-15',
  }}));
  expect(await screen.findByText(/15\.05\.2026 PHI kararı/)).toBeTruthy();
  expect(screen.getByText(/gerekçe zorunlu/i)).toBeTruthy();
  expect(screen.getByRole('textbox', {name: /Erken hasat gerekçesi/})).toBeTruthy();
});

it('warn policy displays warning but does not require an override reason', async () => {
  policy = 'warn';
  await openAndSelect('2026-05-15');
  expect(await screen.findByText(/uyarıyla kayda izin veriyor/i)).toBeTruthy();
  expect(screen.queryByRole('textbox', {name: /Erken hasat gerekçesi/})).toBeNull();
  fireEvent.change(screen.getByRole('textbox', {name: /^Miktar/}), {target: {value: '100'}});
  await waitFor(() => expect(screen.getByRole('button', {name: 'Kaydet'})).not.toBeDisabled());
});

it('block policy displays the decision and keeps save disabled', async () => {
  policy = 'block';
  await openAndSelect('2026-05-15');
  expect(await screen.findByText(/politikası bu tarihte hasadı engelliyor/i)).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', {name: /^Miktar/}), {target: {value: '100'}});
  expect(screen.getByRole('button', {name: 'Kaydet'})).toBeDisabled();
});

it('reacts to an offline event and queues after an online PHI decision failure', async () => {
  decisionAvailable = false;
  saveOrQueue.mockResolvedValue({durum: 'queued'});

  await openAndSelect('2026-05-15');
  fireEvent.change(screen.getByRole('textbox', {name: /^Miktar/}), {target: {value: '12.5'}});

  expect(await screen.findByText(/Hasat bekleme kararı alınamadı/i)).toBeTruthy();
  expect(screen.getByRole('button', {name: 'Kaydet'})).toBeDisabled();
  expect(get.mock.calls.some(([url]) => url === '/field-harvest-decision')).toBe(true);

  Object.defineProperty(navigator, 'onLine', {configurable: true, value: false});
  fireEvent(window, new Event('offline'));

  expect(screen.getByText(/sunucu güvenlik ve politika doğrulamasını senkronizasyon sırasında yapacaktır/i)).toBeTruthy();
  expect(screen.queryByText(/Hasat bekleme süresi dolmuş/i)).toBeNull();
  expect(screen.queryByText(/Hasat bekleme kararı alınamadı/i)).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', {name: 'Kaydet'})).not.toBeDisabled());

  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));
  await waitFor(() => expect(saveOrQueue).toHaveBeenCalledTimes(1));
  expect(saveOrQueue).toHaveBeenCalledWith(1, 'harvest', expect.objectContaining({
    season_id: 5,
    harvested_on: '2026-05-15',
    quantity: '12.5',
  }));
});
