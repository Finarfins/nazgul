import {afterEach, beforeEach, describe, expect, it} from 'vitest';

import {DEFAULT_FARM_POLICIES, type CropSeason} from './farmApi';
import {
  FARM_METADATA_TTL_MS,
  cacheFarmMetadata,
  clearFarmMetadata,
  readFarmMetadata,
  setFarmMetadataStorageProviderForTests,
  type FarmMetadataIdentity,
  type FarmMetadataStorage,
} from './farmMetadataCache';

const season = (id: number): CropSeason => ({
  id, parcel_id: id * 10, season_year: 2026, crop: `Crop ${id}`, variety: null,
  status: 'ACTIVE', started_on: '2026-03-01', ended_on: null,
  planted_area_decare: '20.0000', notes: null, updated_at: '2026-08-08T10:00:00Z',
});

class MemoryStorage implements FarmMetadataStorage {
  private readonly values = new Map<string, string>();

  get length(): number { return this.values.size; }
  key(index: number): string | null { return [...this.values.keys()][index] ?? null; }
  getItem(storageKey: string): string | null { return this.values.get(storageKey) ?? null; }
  setItem(storageKey: string, value: string): void { this.values.set(storageKey, value); }
  removeItem(storageKey: string): void { this.values.delete(storageKey); }
  rawValues(): string[] { return [...this.values.values()]; }
}

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  setFarmMetadataStorageProviderForTests(() => storage);
});
afterEach(() => setFarmMetadataStorageProviderForTests());

const identity = (companyId: number, userId = 11): FarmMetadataIdentity => ({companyId, userId});
const NOW = 1_800_000_000_000;

describe('farm metadata cache', () => {
  it('stores only minimal selector fields and excludes notes and other free text', () => {
    const sensitive = {...season(3), variety: 'Private variety', notes: 'Private field note'};
    cacheFarmMetadata(identity(7), [sensitive], {
      ...DEFAULT_FARM_POLICIES,
      farm_spraying_dose_required: false,
    }, NOW);

    const raw = storage.rawValues()[0] ?? '';
    expect(raw).not.toContain('Private variety');
    expect(raw).not.toContain('Private field note');
    expect(Object.keys(JSON.parse(raw ?? '{}').seasons[0]).sort()).toEqual(
      ['crop', 'id', 'planted_area_decare', 'season_year'],
    );
    expect(readFarmMetadata(identity(7), NOW)).toMatchObject({
      companyId: 7,
      userId: 11,
      seasons: [{id: 3, crop: 'Crop 3'}],
      policies: {farm_spraying_dose_required: false},
    });
  });

  it('rejects another user and removes a mismatched payload', () => {
    cacheFarmMetadata(identity(7, 11), [season(1)], DEFAULT_FARM_POLICIES, NOW);
    expect(readFarmMetadata(identity(7, 12), NOW)).toBeNull();
    expect(readFarmMetadata(identity(7, 11), NOW)?.seasons[0].id).toBe(1);

    const storageKey = storage.key(0) ?? '';
    const tampered = JSON.parse(storage.getItem(storageKey) ?? '{}');
    tampered.userId = 12;
    storage.setItem(storageKey, JSON.stringify(tampered));
    expect(readFarmMetadata(identity(7, 11), NOW)).toBeNull();
    expect(storage.getItem(storageKey)).toBeNull();
  });

  it('removes expired metadata', () => {
    cacheFarmMetadata(identity(7), [season(1)], DEFAULT_FARM_POLICIES, NOW);
    expect(readFarmMetadata(identity(7), NOW + FARM_METADATA_TTL_MS + 1)).toBeNull();
    expect(storage.length).toBe(0);
  });

  it('partitions metadata by company for the same user', () => {
    cacheFarmMetadata(identity(1), [season(1)], DEFAULT_FARM_POLICIES, NOW);
    cacheFarmMetadata(identity(2), [season(2)], DEFAULT_FARM_POLICIES, NOW);
    expect(readFarmMetadata(identity(1), NOW)?.seasons.map(s => s.id)).toEqual([1]);
    expect(readFarmMetadata(identity(2), NOW)?.seasons.map(s => s.id)).toEqual([2]);
    expect(readFarmMetadata(identity(3), NOW)).toBeNull();
  });

  it('clears one identity or every farm metadata partition', () => {
    cacheFarmMetadata(identity(1), [season(1)], DEFAULT_FARM_POLICIES, NOW);
    cacheFarmMetadata(identity(2), [season(2)], DEFAULT_FARM_POLICIES, NOW);
    clearFarmMetadata(identity(1));
    expect(readFarmMetadata(identity(1), NOW)).toBeNull();
    expect(readFarmMetadata(identity(2), NOW)).not.toBeNull();
    clearFarmMetadata();
    expect(storage.length).toBe(0);
  });

  it('degrades throwing storage acquisition and operations to cache miss or no-op', () => {
    setFarmMetadataStorageProviderForTests(() => { throw new Error('storage disabled'); });
    expect(() => cacheFarmMetadata(identity(7), [season(1)], DEFAULT_FARM_POLICIES, NOW)).not.toThrow();
    expect(readFarmMetadata(identity(7), NOW)).toBeNull();
    expect(() => clearFarmMetadata()).not.toThrow();

    const throwingStorage: FarmMetadataStorage = {
      get length(): number { throw new Error('length disabled'); },
      key: () => { throw new Error('key disabled'); },
      getItem: () => { throw new Error('read disabled'); },
      setItem: () => { throw new Error('write disabled'); },
      removeItem: () => { throw new Error('remove disabled'); },
    };
    setFarmMetadataStorageProviderForTests(() => throwingStorage);
    expect(() => cacheFarmMetadata(identity(7), [season(1)], DEFAULT_FARM_POLICIES, NOW)).not.toThrow();
    expect(readFarmMetadata(identity(7), NOW)).toBeNull();
    expect(() => clearFarmMetadata(identity(7))).not.toThrow();
    expect(() => clearFarmMetadata()).not.toThrow();
  });
});
