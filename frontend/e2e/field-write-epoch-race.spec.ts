import {adminApi,expect,loginAs,provisionUser,test} from './helpers';

test('gerçek iki sekme yarışı: logout epoch gerçek applySnapshot commitini reddeder',async({page,context})=>{
 const admin=await adminApi();
 const account=await provisionUser(admin,{username:`field_epoch_${Date.now()}`,role:'satis',displayName:'Epoch Teknisyen'});
 await admin.dispose();
 await loginAs(page,account.username,account.password);
 await expect(page.getByText('Son Satışlar')).toBeVisible({timeout:15000});
 await expect.poll(()=>page.evaluate(async()=>new Promise<number>((resolve,reject)=>{
  const open=indexedDB.open('sungur-field-m1');
  open.onerror=()=>reject(open.error);
  open.onsuccess=()=>{
   const db=open.result,request=db.transaction('meta','readonly').objectStore('meta').count();
   request.onerror=()=>reject(request.error);request.onsuccess=()=>{db.close();resolve(request.result)};
  };
 }))).toBeGreaterThan(0);

 const delayedTab=await context.newPage();
 // Epoch fence'i tek başına ölç: bu sekme logout BroadcastChannel kilidini almasın.
 await delayedTab.addInitScript(()=>{
  const NativeBroadcastChannel=window.BroadcastChannel;
  class EpochTestBroadcastChannel extends NativeBroadcastChannel {
   addEventListener(type:string,listener:EventListenerOrEventListenerObject,options?:boolean|AddEventListenerOptions){
    if(this.name==='sungur-field-session'&&type==='message')return;
    super.addEventListener(type,listener,options);
   }
  }
  window.BroadcastChannel=EpochTestBroadcastChannel;
 });
 await delayedTab.goto('/saha/');
 await expect(delayedTab.getByText('Saha İşlerim')).toBeVisible({timeout:15000});
 const captured=await delayedTab.evaluate(async()=>{
  if(!window.__FIELD_E2E__)throw new Error('E2E field driver yüklenmedi');
  const meta=await new Promise<{companyId:number;userId:number}>((resolve,reject)=>{
   const open=indexedDB.open('sungur-field-m1');
   open.onerror=()=>reject(open.error);
   open.onsuccess=()=>{
    const db=open.result,request=db.transaction('meta','readonly').objectStore('meta').getAll();
    request.onerror=()=>reject(request.error);
    request.onsuccess=()=>{db.close();resolve(request.result[0])};
   };
  });
  return{...meta,epoch:await window.__FIELD_E2E__.captureWriteEpoch()};
 });

 await page.locator('.MuiAvatar-root').click();
 await page.getByRole('menuitem',{name:'Çıkış Yap'}).click();
 await expect(page).toHaveURL(/giris/);

 const result=await delayedTab.evaluate(async({companyId,userId,epoch})=>{
  const snapshot={
   snapshot_version:999,generated_at:new Date().toISOString(),company_id:companyId,user_id:userId,
   work_orders:[{id:999,work_order_no:'LATE-999',status:'OPEN',version:'late',updated_at:new Date().toISOString(),
    priority:'NORMAL',scheduled_date:null,complaint:null,diagnosis:null,repair_summary:null,technician_notes:null,
    machine:{id:999,brand:null,model:null,serial_number:null,chassis_number:null,working_hours:null},
    customer:{display_name:'Gecikmiş kayıt',service_address:null}}],
  };
  try{
   const committed=await window.__FIELD_E2E__!.applySnapshot(snapshot,companyId,userId,epoch);
   return{rejected:false,committed,errorName:null};
  }catch(error){
   return{rejected:true,committed:false,errorName:error instanceof Error?error.name:String(error)};
  }
 },captured);
 expect(result).toEqual({rejected:true,committed:false,errorName:'StaleFieldWriteError'});
 const snapshotCount=await delayedTab.evaluate(async()=>new Promise<number>((resolve,reject)=>{
  const open=indexedDB.open('sungur-field-m1');
  open.onerror=()=>reject(open.error);
  open.onsuccess=()=>{
   const db=open.result,request=db.transaction('work_order_snapshots','readonly').objectStore('work_order_snapshots').count();
   request.onerror=()=>reject(request.error);request.onsuccess=()=>{db.close();resolve(request.result)};
  };
 }));
 expect(snapshotCount).toBe(0);
 await delayedTab.close();
});
