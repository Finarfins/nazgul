const CACHE='sungur-field-pwa-v1:ready:mutation-broken';
self.addEventListener('install',event=>event.waitUntil((async()=>{
 const cache=await caches.open(CACHE);
 // Deliberate mutation: same-shaped ready cache, no content verification.
 await cache.put('/saha/',new Response('corrupted-shell'));
 await cache.put('/field-pwa/release.json',new Response(JSON.stringify({
  hash:'mutation-broken',assets:[{url:'/saha/',sha256:'0'.repeat(64)}],
 })));
})()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
