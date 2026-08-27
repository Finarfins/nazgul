/**
 * Sürü Panosunun DÖRT kuralı — hepsi "iki sayıyı birleştirme" ailesinden ve
 * hepsi bileşenin kendi başlık yorumunda gerekçesiyle yazılı.
 *
 * 1. **Bireysel hayvan sayısı ile grup baş sayısı TOPLANMAZ** (başlık yorumu
 *    madde 1; satır 168-182, `AYRI SAYI` yorumu). Küçükbaş sürülerinde
 *    bireysel kayıt tutulmuyor; iki sayıyı toplamak, bireysel kaydı olan bir
 *    hayvanı grubun beyanı içinde ikinci kez saymayı GİZLERDİ. Sunucu da bu
 *    yüzden iki ayrı alan dönüyor (`herdApi.ts`, `HerdDashboardData.summary`).
 *
 * 2. **Doğum OLAYI ile CANLI YAVRU sayısı ayrı** (madde 2; satır 198-204).
 *    Bir doğumdan ikiz çıkabilir, bir doğum ölü doğum olabilir; tek sayı ikiz
 *    doğuran sürüyü de yavru kaybeden sürüyü de aynı gösterirdi.
 *
 * 3. **Geciken aşı listesi VADESİ GEÇMİŞ ve SÜRÜDEKİ hayvanla sınırlı**
 *    (satır 114-119: `next_due_on < bugun`, sahibinin durumu `ACTIVE`, en
 *    eski gecikme önce). Satılmış hayvanın aşısını listelemek, yapılamayacak
 *    bir işi yapılacak iş gibi gösterirdi.
 *
 * 4. **Sayının NE OLMADIĞI ekranda yazılı** (madde 3; satır 207-221). "Aşı
 *    vadesi geçen 0" ile "zorunlu aşılarım tamam" farklı cümleler; ikincisini
 *    ima etmek olmayan bir güvence vermek olurdu.
 */
import React from 'react';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
vi.mock('../api', () => ({
  api: {get: (...a: unknown[]) => get(...a), post: vi.fn(), put: vi.fn()},
  errorDetail: (_e: unknown, fallback: string) => fallback,
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  Link: ({to, children}: any) => <a href={String(to)}>{children}</a>,
}));

vi.mock('../components/ResponsiveTable', () => ({
  default: ({rows, cardTitle, cardSubtitle, cardFields}: any) => (
    <div data-testid="tablo">
      {rows.map((row: any, i: number) => (
        <div key={row.id ?? i} data-testid="satir">
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

import HerdDashboard from './HerdDashboard';

/**
 * SAYILAR BİLEREK AYRIK SEÇİLDİ: 12 bireysel, 30 grup başı. Toplamları (42)
 * ekranda görünürse iki sayı birleştirilmiş demektir. Aynı şekilde 7 doğum
 * olayı ile 9 canlı yavru: toplamları (16) da ekranda olmamalı.
 */
const PANO = {
  as_of: '2026-08-08',
  summary: {
    individual_active: 12,
    group_head_count: 30,
    pregnant: 4,
    vaccination_overdue: 2,
  },
  by_species: {CATTLE: {total: 12, female: 9, male: 3}},
  births_last_12_months: {events: 7, live_offspring: 9, non_live_events: 2},
};

const hayvan = (id: number, ear_tag: string, status: string) => ({
  id, ear_tag, name: null, species: 'CATTLE', breed: null, sex: 'FEMALE',
  birth_date: '2022-04-01', acquisition: 'BORN', acquired_on: null, group_id: null,
  mother_id: null, father_id: null, status, notes: null,
  updated_at: '2026-08-01T10:00:00+00:00',
});
const HAYVANLAR = [
  hayvan(1, 'TR0000000001', 'ACTIVE'),
  hayvan(2, 'TR0000000002', 'SOLD'),
  hayvan(3, 'TR0000000003', 'ACTIVE'),
];

const asi = (id: number, animal_id: number, vaccine: string, next_due_on: string | null) => ({
  id, animal_id, vaccine, vaccine_code: null, applied_on: '2026-01-01',
  dose_no: null, next_due_on, veterinarian: null, batch_no: null, notes: null,
});
/**
 * Beş kayıt, panoya YALNIZ İKİSİ girer:
 *   GEC-ESKI  → girer (vadesi geçti, hayvan sürüde) ve ÖNCE okunur
 *   GEC-YENI  → girer (vadesi geçti, hayvan sürüde)
 *   SATILAN   → girmez (hayvan sürüde değil)
 *   GELECEK   → girmez (vadesi gelmedi; 08-08 itibarıyla)
 *   TARIHSIZ  → girmez (tekrar tarihi hiç girilmemiş)
 */
const ASILAR = [
  asi(51, 1, 'GEC-YENI', '2026-07-01'),
  asi(52, 2, 'SATILAN', '2026-06-01'),
  asi(53, 1, 'GELECEK', '2026-09-01'),
  asi(54, 3, 'TARIHSIZ', null),
  asi(55, 3, 'GEC-ESKI', '2026-05-01'),
];

const sayfa = (items: unknown[]) => ({data: {items, total: items.length, limit: 200, offset: 0}});

beforeEach(() => {
  get.mockImplementation((url: string) => {
    if (url === '/herd-dashboard') return Promise.resolve({data: PANO});
    if (url === '/animal-vaccinations') return Promise.resolve(sayfa(ASILAR));
    if (url === '/animals') return Promise.resolve(sayfa(HAYVANLAR));
    return Promise.resolve(sayfa([]));
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ciz = () => render(
  <ThemeProvider theme={createTheme()}><HerdDashboard /></ThemeProvider>,
);

it('bireysel hayvan sayısı ile grup baş sayısı AYRI durur, toplanmaz', async () => {
  ciz();
  await screen.findByText('Bireysel kayıtlı hayvan');

  // İki sayı da KENDİ kartında görünür…
  expect(screen.getAllByText('12').length).toBeGreaterThan(0);
  expect(screen.getByText('30')).toBeTruthy();
  // …ve toplamları HİÇBİR yerde yok. Toplamak, aynı hayvanın iki kez
  // sayılma riskini tek sayının arkasına gizlerdi.
  expect(screen.queryByText('42')).toBeNull();

  // Hangi sayının neyi saydığı da ekranda yazılı; kullanıcı ikisini ancak
  // bunu okuyarak ayırt eder.
  expect(screen.getByText(/Küpesiyle tek tek kayıtlı/)).toBeTruthy();
  expect(screen.getByText(/Bireysel kayıt tutulmayan sürülerde beyan edilen/)).toBeTruthy();
});

it('doğum OLAYI ile canlı yavru sayısı ayrı gösterilir', async () => {
  ciz();
  await screen.findByText('Son 12 ay doğum');

  // Kartın büyük sayısı OLAY sayısıdır.
  expect(screen.getByText('7')).toBeTruthy();
  // Canlı yavru ve ölü doğum ayrı ayrı yazılı; toplamları görünmez.
  expect(screen.getByText('9 canlı yavru · 2 ölü doğum/atma')).toBeTruthy();
  expect(screen.queryByText('16')).toBeNull();
});

it('geciken aşı listesi yalnız VADESİ GEÇMİŞ ve SÜRÜDEKİ hayvanı gösterir', async () => {
  ciz();
  await waitFor(() => expect(screen.getAllByTestId('satir')).toHaveLength(2));

  const satirlar = screen.getAllByTestId('satir');
  // En eski gecikme ÖNCE: sıralama bozulursa en acil satır aşağı düşer.
  expect(within(satirlar[0]).getByText('GEC-ESKI')).toBeTruthy();
  expect(within(satirlar[1]).getByText('GEC-YENI')).toBeTruthy();
  expect(within(satirlar[0]).getByText('Tekrar tarihi: 01.05.2026')).toBeTruthy();

  // Kapsam dışı üç kayıt: satılmış hayvanın aşısı, vadesi gelmemiş tekrar ve
  // tekrar tarihi hiç girilmemiş kayıt.
  expect(screen.queryByText('SATILAN')).toBeNull();
  expect(screen.queryByText('GELECEK')).toBeNull();
  expect(screen.queryByText('TARIHSIZ')).toBeNull();
});

it('aşı sayısının NE OLMADIĞI ekranda yazılı', async () => {
  ciz();
  await screen.findByText('Aşı vadesi geçen');

  // Bu sayı yalnız KULLANICININ girdiği tekrar tarihlerine bakar; zorunlu
  // takvim ayrı bir soru ve ekran bunu açıkça söylüyor.
  const uyari = screen.getByText(/sizin girdiğiniz tekrar tarihlerine/).closest('.MuiAlert-root');
  expect(uyari).toBeTruthy();
  expect(uyari!.textContent).toMatch(/tüm zorunlu aşılar tamam.* demek değildir/);
  expect(within(uyari as HTMLElement).getByRole('link', {name: /Zorunlu takvim/}))
    .toHaveAttribute('href', '/hayvancilik/saglik');
});
