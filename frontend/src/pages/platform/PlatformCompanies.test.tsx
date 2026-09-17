import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

import PlatformCompanies from './PlatformCompanies';

const sayfa=(offset:number,total=120):components['schemas']['PlatformSirketListesi']=>({
 total,limit:50,offset,
 items:[
  {id:offset+1,name:`Firma ${offset+1}`,is_active:true,member_count:4,last_activity_at:'2026-09-10T08:00:00Z',created_at:'2026-01-01T00:00:00Z'},
  {id:offset+2,name:`Firma ${offset+2}`,is_active:false,member_count:0,last_activity_at:null,created_at:null},
 ],
});

beforeEach(()=>{get.mockReset();get.mockImplementation((_yol:string,{params}:{params:{offset:number}})=>Promise.resolve({data:sayfa(params.offset)}))});
afterEach(()=>{cleanup();operator=true;post.mockReset()});

const sonParametreler=()=>get.mock.calls[get.mock.calls.length-1][1].params;

it('firmaları üye sayısı ve durumla çizer, toplamı sayfalamada gösterir',async()=>{
 render(<PlatformCompanies/>);
 const satir=await screen.findByTestId('sirket-1');
 expect(within(satir).getByText('Firma 1')).toBeTruthy();
 expect(within(satir).getByText('4')).toBeTruthy();
 expect(within(satir).getByText('Aktif')).toBeTruthy();
 expect(within(screen.getByTestId('sirket-2')).getByText('Pasif')).toBeTruthy();
 expect(screen.getByText('1–50 / 120')).toBeTruthy();
 expect(get).toHaveBeenCalledWith('/platform/companies',{params:{limit:50,offset:0}});
});

it('sonraki sayfa offset=50 ile çağırır',async()=>{
 render(<PlatformCompanies/>);
 await screen.findByTestId('sirket-1');
 fireEvent.click(screen.getByRole('button',{name:/next page/i}));
 await screen.findByTestId('sirket-51');
 expect(sonParametreler()).toEqual({limit:50,offset:50});
});

it('arama ve durum süzgeci sunucuya q/active olarak gider, sayfa başa döner',async()=>{
 const user=userEvent.setup();
 render(<PlatformCompanies/>);
 await screen.findByTestId('sirket-1');
 fireEvent.click(screen.getByRole('button',{name:/next page/i}));
 await screen.findByTestId('sirket-51');
 await user.type(screen.getByLabelText('Ara (firma adı)'),'tarım');
 await waitFor(()=>expect(sonParametreler()).toEqual({q:'tarım',limit:50,offset:0}));
 fireEvent.mouseDown(screen.getByRole('combobox',{name:/Durum/}));
 fireEvent.click(await screen.findByRole('option',{name:'Pasif'}));
 await waitFor(()=>expect(sonParametreler()).toEqual({q:'tarım',active:false,limit:50,offset:0}));
});

it('operatör değilse eylem düğmesi hiç çizilmez',async()=>{
 operator=false;
 render(<PlatformCompanies/>);
 const satir=await screen.findByTestId('sirket-1');
 expect(within(satir).queryByRole('button')).toBeNull();
 expect(within(screen.getByTestId('sirket-2')).queryByRole('button')).toBeNull();
 expect(post).not.toHaveBeenCalled();
});

it('403 → yetki yok paneli, tablo çizilmez',async()=>{
 get.mockReset();get.mockRejectedValue({response:{status:403}});
 render(<PlatformCompanies/>);
 expect(await screen.findByTestId('platform-yetki-yok')).toBeTruthy();
 expect(screen.queryByRole('table')).toBeNull();
});

it('ağ hatası mesaj ve yeniden dene gösterir',async()=>{
 get.mockReset();get.mockRejectedValue(new Error('Network Error'));
 render(<PlatformCompanies/>);
 expect(await screen.findByRole('button',{name:'Yeniden dene'})).toBeTruthy();
 expect(screen.getByText(/Veri yüklenemedi/)).toBeTruthy();
});
