import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import type {components} from '../../api/types.gen';

const get=vi.fn();
vi.mock('../../api',()=>({
 api:{get:(...args:unknown[])=>get(...args)},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
}));

import PlatformSecurity,{PENCERELER} from './PlatformSecurity';

const HIZ=(window_hours:number):components['schemas']['HizSiniriOzeti']=>({
 window_hours,retention_hours:48,
 items:[{action:'login',ip_address:'10.0.0.7',attempts:31,last_at:'2026-09-10T08:00:00Z'}],
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
afterEach(cleanup);

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

it('denetim süzgeci ucun desteklediği tek parametre olan limit ile gider',async()=>{
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
