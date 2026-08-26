import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {MemoryRouter} from 'react-router-dom';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
let authLoading = false;
let authCanFarm = true;
let authUser: {id: number} | null = {id: 42};
vi.mock('../api', () => ({
  api: {get: (...args: unknown[]) => get(...args)},
  errorDetail: (_error: unknown, fallback: string) => fallback,
}));
vi.mock('../AuthContext', () => ({useAuth: () => ({
  can: (permission: string) => permission !== 'farm.manage' || authCanFarm,
  activeCompany: {id: 7, name: 'Farm Seven'}, user: authUser, loading: authLoading,
})}));
const saveOrQueue = vi.fn();
vi.mock('../farm/farmOutbox', () => ({
  flushFarmOutbox: vi.fn().mockResolvedValue({sent: 0, rejected: 0, pending: 0}),
  readFarmOutbox: vi.fn().mockResolvedValue([]),
  saveOrQueueFarmCreate: (...args: unknown[]) => saveOrQueue(...args),
}));

import {DEFAULT_FARM_POLICIES, type CropSeason} from '../farm/farmApi';
import {
  cacheFarmMetadata,
  setFarmMetadataStorageProviderForTests,
  type FarmMetadataStorage,
} from '../farm/farmMetadataCache';
import QuickActivity from './QuickActivity';

const SEASON: CropSeason = {id: 5, parcel_id: 2, season_year: 2026, crop: 'Wheat', variety: null,
  status: 'ACTIVE', started_on: '2026-03-01', ended_on: null,
  planted_area_decare: '40.0000', notes: null, updated_at: '2026-08-08T10:00:00Z'};

const memoryStorage = (): FarmMetadataStorage => {
  const values = new Map<string, string>();
  return {
    get length() { return values.size; },
    key: index => [...values.keys()][index] ?? null,
    getItem: storageKey => values.get(storageKey) ?? null,
    setItem: (storageKey, value) => { values.set(storageKey, value); },
    removeItem: storageKey => { values.delete(storageKey); },
  };
};

const renderPage = () => render(
  <MemoryRouter><ThemeProvider theme={createTheme()}><QuickActivity /></ThemeProvider></MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  get.mockReset();
  saveOrQueue.mockReset();
  const storage = memoryStorage();
  setFarmMetadataStorageProviderForTests(() => storage);
  Object.defineProperty(navigator, 'onLine', {configurable: true, value: true});
  authLoading = false;
  authCanFarm = true;
  authUser = {id: 42};
  saveOrQueue.mockResolvedValue({durum: 'queued', operationId: 'offline-1'});
});
afterEach(() => {
  setFarmMetadataStorageProviderForTests();
  cleanup();
});

it('cold offline start reuses company-scoped cached seasons and policies for a real queued entry', async () => {
  cacheFarmMetadata({companyId: 7, userId: 42}, [SEASON], {
    ...DEFAULT_FARM_POLICIES,
    farm_spraying_dose_required: false,
  });
  get.mockRejectedValue(new Error('offline'));
  Object.defineProperty(navigator, 'onLine', {configurable: true, value: false});
  renderPage();
  expect(await screen.findByText(/cihaz önbelleğinden açıldı/i)).toBeTruthy();
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Sezon/}));
  fireEvent.click(await screen.findByRole('option', {name: /Wheat/}));
  fireEvent.change(screen.getByRole('textbox', {name: /Uygulanan alan/}), {target: {value: '20'}});
  fireEvent.change(screen.getByRole('textbox', {name: /^Ad/}), {target: {value: 'Herbicide'}});
  fireEvent.change(screen.getByRole('textbox', {name: /^Miktar/}), {target: {value: '2'}});
  fireEvent.change(screen.getByRole('textbox', {name: /^Birim$/}), {target: {value: 'LT'}});
  const save = screen.getByRole('button', {name: 'Kaydet'});
  expect(save).not.toBeDisabled();
  fireEvent.click(save);
  await waitFor(() => expect(saveOrQueue).toHaveBeenCalledWith(7, 'activity', expect.objectContaining({
    season_id: 5,
    inputs: [expect.objectContaining({dose: null, dose_unit: null})],
  })));
});

it('does not restore cached metadata before auth and farm permission context is ready', async () => {
  cacheFarmMetadata({companyId: 7, userId: 42}, [SEASON], DEFAULT_FARM_POLICIES);
  get.mockRejectedValue(new Error('offline'));

  authLoading = true;
  const loadingView = renderPage();
  await waitFor(() => expect(get).not.toHaveBeenCalled());
  expect(screen.queryByText(/cihaz önbelleğinden açıldı/i)).toBeNull();
  loadingView.unmount();

  authLoading = false;
  authCanFarm = false;
  renderPage();
  await waitFor(() => expect(get).not.toHaveBeenCalled());
  expect(screen.queryByText(/cihaz önbelleğinden açıldı/i)).toBeNull();
});

it('continues to the online API and leaves loading state when storage acquisition throws', async () => {
  setFarmMetadataStorageProviderForTests(() => { throw new Error('storage disabled'); });
  get.mockImplementation((url: string) => url === '/crop-seasons'
    ? Promise.resolve({data: {items: [], total: 0, limit: 200, offset: 0}})
    : Promise.resolve({data: DEFAULT_FARM_POLICIES}));

  renderPage();

  expect(await screen.findByText(/Önce bir ekim sezonu açın/i)).toBeTruthy();
  expect(get).toHaveBeenCalledWith('/crop-seasons', {params: {limit: 200}});
  expect(get).toHaveBeenCalledWith('/company-settings');
  expect(screen.queryByText(/Sezonlar ve firma politikaları yüklenemedi/i)).toBeNull();
});

it('fetches and obeys block area policy and enabled dose validation', async () => {
  get.mockImplementation((url: string) => url === '/crop-seasons'
    ? Promise.resolve({data: {items: [SEASON], total: 1, limit: 200, offset: 0}})
    : Promise.resolve({data: {...DEFAULT_FARM_POLICIES, farm_area_override_policy: 'block'}}));
  renderPage();
  fireEvent.mouseDown(await screen.findByRole('combobox', {name: /Sezon/}));
  fireEvent.click(await screen.findByRole('option', {name: /Wheat/}));
  fireEvent.change(screen.getByRole('textbox', {name: /Uygulanan alan/}), {target: {value: '60'}});
  expect(await screen.findByText(/politikas.*izin vermiyor/i)).toBeTruthy();
  expect(screen.getByRole('button', {name: 'Kaydet'})).toBeDisabled();
  expect(screen.getByRole('textbox', {name: /^Doz$/})).toBeRequired();
});
