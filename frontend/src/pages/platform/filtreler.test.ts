/**
 * PP4b: Güvenlik ekranının saf yardımcıları.
 *
 * Süzgeç doğrulaması uçtaki aralığın İSTEMCİ KOPYASIDIR
 * (backend/app/routers/platform_audit.py:75-76); burada sınır değerleri tek tek
 * sınanır ki uç kuralı değişip kopya güncellenmediğinde kırmızıya düşsün.
 */
import {describe,expect,it} from 'vitest';

import type {components} from '../../api/types.gen';

import {BOS_SUZGECLER,denetimParametreleri,ipGruplari,suzgecHatalari} from './filtreler';

type HizSiniriSatiri=components['schemas']['HizSiniriSatiri'];
const satir=(action:string,ip_address:string,attempts=1):HizSiniriSatiri=>
 ({action,ip_address,attempts,last_at:'2026-09-10T08:00:00Z'});

describe('suzgecHatalari',()=>{
 it('boş alan hata vermez',()=>{
  expect(suzgecHatalari(BOS_SUZGECLER)).toEqual({});
  expect(suzgecHatalari({...BOS_SUZGECLER,actor_id:'  '})).toEqual({});
 });

 it('aktör kimliği: 1 ve üstü geçerli, 0 ve sayı olmayan geçersiz',()=>{
  expect(suzgecHatalari({...BOS_SUZGECLER,actor_id:'1'})).toEqual({});
  expect(suzgecHatalari({...BOS_SUZGECLER,actor_id:'4200'})).toEqual({});
  expect(suzgecHatalari({...BOS_SUZGECLER,actor_id:'0'}).actor_id).toContain('1 veya daha büyük');
  expect(suzgecHatalari({...BOS_SUZGECLER,actor_id:'-3'}).actor_id).toBeTruthy();
  expect(suzgecHatalari({...BOS_SUZGECLER,actor_id:'abc'}).actor_id).toBeTruthy();
  expect(suzgecHatalari({...BOS_SUZGECLER,actor_id:'1.5'}).actor_id).toBeTruthy();
 });

 it('durum kodu: 100–599 geçerli, dışı geçersiz',()=>{
  expect(suzgecHatalari({...BOS_SUZGECLER,status_code:'100'})).toEqual({});
  expect(suzgecHatalari({...BOS_SUZGECLER,status_code:'599'})).toEqual({});
  expect(suzgecHatalari({...BOS_SUZGECLER,status_code:'99'}).status_code).toContain('100–599');
  expect(suzgecHatalari({...BOS_SUZGECLER,status_code:'600'}).status_code).toBeTruthy();
  expect(suzgecHatalari({...BOS_SUZGECLER,status_code:'4'}).status_code).toBeTruthy();
  expect(suzgecHatalari({...BOS_SUZGECLER,status_code:'4x'}).status_code).toBeTruthy();
 });

 it('iki alan birden bozuksa ikisi de bildirilir',()=>{
  expect(Object.keys(suzgecHatalari({...BOS_SUZGECLER,actor_id:'0',status_code:'7'}))).toEqual(['actor_id','status_code']);
 });
});

// Aralık dışı sayı süzgeç olarak da GÖNDERİLMEZ: doğrulama atlansa bile uç 422 görmez.
it('denetimParametreleri aralık dışı sayıyı düşürür',()=>{
 expect(denetimParametreleri(50,{...BOS_SUZGECLER,actor_id:'0',status_code:'600'})).toEqual({limit:50});
 expect(denetimParametreleri(50,{...BOS_SUZGECLER,actor_id:'3',status_code:'599'})).toEqual({limit:50,actor_id:3,status_code:599});
});

describe('ipGruplari',()=>{
 it('aynı IP tek grupta toplanır, ilk görülme sırası korunur',()=>{
  const gruplar=ipGruplari([satir('login','10.0.0.7'),satir('password_reset','10.0.0.9'),satir('otp','10.0.0.7')]);
  expect(gruplar.map(grup=>grup.ip)).toEqual(['10.0.0.7','10.0.0.9']);
  expect(gruplar[0].satirlar.map(kayit=>kayit.action)).toEqual(['login','otp']);
  expect(gruplar[1].satirlar).toHaveLength(1);
 });

 it('boş liste boş grup listesi verir',()=>{
  expect(ipGruplari([])).toEqual([]);
 });
});
