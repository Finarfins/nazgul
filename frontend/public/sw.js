// Legacy service worker KILL-SWITCH — one-release migration measure.
//
// Earlier builds shipped this file as a network-first PWA service worker
// (CACHE 'yhp-shell-v1'). On some browsers it pinned users to a stale
// index.html + old hashed chunks even after a new image was deployed: an open
// tab kept being served old assets, a later dynamic import hit a purged chunk
// ("Failed to fetch dynamically imported module"), and the page fell to the
// ErrorBoundary. The app itself no longer registers ANY service worker
// (see main.tsx, which only unregisters + clears caches).
//
// This replacement does the opposite of a caching worker: it neutralises any
// previously-registered legacy worker. On activation it deletes only the known
// legacy Cache Storage entry, claims open clients, unregisters itself, and reloads the
// windows it controls once — over the network, with a one-time cache-busting
// query param — so they land on the current index.html and bundle. It
// intentionally registers NO fetch handler, so while briefly active it never
// serves anything from cache.
//
// Offline M1 build-1 emeklilik bridge'i bu dosyayı yalnız legacy kayıtların
// güvenli kapanışı için tutar. Global cache temizliği yapmaz.

// Tek kullanımlık önbellek-kırıcı sorgu parametresi. Bir pencere zaten bu
// parametreyle yeniden yüklendiyse tekrar yönlendirilmez (yeniden yükleme
// döngüsü önleme).
const REFRESH_PARAM = 'sw-refresh';
const LEGACY_CACHE_ALLOWLIST = new Set(['yhp-shell-v1']);

self.addEventListener('install', (event) => {
  // Bekleyen eski worker'ı atlayıp hemen aktive ol. skipWaiting promise'ini
  // event.waitUntil'a vererek devralmayı yaşam döngüsü açısından güvenli kıl.
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // 1) Yalnız bilinen legacy cache'i sil. Yeni saha prefix'i ve origin'deki
    // diğer uygulamalar bu emeklilik worker'ından yapısal olarak etkilenmez.
    try {
      const keys = await self.caches.keys();
      await Promise.all(
        keys.filter((key) => LEGACY_CACHE_ALLOWLIST.has(key))
          .map((key) => self.caches.delete(key)),
      );
    } catch (error) {
      /* Cache Storage erişilemiyor - yok say. */
    }

    // 2) Açık istemcilerin kontrolünü al ki aşağıdaki yeniden yükleme uygulansın.
    try {
      await self.clients.claim();
    } catch (error) {
      /* yok say */
    }

    // 3) Bu worker kaydını kaldır - bundan sonra hiçbir isteği yakalamaz.
    try {
      await self.registration.unregister();
    } catch (error) {
      /* yok say */
    }

    // 4) Kontrol edilen pencereleri bir kez, önbellek-kırıcı tek kullanımlık bir
    //    sorgu parametresiyle ağdan yeniden yükle: önbellek silindi ve worker
    //    kaldırıldı, dolayısıyla güncel index.html + chunk'lar gelir. Mevcut yol,
    //    sorgu ve hash korunur; parametre zaten varsa yeniden yükleme atlanır
    //    (döngü önleme).
    try {
      const clients = await self.clients.matchAll({type: 'window'});
      await Promise.all(clients.map((client) => {
        let target;
        try {
          target = new URL(client.url);
        } catch (error) {
          return undefined; // URL ayrıştırılamıyorsa pencereye dokunma
        }
        if (target.searchParams.has(REFRESH_PARAM)) {
          return undefined; // zaten yenilendi - döngüyü önle
        }
        // searchParams.set yalnızca bu parametreyi ekler/günceller; diğer sorgu
        // değerleri, pathname ve hash olduğu gibi korunur.
        target.searchParams.set(REFRESH_PARAM, Date.now().toString());
        return client.navigate(target.href).catch(() => undefined);
      }));
    } catch (error) {
      /* yok say */
    }
  })());
});

// Kasıtlı olarak 'fetch' dinleyicisi YOK: bu worker hiçbir isteği yakalamaz,
// böylece tarayıcı doğrudan ağa gider ve bayat içerik servis edilmez.
