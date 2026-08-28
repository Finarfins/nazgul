/**
 * Hayvanlar & Sürüler ekranının DÖRT kuralı — dördü de sessizce bozulabilir ve
 * dördünün bozulması gerçek veri hatası (ya da girilemeyen bir kayıt) üretir.
 *
 * 1. **Küpe uyarısı ENGEL DEĞİL.** Sunucu alışılmadık biçimdeki küpeyi KABUL
 *    edip yanıtta `warnings` döndürüyor; ekran bunu kayıt SONRASI `info`
 *    olarak gösteriyor (bileşenin başlık yorumu, madde 1 ve satır 297-301).
 *    Uyarıyı `error`a çevirmek ya da kaydı reddetmek, 2026'da değişen küpe
 *    standardındaki geçerli bir küpeyi girilemez hâle getirirdi.
 *
 * 2. **Bireysel kayıt varken grubun baş sayısı GİRİLEMEZ.** `grupBireyselSayisi`
 *    (satır 106-113) sayıyor, alan `disabled={duzenlenenGrupBireysel > 0}`
 *    (satır 440) ile kapanıyor ve listede beyan edilen sayı değil BİREYSEL
 *    sayı gösteriliyor (satır 227-232 / 349-352). İkisi birden dolu olursa
 *    aynı hayvan iki kez sayılır.
 *
 * 3. **Yazma düğmeleri `herd.manage` iznine bağlı** (satır 59, 277, 332, 356).
 *    Okuma yolu — hayvanın detayına gitmek — izinden BAĞIMSIZ; onu da kapatmak
 *    ekranı izinsiz kullanıcı için işe yaramaz hâle getirirdi.
 *
 * 4. **Düzenleme hayvanın DURUMUNU değiştirmez** (satır 152-158). Durumu
 *    değiştiren tek yer hareket kaydı; ad düzeltmenin satılmış bir hayvanı
 *    sürüye geri döndürmesi sürü sayısını sessizce şişirirdi. Aynı gövde
 *    `expected_updated_at` taşır: 409 ayrımı buna dayanıyor.
 */
import React from 'react';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {ThemeProvider, createTheme} from '@mui/material/styles';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const get = vi.fn();
const post = vi.fn();
const put = vi.fn();
vi.mock('../api', () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
    put: (...a: unknown[]) => put(...a),
  },
  errorDetail: (_e: unknown, fallback: string) => fallback,
}));

const can = vi.fn();
vi.mock('../AuthContext', () => ({useAuth: () => ({can})}));

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({useNavigate: () => navigate}));

// ResponsiveTable yerine sade liste: jsdom'da `useMediaQuery` daima false
// döndüğü için gerçek bileşen DataGrid çiziyor. Bu testin konusu tablo değil;
// kart alanları ve kart eylemleri (izin kapısı orada görünür).
vi.mock('../components/ResponsiveTable', () => ({
  default: ({rows, cardTitle, cardSubtitle, cardFields, cardActions}: any) => (
    <div data-testid="tablo">
      {rows.map((row: any, i: number) => (
        <div key={row.id ?? i}>
          <span>{cardTitle(row)}</span>
          <span>{cardSubtitle?.(row)}</span>
          {cardFields?.map((f: any) => (
            <span key={f.label}>{f.label}: {String(f.value(row))}</span>
          ))}
          {cardActions?.map((a: any) => (
            <button key={a.label} onClick={() => a.onClick(row)}>
              {cardTitle(row)} — {a.label}
            </button>
          ))}
        </div>
      ))}
    </div>
  ),
}));

import Animals from './Animals';

const INEK = {
  id: 7, ear_tag: 'TR0123456789', name: 'Sarıkız', species: 'CATTLE', breed: null,
  sex: 'FEMALE', birth_date: '2022-04-01', acquisition: 'BORN', acquired_on: null,
  group_id: 3, mother_id: null, father_id: null, status: 'ACTIVE', notes: null,
  updated_at: '2026-08-01T10:00:00+00:00',
};
const DANA = {
  ...INEK, id: 8, ear_tag: 'TR9876543210', name: 'Karayel', sex: 'MALE',
  group_id: 3, updated_at: '2026-08-02T10:00:00+00:00',
};
/** SATILMIŞ hayvan: düzenlemenin durumu geri diriltmediğini ancak bu gösterir. */
const SATILAN = {
  ...INEK, id: 9, ear_tag: 'TR5555555555', name: 'Benekli', group_id: null,
  status: 'SOLD', updated_at: '2026-07-20T08:30:00+00:00',
};

/**
 * Sürünün BEYAN ETTİĞİ baş sayısı 40, ama içinde 2 bireysel kayıt var.
 * Sayılar bilerek ayrı: liste beyan edileni gösterse 40, toplasa 42 yazardı;
 * doğru cevap 2'dir (bireysel kayıt gerçek sayının kendisidir).
 */
const SURU = {
  id: 3, code: 'S1', name: 'Sağmal Sürü', species: 'CATTLE', location: 'Ağıl 1',
  head_count: 40, status: 'ACTIVE', notes: null,
  updated_at: '2026-08-01T10:00:00+00:00',
};

const sayfa = (items: unknown[]) => ({data: {items, total: items.length, limit: 200, offset: 0}});

beforeEach(() => {
  can.mockReturnValue(true);
  get.mockImplementation((url: string) => {
    if (url === '/animals') return Promise.resolve(sayfa([INEK, DANA, SATILAN]));
    if (url === '/animal-groups') return Promise.resolve(sayfa([SURU]));
    return Promise.resolve(sayfa([]));
  });
  post.mockResolvedValue({data: {...INEK, id: 10}});
  put.mockResolvedValue({data: INEK});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ciz = () => render(
  <ThemeProvider theme={createTheme()}><Animals /></ThemeProvider>,
);

const suruSekmesi = async () => {
  fireEvent.click(await screen.findByRole('tab', {name: /Sürüler/}));
};

it('alışılmadık küpe kaydı ENGELLEMEZ; uyarı BİLGİ olarak kayıttan sonra çıkar', async () => {
  const UYARI = 'Küpe numarası alışılmadık biçimde; kayıt yapıldı.';
  post.mockResolvedValue({data: {...INEK, id: 10, ear_tag: 'X-1', warnings: [UYARI]}});

  ciz();
  fireEvent.click(await screen.findByRole('button', {name: 'Yeni hayvan'}));
  const form = await screen.findByRole('dialog');

  // Kural formda da YAZILI: kullanıcı duvara çarpmadan önce öğreniyor.
  expect(within(form).getByText(/Alışılmadık biçim kaydı ENGELLEMEZ/)).toBeTruthy();

  fireEvent.change(within(form).getByLabelText(/Küpe no/), {target: {value: 'X-1'}});
  fireEvent.click(within(form).getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(post).toHaveBeenCalled());
  const [url, gövde] = post.mock.calls[0] as [string, Record<string, unknown>];
  expect(url).toBe('/animals');
  expect(gövde.ear_tag).toBe('X-1');
  // Sürü seçilmedi: boş metin '' değil NULL gider, yoksa "sürüsü var" sanılırdı.
  expect(gövde.group_id).toBeNull();

  // ASIL İDDİA: kayıt İNDİ. Form kapandı ve liste yeniden yüklendi.
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(get.mock.calls.filter(([u]) => u === '/animals').length).toBeGreaterThan(1);

  // Ve uyarı BİLGİ seviyesinde duruyor — hata değil.
  const kutu = (await screen.findByText(UYARI)).closest('.MuiAlert-root');
  expect(kutu).toBeTruthy();
  expect(kutu!.className).toMatch(/Info/);
  expect(kutu!.className).not.toMatch(/Error/);
});

it('bireysel kayıtlı sürüde baş sayısı GİRİLEMEZ ve beyan edilen sayı gösterilmez', async () => {
  ciz();
  await suruSekmesi();

  // Listede beyan edilen 40 değil, bireysel kayıt sayısı görünür.
  expect(await screen.findByText(/Baş sayısı: 2 \(bireysel kayıt\)/)).toBeTruthy();
  expect(screen.queryByText(/Baş sayısı: 40/)).toBeNull();
  expect(screen.queryByText(/Baş sayısı: 42/)).toBeNull();

  fireEvent.click(screen.getByRole('button', {name: /Sağmal Sürü — Düzenle/}));
  const form = await screen.findByRole('dialog');

  // ASIL İDDİA: alan kapalı ve SEBEBİ yazılı — sunucunun 422'sine çarpmadan.
  expect(within(form).getByLabelText('Baş sayısı')).toBeDisabled();
  expect(within(form).getByText(/2 hayvan bireysel kayıtlı/)).toBeTruthy();
  expect(within(form).getByText(/aynı hayvanlar iki kez sayılırdı/)).toBeTruthy();
});

it('bireysel kaydı olmayan sürüde baş sayısı GİRİLEBİLİR', async () => {
  // Karşı yön: kapı her sürüde kapalı olsaydı yukarıdaki test yine yeşil
  // kalırdı ve baş sayısı hiç girilemezdi.
  get.mockImplementation((url: string) => {
    if (url === '/animals') return Promise.resolve(sayfa([SATILAN]));   // group_id: null
    if (url === '/animal-groups') return Promise.resolve(sayfa([SURU]));
    return Promise.resolve(sayfa([]));
  });

  ciz();
  await suruSekmesi();
  expect(await screen.findByText(/Baş sayısı: 40/)).toBeTruthy();

  fireEvent.click(screen.getByRole('button', {name: /Sağmal Sürü — Düzenle/}));
  const form = await screen.findByRole('dialog');
  expect(within(form).getByLabelText('Baş sayısı')).not.toBeDisabled();
  expect(within(form).getByText(/Yalnız bireysel kayıt tutulmayan sürüler için/)).toBeTruthy();
});

it('herd.manage yoksa yazma düğmeleri yok, DETAY yolu açık kalır', async () => {
  can.mockReturnValue(false);
  ciz();

  await screen.findByText('TR0123456789');
  expect(screen.queryByRole('button', {name: 'Yeni hayvan'})).toBeNull();
  expect(screen.queryByRole('button', {name: /Düzenle/})).toBeNull();
  expect(can).toHaveBeenCalledWith('herd.manage');

  // Okuma yolu izinden BAĞIMSIZ: kapatmak ekranı izinsiz kullanıcı için
  // tamamen işlevsiz bırakırdı.
  fireEvent.click(screen.getByRole('button', {name: /TR0123456789 — Detay/}));
  expect(navigate).toHaveBeenCalledWith('/hayvancilik/hayvanlar/7');

  await suruSekmesi();
  expect(screen.queryByRole('button', {name: 'Yeni sürü'})).toBeNull();
  expect(screen.queryByRole('button', {name: /Sağmal Sürü — Düzenle/})).toBeNull();
});

it('hayvan düzenlemesi DURUMU korur ve expected_updated_at gönderir', async () => {
  ciz();
  fireEvent.click(await screen.findByRole('button', {name: /TR5555555555 — Düzenle/}));
  const form = await screen.findByRole('dialog');

  fireEvent.change(within(form).getByLabelText(/Ad \/ lakap/), {target: {value: 'Benekli II'}});
  fireEvent.click(within(form).getByRole('button', {name: 'Kaydet'}));

  await waitFor(() => expect(put).toHaveBeenCalled());
  const [url, gövde] = put.mock.calls[0] as [string, Record<string, unknown>];
  expect(url).toBe('/animals/9');
  expect(gövde.name).toBe('Benekli II');
  // ASIL İDDİA: satılmış hayvan ad düzeltmesiyle sürüye DÖNMEZ.
  expect(gövde.status).toBe('SOLD');
  // Ve çakışma ayrımının dayanağı gövdede: bu olmadan başkasının yazdığı
  // sessizce ezilir.
  expect(gövde.expected_updated_at).toBe('2026-07-20T08:30:00+00:00');
});
