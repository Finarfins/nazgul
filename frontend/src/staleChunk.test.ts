import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {isStaleChunkError, reloadOnceForStaleChunk} from './staleChunk';

describe('isStaleChunkError', () => {
  it('matches the browser messages produced by a redeploy-stale chunk', () => {
    expect(isStaleChunkError(new Error('Failed to fetch dynamically imported module: https://x/assets/AppShell-abc.js'))).toBe(true);
    expect(isStaleChunkError(new Error('Importing a module script failed.'))).toBe(true);
    expect(isStaleChunkError(new Error('error loading dynamically imported module'))).toBe(true);
  });
  it('ignores unrelated errors', () => {
    expect(isStaleChunkError(new Error('Objects are not valid as a React child'))).toBe(false);
    expect(isStaleChunkError(null)).toBe(false);
  });
});

describe('reloadOnceForStaleChunk', () => {
  const reload = vi.fn();
  beforeEach(() => {
    sessionStorage.clear();
    reload.mockClear();
    vi.stubGlobal('location', {...window.location, reload});
  });
  afterEach(() => vi.unstubAllGlobals());

  it('reloads on the first hit and refuses within the guard window', () => {
    expect(reloadOnceForStaleChunk()).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    // ikinci çağrı koruma penceresi içinde — sonsuz reload döngüsü yok
    expect(reloadOnceForStaleChunk()).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('allows a fresh reload after the guard window expires', () => {
    expect(reloadOnceForStaleChunk()).toBe(true);
    sessionStorage.setItem('yhp_stale_chunk_reload_at', String(Date.now() - 60_000));
    expect(reloadOnceForStaleChunk()).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });
});
