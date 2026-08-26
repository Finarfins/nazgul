/**
 * Faaliyet ekranının İKİ kuralı — ikisi de sessizce bozulabilecek türden.
 *
 * 1. **Girdi kaydında `total_cost` GÖNDERİLMEZ.** Sunucu şeması onu reddediyor
 *    (`extra="forbid"`), ama asıl mesele şu: birisi "kullanıcı zaten toplamı
 *    görüyor, gönderelim" diye eklerse istek 422 ile kırılır. Test gövdeyi
 *    doğrudan denetliyor.
 * 2. **Alan aşımında gerekçe olmadan kaydedilemez.** Düğme kilitli kalmalı;
 *    aksi hâlde kullanıcı sunucudan 422 yiyor ve neden olduğunu anlamıyor.
 */
import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
const post = vi.fn();
const saveOrQueue = vi.fn();
const readOutbox = vi.fn();
const flushOutbox = vi.fn();
let outboxEntries: Array<Record<string, unknown>> = [];
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

// ResponsiveTable yerine sade bir liste: jsdom'da `useMediaQuery` her zaman
// false döndüğü için gerçek bileşen DataGrid çiziyor ve kart eylemleri hiç
// oluşmuyordu. Bu testin konusu tablo değil, faaliyet ekranının KURALLARI.
vi.mock('../components/ResponsiveTable', () => ({
  default: ({rows, cardActions, cardTitle}: any) => (
    <div>
      {rows.map((row: any) => (
        <div key={row.id}>
          <span>{cardTitle(row)}</span>
          {cardActions?.map((action: any) => (
            <button key={action.label} onClick={() => action.onClick(row)}>{action.label}</button>
          ))}
        </div>
      ))}
    </div>
  ),
}));

import FieldActivities from './FieldActivities';

const SEZON = {
  id: 5, parcel_id: 2, season_year: 2026, crop: 'Buğday', variety: null,
  status: 'ACTIVE', started_on: '2026-03-01', ended_on: null,
  planted_area_decare: '45.0000', notes: null, updated_at: '2026-08-01T10:00:00+00:00',
};
const FAALIYET = {
  id: 9, season_id: 5, activity_type: 'SPRAYING', performed_at: '2026-04-10T08:00:00+00:00',
  applied_area_decare: '40.0000', area_override_reason: null, operator_user_id: null,
  machine_id: null, reentry_interval_days: null, preharvest_interval_days: 21, notes: null,
};
let POLICIES = {
  farm_area_override_policy: 'require_reason',
  farm_early_harvest_policy: 'require_reason',
  farm_spraying_dose_required: true,
};

function cevapla(inputs: Array<Record<string, unknown>> = []) {
  get.mockImplementation((url: string) => {
    if (url === '/field-activities') return Promise.resolve({data: {items: [FAALIYET], total: 1, limit: 200, offset: 0}});
    if (url === '/crop-seasons') return Promise.resolve({data: {items: [SEZON], total: 1, limit: 200, offset: 0}});
    if (url === '/field-activities/9') return Promise.resolve({data: {...FAALIYET, inputs}});
    if (url === '/company-settings') return Promise.resolve({data: POLICIES});
    return Promise.resolve({data: {items: [], total: 0, limit: 200, offset: 0}});
  });
}

const ciz = () =>
  render(
    <ThemeProvider theme={createTheme()}>
      <FieldActivities />
    </ThemeProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(navigator, 'onLine', {configurable: true, value: true});
  outboxEntries = [];
  readOutbox.mockImplementation(async () => [...outboxEntries]);
  flushOutbox.mockImplementation(async () => ({sent: 0, rejected: 0, pending: outboxEntries.length}));
  saveOrQueue.mockImplementation(async (
    companyId: number,
    kind: string,
    payload: Record<string, unknown>,
    parentId?: number,
  ) => {
    if (navigator.onLine === false) {
      const operation = {
        operationId: `queued-${kind}`,
        companyId,
        kind,
        payload,
        parentId: parentId ?? null,
        createdAt: Date.now(),
        attemptCount: 0,
        rejectedReason: null,
      };
      outboxEntries = [operation];
      return {durum: 'queued', operationId: operation.operationId};
    }
    const url = kind === 'activity'
      ? '/field-activities'
      : `/field-activities/${parentId}/inputs`;
    const response = await post(url, payload);
    return {durum: 'sent', kayit: response.data};
  });
  can.mockReturnValue(true);
  POLICIES = {
    farm_area_override_policy: 'require_reason',
    farm_early_harvest_policy: 'require_reason',
    farm_spraying_dose_required: true,
  };
  cevapla();
  post.mockResolvedValue({data: {id: 1}});
});
afterEach(() => {
  cleanup();
  Object.defineProperty(navigator, 'onLine', {configurable: true, value: true});
});

it('girdi kaydında total_cost GÖNDERİLMEZ — toplam sunucuda hesaplanır', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Girdiler/i}));

  // Detay diyaloğu ASENKRON açılıyor (faaliyet detayı ayrıca çekiliyor).
  const alan = (etiket: RegExp) => screen.getByRole('textbox', {name: etiket});
  fireEvent.change(await screen.findByRole('textbox', {name: /Girdi adı/}), {target: {value: 'Herbisit'}});
  fireEvent.change(alan(/^Miktar/), {target: {value: '10'}});
  fireEvent.change(alan(/^Birim$/), {target: {value: 'LT'}});
  fireEvent.change(alan(/Birim fiyat/), {target: {value: '250'}});
  // Fixture faaliyeti İLAÇLAMA; FAZ 6'dan beri doz ve doz birimi zorunlu.
  fireEvent.change(alan(/^Doz$/), {target: {value: '100'}});
  fireEvent.change(alan(/Doz birimi/), {target: {value: 'ML/DEKAR'}});

  fireEvent.click(screen.getByRole('button', {name: /Girdiyi ekle/}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [url, gövde] = post.mock.calls[0];
  expect(url).toBe('/field-activities/9/inputs');
  // ASIL İDDİA: toplam istemciden gitmiyor.
  expect(gövde).not.toHaveProperty('total_cost');
  expect(gövde).toMatchObject({
    input_name: 'Herbisit', quantity: '10', unit: 'LT', unit_cost: '250',
    dose: '100', dose_unit: 'ML/DEKAR',
  });
});

it('toplam tutar yalnız ÖNİZLEME olarak etiketlenir', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Girdiler/i}));
  fireEvent.change(await screen.findByRole('textbox', {name: /^Miktar/}), {target: {value: '10'}});
  fireEvent.change(screen.getByRole('textbox', {name: /Birim fiyat/}), {target: {value: '250'}});

  // Metin "önizleme" demeli; kullanıcı bunun kaydedilen değer olduğunu sanmamalı.
  const ipucu = await screen.findByText(/Önizleme/);
  expect(ipucu.textContent).toMatch(/sunucunun hesaplayacağı/i);
  expect(ipucu.textContent).toMatch(/2\.500,00/);
});

it('faaliyet detayı girdi toplamını korur', async () => {
  cevapla([
    {
      id: 21, field_activity_id: 9, input_name: 'Herbisit', quantity: '10.0000', unit: 'LT',
      unit_cost: '250.0000', total_cost: '2500.0050', dose: '100.0000', dose_unit: 'ML/DEKAR',
    },
    {
      id: 22, field_activity_id: 9, input_name: 'Yaprak gübresi', quantity: '1.0000', unit: 'LT',
      unit_cost: '125.5000', total_cost: '125.5050', dose: null, dose_unit: null,
    },
  ]);
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Girdiler/i}));

  expect(await screen.findByText('Faaliyet toplamı')).toBeInTheDocument();
  expect(screen.getByText(/2\.625,51/)).toBeInTheDocument();
});

it('faaliyet toplamı bilinmeyen maliyeti sıfır saymaz', async () => {
  cevapla([{
    id: 21, field_activity_id: 9, input_name: 'Fiyatı beklenen girdi', quantity: '1.0000', unit: 'LT',
    unit_cost: null, total_cost: null, dose: null, dose_unit: null,
  }]);
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Girdiler/i}));

  const totalRow = await screen.findByTestId('activity-total-row');
  expect(totalRow).toHaveTextContent('Faaliyet toplamı');
  expect(totalRow).toHaveTextContent('—');
});

it('alan aşımında gerekçe girilmeden kaydedilemez', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Yeni faaliyet/}));

  // Sezonun ekilen alanı 45 dekar; 60 girince aşım uyarısı çıkmalı.
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Sezon/}));
  fireEvent.click(await screen.findByRole('option', {name: /Buğday/}));
  fireEvent.change(screen.getByRole('textbox', {name: /Uygulanan alan/}), {target: {value: '60'}});

  const kaydet = screen.getByRole('button', {name: 'Kaydet'});
  expect(await screen.findByText(/gerekçesi kayda geçmeli/)).toBeTruthy();
  expect(kaydet).toBeDisabled();

  // Gerekçe girilince açılır.
  fireEvent.change(screen.getByRole('textbox', {name: /Aşım gerekçesi/}), {target: {value: 'İkinci geçiş yapıldı'}});
  await waitFor(() => expect(kaydet).not.toBeDisabled());

  fireEvent.click(kaydet);
  await waitFor(() => expect(post).toHaveBeenCalledWith('/field-activities', expect.objectContaining({
    area_override_reason: 'İkinci geçiş yapıldı',
  })));
});

it('girdi yetkisi olmayan kullanıcıya girdi formu gösterilmez', async () => {
  // Sunucu zaten reddeder; buradaki gizleme kullanıcıyı 403 duvarından korur.
  can.mockImplementation((izin: string) => izin !== 'farm.inputs');
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Girdiler/i}));
  await screen.findByText(/henüz girdi bağlanmamış/);
  expect(screen.queryByRole('button', {name: /Girdiyi ekle/})).toBeNull();
});

it('ilaçlamada uygulanan alan girilmeden kaydedilemez', async () => {
  // Konu #2 değişmezi. Sunucu da reddediyor; buradaki kilit kullanıcıyı 422
  // duvarına çarpmaktan kurtarıyor.
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Yeni faaliyet/}));

  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Sezon/}));
  fireEvent.click(await screen.findByRole('option', {name: /Buğday/}));
  // Varsayılan tür zaten İlaçlama.
  const kaydet = screen.getByRole('button', {name: 'Kaydet'});
  expect(kaydet).toBeDisabled();
  expect(await screen.findByText(/doz ve kalıntı hesabı buna dayanır/)).toBeTruthy();

  fireEvent.change(screen.getByRole('textbox', {name: /Uygulanan alan/}), {target: {value: '40'}});
  await waitFor(() => expect(kaydet).not.toBeDisabled());
});

it('sulamada uygulanan alan zorunlu DEĞİL', async () => {
  // Kuralın yalnız ilaçlamaya uygulandığını çiviler: aşırı zorlamıyoruz.
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Yeni faaliyet/}));
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Sezon/}));
  fireEvent.click(await screen.findByRole('option', {name: /Buğday/}));

  fireEvent.mouseDown(screen.getByRole('combobox', {name: /^Tür/}));
  fireEvent.click(await screen.findByRole('option', {name: 'Sulama'}));

  await waitFor(() => expect(screen.getByRole('button', {name: 'Kaydet'})).not.toBeDisabled());
});

it('ilaçlama girdisinde doz ve doz birimi zorunlu', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Girdiler/i}));

  const alan = (etiket: RegExp) => screen.getByRole('textbox', {name: etiket});
  fireEvent.change(await screen.findByRole('textbox', {name: /Girdi adı/}), {target: {value: 'Herbisit'}});
  fireEvent.change(alan(/^Miktar/), {target: {value: '5'}});
  fireEvent.change(alan(/^Birim$/), {target: {value: 'LT'}});

  const ekle = screen.getByRole('button', {name: /Girdiyi ekle/});
  expect(ekle).toBeDisabled();

  fireEvent.change(alan(/^Doz$/), {target: {value: '100'}});
  // Yalnız doz yetmez.
  await waitFor(() => expect(ekle).toBeDisabled());

  fireEvent.change(alan(/Doz birimi/), {target: {value: 'ML/DEKAR'}});
  await waitFor(() => expect(ekle).not.toBeDisabled());
});

it('firma politikası block ise alan aşımını gerekçeyle bile kaydetmez', async () => {
  POLICIES = {...POLICIES, farm_area_override_policy: 'block'};
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Yeni faaliyet/}));
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Sezon/}));
  fireEvent.click(await screen.findByRole('option', {name: /Buğday/}));
  fireEvent.change(screen.getByRole('textbox', {name: /Uygulanan alan/}), {target: {value: '60'}});
  expect(await screen.findByText(/politikas.*izin vermiyor/i)).toBeTruthy();
  expect(screen.queryByRole('textbox', {name: /Aşım gerekçesi/})).toBeNull();
  expect(screen.getByRole('button', {name: 'Kaydet'})).toBeDisabled();
});

it('doz doğrulaması kapalıysa ilaç girdisini doz olmadan kabul eder', async () => {
  POLICIES = {...POLICIES, farm_spraying_dose_required: false};
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Girdiler/i}));
  fireEvent.change(await screen.findByRole('textbox', {name: /Girdi adı/}), {target: {value: 'Herbisit'}});
  fireEvent.change(screen.getByRole('textbox', {name: /^Miktar/}), {target: {value: '5'}});
  fireEvent.change(screen.getByRole('textbox', {name: /^Birim$/}), {target: {value: 'LT'}});
  expect(screen.getByRole('button', {name: /Girdiyi ekle/})).not.toBeDisabled();
});

it('çevrimdışı kuyruğa alınan faaliyet sonrası sunucu listesini yenilemez', async () => {
  Object.defineProperty(navigator, 'onLine', {configurable: true, value: false});
  ciz();
  await waitFor(() => expect(readOutbox).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole('button', {name: /Yeni faaliyet/}));
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Sezon/}));
  fireEvent.click(await screen.findByRole('option', {name: /Buğday/}));
  fireEvent.change(screen.getByRole('textbox', {name: /Uygulanan alan/}), {target: {value: '40'}});
  get.mockClear();
  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));
  await waitFor(() => expect(screen.getByText(/gönderilmeyi bekleyen 1 kayıt/i)).toBeTruthy());
  expect(outboxEntries).toHaveLength(1);
  expect(get).not.toHaveBeenCalled();
});

it('çevrimdışı kuyruğa alınan girdi sonrası sunucu detayını yeniden açmaz', async () => {
  ciz();
  await waitFor(() => expect(readOutbox).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole('button', {name: /Girdiler/i}));
  fireEvent.change(await screen.findByRole('textbox', {name: /Girdi adı/}), {target: {value: 'Herbisit'}});
  fireEvent.change(screen.getByRole('textbox', {name: /^Miktar/}), {target: {value: '5'}});
  fireEvent.change(screen.getByRole('textbox', {name: /^Birim$/}), {target: {value: 'LT'}});
  fireEvent.change(screen.getByRole('textbox', {name: /^Doz$/}), {target: {value: '100'}});
  fireEvent.change(screen.getByRole('textbox', {name: /Doz birimi/}), {target: {value: 'ML/DEKAR'}});
  Object.defineProperty(navigator, 'onLine', {configurable: true, value: false});
  get.mockClear();
  fireEvent.click(screen.getByRole('button', {name: /Girdiyi ekle/}));
  await waitFor(() => expect(screen.getByText(/gönderilmeyi bekleyen 1 kayıt/i)).toBeTruthy());
  expect(outboxEntries).toHaveLength(1);
  expect(get).not.toHaveBeenCalled();
});
