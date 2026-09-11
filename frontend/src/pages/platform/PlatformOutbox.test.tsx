import React from 'react';
import {cleanup,render,screen,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import type {components} from '../../api/types.gen';

const get=vi.fn();
vi.mock('../../api',()=>({
 api:{get:(...args:unknown[])=>get(...args)},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
}));

import PlatformOutbox from './PlatformOutbox';

const SAGLIK:components['schemas']['KuyrukSagligi']={
 channels:[
  {channel:'whatsapp',pending:6,failed:2,sent_last_24h:140,oldest_pending_age_seconds:900},
  {channel:'email',pending:0,failed:0,sent_last_24h:12,oldest_pending_age_seconds:null},
 ],
 field_stock_scheduler:{enabled:true,alive:false,interval_seconds:30},
};

beforeEach(()=>{get.mockReset()});
afterEach(cleanup);

it('kanal başına durum sayılarını ve en eski bekleyen yaşını çizer',async()=>{
 get.mockResolvedValue({data:SAGLIK});
 render(<PlatformOutbox/>);
 const whatsapp=await screen.findByTestId('kanal-whatsapp');
 expect(within(whatsapp).getByText('6')).toBeTruthy();
 expect(within(whatsapp).getByText('2')).toBeTruthy();
 expect(within(whatsapp).getByText('140')).toBeTruthy();
 expect(within(whatsapp).getByText('15 dk')).toBeTruthy();
 expect(within(screen.getByTestId('kanal-email')).getByText('—')).toBeTruthy();
 expect(get).toHaveBeenCalledWith('/platform/outbox/health',{params:{}});
 // Zamanlayıcı canlılığı düz metin: alive=false görünür.
 expect(screen.getByText('alive:')).toBeTruthy();
 expect(screen.getByText('hayır')).toBeTruthy();
});

it('yeniden dene düğmesi PP2 yer tutucusu ve devre dışı',async()=>{
 get.mockResolvedValue({data:SAGLIK});
 render(<PlatformOutbox/>);
 const satir=await screen.findByTestId('kanal-whatsapp');
 expect((within(satir).getByRole('button',{name:'Yeniden dene'}) as HTMLButtonElement).disabled).toBe(true);
});

it('403 → yetki yok paneli',async()=>{
 get.mockRejectedValue({response:{status:403}});
 render(<PlatformOutbox/>);
 expect(await screen.findByTestId('platform-yetki-yok')).toBeTruthy();
});

it('ağ hatası mesaj gösterir',async()=>{
 get.mockRejectedValue(new Error('Network Error'));
 render(<PlatformOutbox/>);
 expect(await screen.findByText(/Veri yüklenemedi/)).toBeTruthy();
});
