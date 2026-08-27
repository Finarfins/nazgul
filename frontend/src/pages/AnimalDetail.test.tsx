/**
 * Hayvan Detayı ekranının DÖRT kuralı — dördü de bileşenin kendi başlık
 * yorumunda yazılı ve dördü de sessizce bozulabilir.
 *
 * 1. **Yaş SUNUCUDAN gelir, cihaz saatinden hesaplanmaz** (başlık yorumu
 *    madde 1; satır 291-292 `ageLabel(animal.age_days)`). Saati yanlış bir
 *    telefonda hesaplanan yaş, FAZ 4'te yanlış aşı zamanı demekti. Bu yüzden
 *    çapa `age_days` ile doğum tarihinin BİRBİRİNİ TUTMADIĞI bir kayıt:
 *    ekranda hangisinin okunduğu ancak böyle ayırt edilir.
 *
 * 2. **Altı kaynak TEK çizelgede, EN YENİ ÜSTTE birleşir** (madde 2; satır
 *    130-165). Kaynağın biri düşerse "geçen ay ne oldu" sorusunun cevabı
 *    eksilir ve bunu kimse fark etmez — sayfa yine dolu görünür. Doğum
 *    kayıtları `mother_id` ile çekilir (satır 105): `animal_id` ile çekmek
 *    hiçbir doğum döndürmezdi.
 *
 * 3. **Sürüden ÇIKARAN hareket ÖNCEDEN söylenir** (madde 3; satır 257,
 *    455-460 + `MOVEMENT_REMOVES_FROM_HERD`). Uyarı yalnız çıkaran türlerde
 *    çıkar: her türde çıkarsa uyarı bilgi taşımayı bırakır.
 *
 * 4. **Hareketten sonra durum SUNUCUDAN tazelenir** (satır 175-187 `kaydet`
 *    → `yukle`). Durumu istemcide tahmin etmek, sunucunun tek işlemde yaptığı
 *    şeyi ikinci bir gerçek hâline getirirdi.
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

vi.mock('react-router-dom', () => ({
  useParams: () => ({id: '7'}),
  Link: ({to, children}: any) => <a href={String(to)}>{children}</a>,
}));

import AnimalDetail from './AnimalDetail';

/**
 * ÇAPA: doğum tarihi 2022-04-01 ama sunucunun verdiği yaş 1000 gün
 * (= "2 yıl 9 ay"). Cihaz saatinden hesaplansaydı bugün dört yılı aşkın bir
 * yaş çıkardı; iki sayı bilerek uyuşmuyor ki test hangisinin okunduğunu
 * gerçekten ayırt edebilsin.
 */
const HAYVAN = {
  id: 7, ear_tag: 'TR0123456789', name: 'Sarıkız', species: 'CATTLE', breed: null,
  sex: 'FEMALE', birth_date: '2022-04-01', acquisition: 'BORN', acquired_on: null,
  group_id: null, mother_id: null, father_id: null, status: 'ACTIVE', notes: null,
  updated_at: '2026-08-01T10:00:00+00:00', age_days: 1000,
};

// ALTI KAYNAK, ALTI AYRI TARİH. Tarihler bilerek karışık girildi ve hiçbiri
// eşit değil: sıralama bozulursa ya da bir kaynak listeden düşerse fark edilsin.
const ASILAR = [{id: 11, animal_id: 7, vaccine: 'Şap', vaccine_code: 'FMD',
  applied_on: '2026-03-05', dose_no: 2, next_due_on: null, veterinarian: null,
  batch_no: null, notes: null}];
const TOHUMLAMALAR = [{id: 21, animal_id: 7, bred_on: '2026-01-10', method: 'AI',
  sire_code: 'B-9', sire_animal_id: null, result: 'PREGNANT', checked_on: null,
  expected_birth_on: null, technician: null, notes: null,
  updated_at: '2026-01-10T00:00:00+00:00'}];
const DOGUMLAR = [{id: 31, mother_id: 7, breeding_id: null, birth_date: '2026-06-20',
  outcome: 'LIVE', difficulty: null, offspring_count: 1, status: 'ACTIVE', notes: null}];
const TARTIMLAR = [{id: 41, animal_id: 7, weighed_on: '2026-07-01',
  weight_kg: '480.5', notes: null}];
const SUTLER = [{id: 51, animal_id: 7, group_id: null, milked_on: '2026-08-02',
  session: 'SABAH', quantity_liters: '18', notes: null}];
const HAREKETLER = [{id: 61, animal_id: 7, kind: 'PURCHASE', moved_on: '2026-02-14',
  amount: '15000', counterparty: 'Yılmaz Çiftliği', reason: null, notes: null}];

const sayfa = (items: unknown[]) => ({data: {items, total: items.length, limit: 200, offset: 0}});

/** Sunucunun döndürdüğü durum; hareket kaydından sonra değişir. */
let durum = 'ACTIVE';

beforeEach(() => {
  durum = 'ACTIVE';
  can.mockReturnValue(true);
  get.mockImplementation((url: string) => {
    if (url === '/animals/7') return Promise.resolve({data: {...HAYVAN, status: durum}});
    if (url === '/animal-vaccinations') return Promise.resolve(sayfa(ASILAR));
    if (url === '/animal-breedings') return Promise.resolve(sayfa(TOHUMLAMALAR));
    if (url === '/animal-births') return Promise.resolve(sayfa(DOGUMLAR));
    if (url === '/animal-weights') return Promise.resolve(sayfa(TARTIMLAR));
    if (url === '/milk-yields') return Promise.resolve(sayfa(SUTLER));
    if (url === '/animal-movements') return Promise.resolve(sayfa(HAREKETLER));
    return Promise.resolve(sayfa([]));
  });
  post.mockResolvedValue({data: {id: 99}});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ciz = () => render(
  <ThemeProvider theme={createTheme()}><AnimalDetail /></ThemeProvider>,
);

/** Çizelgenin satırları: [tarih, tür] — başlık satırı hariç. */
const cizelge = () =>
  screen.getAllByRole('row').slice(1).map(satir => {
    const hucre = within(satir).getAllByRole('cell');
    return [hucre[0].textContent, hucre[1].textContent];
  });

it('yaş SUNUCUNUN age_days alanından okunur, doğum tarihinden hesaplanmaz', async () => {
  ciz();
  // 1000 gün = "2 yıl 9 ay". Cihaz saatinden hesaplansaydı bugün 4 yılı aşardı.
  expect(await screen.findByText('2 yıl 9 ay')).toBeTruthy();
  expect(screen.queryByText(/^4 yıl/)).toBeNull();
  // Doğum tarihi de ekranda — yaş oradan TÜRETİLMİYOR, ikisi ayrı alan.
  expect(screen.getByText('01.04.2022')).toBeTruthy();
});

it('altı kayıt türü TEK çizelgede, en yeni üstte birleşir', async () => {
  ciz();
  await screen.findByText('Geçmiş (6)');

  // ASIL İDDİA: altı kaynağın hepsi var VE sıra tarihe göre azalan.
  expect(cizelge()).toEqual([
    ['02.08.2026', 'Süt'],
    ['01.07.2026', 'Tartım'],
    ['20.06.2026', 'Doğum'],
    ['05.03.2026', 'Aşı'],
    ['14.02.2026', 'Hareket'],
    ['10.01.2026', 'Tohumlama'],
  ]);

  // Doğumlar ANNE bağıyla çekilir; `animal_id` ile çekmek bu hayvanın
  // doğumlarını hiç getirmezdi.
  expect(get).toHaveBeenCalledWith('/animal-births', {params: {limit: 200, mother_id: 7}});
  expect(get).toHaveBeenCalledWith('/animal-vaccinations', {params: {limit: 200, animal_id: 7}});
});

it('sürüden ÇIKARAN hareket önceden uyarır, çıkarmayan uyarmaz', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Hareket kaydet/}));
  const form = await screen.findByRole('dialog');

  // Varsayılan tür SATIŞ: hayvan sürüden düşecek ve bu ÖNCEDEN yazılı.
  const uyari = within(form).getByRole('alert');
  expect(uyari.textContent).toMatch(/Satıldı/);
  expect(uyari.textContent).toMatch(/sürü sayısından düşer/);

  // SATIN ALMA sürüden çıkarmaz: uyarı KALKMALI, yoksa uyarı bilgi taşımaz.
  fireEvent.mouseDown(within(form).getByRole('combobox', {name: /Hareket türü/}));
  fireEvent.click(within(await screen.findByRole('listbox')).getByText('Satın alma'));
  await waitFor(() => expect(within(form).queryByRole('alert')).toBeNull());
});

it('hareket kaydedilince hayvanın durumu SUNUCUDAN tazelenir', async () => {
  // Sunucu satışı tek işlemde yapıyor: kayıt + durum. Ekran ikinci gerçeği
  // istemcide üretmiyor, yeniden okuyor.
  post.mockImplementation((url: string) => {
    if (url === '/animal-movements') durum = 'SOLD';
    return Promise.resolve({data: {id: 99}});
  });

  ciz();
  expect(await screen.findByText('Sürüde')).toBeTruthy();

  fireEvent.click(screen.getByRole('button', {name: /Hareket kaydet/}));
  const form = await screen.findByRole('dialog');
  fireEvent.change(within(form).getByLabelText('Tutar'), {target: {value: '22500,75'}});
  fireEvent.change(within(form).getByLabelText(/Karşı taraf/), {target: {value: 'Demir Et'}});
  fireEvent.click(within(form).getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [url, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(url).toBe('/animal-movements');
  expect(gövde.animal_id).toBe(7);
  expect(gövde.kind).toBe('SALE');
  // Tutar STRING kalır ve virgül noktaya çevrilir — sayıya çevirip geri
  // yazmak kuruşta kayan nokta hatası üretirdi.
  expect(gövde.amount).toBe('22500.75');

  // ASIL İDDİA: durum yeniden okundu.
  await waitFor(() => expect(screen.getByText('Satıldı')).toBeTruthy());
  expect(screen.queryByText('Sürüde')).toBeNull();
  expect(get.mock.calls.filter(([u]) => u === '/animals/7').length).toBe(2);
});

it('tartım virgülle girilir, sunucuya NOKTALI STRING gider', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /Tartım kaydet/}));
  const form = await screen.findByRole('dialog');

  fireEvent.change(within(form).getByLabelText(/Ağırlık \(kg\)/), {target: {value: '512,4'}});
  fireEvent.click(within(form).getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [url, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(url).toBe('/animal-weights');
  expect(gövde.weight_kg).toBe('512.4');
  expect(typeof gövde.weight_kg).toBe('string');
});
