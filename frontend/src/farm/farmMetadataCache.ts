import {
  DEFAULT_FARM_POLICIES,
  type CropSeason,
  type FarmPolicySettings,
} from './farmApi';

const STORAGE_PREFIX = 'harman-farm-metadata:';
const PREFIX = `${STORAGE_PREFIX}v2`;
export const FARM_METADATA_TTL_MS = 24 * 60 * 60 * 1000;

export type FarmMetadataIdentity = {
  companyId: number;
  userId: number;
};

export type FarmMetadataStorage = Pick<
  Storage,
  'length' | 'key' | 'getItem' | 'setItem' | 'removeItem'
>;

type FarmMetadataStorageProvider = () => FarmMetadataStorage | null;

const browserStorage: FarmMetadataStorageProvider = () => {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
};

let storageProvider: FarmMetadataStorageProvider = browserStorage;

export function setFarmMetadataStorageProviderForTests(
  provider?: FarmMetadataStorageProvider,
): void {
  storageProvider = provider ?? browserStorage;
}

export type FarmSeasonSelector = Pick<
  CropSeason,
  'id' | 'season_year' | 'crop' | 'planted_area_decare'
>;

export type FarmMetadataCache = {
  companyId: number;
  userId: number;
  seasons: FarmSeasonSelector[];
  policies: FarmPolicySettings;
  cachedAt: number;
};

const validIdentity = (identity: FarmMetadataIdentity) =>
  Number.isInteger(identity.companyId) && identity.companyId > 0 &&
  Number.isInteger(identity.userId) && identity.userId > 0;

const key = (identity: FarmMetadataIdentity) =>
  `${PREFIX}:${identity.companyId}:${identity.userId}`;

const acquireStorage = (): FarmMetadataStorage | null => {
  try {
    return storageProvider();
  } catch {
    return null;
  }
};

const safeKeys = (storage: FarmMetadataStorage): string[] => {
  let length: number;
  try {
    length = storage.length;
  } catch {
    return [];
  }
  const keys: string[] = [];
  for (let index = 0; index < length; index += 1) {
    try {
      const storageKey = storage.key(index);
      if (storageKey !== null) keys.push(storageKey);
    } catch {
      // Tek bir bozuk anahtar okuması diğer güvenli temizliği engellemez.
    }
  }
  return keys;
};

const safeGet = (storage: FarmMetadataStorage, storageKey: string): string | null => {
  try {
    return storage.getItem(storageKey);
  } catch {
    return null;
  }
};

const safeSet = (storage: FarmMetadataStorage, storageKey: string, value: string): void => {
  try {
    storage.setItem(storageKey, value);
  } catch {
    // Depolama kapalı/doluysa çevrimiçi akış çalışmaya devam eder.
  }
};

const safeRemove = (storage: FarmMetadataStorage, storageKey: string): void => {
  try {
    storage.removeItem(storageKey);
  } catch {
    // Silme engelliyse önbellek yalnızca kullanılamaz sayılır.
  }
};

const removeLegacyEntries = (storage: FarmMetadataStorage): void => {
  try {
    for (const storageKey of safeKeys(storage)) {
      if (storageKey.startsWith(STORAGE_PREFIX) && !storageKey.startsWith(`${PREFIX}:`)) {
        safeRemove(storage, storageKey);
      }
    }
  } catch {
    // Eski veri temizliği hiçbir zaman uygulama akışını kesmemeli.
  }
};

const selector = (season: CropSeason): FarmSeasonSelector => ({
  id: season.id,
  season_year: season.season_year,
  crop: season.crop,
  planted_area_decare: season.planted_area_decare,
});

const isSelector = (value: unknown): value is FarmSeasonSelector => {
  if (!value || typeof value !== 'object') return false;
  const season = value as Partial<FarmSeasonSelector>;
  return Number.isInteger(season.id) && Number.isInteger(season.season_year) &&
    typeof season.crop === 'string' &&
    (typeof season.planted_area_decare === 'string' || season.planted_area_decare === null);
};

const isPolicyMode = (value: unknown): value is 'allow' | 'require_reason' | 'block' =>
  value === 'allow' || value === 'require_reason' || value === 'block';

const isSafetyPolicyMode = (value: unknown): value is 'warn' | 'require_reason' | 'block' =>
  value === 'warn' || value === 'require_reason' || value === 'block';

const normalizedPolicies = (value?: Partial<FarmPolicySettings>): FarmPolicySettings => ({
  farm_area_override_policy: isPolicyMode(value?.farm_area_override_policy)
    ? value.farm_area_override_policy
    : DEFAULT_FARM_POLICIES.farm_area_override_policy,
  farm_early_harvest_policy: isSafetyPolicyMode(value?.farm_early_harvest_policy)
    ? value.farm_early_harvest_policy
    : DEFAULT_FARM_POLICIES.farm_early_harvest_policy,
  farm_spraying_dose_required: typeof value?.farm_spraying_dose_required === 'boolean'
    ? value.farm_spraying_dose_required
    : DEFAULT_FARM_POLICIES.farm_spraying_dose_required,
  farm_monoculture_policy: isSafetyPolicyMode(value?.farm_monoculture_policy)
    ? value.farm_monoculture_policy
    : DEFAULT_FARM_POLICIES.farm_monoculture_policy,
  farm_reentry_policy: isSafetyPolicyMode(value?.farm_reentry_policy)
    ? value.farm_reentry_policy
    : DEFAULT_FARM_POLICIES.farm_reentry_policy,
});

export function cacheFarmMetadata(
  identity: FarmMetadataIdentity,
  seasons: CropSeason[],
  policies: FarmPolicySettings,
  now = Date.now(),
): void {
  if (!validIdentity(identity)) return;
  const storage = acquireStorage();
  if (!storage) return;
  removeLegacyEntries(storage);
  const value: FarmMetadataCache = {
    companyId: identity.companyId,
    userId: identity.userId,
    seasons: seasons.map(selector),
    policies: normalizedPolicies(policies),
    cachedAt: now,
  };
  try {
    safeSet(storage, key(identity), JSON.stringify(value));
  } catch {
    // Serileştirme dahil bütün önbellek hataları no-op'tur.
  }
}

export function readFarmMetadata(
  identity: FarmMetadataIdentity,
  now = Date.now(),
): FarmMetadataCache | null {
  if (!validIdentity(identity)) return null;
  const storage = acquireStorage();
  if (!storage) return null;
  removeLegacyEntries(storage);
  const storageKey = key(identity);
  try {
    const raw = safeGet(storage, storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<FarmMetadataCache>;
    const seasons = parsed.seasons;
    const cachedAt = parsed.cachedAt;
    if (
      parsed.companyId !== identity.companyId ||
      parsed.userId !== identity.userId ||
      !Array.isArray(seasons) ||
      !seasons.every(isSelector) ||
      typeof cachedAt !== 'number' ||
      cachedAt > now ||
      now - cachedAt > FARM_METADATA_TTL_MS
    ) {
      safeRemove(storage, storageKey);
      return null;
    }
    return {
      companyId: identity.companyId,
      userId: identity.userId,
      seasons: seasons.map(season => ({...season})),
      policies: normalizedPolicies(parsed.policies),
      cachedAt,
    };
  } catch {
    safeRemove(storage, storageKey);
    return null;
  }
}

export function clearFarmMetadata(identity?: FarmMetadataIdentity): void {
  const storage = acquireStorage();
  if (!storage) return;
  if (identity && validIdentity(identity)) {
    safeRemove(storage, key(identity));
    safeRemove(storage, `${STORAGE_PREFIX}v1:${identity.companyId}`);
    return;
  }
  try {
    for (const storageKey of safeKeys(storage)) {
      if (storageKey.startsWith(STORAGE_PREFIX)) safeRemove(storage, storageKey);
    }
  } catch {
    // Toplu temizleme de depolama kapalıyken no-op'tur.
  }
}
