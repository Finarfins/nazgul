/**
 * PP4: platform panelinin yazma eylemleri (PP2 uçları).
 *
 * Her eylem için: onay penceresi hedefi adıyla anar, istek gövdesi AYNEN
 * sözleşmedir, `changed:false` "Zaten bu durumda" bilgi bildirimidir, sunucu
 * hatası (409/429/404) sunucunun cümlesiyle görünür ve liste YENİLENMEZ,
 * başarıda liste yeniden çekilir.
 *
 * `errorDetail` gerçeğidir: sunucu cümlesinin ekrana geçtiğini sınar.
 *
 * `PlatformEylemleri.tsx` diye bir bileşen YOKTUR, hiç olmadı (H86): dosya
 * adı konuyu anar. Sınanan, `ortak/eylem.tsx` `EylemDugmesi` + `ortak/bildirim.tsx`
 * bildirimidir; dört sayfa (Companies, Users, Outbox, Security) üzerinden ve
 * `filtreler.ts` denetim süzgeçleriyle birlikte.
 */
import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import type {components} from '../../api/types.gen';

const get=vi.fn();
const post=vi.fn();
const sil=vi.fn();
vi.mock('../../api',async()=>{
 const gercek=await vi.importActual<typeof import('../../api')>('../../api');
 return {
  api:{get:(...args:unknown[])=>get(...args),post:(...args:unknown[])=>post(...args),delete:(...args:unknown[])=>sil(...args)},
  errorDetail:gercek.errorDetail,
 };
});
vi.mock('../../AuthContext',()=>({useAuth:()=>({can:(izin:string)=>izin==='platform'})}));

import PlatformCompanies from './PlatformCompanies';
import PlatformOutbox from './PlatformOutbox';
import PlatformSecurity from './PlatformSecurity';
import {BOS_SUZGECLER,denetimParametreleri,gunBasi} from './filtreler';
import PlatformUsers from './PlatformUsers';
import {ZATEN_BU_DURUMDA} from './ortak/eylem';

const sunucuHatasi=(status:number,detail:string)=>({response:{status,data:{detail}}});

const SIRKETLER:components['schemas']['PlatformSirketListesi']={
 total:2,limit:50,offset:0,
 items:[
  {id:7,name:'Merkez Tarım',is_active:true,member_count:4,last_activity_at:null,created_at:null},
  {id:8,name:'Eski Şube',is_active:false,member_count:0,last_activity_at:null,created_at:null},
 ],
};
const KULLANICILAR:components['schemas']['PlatformKullaniciListesi']={
 total:2,limit:50,offset:0,
 items:[
  {id:11,username:'ayse@ornek.com',display_name:'Ayşe',role:'admin',is_active:true,email_verified:false,
   must_change_password:false,created_at:null,last_login_at:null,memberships:[]},
  {id:12,username:'mehmet',display_name:'Mehmet',role:'user',is_active:false,email_verified:true,
   must_change_password:false,created_at:null,last_login_at:null,memberships:[]},
 ],
};
const SAGLIK:components['schemas']['KuyrukSagligi']={
 channels:[{channel:'whatsapp',pending:1,failed:5,sent_last_24h:0,oldest_pending_age_seconds:60}],
 field_stock_scheduler:{},
};
const HIZ:components['schemas']['HizSiniriOzeti']={
 window_hours:24,retention_hours:48,
 items:[{action:'login',ip_address:'10.0.0.7',attempts:31,last_at:'2026-09-10T08:00:00Z'}],
};

beforeEach(()=>{
 get.mockReset();post.mockReset();sil.mockReset();
 get.mockImplementation((yol:string)=>Promise.resolve({data:
  yol==='/platform/companies'?SIRKETLER:
  yol==='/platform/users'?KULLANICILAR:
  yol==='/platform/verifications'?{total:0,limit:20,offset:0,items:[]}:
  yol==='/platform/outbox/health'?SAGLIK:
  yol==='/platform/rate-limits'?HIZ:[]}));
});
afterEach(cleanup);

const cagriSayisi=(yol:string)=>get.mock.calls.filter(cagri=>cagri[0]===yol).length;
const onayMetni=()=>screen.getByTestId('eylem-onay-metni').textContent??'';
const onayla=(etiket:string)=>fireEvent.click(within(screen.getByRole('dialog')).getByRole('button',{name:etiket}));
const bildirim=async()=>(await screen.findByTestId('eylem-bildirimi')).textContent??'';

// ---------------------------------------------------------------- şirketler
it('askıya al: onay firma adını ve sonucu anar, gövdesiz POST, başarıda liste yenilenir',async()=>{
 post.mockResolvedValue({data:{id:7,is_active:false,changed:true}});
 render(<PlatformCompanies/>);
 fireEvent.click(await screen.findByTestId('askiya-al-7'));
 expect(onayMetni()).toContain('Merkez Tarım');
 expect(onayMetni()).toContain('oturum açamaz');
 expect(post).not.toHaveBeenCalled();
 const once=cagriSayisi('/platform/companies');
 onayla('Askıya al');
 expect(await bildirim()).toContain('Merkez Tarım askıya alındı');
 expect(post).toHaveBeenCalledWith('/platform/companies/7/deactivate');
 await waitFor(()=>expect(cagriSayisi('/platform/companies')).toBe(once+1));
});

it('askıya al vazgeç: çağrı yapılmaz',async()=>{
 render(<PlatformCompanies/>);
 fireEvent.click(await screen.findByTestId('askiya-al-7'));
 onayla('Vazgeç');
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 expect(post).not.toHaveBeenCalled();
});

it('aç: onaysız activate çağrısı; changed:false bilgi bildirimi, hata değil',async()=>{
 post.mockResolvedValue({data:{id:8,is_active:true,changed:false}});
 render(<PlatformCompanies/>);
 fireEvent.click(await screen.findByTestId('ac-8'));
 expect(await bildirim()).toBe(ZATEN_BU_DURUMDA);
 expect(post).toHaveBeenCalledWith('/platform/companies/8/activate');
 expect(screen.getByTestId('eylem-bildirimi').className).toMatch(/Info/);
});

// ---------------------------------------------------------------- kullanıcılar
it('kilitle: onay kullanıcı adını ve sonucu anar, gövde {locked:true}',async()=>{
 post.mockResolvedValue({data:{id:11,locked:true,changed:true}});
 render(<PlatformUsers/>);
 fireEvent.click(await screen.findByTestId('kilitle-11'));
 expect(onayMetni()).toContain('ayse@ornek.com');
 expect(onayMetni()).toContain('oturumları hemen kapanır');
 const once=cagriSayisi('/platform/users');
 onayla('Kilitle');
 expect(await bildirim()).toContain('ayse@ornek.com kilitlendi');
 expect(post).toHaveBeenCalledWith('/platform/users/11/status',{locked:true});
 await waitFor(()=>expect(cagriSayisi('/platform/users')).toBe(once+1));
});

it('kilitle 409 (kendi hesabı): sunucu cümlesi görünür, liste yenilenmez, satır aynı',async()=>{
 post.mockRejectedValue(sunucuHatasi(409,'Kendi hesabınızı kilitleyemezsiniz'));
 render(<PlatformUsers/>);
 fireEvent.click(await screen.findByTestId('kilitle-11'));
 const once=cagriSayisi('/platform/users');
 onayla('Kilitle');
 expect(await bildirim()).toBe('Kendi hesabınızı kilitleyemezsiniz');
 expect(screen.getByTestId('eylem-bildirimi').className).toMatch(/Error/);
 expect(cagriSayisi('/platform/users')).toBe(once);
 expect(within(screen.getByTestId('kullanici-11')).getByText('Aktif')).toBeTruthy();
 expect(screen.getByTestId('kilitle-11')).toBeTruthy();
});

it('kilidi aç: onaysız, gövde {locked:false}; changed:false bilgi bildirimi',async()=>{
 post.mockResolvedValue({data:{id:12,locked:false,changed:false}});
 render(<PlatformUsers/>);
 fireEvent.click(await screen.findByTestId('kilit-ac-12'));
 expect(await bildirim()).toBe(ZATEN_BU_DURUMDA);
 expect(post).toHaveBeenCalledWith('/platform/users/12/status',{locked:false});
});

it('şifre sıfırlat: onay kullanıcı adını anar, gövdesiz POST',async()=>{
 post.mockResolvedValue({data:{user_id:12,must_change_password:true,changed:true}});
 render(<PlatformUsers/>);
 fireEvent.click(await screen.findByTestId('sifre-12'));
 expect(onayMetni()).toContain('mehmet');
 onayla('Şifre sıfırlat');
 expect(await bildirim()).toContain('mehmet için şifre değişimi zorunlu kılındı');
 expect(post).toHaveBeenCalledWith('/platform/users/12/force-password-reset');
});

it('şifre sıfırlat changed:false → Zaten bu durumda',async()=>{
 post.mockResolvedValue({data:{user_id:12,must_change_password:true,changed:false}});
 render(<PlatformUsers/>);
 fireEvent.click(await screen.findByTestId('sifre-12'));
 onayla('Şifre sıfırlat');
 expect(await bildirim()).toBe(ZATEN_BU_DURUMDA);
});

it('doğrulama gönder: yalnız doğrulanmamış hesapta; 200 başarı bildirimi',async()=>{
 post.mockResolvedValue({data:{user_id:11,queued:true,changed:true}});
 render(<PlatformUsers/>);
 await screen.findByTestId('kullanici-12');
 expect(screen.queryByTestId('dogrulama-12')).toBeNull();
 fireEvent.click(screen.getByTestId('dogrulama-11'));
 expect(await bildirim()).toContain('doğrulama postası kuyruğa alındı');
 expect(post).toHaveBeenCalledWith('/platform/users/11/resend-verification');
});

it('doğrulama gönder 429: hız sınırı cümlesi görünür',async()=>{
 post.mockRejectedValue(sunucuHatasi(429,'Çok fazla deneme yapıldı. Lütfen daha sonra tekrar deneyin.'));
 render(<PlatformUsers/>);
 fireEvent.click(await screen.findByTestId('dogrulama-11'));
 expect(await bildirim()).toBe('Çok fazla deneme yapıldı. Lütfen daha sonra tekrar deneyin.');
});

it('doğrulama gönder 409: zaten doğrulanmış cümlesi görünür',async()=>{
 post.mockRejectedValue(sunucuHatasi(409,'Hesap zaten doğrulanmış'));
 render(<PlatformUsers/>);
 fireEvent.click(await screen.findByTestId('dogrulama-11'));
 expect(await bildirim()).toBe('Hesap zaten doğrulanmış');
});

// ---------------------------------------------------------------- kuyruk
it('yeniden dene: onay kanalı ve başarısız sayısını anar, gövde {channel}, requeued görünür',async()=>{
 post.mockResolvedValue({data:{requeued:3,remaining:0,not_retryable:2,changed:true}});
 render(<PlatformOutbox/>);
 fireEvent.click(await screen.findByTestId('yeniden-whatsapp'));
 expect(onayMetni()).toContain('whatsapp');
 expect(onayMetni()).toContain('5 başarısız');
 const once=cagriSayisi('/platform/outbox/health');
 onayla('Yeniden dene');
 const metin=await bildirim();
 expect(metin).toContain('3 bildirim yeniden kuyruğa alındı');
 expect(metin).toContain('yeniden denenemez 2');
 expect(post).toHaveBeenCalledWith('/platform/outbox/retry',{channel:'whatsapp'});
 await waitFor(()=>expect(cagriSayisi('/platform/outbox/health')).toBe(once+1));
});

// H62: burada `changed:false` "zaten bu durumda" değil, "uygun kayıt yok"tur;
// genel cümle yerine yanıtın `not_retryable` sayısı gösterilir.
it('yeniden dene changed:false → kalıcı hata sayısını anan cümle, genel cümle DEĞİL',async()=>{
 post.mockResolvedValue({data:{requeued:0,remaining:0,not_retryable:5,changed:false}});
 render(<PlatformOutbox/>);
 fireEvent.click(await screen.findByTestId('yeniden-whatsapp'));
 const once=cagriSayisi('/platform/outbox/health');
 onayla('Yeniden dene');
 expect(await bildirim()).toBe('Kuyruğa alınacak kayıt yok · kalıcı hata: 5');
 expect(screen.getByTestId('eylem-bildirimi').textContent).not.toContain(ZATEN_BU_DURUMDA);
 expect(screen.getByTestId('eylem-bildirimi').className).toMatch(/Info/);
 // Liste yine de tazelenir (`ortak/eylem.tsx` `EylemDugmesi`: Şef kararı, mercek #142 3a).
 await waitFor(()=>expect(cagriSayisi('/platform/outbox/health')).toBe(once+1));
});

// ---------------------------------------------------------------- güvenlik
it('hız sınırı temizle: onay IP’yi anar, DELETE ?ip=, iki sayı ayrı görünür',async()=>{
 sil.mockResolvedValue({data:{ip_address:'10.0.0.7',rate_limit_rows:4,login_attempt_rows:2,changed:true}});
 render(<PlatformSecurity/>);
 fireEvent.click(await screen.findByTestId('temizle-10.0.0.7'));
 expect(onayMetni()).toContain('10.0.0.7');
 const once=cagriSayisi('/platform/rate-limits');
 onayla('Kilidi temizle');
 const metin=await bildirim();
 expect(metin).toContain('4 hız sınırı kaydı');
 expect(metin).toContain('2 giriş kilidi kaydı');
 expect(sil).toHaveBeenCalledWith('/platform/rate-limits',{params:{ip:'10.0.0.7'}});
 await waitFor(()=>expect(cagriSayisi('/platform/rate-limits')).toBe(once+1));
});

it('hız sınırı temizle 404: sunucu cümlesi görünür, liste yenilenmez',async()=>{
 sil.mockRejectedValue(sunucuHatasi(404,'Bu IP için hız sınırı ya da giriş kilidi kaydı yok'));
 render(<PlatformSecurity/>);
 fireEvent.click(await screen.findByTestId('temizle-10.0.0.7'));
 const once=cagriSayisi('/platform/rate-limits');
 onayla('Kilidi temizle');
 expect(await bildirim()).toBe('Bu IP için hız sınırı ya da giriş kilidi kaydı yok');
 expect(cagriSayisi('/platform/rate-limits')).toBe(once);
});

it('denetim süzgeçleri uç parametrelerine AYNEN eşlenir',()=>{
 expect(denetimParametreleri(250,BOS_SUZGECLER)).toEqual({limit:250});
 expect(denetimParametreleri(50,{
  action:' POST ',ip_address:'10.0.0.7',username:'ayse',status_code:'409',actor_id:'3',
  date_from:'2026-09-01',date_to:'2026-09-15',
 })).toEqual({
  limit:50,action:'POST',ip_address:'10.0.0.7',username:'ayse',status_code:409,actor_id:3,
  date_from:new Date('2026-09-01T00:00:00').toISOString(),
  // `date_to` uçta hariç: bitiş gününü kapsamak için ertesi gece yarısı.
  date_to:new Date('2026-09-16T00:00:00').toISOString(),
 });
 // Sayı olmayan kimlik/kod gönderilmez (uç 422 dönerdi).
 expect(denetimParametreleri(50,{...BOS_SUZGECLER,actor_id:'abc',status_code:'4x'})).toEqual({limit:50});
 expect(gunBasi('')).toBeUndefined();
});

it('denetim süzgeç alanları yazıldıkça sorgu parametreleri gider',async()=>{
 render(<PlatformSecurity/>);
 await screen.findByTestId('temizle-10.0.0.7');
 const yaz=(etiket:string,deger:string)=>fireEvent.change(screen.getByLabelText(etiket),{target:{value:deger}});
 yaz('İşlem','DELETE');
 yaz('IP adresi','10.0.0.9');
 yaz('Kullanıcı adı','op');
 yaz('Aktör kimliği','42');
 yaz('Durum kodu','404');
 yaz('Başlangıç','2026-09-01');
 yaz('Bitiş','2026-09-02');
 await waitFor(()=>expect(get.mock.calls.filter(cagri=>cagri[0]==='/platform/audit').at(-1)![1]).toEqual({params:{
  limit:250,action:'DELETE',ip_address:'10.0.0.9',username:'op',status_code:404,actor_id:42,
  date_from:new Date('2026-09-01T00:00:00').toISOString(),date_to:new Date('2026-09-03T00:00:00').toISOString(),
 }}));
 fireEvent.click(screen.getByRole('button',{name:'Süzgeçleri temizle'}));
 await waitFor(()=>expect(get.mock.calls.filter(cagri=>cagri[0]==='/platform/audit').at(-1)![1]).toEqual({params:{limit:250}}));
});
