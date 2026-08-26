/**
 * Hayvancılık V1 — ekranların paylaştığı tipler, etiketler ve yardımcılar.
 *
 * Konu: mobil-erp#17, FAZ 3.
 *
 * **Genel yardımcılar tarla modülünden İTHAL EDİLİYOR, KOPYALANMIYOR.**
 * `saveFarmRecord` (409 → çakışma hatası) ve biçimlendiriciler modüle özgü
 * değil; ikinci bir kopya çıkarmak, birinde düzeltilen bir hatanın diğerinde
 * sessizce yaşamaya devam etmesi demek olurdu — 409'u sıradan hata sanan bir
 * ekran, kullanıcıyı "tekrar dene"ye iterek başkasının yazdığını ezdirir.
 *
 * Ortak bir `shared/` modülüne TAŞIMADIK: tarla dosyaları şu an açık PR
 * yığınında ve taşımak o PR'ların hepsinde çakışma üretirdi. Tarla yığını
 * develop'a indiğinde taşınacak (bkz. #17 FAZ 3 notu).
 */
import {api} from '../api';
import {
  farmErrorText, isoDate, money, qty, saveFarmRecord, type Numeric, type Page,
} from '../farm/farmApi';

// Yeniden dışa aktarım: hayvancılık ekranları tarla modülünü doğrudan import
// etmesin — bağımlılık tek kapıdan geçsin ki taşıma günü tek dosya değişsin.
export {farmErrorText as herdErrorText, isoDate, money, qty, saveFarmRecord as saveHerdRecord};
export type {Numeric, Page};

// ---------------------------------------------------------------------------
// Tipler — backend/app/routers/herd.py yanıtlarıyla birebir.
// ---------------------------------------------------------------------------

export type AnimalGroup = {
  id: number; code: string; name: string; species: string | null;
  location: string | null; head_count: number | null; status: string;
  notes: string | null; updated_at: string;
};

export type Animal = {
  id: number; ear_tag: string | null; name: string | null; species: string;
  breed: string | null; sex: string; birth_date: string | null;
  acquisition: string; acquired_on: string | null; group_id: number | null;
  mother_id: number | null; father_id: number | null; status: string;
  notes: string | null; updated_at: string;
  /** Yalnız tekil okuma (`GET /animals/{id}`) döndürür — SUNUCUDA türetilir. */
  age_days?: number | null;
  /** Küpe biçimi alışılmadıksa dolu gelir. Kayıt YAPILMIŞTIR; bu bir hata değil. */
  warnings?: string[];
};

export type Vaccination = {
  id: number; animal_id: number; vaccine: string;
  /** Takvim hesabının EŞLEŞTİĞİ kod. Boşsa kayıt takvime girmez (bkz. FAZ 4). */
  vaccine_code: string | null;
  applied_on: string;
  dose_no: number | null; next_due_on: string | null;
  veterinarian: string | null; batch_no: string | null; notes: string | null;
};

/** Zorunlu aşı takvimi satırı (`GET /api/vaccination-calendar`). */
export type CalendarRow = {
  animal_id: number; ear_tag: string | null; name: string | null;
  species: string; sex: string;
  vaccine_code: string; vaccine_name: string;
  /** UPCOMING | DUE | OVERDUE | UNKNOWN */
  state: string;
  dose_no: number | null;
  /** Pencere ARALIK: tek tarihe indirilmiyor (şap 4–6 ay, brusella 4–12 ay). */
  due_from: string | null; due_to: string | null;
  last_applied_on: string | null;
  overdue_days: number | null;
  /** Hesabın dayanağı. Kullanıcı sayıya değil GEREKÇEYE güvenir. */
  basis: string;
};

export type VaccinationCalendar = {
  as_of: string;
  summary: {
    overdue: number; due: number; upcoming: number; unknown: number;
    /** Kodsuz aşı kaydı — hesaba GİRMEYEN kayıt sayısı. */
    uncoded_vaccinations: number;
    /** Doğum tarihi girilmemiş hayvan — takvimi hesaplanamayan. */
    unknown_birth_date: number;
  };
  items: CalendarRow[];
};

/**
 * Takvimi olan zorunlu aşılar. Kapalı liste DEĞİL: bunlar hesaba giren
 * kodlar; başka aşılar kodsuz (ya da işletmenin kendi koduyla) kaydedilir ve
 * takvime girmez. Kapalı liste yapmak, takvimi olmayan bir aşıyı
 * kaydedilemez hâle getirirdi.
 */
export const VACCINE_CODES = [
  {value: 'FMD', label: 'Şap (zorunlu — takvime girer)'},
  {value: 'BRUCELLA', label: 'Brusella (zorunlu — takvime girer)'},
] as const;

export const CALENDAR_STATES: Record<string, {label: string; color: 'error' | 'warning' | 'default' | 'info'}> = {
  OVERDUE: {label: 'Gecikti', color: 'error'},
  DUE: {label: 'Zamanı geldi', color: 'warning'},
  UPCOMING: {label: 'Yaklaşıyor', color: 'default'},
  // UNKNOWN "sorun yok" DEĞİL: doğum tarihi girilmediği için hesaplanamadı.
  UNKNOWN: {label: 'Hesaplanamadı', color: 'info'},
};

export type Breeding = {
  id: number; animal_id: number; bred_on: string; method: string;
  sire_code: string | null; sire_animal_id: number | null;
  result: string; checked_on: string | null; expected_birth_on: string | null;
  technician: string | null; notes: string | null; updated_at: string;
};

export type Birth = {
  id: number; mother_id: number; breeding_id: number | null; birth_date: string;
  outcome: string; difficulty: number | null; offspring_count: number | null;
  status: string; notes: string | null;
};

export type Weight = {
  id: number; animal_id: number; weighed_on: string;
  weight_kg: Numeric; notes: string | null;
};

export type MilkYield = {
  id: number; animal_id: number | null; group_id: number | null;
  milked_on: string; session: string | null;
  quantity_liters: Numeric; notes: string | null;
};

export type Movement = {
  id: number; animal_id: number; kind: string; moved_on: string;
  amount: Numeric | null; counterparty: string | null;
  reason: string | null; notes: string | null;
};

export type HerdDashboardData = {
  as_of: string;
  summary: {
    /** Bireysel kayıtlı AKTİF hayvan sayısı. */
    individual_active: number;
    /** Bireysel kayıt tutulmayan sürülerin baş sayısı — AYRI toplanır ki aynı
     *  hayvan iki kez sayılmasın. */
    group_head_count: number;
    pregnant: number;
    /** "Planlanmış ama yapılmamış" aşı. ZORUNLU aşı eksiği DEĞİL (FAZ 4). */
    vaccination_overdue: number;
  };
  by_species: Record<string, {total: number; female: number; male: number}>;
  births_last_12_months: {events: number; live_offspring: number; non_live_events: number};
};

// ---------------------------------------------------------------------------
// Etiketler
// ---------------------------------------------------------------------------

export const SPECIES = [
  {value: 'CATTLE', label: 'Sığır'},
  {value: 'BUFFALO', label: 'Manda'},
  {value: 'SHEEP', label: 'Koyun'},
  {value: 'GOAT', label: 'Keçi'},
] as const;

/** Gruplarda karma sürü olabilir; bireysel hayvanda olamaz. */
export const GROUP_SPECIES = [...SPECIES, {value: 'MIXED', label: 'Karma'}] as const;

export const SEX = [
  {value: 'FEMALE', label: 'Dişi'},
  {value: 'MALE', label: 'Erkek'},
] as const;

export const ANIMAL_STATUS = [
  {value: 'ACTIVE', label: 'Sürüde'},
  {value: 'SOLD', label: 'Satıldı'},
  {value: 'DEAD', label: 'Öldü'},
  {value: 'SLAUGHTERED', label: 'Kesildi'},
  {value: 'ARCHIVED', label: 'Arşiv'},
] as const;

export const ACQUISITION = [
  {value: 'BORN', label: 'İşletmede doğdu'},
  {value: 'PURCHASED', label: 'Satın alındı'},
  {value: 'GIFTED', label: 'Bağış / hibe'},
  {value: 'OTHER', label: 'Diğer'},
] as const;

export const BREEDING_METHOD = [
  {value: 'AI', label: 'Suni tohumlama'},
  {value: 'NATURAL', label: 'Tabii tohumlama'},
  {value: 'EMBRYO_TRANSFER', label: 'Embriyo transferi'},
] as const;

export const BREEDING_RESULT = [
  {value: 'UNKNOWN', label: 'Sonuç bekleniyor'},
  {value: 'PREGNANT', label: 'Gebe'},
  {value: 'NOT_PREGNANT', label: 'Gebe değil'},
  {value: 'ABORTED', label: 'Yavru attı'},
] as const;

export const BIRTH_OUTCOME = [
  {value: 'LIVE', label: 'Canlı doğum'},
  {value: 'STILLBORN', label: 'Ölü doğum'},
  {value: 'ABORTION', label: 'Yavru atma'},
] as const;

export const MOVEMENT_KIND = [
  {value: 'PURCHASE', label: 'Satın alma'},
  {value: 'SALE', label: 'Satış'},
  {value: 'DEATH', label: 'Ölüm'},
  {value: 'SLAUGHTER', label: 'Kesim'},
  {value: 'TRANSFER_IN', label: 'Giriş / nakil'},
  {value: 'TRANSFER_OUT', label: 'Çıkış / nakil'},
] as const;

/**
 * Sürüden ÇIKARAN hareketler — `herd_schemas.MOVEMENT_TO_STATUS` ile aynı.
 *
 * Ekran bunu kullanıcıya ÖNCEDEN söylüyor: kayıt tek işlemde hayvanın
 * durumunu da değiştiriyor ve bu geri alınabilir bir şey değil. Sürpriz olarak
 * yaşanması, "satış girdim, hayvan listeden kayboldu" şikâyeti demek.
 */
export const MOVEMENT_REMOVES_FROM_HERD: Record<string, string> = {
  SALE: 'Satıldı', DEATH: 'Öldü', SLAUGHTER: 'Kesildi', TRANSFER_OUT: 'Arşiv',
};

const etiket = (list: readonly {value: string; label: string}[], value: string | null | undefined) =>
  value ? list.find(x => x.value === value)?.label ?? value : '—';

export const speciesLabel = (v: string | null | undefined) => etiket(GROUP_SPECIES, v);
export const sexLabel = (v: string | null | undefined) => etiket(SEX, v);
export const animalStatusLabel = (v: string | null | undefined) => etiket(ANIMAL_STATUS, v);
export const acquisitionLabel = (v: string | null | undefined) => etiket(ACQUISITION, v);
export const breedingMethodLabel = (v: string | null | undefined) => etiket(BREEDING_METHOD, v);
export const breedingResultLabel = (v: string | null | undefined) => etiket(BREEDING_RESULT, v);
export const birthOutcomeLabel = (v: string | null | undefined) => etiket(BIRTH_OUTCOME, v);
export const movementKindLabel = (v: string | null | undefined) => etiket(MOVEMENT_KIND, v);

/**
 * Listede hayvanı tanıtan tek satır: küpe varsa küpe, yoksa ad, o da yoksa #id.
 *
 * Kimlik `id` YA DA `animal_id` alanından okunuyor: aynı hayvan bazı yanıtlarda
 * kaydın kendisi (`animals.id`), bazılarında bir bağ (`animal_id`) olarak
 * geliyor — takvim satırları gibi. İki ayrı etiketleyici yazmak, birinde
 * yapılan düzeltmenin diğerine geçmemesi demek olurdu.
 */
export const animalLabel = (
  a: {id?: number; animal_id?: number; ear_tag: string | null; name: string | null} | undefined | null,
) => (!a ? '—' : a.ear_tag || a.name || `#${a.id ?? a.animal_id}`);

/**
 * Gün cinsinden yaşı okunur metne çevirir.
 *
 * Yaş SUNUCUDAN gün olarak geliyor; burada yalnız gösterim yapılıyor. Cihaz
 * saatinden hesaplamak, saati yanlış bir telefonda yanlış yaş — dolayısıyla
 * yanlış aşı zamanı — gösterirdi.
 */
export const ageLabel = (days: number | null | undefined) => {
  if (days === null || days === undefined) return '—';
  if (days < 60) return `${days} gün`;
  const ay = Math.floor(days / 30);
  if (ay < 24) return `${ay} ay`;
  const yil = Math.floor(days / 365);
  const kalanAy = Math.floor((days - yil * 365) / 30);
  return kalanAy ? `${yil} yıl ${kalanAy} ay` : `${yil} yıl`;
};

// ---------------------------------------------------------------------------
// Yükleme yardımcıları
// ---------------------------------------------------------------------------

/** Döl verimi göstergesi satırı (`GET /api/herd-fertility`). */
export type FertilityRow = {
  animal_id: number; ear_tag: string | null; name: string | null; species: string;
  calving_count: number;
  /** Tek doğumlu hayvanda `null` — 0 DEĞİL (0 "mükemmel aralık" gibi okunurdu). */
  avg_calving_interval_days: number | null;
  last_calving_interval_days: number | null;
  age_at_first_calving_days: number | null;
  /** Yalnız tohumlamaya BAĞLI doğumlardan hesaplanır. */
  service_period_days: number | null;
  services_per_conception: Numeric | null;
  /** Kaynak eşiği: son aralık 13 aydan uzun. */
  interval_is_problem: boolean;
  last_calving_on: string;
};

export type HerdFertility = {
  as_of: string;
  thresholds: {
    target_calving_interval_days: number;
    problem_calving_interval_days: number;
    service_period_low_days: number;
    service_period_high_days: number;
  };
  summary: {
    /** Ortalamanın KAÇ ölçümden çıktığı. Bu sayı olmadan ortalama gösterilmez. */
    calving_interval_sample: number;
    avg_calving_interval_days: number | null;
    problem_interval_count: number;
    age_at_first_calving_sample: number;
    avg_age_at_first_calving_days: number | null;
    service_period_sample: number;
    avg_service_period_days: number | null;
    /** Hiç doğurmamış dişi — ortalamaya girmez, ayrı durur. */
    never_calved: number;
    /** Tohumlamaya bağlanmamış doğum — gebelik başına tohumlamanın kapsamı dışı. */
    births_without_breeding_link: number;
  };
  items: FertilityRow[];
};

export const fetchFertility = async () =>
  (await api.get<HerdFertility>('/herd-fertility')).data;

/**
 * Günü okunur süreye çevirir. Gösterge GÜN cinsinden hesaplanıyor (takvim ayı
 * eşikleri ay sonu taşmasına duyarlı kılardı); burada yalnız GÖSTERİM.
 */
export const dayLabel = (days: number | null | undefined) => {
  if (days === null || days === undefined) return '—';
  if (days < 60) return `${days} gün`;
  const ay = Math.round(days / 30);
  return `${days} gün (~${ay} ay)`;
};

/** Zorunlu aşı takvimini çeker. Sayfalı DEĞİL: hesap sunucuda tek seferde. */
export const fetchCalendar = async (state?: string) =>
  (await api.get<VaccinationCalendar>('/vaccination-calendar', {
    params: state ? {state} : undefined,
  })).data;

export const HERD_PAGE_LIMIT = 200;

/** Bir liste sayfasını çeker; `limit` her ekranda ayrı yazılmasın. */
export const listHerd = async <T>(url: string, params: Record<string, unknown> = {}) =>
  (await api.get<Page<T>>(url, {params: {limit: HERD_PAGE_LIMIT, ...params}})).data;

/**
 * Seçici/etiket bağımlılıkları ve istemci tarafı özetleri için bütün sayfaları
 * çeker. İlk 200 kaydı tam veri sanmak, ikinci sayfadaki hayvanı seçilemez ve
 * ikinci sayfadaki verimi toplamın dışında bırakır.
 */
export const fetchAllHerd = async <T>(url: string, params: Record<string, unknown> = {}) => {
  const {limit: requestedLimit, offset: _ignoredOffset, ...filters} = params;
  const limit = typeof requestedLimit === 'number' ? requestedLimit : HERD_PAGE_LIMIT;
  const items: T[] = [];
  let offset = 0;

  for (;;) {
    const page = await listHerd<T>(url, {...filters, limit, offset});
    items.push(...page.items);
    const nextOffset = page.offset + page.items.length;

    if (page.items.length === 0 || nextOffset >= page.total) return items;
    if (nextOffset <= offset) throw new Error(`${url} sayfalaması ilerlemedi.`);
    offset = nextOffset;
  }
};

/** Boş metin → `null`. '' göndermek "değer var" sayılır ve sonraki kontroller yanılır. */
export const bosNull = (v: string) => {
  const t = v.trim();
  return t === '' ? null : t;
};

/** Ondalık alan: virgülü noktaya çevirir, STRING bırakır (kayan nokta hatası olmasın). */
export const ondalik = (v: string) => v.trim().replace(',', '.');
