import {describe, expect, it, vi} from 'vitest';
// public/ altındaki kill-switch worker'ını ham metin olarak oku; sahte bir
// ServiceWorkerGlobalScope içinde çalıştırıp davranışını doğrula. Dosya
// derlenen paketin parçası değil (public statik dosyası) olduğundan doğrudan
// import edilemez; bu yüzden ?raw ile okunup new Function ile değerlendirilir.
// ?raw içe aktarım tipi src/vite-env.d.ts içindeki vite/client referansından gelir.
import swSource from '../public/sw.js?raw';

type SwHandler = (event: {waitUntil?: (promise: Promise<unknown>) => void}) => void;

function loadKillSwitch(clientUrl = 'https://sungurtarim.example/panel') {
  const handlers: Record<string, SwHandler> = {};
  const caches = {
    keys: vi.fn().mockResolvedValue(['yhp-shell-v1', 'runtime-cache']),
    delete: vi.fn().mockResolvedValue(true),
  };
  const client = {
    url: clientUrl,
    navigate: vi.fn().mockResolvedValue(undefined),
  };
  // skipWaiting bilinen bir promise döndürsün ki install'ın onu doğrudan
  // event.waitUntil'a verdiğini kimlik eşitliğiyle doğrulayabilelim.
  const skipWaitingPromise = Promise.resolve();
  const self = {
    addEventListener: (type: string, handler: SwHandler) => {
      handlers[type] = handler;
    },
    skipWaiting: vi.fn().mockReturnValue(skipWaitingPromise),
    caches,
    clients: {
      claim: vi.fn().mockResolvedValue(undefined),
      matchAll: vi.fn().mockResolvedValue([client]),
    },
    registration: {unregister: vi.fn().mockResolvedValue(true)},
  };
  // sw.js her şeye yalnızca `self` üzerinden erişir; parametre global self'i gölgeler.
  // eslint-disable-next-line no-new-func
  new Function('self', swSource)(self);
  return {handlers, self, caches, client, skipWaitingPromise};
}

// activate handler'ının event.waitUntil'a verdiği promise'i yakala ve bekle.
async function runActivate(handlers: Record<string, SwHandler>): Promise<void> {
  let pending: Promise<unknown> = Promise.resolve();
  handlers.activate({waitUntil: (promise: Promise<unknown>) => {pending = promise;}});
  await pending;
}

describe('legacy service worker kill-switch (public/sw.js)', () => {
  it('is a kill-switch, not the old caching worker (no fetch handler, no cache writes)', () => {
    const {handlers} = loadKillSwitch();
    expect(handlers.fetch).toBeUndefined();
    // Önbelleğe yazma yok: addAll/put/respondWith hiçbir yerde geçmemeli.
    expect(swSource).not.toContain('addAll');
    expect(swSource).not.toContain('.put(');
    expect(swSource).not.toContain('respondWith');
  });

  it('install lifecycle-safe: passes the skipWaiting promise to event.waitUntil', () => {
    const {handlers, self, skipWaitingPromise} = loadKillSwitch();
    let waited: Promise<unknown> | undefined;
    handlers.install({waitUntil: (promise: Promise<unknown>) => {waited = promise;}});
    expect(self.skipWaiting).toHaveBeenCalledTimes(1);
    // waitUntil, skipWaiting'in döndürdüğü promise'i almalı (kimlik eşitliği).
    expect(waited).toBe(skipWaitingPromise);
  });

  it('on activate runs in order: delete caches -> claim -> unregister -> navigate', async () => {
    const {handlers, self, caches, client} = loadKillSwitch();
    await runActivate(handlers);
    expect(caches.keys).toHaveBeenCalledTimes(1);
    expect(caches.delete).toHaveBeenCalledWith('yhp-shell-v1');
    expect(caches.delete).not.toHaveBeenCalledWith('runtime-cache');
    expect(self.clients.claim).toHaveBeenCalledTimes(1);
    expect(self.registration.unregister).toHaveBeenCalledTimes(1);
    expect(client.navigate).toHaveBeenCalledTimes(1);
    // Çağrı sırası: her mock'un invocationCallOrder değeri monoton artar.
    const deleteOrder = Math.max(...caches.delete.mock.invocationCallOrder);
    const claimOrder = self.clients.claim.mock.invocationCallOrder[0];
    const unregisterOrder = self.registration.unregister.mock.invocationCallOrder[0];
    const navigateOrder = client.navigate.mock.invocationCallOrder[0];
    expect(deleteOrder).toBeLessThan(claimOrder);
    expect(claimOrder).toBeLessThan(unregisterOrder);
    expect(unregisterOrder).toBeLessThan(navigateOrder);
  });

  it('cache-busting navigation preserves path, query and hash', async () => {
    const {handlers, client} = loadKillSwitch(
      'https://sungurtarim.example/panel/is-emirleri?durum=OPEN&sayfa=2#detay',
    );
    await runActivate(handlers);
    const navigatedTo = client.navigate.mock.calls[0][0] as string;
    const url = new URL(navigatedTo);
    // Yol, mevcut sorgu değerleri ve hash korunur.
    expect(url.pathname).toBe('/panel/is-emirleri');
    expect(url.searchParams.get('durum')).toBe('OPEN');
    expect(url.searchParams.get('sayfa')).toBe('2');
    expect(url.hash).toBe('#detay');
    // Tek kullanımlık önbellek-kırıcı parametre eklenir.
    expect(url.searchParams.get('sw-refresh')).toBeTruthy();
  });

  it('prevents a reload loop: does not re-navigate a window already carrying the refresh param', async () => {
    const {handlers, client} = loadKillSwitch(
      'https://sungurtarim.example/panel?sw-refresh=123#x',
    );
    await runActivate(handlers);
    expect(client.navigate).not.toHaveBeenCalled();
  });

  it('still unregisters and does not reject when storage/clients throw', async () => {
    const {handlers, self} = loadKillSwitch();
    self.caches.keys.mockRejectedValueOnce(new Error('cache storage unavailable'));
    self.clients.claim.mockRejectedValueOnce(new Error('clients unavailable'));
    await expect(runActivate(handlers)).resolves.toBeUndefined();
    expect(self.registration.unregister).toHaveBeenCalledTimes(1);
  });
});
