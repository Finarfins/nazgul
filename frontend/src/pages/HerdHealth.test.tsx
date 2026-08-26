/**
 * Sürü sağlığı ekranının FAZ 4 kuralları — üçü de sessizce bozulabilir.
 *
 * 1. **İki liste AYRI.** Türetilmiş zorunlu takvim ("mevzuat gereği
 *    yapılmalıydı") ile elle girilen plan ("planladım, yapmadım") ayrı
 *    sekmelerde. Biri diğerinin sayısını kullanmaya başlarsa, hiç plan
 *    girmemiş bir işletmeye "aşınız tamam" denir.
 *
 * 2. **Pencere ARALIK olarak gösterilir.** Tek tarihe indirmek ya erken uyarır
 *    ya zorunlu pencereyi kaçırtır.
 *
 * 3. **Kapsam DIŞI kalanlar ekranda yazılı.** Kodsuz kayıt ve doğum tarihi
 *    girilmemiş hayvan takvime girmiyor; gizlenirse "eksiği yok" cevabı yanlış
 *    bir güvence olur.
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
  default: ({rows, cardTitle, cardSubtitle, cardFields}: any) => (
    <div data-testid="tablo">
      {rows.map((row: any, i: number) => (
        <div key={row.id ?? i}>
          <span>{cardTitle(row)}</span>
          <span>{cardSubtitle?.(row)}</span>
          {cardFields?.map((f: any) => (
            <span key={f.label}>{f.label}: {String(f.value(row))}</span>
          ))}
        </div>
      ))}
    </div>
  ),
}));

import HerdHealth from './HerdHealth';

const INEK = {
  id: 7, ear_tag: 'TR0123456789', name: 'Sarıkız', species: 'CATTLE', breed: null,
  sex: 'FEMALE', birth_date: '2022-04-01', acquisition: 'BORN', acquired_on: null,
  group_id: null, mother_id: null, father_id: null, status: 'ACTIVE', notes: null,
  updated_at: '2026-08-01T10:00:00+00:00',
};

const TAKVIM = {
  as_of: '2026-08-08',
  summary: {
    overdue: 1, due: 0, upcoming: 0, unknown: 0,
    uncoded_vaccinations: 2, unknown_birth_date: 1,
  },
  items: [{
    animal_id: 7, ear_tag: 'TR0123456789', name: 'Sarıkız', species: 'CATTLE',
    sex: 'FEMALE', vaccine_code: 'FMD', vaccine_name: 'Şap', state: 'OVERDUE',
    dose_no: 3, due_from: '2026-05-01', due_to: '2026-07-01',
    last_applied_on: '2026-01-01', overdue_days: 38,
    basis: 'Şap tekrarı son dozdan 4–6 ay sonra',
  }],
};

const sayfa = (items: unknown[], total = items.length, offset = 0) => (
  {data: {items, total, limit: 200, offset}}
);

beforeEach(() => {
  can.mockReturnValue(true);
  get.mockImplementation((url: string) => {
    if (url === '/animals') return Promise.resolve(sayfa([INEK]));
    if (url === '/vaccination-calendar') return Promise.resolve({data: TAKVIM});
    return Promise.resolve(sayfa([]));
  });
  post.mockResolvedValue({data: {id: 1}});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ciz = () => render(
  <ThemeProvider theme={createTheme()}><HerdHealth /></ThemeProvider>,
);

it('zorunlu takvim ile elle plan AYRI sekmelerde ve karışmıyor', async () => {
  ciz();
  // Elle plan listesi BOŞ (hiç next_due_on yok) ama zorunlu takvim 1 satır
  // veriyor. İkisi tek sayıya indirilse bu fark kaybolurdu.
  const elle = await screen.findByRole('tab', {name: /Elle plan geçenler/});
  expect(elle.textContent).toContain('(0)');
  const zorunlu = screen.getByRole('tab', {name: /Zorunlu takvim/});
  expect(zorunlu.textContent).toContain('(1)');

  fireEvent.click(zorunlu);
  const tablo = await screen.findByTestId('tablo');
  expect(within(tablo).getByText(/TR0123456789/)).toBeTruthy();
  // Dayanak satırla birlikte geliyor: kullanıcı sayıya değil gerekçeye güvenir.
  expect(within(tablo).getByText(/Dayanak: Şap tekrarı son dozdan 4–6 ay sonra/)).toBeTruthy();
});

it('beklenen pencere ARALIK olarak gösterilir, tek tarih değil', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('tab', {name: /Zorunlu takvim/}));
  const tablo = await screen.findByTestId('tablo');
  // İki tarih ve arada tire: 01.05.2026 – 01.07.2026
  expect(within(tablo).getByText(/Beklenen aralık: 01\.05\.2026 – 01\.07\.2026/)).toBeTruthy();
});

it('takvime GİRMEYEN kayıtlar ekranda açıkça yazılı', async () => {
  ciz();
  // Kodsuz kayıt ve doğum tarihi eksik hayvan sayıları gizlenmiyor.
  await waitFor(() => expect(screen.getByText(/girmeyen/)).toBeTruthy());
  const uyari = screen.getByText(/girmeyen/).closest('.MuiAlert-message');
  expect(uyari?.textContent).toContain('2 aşı kaydında');
  expect(uyari?.textContent).toContain('1 hayvanın doğum tarihi');
  // "aşısı tam" sayılmadığı AÇIKÇA yazıyor.
  expect(uyari?.textContent).toContain('hesaplanamadı');
});

it('zorunlu aşı kodu seçilince aşı adı da dolar', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Aşı kaydet/}));
  await screen.findByRole('dialog');

  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Zorunlu aşı kodu/}));
  const liste = await screen.findByRole('listbox');
  fireEvent.click(within(liste).getByText(/^Şap/));

  // Ad boşken koddan dolduruluyor: kod ile ad ayrışırsa kullanıcı listede
  // 'Şap' görüp takvimde göremez.
  await waitFor(() => expect(
    (screen.getByRole('textbox', {name: /^Aşı$/}) as HTMLInputElement).value,
  ).toBe('Şap'));

  fireEvent.mouseDown(screen.getByRole('combobox', {name: /Hayvan/}));
  const hayvanlar = await screen.findByRole('listbox');
  fireEvent.click(within(hayvanlar).getByText(/TR0123456789/));
  fireEvent.click(screen.getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [url, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(url).toBe('/animal-vaccinations');
  expect(gövde.vaccine_code).toBe('FMD');
  expect(gövde.vaccine).toBe('Şap');
});

it('hayvan seçicisi ikinci API sayfasındaki hayvanı da içerir', async () => {
  const ilkSayfa = Array.from({length: 200}, (_, i) => ({
    ...INEK, id: i + 100, ear_tag: `TR${String(i).padStart(10, '0')}`, name: null,
  }));
  const ikinciSayfaHayvani = {...INEK, id: 999, ear_tag: 'TR-PAGE-2', name: 'İkinci Sayfa'};
  get.mockImplementation((url: string, config?: {params?: {offset?: number}}) => {
    if (url === '/animals') {
      const offset = config?.params?.offset ?? 0;
      return Promise.resolve(offset === 0
        ? sayfa(ilkSayfa, 201, 0)
        : sayfa([ikinciSayfaHayvani], 201, 200));
    }
    if (url === '/vaccination-calendar') return Promise.resolve({data: TAKVIM});
    return Promise.resolve(sayfa([]));
  });

  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Aşı kaydet/}));
  fireEvent.mouseDown(screen.getByRole('combobox', {name: /^Hayvan/}));

  expect(within(await screen.findByRole('listbox')).getByText('TR-PAGE-2')).toBeTruthy();
  expect(get).toHaveBeenCalledWith('/animals', {params: {limit: 200, offset: 200}});
});
