import React from 'react';
import {cleanup,render,screen,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import type {components} from '../../api/types.gen';

const get=vi.fn();
vi.mock('../../api',()=>({
 api:{get:(...args:unknown[])=>get(...args)},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
}));

import PlatformEDocuments from './PlatformEDocuments';

const SAGLIK:components['schemas']['EBelgeSagligi']={
 izibiz_env:'test',
 companies:[
  {company_id:1,company_name:'Merkez Tarım',total:25,by_status:{SENT:22,ERROR:3}},
  {company_id:2,company_name:'Ova Bayi',total:4,by_status:{DRAFT:4}},
 ],
};

beforeEach(()=>{get.mockReset()});
afterEach(cleanup);

it('firma başına durum dağılımını, durumların birleşimini sütun yaparak çizer',async()=>{
 get.mockResolvedValue({data:SAGLIK});
 render(<PlatformEDocuments/>);
 const merkez=await screen.findByTestId('ebelge-1');
 expect(screen.getByText('Entegratör ortamı: test')).toBeTruthy();
 for(const durum of ['DRAFT','ERROR','SENT'])expect(screen.getByRole('columnheader',{name:durum})).toBeTruthy();
 // Sütun sırası: DRAFT, ERROR, SENT — firmada olmayan durum 0.
 expect(within(merkez).getAllByRole('cell').map(hucre=>hucre.textContent)).toEqual(['Merkez Tarım','0','3','22','25']);
 expect(within(screen.getByTestId('ebelge-2')).getAllByRole('cell').map(hucre=>hucre.textContent)).toEqual(['Ova Bayi','4','0','0','4']);
});

it('403 → yetki yok paneli',async()=>{
 get.mockRejectedValue({response:{status:403}});
 render(<PlatformEDocuments/>);
 expect(await screen.findByTestId('platform-yetki-yok')).toBeTruthy();
});

it('ağ hatası mesaj gösterir',async()=>{
 get.mockRejectedValue(new Error('Network Error'));
 render(<PlatformEDocuments/>);
 expect(await screen.findByText(/Veri yüklenemedi/)).toBeTruthy();
});
