/**
 * Tarla Yönetimi V1 — ekranların paylaştığı tipler ve yardımcılar.
 *
 * Burada iki şey BİLEREK merkezîleştirildi, çünkü her ekranda ayrı ayrı
 * yazılsaydı biri unutulduğunda sessizce yanlış davranırdı:
 *
 * 1. **409 (çakışma) ayrı bir hata türü.** Sunucu iyimser kilit uyguluyor;
 *    kaydı bizden sonra biri değiştirmişse 409 döner. Bunu sıradan bir hata
 *    gibi "kaydedilemedi" diye göstermek kullanıcıyı tekrar denemeye iter ve
 *    ikinci deneme diğerinin yazdığını ezer. `saveFarmRecord` bu durumu ayırıp
 *    çağırana "yeniden yükle" dedirtir.
 *
 * 2. **Para ve miktar STRING geliyor.** Backend'in sözleşmesi bu (bkz. test
 *    altyapısı kararı): `Number()`'a çevirip geri yazmak 0.1+0.2 sınıfı kayan
 *    nokta hatası üretir. Burada yalnız GÖSTERİM için sayıya çevriliyor;
 *    sunucuya giden değerler string olarak taşınıyor.
 */
import {api, errorDetail} from '../api';

// ---------------------------------------------------------------------------
// Tipler — backend/app/routers/farm.py yanıtlarıyla birebir.
// ---------------------------------------------------------------------------

/** Para/miktar alanları sunucudan string gelir; tip bunu görünür kılar. */
export type Numeric = string;

export type Farm = {
  id: number; code: string; name: string; status: string;
  customer_id: number | null; city: string | null; district: string | null;
  notes: string | null; updated_at: string;
};

export type Parcel = {
  id: number; farm_id: number; farm_name?: string | null;
  code: string; name: string; status: string;
  area_decare: Numeric; parcel_no: string | null; block_no: string | null;
  city: string | null; district: string | null; neighborhood: string | null;
  updated_at: string;
};

export type CropSeason = {
  id: number; parcel_id: number; parcel_name?: string | null;
  season_year: number; crop: string; variety: string | null; status: string;
  // Sezonun bildirdiği stok ürünü. `crop` insanın okuduğu serbest metin,
  // `product_id` ise stok defterine giden bağ — hasat ürününü buradan
  // devralır. Bildirilmemişse null ve o sezonun hasatları stok hareketi
  // üretmez (sunucuda adı konmuş `SKIPPED_NO_PRODUCT` kovası).
  product_id: number | null;
  started_on: string | null; ended_on: string | null;
  planted_area_decare: Numeric | null; notes: string | null; updated_at: string;
  monoculture_override_reason?: string | null;
  monoculture_warning?: string | null;
};

/**
 * PHI değerinin KÖKENİ (göç 20260901_0063).
 *
 * `null` iki farklı şeyi birden söyler ve ikisi de "kilit yok" demektir:
 * ya süre hiç girilmedi ya da satır katalog çağından önce yazıldı.
 */
export type PreharvestSource = 'CATALOGUE' | 'OPERATOR' | 'OPERATOR_OVERRIDE';

export type FieldActivity = {
  id: number; season_id: number; activity_type: string; performed_at: string;
  applied_area_decare: Numeric | null; area_override_reason: string | null;
  operator_user_id: number | null; machine_id: number | null;
  reentry_interval_days: number | null; preharvest_interval_days: number | null;
  reentry_override_reason?: string | null;
  reentry_warning?: string | null;
  // Katalogun DEDİĞİ, operatörün yazdığından AYRI durur: üstüne yazma
  // denetimde görünür olsun diye (sunucu tarafında aynı gerekçe, göç 0048).
  preharvest_source: PreharvestSource | null;
  catalogue_preharvest_days: number | null;
  notes: string | null; crop?: string | null; parcel_name?: string | null;
};

/** BKÜ katalog satırı — bir stok ürününün etiketten gelen bekleme süreleri. */
export type PlantProtectionProduct = {
  id: number; product_id: number;
  /** Listede birleştirilerek geliyor; ham id kullanıcıya bir şey söylemez. */
  product_name?: string;
  /** BOŞ DİZE = bütün bitkiler. Bitkiye özel satır bunu yener. */
  crop: string;
  registration_no: string | null;
  preharvest_interval_days: number;
  reentry_interval_days: number | null;
  notes: string | null; status: string; updated_at: string;
};

export const PPP_PATH = '/plant-protection-products';

export async function fetchPlantProtectionProducts(
  params: {limit?: number; offset?: number; product_id?: number; status?: string} = {},
): Promise<Page<PlantProtectionProduct>> {
  const response = await api.get(PPP_PATH, {params});
  return response.data as Page<PlantProtectionProduct>;
}

/**
 * Bir ürün+bitki için katalogdaki satır; yoksa `null`.
 *
 * SUNUCUDAKİ ÇÖZÜMÜN AYNISI DEĞİL, ÖNİZLEMESİ. Etkin değeri sunucu belirliyor
 * (`_phi_coz`); buradaki hesap yalnız formu DOLDURMAK için. İkisinin ayrı
 * durması bilinçli: istemci hesabı otorite olsaydı, eski bir sekme yeni bir
 * katalog satırını görmeden yazar ve kullanıcı yanlış süreyi onaylardı.
 *
 * Bitkiye ÖZEL satır, bitkiden bağımsız satırı (`crop === ''`) yener — sunucu
 * tarafındaki `_katalog_phi` ile aynı sıra.
 */
export function catalogueMatch(
  rows: PlantProtectionProduct[], crop: string,
): PlantProtectionProduct | null {
  const aktif = rows.filter(r => r.status === 'ACTIVE');
  const hedef = crop.trim().toLocaleLowerCase('tr');
  const ozel = aktif.find(
    r => r.crop.trim() !== '' && r.crop.trim().toLocaleLowerCase('tr') === hedef,
  );
  return ozel ?? aktif.find(r => r.crop.trim() === '') ?? null;
}

export type ActivityInput = {
  id: number; activity_id: number; product_id: number | null;
  input_name: string; quantity: Numeric; unit: string;
  unit_cost: Numeric | null; total_cost: Numeric | null;
  dose: Numeric | null; dose_unit: string | null;
};

export type FieldTask = {
  id: number; title: string; status: string; priority: string;
  season_id: number | null; parcel_id: number | null;
  due_date: string | null; assigned_user_id: number | null;
  notes: string | null; updated_at: string;
};

export type DashboardSeason = {
  season_id: number; season_year: number; crop: string; status: string;
  parcel_name: string; area_decare: Numeric; total_cost: Numeric;
  harvest_quantity: Numeric;
  /** Alansız sezonda `null` — sunucu sıfıra bölmek yerine "hesaplanamadı" der. */
  yield_per_decare: Numeric | null;
  cost_per_decare: Numeric | null;
  /** Gelir GİRİLMEMİŞSE `null` — sıfır DEĞİL. Sıfır göstermek, ürününü henüz
   *  satmamış bir sezonu "hiç kazanmadı" diye gösterir. */
  revenue_amount: Numeric | null;
  /** BRÜT KATKI: gelir − GİRDİ maliyeti. KÂR DEĞİL — işçilik, makine ve yakıt
   *  bu hesaba girmiyor (V1'de o veriler toplanmıyor). */
  gross_margin: Numeric | null;
};

export type FarmDashboardData = {
  summary: {farm_count: number; parcel_count: number; total_area_decare: Numeric; active_season_count: number};
  tasks: {overdue: number; due_today: number; upcoming: number};
  seasons: DashboardSeason[];
};

export type Page<T> = {items: T[]; total: number; limit: number; offset: number};

// ---------------------------------------------------------------------------
// Outbox okuma yüzeyi (FIELD_STOK_OUTBOX açılış koşulu 2).
//
// TİPLER ALAN ADI TAŞIMIYOR — bilerek. Sunucu tarafında alan bir PARAMETRE
// (`OlayYuzeyi`); ikinci outbox tablosu (`herd_integration_events`) eklendiğinde
// yanıt AYNI anahtarları döndürecek, yalnız `source` değişecek. Tipleri
// `Field...` diye adlandırmak, o gün her şeyi yeniden adlandırmak demek olurdu.
//
// `attempts` ve `processed_at` NULL OLABİLİR: sürü tablosunda o sütunlar YOK ve
// sunucu onları `null` olarak döndürüyor. Bugün tarla için hep dolu; tip yine de
// gerçeği söylüyor.
// ---------------------------------------------------------------------------

/** Outbox olayı — kuyruğun tek satırı. */
export type IntegrationEvent = {
  id: number;
  source_type: string;
  source_id: number;
  target: string;
  status: string;
  attempts: number | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
};

/** Kaynak tipi × durum kırılımında tek kova. */
export type IntegrationEventBucket = {
  source_type: string;
  status: string;
  count: number;
  /** Kovadaki EN ESKİ olayın zamanı: birikimin yaşı. */
  oldest_created_at: string | null;
};

export type IntegrationEventSummary = {
  source: string;
  buckets: IntegrationEventBucket[];
  total: number;
  pending_total: number;
  failed_total: number;
};

export type IntegrationEventPage = Page<IntegrationEvent> & {source: string};

/** Yüzeyin kök yolu. İkinci alan eklendiğinde burası bir eşleme olur. */
export const FIELD_EVENTS_PATH = '/field-integration-events';

export async function fetchIntegrationEventSummary(
  path: string = FIELD_EVENTS_PATH,
): Promise<IntegrationEventSummary> {
  const response = await api.get(`${path}/summary`);
  return response.data as IntegrationEventSummary;
}

export async function fetchIntegrationEvents(
  params: {limit?: number; offset?: number; status?: string; source_type?: string; failed_only?: boolean} = {},
  path: string = FIELD_EVENTS_PATH,
): Promise<IntegrationEventPage> {
  const response = await api.get(path, {params});
  return response.data as IntegrationEventPage;
}

/**
 * Kova adının insan okunur karşılığı. ADI OLMAYAN bir durum GİZLENMEZ —
 * ham değer geri döner: sunucu yeni bir kova eklerse ekran onu "bilinmeyen"
 * diye yutmak yerine adıyla gösterir.
 */
export const EVENT_STATUS_LABELS: Record<string, string> = {
  PENDING: 'Bekliyor',
  CLAIMED: 'İşleniyor',
  SENT: 'Stok hareketi yazıldı',
  SKIPPED_SOURCE_NOT_VISIBLE: 'Kaynak kayıt bu firmada yok',
  SKIPPED_NO_PRODUCT: 'Ürün bağı yok',
  DEAD: 'Deneme hakkı bitti',
};
export const eventStatusLabel = (v: string) => EVENT_STATUS_LABELS[v] ?? v;

export const EVENT_SOURCE_LABELS: Record<string, string> = {
  field_activity: 'Faaliyet',
  field_harvest: 'Hasat',
};
export const eventSourceLabel = (v: string) => EVENT_SOURCE_LABELS[v] ?? v;

/** BAŞARISIZLIK kovaları — sunucudaki `basarisiz_kovalar` demetinin ikizi. */
export const FAILED_EVENT_STATUSES = [
  'SKIPPED_SOURCE_NOT_VISIBLE',
  'SKIPPED_NO_PRODUCT',
  'DEAD',
] as const;

/** Yürürlükteki ilaç güvenlik kısıtı (bkz. /api/field-safety). */
export type SafetyBlock = {
  activity_id: number; activity_type: string; performed_on: string;
  interval_days: number; safe_from: string;
};
export type HarvestBlock = {
  season_id: number; crop: string; season_year: number; parcel_name: string;
  safe_from: string; blocking: SafetyBlock[];
};
export type ReentryBlock = SafetyBlock & {parcel_id: number; parcel_name: string};
export type FieldSafety = {
  as_of: string; harvest_blocks: HarvestBlock[]; reentry_blocks: ReentryBlock[];
};

export type FarmAreaPolicy = 'allow' | 'require_reason' | 'block';
export type FarmHarvestPolicy = 'warn' | 'require_reason' | 'block';
export type FarmSafetyPolicy = 'warn' | 'require_reason' | 'block';
export type FarmPolicySettings = {
  farm_area_override_policy: FarmAreaPolicy;
  farm_early_harvest_policy: FarmHarvestPolicy;
  farm_spraying_dose_required: boolean;
  farm_monoculture_policy: FarmSafetyPolicy;
  farm_reentry_policy: FarmSafetyPolicy;
};
export const DEFAULT_FARM_POLICIES: FarmPolicySettings = {
  farm_area_override_policy: 'require_reason',
  farm_early_harvest_policy: 'require_reason',
  farm_spraying_dose_required: true,
  farm_monoculture_policy: 'require_reason',
  farm_reentry_policy: 'require_reason',
};

export type HarvestDecision = {
  season_id: number;
  harvested_on: string;
  policy: FarmHarvestPolicy;
  decision: 'safe' | 'warning' | 'reason_required' | 'blocked';
  safe_from: string | null;
  warning: string | null;
  blocking: SafetyBlock[];
};

// ---------------------------------------------------------------------------
// Etiketler
// ---------------------------------------------------------------------------

export const ACTIVITY_TYPES = [
  {value: 'SOWING', label: 'Ekim / dikim'},
  {value: 'FERTILIZING', label: 'Gübreleme'},
  {value: 'SPRAYING', label: 'İlaçlama'},
  {value: 'IRRIGATION', label: 'Sulama'},
  {value: 'TILLAGE', label: 'Toprak işleme'},
  {value: 'OTHER', label: 'Diğer'},
] as const;

export const SEASON_STATUSES = [
  {value: 'PLANNED', label: 'Planlandı'},
  {value: 'ACTIVE', label: 'Sürüyor'},
  {value: 'HARVESTED', label: 'Hasat edildi'},
  {value: 'CLOSED', label: 'Kapandı'},
  {value: 'CANCELLED', label: 'İptal'},
] as const;

export const TASK_PRIORITIES = [
  {value: 'LOW', label: 'Düşük'},
  {value: 'NORMAL', label: 'Normal'},
  {value: 'HIGH', label: 'Yüksek'},
] as const;

const etiket = (list: readonly {value: string; label: string}[], value: string) =>
  list.find(x => x.value === value)?.label ?? value;

export const activityTypeLabel = (v: string) => etiket(ACTIVITY_TYPES, v);
export const seasonStatusLabel = (v: string) => etiket(SEASON_STATUSES, v);
export const taskPriorityLabel = (v: string) => etiket(TASK_PRIORITIES, v);

// ---------------------------------------------------------------------------
// Biçimlendirme — yalnız GÖSTERİM için sayıya çevirir.
// ---------------------------------------------------------------------------

const bicim = (max: number) => new Intl.NumberFormat('tr-TR', {minimumFractionDigits: 0, maximumFractionDigits: max});

/** Dekar/miktar: 4 basamağa kadar. */
export const qty = (v: Numeric | number | null | undefined) => (v === null || v === undefined || v === '' ? '—' : bicim(4).format(Number(v)));

/** Tutar: her zaman 2 basamak + ₺. */
export const money = (v: Numeric | number | null | undefined) =>
  v === null || v === undefined || v === ''
    ? '—'
    : new Intl.NumberFormat('tr-TR', {style: 'currency', currency: 'TRY'}).format(Number(v));

/** ISO tarih (YYYY-AA-GG) → gg.aa.yyyy. Saat dilimi kaydırması olmasın diye
 *  `new Date()` KULLANILMAZ: '2026-03-01' UTC gece yarısı olarak ayrıştırılır
 *  ve Türkiye'de bir gün geriye kayardı. */
export const isoDate = (v: string | null | undefined) => {
  if (!v) return '—';
  const [y, m, d] = v.slice(0, 10).split('-');
  return y && m && d ? `${d}.${m}.${y}` : v;
};

/** ISO zaman damgası → gg.aa.yyyy SS:DD (yerel saat; anlık zaman burada doğru). */
export const isoDateTime = (v: string | null | undefined) => {
  if (!v) return '—';
  const t = new Date(v);
  return Number.isNaN(t.getTime()) ? v : t.toLocaleString('tr-TR', {dateStyle: 'short', timeStyle: 'short'});
};

/** Bugünün yerel tarihi, `<input type="date">` biçiminde. */
export const todayIso = () => {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

// ---------------------------------------------------------------------------
// Kaydetme — çakışmayı AYIRAN sarmalayıcı
// ---------------------------------------------------------------------------

/** Kaydın bizden sonra değiştiğini bildiren hata. Yeniden denemek YANLIŞTIR. */
export class FarmConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FarmConflictError';
  }
}

const CAKISMA_MESAJI =
  'Bu kaydı siz açtıktan sonra başka biri değiştirdi. Kaydedilmedi — ' +
  'listeyi yenileyip değişikliğinizi güncel veri üzerinde tekrar yapın.';

/**
 * POST/PUT sarmalayıcısı: 409'u `FarmConflictError` olarak fırlatır, diğer
 * hataları okunur metne çevirir.
 *
 * Çakışmada kullanıcıya "tekrar dene" DEMİYORUZ. Aynı gövdeyi yeni sürümle
 * göndermek, arada yazılanı sessizce ezmek demektir; kullanıcının önce
 * diğerinin ne yazdığını görmesi gerekir.
 */
export async function saveFarmRecord<T>(
  method: 'post' | 'put',
  url: string,
  body: unknown,
  fallback: string,
): Promise<T> {
  try {
    const response = method === 'post' ? await api.post(url, body) : await api.put(url, body);
    return response.data as T;
  } catch (err) {
    const status = (err as {response?: {status?: number}})?.response?.status;
    if (status === 409) throw new FarmConflictError(CAKISMA_MESAJI);
    throw new Error(errorDetail(err, fallback));
  }
}

/** Hata nesnesini kullanıcıya gösterilecek metne çevirir. */
export const farmErrorText = (err: unknown, fallback: string) =>
  err instanceof FarmConflictError || err instanceof Error ? err.message : errorDetail(err, fallback);
