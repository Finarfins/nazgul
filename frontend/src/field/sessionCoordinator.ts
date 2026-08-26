import {acceptRemoteFieldLock} from './fieldDatabase';

const controllers=new Set<AbortController>();
const listeners=new Set<()=>void>();
const channel=typeof BroadcastChannel==='undefined'?null:new BroadcastChannel('sungur-field-session');

channel?.addEventListener('message',event=>{
 if(event.data?.type!=='FIELD_LOGOUT')return;
 acceptRemoteFieldLock();abortFieldSyncs();for(const listener of listeners)listener();
});
export function createFieldSyncController(){
 const controller=new AbortController();controllers.add(controller);
 return{controller,release:()=>controllers.delete(controller)};
}
export function abortFieldSyncs(){for(const controller of controllers)controller.abort();controllers.clear()}
export function broadcastFieldLogout(){channel?.postMessage({type:'FIELD_LOGOUT'});for(const listener of listeners)listener()}
export function subscribeFieldLogout(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener)}}
