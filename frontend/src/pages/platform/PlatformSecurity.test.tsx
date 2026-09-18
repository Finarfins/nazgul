import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import type {components} from '../../api/types.gen';

const get=vi.fn();
const post=vi.fn();
vi.mock('../../api',()=>({
 api:{get:(...args:unknown[])=>get(...args),post:(...args:unknown[])=>post(...args),delete:(...args:unknown[])=>post(...args)},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
}));

let operator=true;
vi.mock('../../AuthContext',()=>({useAuth:()=>({can:(izin:string)=>izin==='platform'?operator:true})}));

import PlatformSecurity from './PlatformSecurity';
import {PENCERELER} from './filtreler';

// Aynı IP iki eylem satırında: temizlik ucu IP başına olduğu için tek düğme beklenir.
const HIZ=(window_hours:number):components['schemas']['HizSiniriOzeti']=>({
 window_hours,retention_hours:48,
 items:[
  {action:'login',ip_address:'10.0.0.7',attempts:31,last_at:'2026-09-10T08:00:00Z'},
  {action:'password_reset',ip_address:'10.0.0.7',attempts:4,last_at:'2026-09-10T09:00:00Z'},
  {action:'login',ip_address:'10.0.0.9',attempts:2,last_at:'2026-09-10T07:00:00Z'},
 ],
});
const DENETIM=[
 {id:501,username:null,action:'POST',path:'/api/auth/login',status_code:401,ip_address:'10.0.0.7',
  created_at:'2026-09-10T08:00:00Z',outcome:'failure',failure_reason:'bad_credentials'},
];

beforeEach(()=>{
 get.mockReset();
 get.mockImplementation((yol:string,{params}:{params:{window_hours:number}})=>
  Promise.resolve({data:yol==='/platform/rate-limits'?HIZ(params.window_hours):DENETIM}));
});
afterEach(()=>{cleanup();operator=true;post.mockReset()});

const cagrilar=(yol:string)=>get.mock.calls.filter(cagri=>cagri[0]===yol);

it('hız sınırı özetini, saklama notunu ve firmasız denetim olaylarını çizer',async()=>{
 render(<PlatformSecurity/>);
 expect(await screen.findByText('31')).toBeTruthy();
 expect(screen.getByText(/48 saat saklanır/)).toBeTruthy();
 const olay=await screen.findByTestId('denetim-501');
 expect(within(olay).getByText('/api/auth/login')).toBeTruthy();
 expect(within(olay).getByText('401 · failure')).toBeTruthy();
 expect(within(olay).getByText('bad_credentials')).toBeTruthy();
 expect(cagrilar('/platform/rate-limits')[0][1]).toEqual({params:{window_hours:24}});
 expect(cagrilar('/platform/audit')[0][1]).toEqual({params:{limit:250}});
});

it('pencere seçici en fazla 168 saat sunar ve window_hours gönderir',async()=>{
 expect(Math.max(...PENCERELER)).toBe(168);
 render(<PlatformSecurity/>);
 await screen.findByText('31');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:/Pencere/}));
 fireEvent.click(await screen.findByRole('option',{name:'Son 168 saat'}));
 await waitFor(()=>expect(cagrilar('/platform/rate-limits').at(-1)![1]).toEqual({params:{window_hours:168}}));
});

it('limit seçici yalnız limit gönderir; boş süzgeçler gönderilmez',async()=>{
 render(<PlatformSecurity/>);
 await screen.findByTestId('denetim-501');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:/Son kayıt/}));
 fireEvent.click(await screen.findByRole('option',{name:'Son 1000'}));
 await waitFor(()=>expect(cagrilar('/platform/audit').at(-1)![1]).toEqual({params:{limit:1000}}));
});

it('403 → yetki yok paneli',async()=>{
 get.mockReset();get.mockRejectedValue({response:{status:403}});
 render(<PlatformSecurity/>);
 expect(await screen.findByTestId('platform-yetki-yok')).toBeTruthy();
});

it('ağ hatası iki bölümde de mesaj gösterir',async()=>{
 get.mockReset();get.mockRejectedValue(new Error('Network Error'));
 render(<PlatformSecurity/>);
 await waitFor(()=>expect(screen.getAllByText(/Veri yüklenemedi/)).toHaveLength(2));
});

// ------------------------------------------------ H63: IP başına tek düğme
it('aynı IP’nin eylem satırları tek "Kilidi temizle" düğmesi paylaşır',async()=>{
 render(<PlatformSecurity/>);
 await screen.findByText('31');
 // Üç satır çizilir ama iki farklı IP → iki düğme.
 expect(screen.getAllByRole('button',{name:'Kilidi temizle'})).toHaveLength(2);
 expect(screen.getByTestId('temizle-10.0.0.7')).toBeTruthy();
 expect(screen.getByTestId('temizle-10.0.0.9')).toBeTruthy();
 expect(screen.getByText('password_reset')).toBeTruthy();
});

it('onay penceresi IP’yi ve kapsadığı satır sayısını anar',async()=>{
 render(<PlatformSecurity/>);
 fireEvent.click(await screen.findByTestId('temizle-10.0.0.7'));
 const metin=screen.getByTestId('eylem-onay-metni').textContent??'';
 expect(metin).toContain('10.0.0.7');
 expect(metin).toContain('2');
 expect(metin).toContain('login, password_reset');
 expect(post).not.toHaveBeenCalled();
});

// ------------------------------------------------ H67a: operatör olmayan
it('operatör olmayan kullanıcıya temizleme düğmesi HİÇ çizilmez',async()=>{
 operator=false;
 render(<PlatformSecurity/>);
 await screen.findByText('31');
 expect(screen.queryAllByRole('button',{name:'Kilidi temizle'})).toHaveLength(0);
 expect(screen.queryByTestId('temizle-10.0.0.7')).toBeNull();
 // Tablo yine görünür: okuma yetkisi ayrıdır.
 expect(screen.getByText('10.0.0.9')).toBeTruthy();
});

// ------------------------------------------------ H67b: süzgeç sınırları
it('aralık dışı aktör kimliği: alan hatası ve İSTEK GİTMEZ',async()=>{
 render(<PlatformSecurity/>);
 await screen.findByTestId('denetim-501');
 const once=cagrilar('/platform/audit').length;
 fireEvent.change(screen.getByLabelText('Aktör kimliği'),{target:{value:'0'}});
 expect(await screen.findByText(/1 veya daha büyük bir tam sayı/)).toBeTruthy();
 await waitFor(()=>expect(cagrilar('/platform/audit').length).toBe(once));
 // Tablo eldeki veriyle ayakta kalır; hata paneline dönmez.
 expect(screen.getByTestId('denetim-501')).toBeTruthy();
});

it('aralık dışı durum kodu: alan hatası; düzeltilince istek gider',async()=>{
 render(<PlatformSecurity/>);
 await screen.findByTestId('denetim-501');
 const once=cagrilar('/platform/audit').length;
 fireEvent.change(screen.getByLabelText('Durum kodu'),{target:{value:'4'}});
 expect(await screen.findByText(/100–599 arası bir tam sayı/)).toBeTruthy();
 await waitFor(()=>expect(cagrilar('/platform/audit').length).toBe(once));
 fireEvent.change(screen.getByLabelText('Durum kodu'),{target:{value:'404'}});
 await waitFor(()=>expect(cagrilar('/platform/audit').at(-1)![1]).toEqual({params:{limit:250,status_code:404}}));
 expect(screen.queryByText(/100–599 arası bir tam sayı/)).toBeNull();
});
