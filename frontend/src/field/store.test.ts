import 'fake-indexeddb/auto';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import {
  applySnapshot,captureWriteEpoch,clearAllFieldData,closeFieldDatabase,FIELD_CACHE_PREFIX,lockFieldWrites,
  rememberOnlineIdentity,resetFieldStorageForTests,unlockFieldWritesAfterLogin,
} from './fieldDatabase';
import {OFFLINE_VERIFICATION_MS,readFieldAccess} from './store';

const user={id:7,username:'tech',display_name:'Teknisyen',role:'satis',must_change_password:false};
const order=(id:number)=>({id,work_order_no:`WO-${id}`,status:'OPEN',version:'1',updated_at:new Date().toISOString(),
 priority:'NORMAL',scheduled_date:null,complaint:null,diagnosis:null,repair_summary:null,technician_notes:null,
 machine:{id,brand:null,model:null,serial_number:null,chassis_number:null,working_hours:null},
 customer:{display_name:`Müşteri ${id}`,service_address:null}});
const identity=(companyId:number)=>({companyId,companyName:`Firma ${companyId}`,userId:user.id,user,permissions:['field_service']});
const snapshot=(companyId:number,id:number)=>({snapshot_version:companyId,generated_at:new Date().toISOString(),
 user_id:user.id,company_id:companyId,work_orders:[order(id)]});

async function readRawFieldStore<T>(storeName:string):Promise<T[]> {
 return new Promise((resolve,reject)=>{
  const request=indexedDB.open('sungur-field-m1');
  request.onerror=()=>reject(request.error);
  request.onsuccess=()=>{
   const db=request.result;
   const tx=db.transaction(storeName,'readonly');
   const rows=tx.objectStore(storeName).getAll();
   rows.onerror=()=>reject(rows.error);
   rows.onsuccess=()=>resolve(rows.result as T[]);
   tx.oncomplete=()=>db.close();
  };
 });
}

async function countEveryRuntimeStore():Promise<Record<string,number>> {
 return new Promise((resolve,reject)=>{
  const request=indexedDB.open('sungur-field-m1');
  request.onerror=()=>reject(request.error);
  request.onsuccess=()=>{
   const db=request.result;
   const names=Array.from(db.objectStoreNames);
   const tx=db.transaction(names,'readonly');
   Promise.all(names.map(name=>new Promise<[string,number]>((done,fail)=>{
    const count=tx.objectStore(name).count();
    count.onerror=()=>fail(count.error);
    count.onsuccess=()=>done([name,count.result]);
   }))).then(entries=>resolve(Object.fromEntries(entries)),reject);
   tx.oncomplete=()=>db.close();
  };
 });
}

async function seedAuxiliaryStores() {
 return new Promise<void>((resolve,reject)=>{
  const request=indexedDB.open('sungur-field-m1');
  request.onerror=()=>reject(request.error);
  request.onsuccess=()=>{
   const db=request.result;
   const names=['outbox','photo_blobs','conflicts'];
   const tx=db.transaction(names,'readwrite');
   for(const name of names)tx.objectStore(name).put({operationId:`seed-${name}`,payload:'field-secret'});
   tx.oncomplete=()=>{db.close();resolve()};
   tx.onerror=tx.onabort=()=>reject(tx.error);
  };
 });
}

function installMeasuredCacheStorage(){
 const buckets=new Map<string,Map<string,Response>>();
 const storage={
  keys:async()=>[...buckets.keys()],
  delete:async(name:string)=>buckets.delete(name),
  open:async(name:string)=>{
   const bucket=buckets.get(name)||new Map<string,Response>();buckets.set(name,bucket);
   return{
    put:async(key:string|Request,response:Response)=>{bucket.set(String(key),response)},
    keys:async()=>[...bucket.keys()].map(key=>new Request(new URL(key,'https://field.test'))),
   };
  },
 };
 vi.stubGlobal('caches',storage);
 return storage;
}

async function seedBrowserSurfaces(){
 localStorage.setItem('sungur-field-expiry-fixture','local-secret');
 sessionStorage.setItem('sungur-field-expiry-fixture','session-secret');
 localStorage.setItem('unrelated-fixture','keep');
 sessionStorage.setItem('unrelated-fixture','keep');
 const cache=await caches.open(`${FIELD_CACHE_PREFIX}expiry-fixture`);
 await cache.put('/field-secret',new Response('cache-secret'));
}

async function expectEverySurfaceSeeded(){
 const counts=await countEveryRuntimeStore();
 expect(Object.keys(counts).sort()).toEqual(['conflicts','control','meta','outbox','photo_blobs','work_order_snapshots']);
 for(const [name,count] of Object.entries(counts))expect(count,`${name} purge öncesi seed`).toBeGreaterThan(0);
 expect(localStorage.getItem('sungur-field-expiry-fixture')).not.toBeNull();
 expect(sessionStorage.getItem('sungur-field-expiry-fixture')).not.toBeNull();
 const names=(await caches.keys()).filter(name=>name.startsWith(FIELD_CACHE_PREFIX));
 expect(names.length).toBeGreaterThan(0);
 let entries=0;
 for(const name of names)entries+=(await (await caches.open(name)).keys()).length;
 expect(entries,'Cache API purge öncesi seed').toBeGreaterThan(0);
}

async function expectEverySurfacePurged(){
 const counts=await countEveryRuntimeStore();
 expect(Object.keys(counts).sort()).toEqual(['conflicts','control','meta','outbox','photo_blobs','work_order_snapshots']);
 expect(Object.values(counts)).toEqual([0,0,0,0,0,0]);
 expect(localStorage.getItem('sungur-field-expiry-fixture')).toBeNull();
 expect(sessionStorage.getItem('sungur-field-expiry-fixture')).toBeNull();
 expect((await caches.keys()).filter(name=>name.startsWith(FIELD_CACHE_PREFIX))).toEqual([]);
 expect(localStorage.getItem('unrelated-fixture')).toBe('keep');
 expect(sessionStorage.getItem('unrelated-fixture')).toBe('keep');
}

describe('field device partitions',()=>{
 let now=0;
 beforeEach(async()=>{now=new Date('2026-01-01T00:00:00Z').getTime();vi.spyOn(Date,'now').mockImplementation(()=>now);
  installMeasuredCacheStorage();localStorage.clear();sessionStorage.clear();await resetFieldStorageForTests()});
 afterEach(async()=>{await resetFieldStorageForTests();localStorage.clear();sessionStorage.clear();vi.restoreAllMocks();vi.unstubAllGlobals()});

 it('atomically clears every company partition on logout and cannot resurrect a session',async()=>{
  await rememberOnlineIdentity(identity(1));await applySnapshot(snapshot(1,101),1,user.id,await captureWriteEpoch());
  now+=1;
  await rememberOnlineIdentity(identity(2));await applySnapshot(snapshot(2,202),2,user.id,await captureWriteEpoch());
  expect((await readFieldAccess(1,user.id))?.workOrders).toHaveLength(1);
  expect((await readFieldAccess(2,user.id))?.workOrders).toHaveLength(1);
  await clearAllFieldData();
  expect(await readFieldAccess()).toBeNull();
  expect(await readFieldAccess(1,user.id)).toBeNull();
  expect(await readFieldAccess(2,user.id)).toBeNull();
 });
 it('expires and empties every runtime DB store plus all four storage surfaces',async()=>{
  await rememberOnlineIdentity(identity(1));await applySnapshot(snapshot(1,101),1,user.id,await captureWriteEpoch());
  await seedAuxiliaryStores();await seedBrowserSurfaces();
  await expectEverySurfaceSeeded();
  now+=OFFLINE_VERIFICATION_MS;
  expect(await readFieldAccess(1,user.id)).toBeNull();
  await expectEverySurfacePurged();
 });
 it('rollback empties every runtime DB store plus all four storage surfaces',async()=>{
  await rememberOnlineIdentity(identity(1));await applySnapshot(snapshot(1,101),1,user.id,await captureWriteEpoch());
  await seedAuxiliaryStores();await seedBrowserSurfaces();
  await expectEverySurfaceSeeded();
  now+=120000;
  expect(await readFieldAccess(1,user.id)).not.toBeNull();
  const [persistedMeta]=await readRawFieldStore<{lastSeenNow:number}>('meta');
  expect(persistedMeta.lastSeenNow).toBe(now);
  now-=61000;
  expect(await readFieldAccess(1,user.id)).toBeNull();
  await expectEverySurfacePurged();
 });
 it('keeps the monotonic high-water mark across a closed and reopened DB connection',async()=>{
  await rememberOnlineIdentity(identity(1));await applySnapshot(snapshot(1,101),1,user.id,await captureWriteEpoch());
  now+=120000;expect(await readFieldAccess(1,user.id)).not.toBeNull();
  const highWater=now;closeFieldDatabase();
  now-=30000;
  expect((await readFieldAccess(1,user.id))?.meta.lastSeenNow).toBe(highWater);
  closeFieldDatabase();
  expect((await readRawFieldStore<{lastSeenNow:number}>('meta'))[0].lastSeenNow).toBe(highWater);
  now-=31001;
  expect(await readFieldAccess(1,user.id)).toBeNull();
  expect(Object.values(await countEveryRuntimeStore())).toEqual([0,0,0,0,0,0]);
 });
 it('rejects an in-flight write from another tab after logout epoch commits',async()=>{
  await rememberOnlineIdentity(identity(1));const staleEpoch=await captureWriteEpoch();
  await lockFieldWrites();await clearAllFieldData();
  await expect(applySnapshot(snapshot(1,101),1,user.id,staleEpoch)).rejects.toThrow('eski oturum kuşağı');
  expect(await readFieldAccess()).toBeNull();
 });
 it('does not resurrect company A across A logout, B logout and A login',async()=>{
  await rememberOnlineIdentity(identity(1));await applySnapshot(snapshot(1,101),1,user.id,await captureWriteEpoch());
  await lockFieldWrites();await clearAllFieldData();unlockFieldWritesAfterLogin();
  await rememberOnlineIdentity(identity(2));await applySnapshot(snapshot(2,202),2,user.id,await captureWriteEpoch());
  await lockFieldWrites();await clearAllFieldData();unlockFieldWritesAfterLogin();
  await rememberOnlineIdentity(identity(1));
  expect((await readFieldAccess(1,user.id))?.workOrders).toEqual([]);
 });
});
