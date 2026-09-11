import React from 'react';
import {act,cleanup,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import type {components} from '../../api/types.gen';

const get=vi.fn();
vi.mock('../../api',()=>({
 api:{get:(...args:unknown[])=>get(...args)},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
}));

import PlatformDashboard,{PANO_YENILEME_MS} from './PlatformDashboard';

const OZET:components['schemas']['PlatformOzeti']={
 companies:{total:12,active:10,inactive:2},
 users:{total:37,verified:30,unverified:7},
 pending_verifications:5,
 rate_limit_blocks_last_24h:9,
 outbox:{pending:4,failed:1,oldest_pending_age_seconds:7260},
 einvoice_status_histogram:{SENT:20,ERROR:3},
 field_stock_scheduler:{enabled:true,alive:true,stale:false},
};

const mount=()=>render(<MemoryRouter><PlatformDashboard/></MemoryRouter>);

beforeEach(()=>{get.mockReset()});
afterEach(()=>{cleanup();vi.useRealTimers()});

it('özet sayaçlarını ve sağlık kartlarını sunucunun sayılarıyla çizer',async()=>{
 get.mockResolvedValue({data:OZET});
 mount();
 expect(await screen.findByText('12')).toBeTruthy();
 expect(get).toHaveBeenCalledWith('/platform/overview',{params:{}});
 expect(screen.getByText('37')).toBeTruthy();
 expect(screen.getByText('10 aktif · 2 pasif')).toBeTruthy();
 expect(screen.getByText('5')).toBeTruthy();
 expect(screen.getByText('9')).toBeTruthy();
 // Kuyruk kartı: başarısız var → "Sorun"; en eski bekleyen 2 sa 1 dk.
 expect(screen.getByText('2 sa 1 dk')).toBeTruthy();
 expect(screen.getByText('Sorun')).toBeTruthy();
 expect(screen.getByText('ERROR: 3')).toBeTruthy();
 expect(screen.getByText('SENT: 20')).toBeTruthy();
});

it('60 sn sonra kendini tazeler',async()=>{
 vi.useFakeTimers({shouldAdvanceTime:true});
 get.mockResolvedValue({data:OZET});
 mount();
 await screen.findByText('12');
 expect(get).toHaveBeenCalledTimes(1);
 await act(async()=>{vi.advanceTimersByTime(PANO_YENILEME_MS)});
 await waitFor(()=>expect(get).toHaveBeenCalledTimes(2));
});

it('403 → boş sayfa değil, "yetki yok" paneli',async()=>{
 get.mockRejectedValue({response:{status:403,data:{detail:'Platform operatörü değil'}}});
 mount();
 expect(await screen.findByTestId('platform-yetki-yok')).toBeTruthy();
 expect(screen.getByText('Bu ekrana yetkiniz yok')).toBeTruthy();
});

it('ağ hatasında yeniden dene düğmesiyle hata gösterir ve yeniden çağırır',async()=>{
 get.mockRejectedValueOnce(new Error('Network Error')).mockResolvedValueOnce({data:OZET});
 mount();
 const dugme=await screen.findByRole('button',{name:'Yeniden dene'});
 expect(screen.getByText(/Veri yüklenemedi/)).toBeTruthy();
 act(()=>dugme.click());
 expect(await screen.findByText('12')).toBeTruthy();
});
