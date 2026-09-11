import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import type {components} from '../../api/types.gen';

const get=vi.fn();
vi.mock('../../api',()=>({
 api:{get:(...args:unknown[])=>get(...args)},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
}));

import PlatformUsers from './PlatformUsers';

const KULLANICILAR=(offset:number):components['schemas']['PlatformKullaniciListesi']=>({
 total:75,limit:50,offset,
 items:[{
  id:offset+1,username:`kullanici${offset+1}`,display_name:'Ayşe Yılmaz',role:'yonetici',is_active:true,
  email_verified:false,must_change_password:true,created_at:null,last_login_at:null,
  memberships:[
   {company_id:3,company_name:'Merkez Tarım',company_is_active:true,is_default:true},
   {company_id:4,company_name:'Eski Şube',company_is_active:false,is_default:false},
  ],
 }],
});
const DOGRULAMALAR:components['schemas']['BekleyenDogrulamaListesi']={
 total:2,limit:20,offset:0,
 items:[{user_id:1,username:'kullanici1',created_at:'2026-09-10T08:00:00Z',expires_at:'2026-09-11T08:00:00Z'}],
};

const yanit=(yol:string,params:{offset:number})=>
 Promise.resolve({data:yol==='/platform/verifications'?DOGRULAMALAR:KULLANICILAR(params.offset)});

beforeEach(()=>{get.mockReset();get.mockImplementation((yol:string,{params}:{params:{offset:number}})=>yanit(yol,params))});
afterEach(cleanup);

const kullaniciCagrilari=()=>get.mock.calls.filter(cagri=>cagri[0]==='/platform/users');

it('üyelikleri çip olarak, doğrulama durumunu ve bekleyen doğrulamaları çizer',async()=>{
 render(<PlatformUsers/>);
 const satir=await screen.findByTestId('kullanici-1');
 expect(within(satir).getByText('Merkez Tarım ★')).toBeTruthy();
 expect(within(satir).getByText('Eski Şube (pasif)')).toBeTruthy();
 expect(within(satir).getByText('Doğrulanmamış')).toBeTruthy();
 expect(within(satir).getByText('Şifre değişimi bekliyor')).toBeTruthy();
 expect(await screen.findByText('Bekleyen e-posta doğrulamaları (2)')).toBeTruthy();
 expect(screen.getByText('1–50 / 75')).toBeTruthy();
 expect(get).toHaveBeenCalledWith('/platform/verifications',{params:{limit:20,offset:0}});
});

it('sonraki sayfa offset=50, doğrulama süzgeci verified=true gönderir',async()=>{
 render(<PlatformUsers/>);
 await screen.findByTestId('kullanici-1');
 fireEvent.click(screen.getByRole('button',{name:/next page/i}));
 await screen.findByTestId('kullanici-51');
 expect(kullaniciCagrilari().at(-1)![1].params).toEqual({limit:50,offset:50});
 fireEvent.mouseDown(screen.getByRole('combobox',{name:/E-posta doğrulaması/}));
 fireEvent.click(await screen.findByRole('option',{name:'Doğrulanmış'}));
 await waitFor(()=>expect(kullaniciCagrilari().at(-1)![1].params).toEqual({verified:true,limit:50,offset:0}));
});

it('PP2 eylemleri devre dışı',async()=>{
 render(<PlatformUsers/>);
 const satir=await screen.findByTestId('kullanici-1');
 for(const ad of ['Şifre sıfırla','Doğrulama gönder']){
  expect((within(satir).getByRole('button',{name:ad}) as HTMLButtonElement).disabled).toBe(true);
 }
});

it('403 → yetki yok paneli',async()=>{
 get.mockReset();get.mockRejectedValue({response:{status:403}});
 render(<PlatformUsers/>);
 expect(await screen.findByTestId('platform-yetki-yok')).toBeTruthy();
});

it('ağ hatası mesaj gösterir',async()=>{
 get.mockReset();get.mockRejectedValue(new Error('Network Error'));
 render(<PlatformUsers/>);
 expect((await screen.findAllByRole('button',{name:'Yeniden dene'})).length).toBeGreaterThan(0);
});
