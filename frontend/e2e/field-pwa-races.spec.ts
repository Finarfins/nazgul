import {expect,test,type Page} from '@playwright/test';

const SCRIPT='/field-pwa/sw-v1.js',SCOPE='/saha/',PREFIX='sungur-field-pwa-v1:';
const scenarios=[
 'unregister-before-register','unregister-between-register-install','cache-delete-during-precache',
 'cache-delete-after-install','unregister-between-activate-claim','cleanup-immediately-after-ready',
] as const;

async function cleanup(page:Page){
 await page.goto('/giris');
 await page.evaluate(async(prefix)=>{
  for(const registration of await navigator.serviceWorker.getRegistrations())await registration.unregister();
  for(const name of await caches.keys())if(name.startsWith(prefix))await caches.delete(name);
 },PREFIX);
}
async function registerAndReady(page:Page){
 await page.evaluate(async({script,scope})=>{
  const registration=await navigator.serviceWorker.register(script,{scope});
  await new Promise<void>((resolve,reject)=>{
   const timeout=setTimeout(()=>reject(new Error('worker-ready-timeout')),30000);
   const receive=(event:MessageEvent)=>{if(event.data?.type==='FIELD_PRECACHE_READY'){clearTimeout(timeout);navigator.serviceWorker.removeEventListener('message',receive);resolve()}};
   navigator.serviceWorker.addEventListener('message',receive);
   const request=()=>{if(registration.active)registration.active.postMessage({type:'FIELD_PRECACHE_STATUS'});else setTimeout(request,25)};
   request();
  });
 },{script:SCRIPT,scope:SCOPE});
}
async function measure(page:Page){
 return page.evaluate(async({scope,prefix})=>{
  const registrations=(await navigator.serviceWorker.getRegistrations()).filter(item=>new URL(item.scope).pathname===scope);
  const readyNames=(await caches.keys()).filter(name=>name.startsWith(prefix)&&name.includes(':ready:'));
  let expectedAssets=0,readyAssets=0,hashesValid=false;
  if(readyNames.length===1){
   const cache=await caches.open(readyNames[0]),manifestResponse=await cache.match('/field-pwa/release.json');
   const manifest=manifestResponse?await manifestResponse.json():null;
   expectedAssets=manifest?.assets?.length||0;readyAssets=(await cache.keys()).filter(request=>new URL(request.url).pathname!=='/field-pwa/release.json').length;
   hashesValid=Boolean(manifest)&&await Promise.all(manifest.assets.map(async(asset:{url:string;sha256:string})=>{
    const response=await cache.match(asset.url,{ignoreSearch:true});if(!response)return false;
    const hash=await crypto.subtle.digest('SHA-256',await response.arrayBuffer());
    return [...new Uint8Array(hash)].map(byte=>byte.toString(16).padStart(2,'0')).join('')===asset.sha256;
   })).then(results=>results.every(Boolean));
  }
  return{registrationCount:registrations.length,readyCacheCount:readyNames.length,expectedAssets,readyAssets,hashesValid};
 },{scope:SCOPE,prefix:PREFIX});
}

for(const scenario of scenarios)test(`gerçek SW yarışı: ${scenario}`,async({page})=>{
 await cleanup(page);let reloads=0,deletedChunkResponses=0;
 page.on('framenavigated',frame=>{if(frame===page.mainFrame())reloads+=1});
 page.on('response',response=>{if(/\/assets\/.+-[\w-]+\.(js|css)$/.test(new URL(response.url()).pathname)&&response.status()===404)deletedChunkResponses+=1});
 if(scenario==='unregister-before-register')await page.evaluate(async()=>{for(const item of await navigator.serviceWorker.getRegistrations())await item.unregister()});
 const registration=await page.evaluate(async({script,scope})=>navigator.serviceWorker.register(script,{scope}).then(item=>({scope:item.scope})),{script:SCRIPT,scope:SCOPE});
 expect(new URL(registration.scope).pathname).toBe(SCOPE);
 if(scenario==='unregister-between-register-install'||scenario==='unregister-between-activate-claim')await page.evaluate(async scope=>{
  const item=(await navigator.serviceWorker.getRegistrations()).find(candidate=>new URL(candidate.scope).pathname===scope);await item?.unregister();
 },SCOPE);
 if(scenario==='cache-delete-during-precache')await page.evaluate(async prefix=>{for(const name of await caches.keys())if(name.startsWith(prefix))await caches.delete(name)},PREFIX);
 await registerAndReady(page);
 if(scenario==='cache-delete-after-install'||scenario==='cleanup-immediately-after-ready'){
  await page.evaluate(async prefix=>{for(const name of await caches.keys())if(name.startsWith(prefix))await caches.delete(name)},PREFIX);
  if(scenario==='cleanup-immediately-after-ready')await page.evaluate(async()=>{for(const item of await navigator.serviceWorker.getRegistrations())await item.unregister()});
  else{
   await page.evaluate(async scope=>{const registration=(await navigator.serviceWorker.getRegistrations()).find(item=>new URL(item.scope).pathname===scope);registration?.active?.postMessage({type:'FIELD_PRECACHE_STATUS'})},SCOPE);
   await expect.poll(async()=>(await measure(page)).hashesValid,{timeout:30000}).toBe(true);
  }
  await registerAndReady(page);
 }
 const result=await measure(page);
 expect(result.registrationCount).toBe(1);expect(result.readyCacheCount).toBe(1);
 expect(result.readyAssets).toBe(result.expectedAssets);expect(result.hashesValid).toBe(true);
 expect(deletedChunkResponses).toBe(0);expect(reloads).toBeLessThanOrEqual(1);
});

test('mutasyon kanıtı: doğrulamayı atlayan gerçek worker integrity kapısında kırmızı ölçülür',async({page})=>{
 await cleanup(page);
 await page.evaluate(async()=>{const registration=await navigator.serviceWorker.register('/saha/sw-broken-test.js',{scope:'/saha/'});
  while(!registration.active)await new Promise(resolve=>setTimeout(resolve,25));return registration.scope});
 const result=await measure(page);
 expect(result.readyAssets).toBe(result.expectedAssets);
 expect(result.hashesValid).toBe(false);
});
