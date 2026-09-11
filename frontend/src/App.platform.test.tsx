/**
 * PP3: `/yedekler` artık `/platform/yedekler`e YÖNLENDİRİR. Eski yer imi
 * operatörü doğru ekrana taşımalı; operatör olmayana ise ne ekranı ne de
 * yönlendirmeyi göstermeli (Protected `/`ye düşürür).
 *
 * Gerçek `App` ağacı koşar (Protected + permissionForPath + Navigate); yalnız
 * kabuk ve sayfa bileşenleri hafif vekillerle değiştirilir.
 */
import React from 'react';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';

let operator=false;
vi.mock('./AuthContext',()=>({
 useAuth:()=>({
  user:{id:1,username:'op',display_name:'Operatör',role:'admin',must_change_password:false},
  loading:false,
  can:(permission:string)=>permission==='platform'?operator:true,
 }),
}));
vi.mock('./components/AppShell',async()=>{
 const {Outlet}=await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
 return {default:()=><div data-testid="kabuk"><Outlet/></div>};
});
vi.mock('./pages/Backups',()=>({default:()=><div>YEDEK-EKRANI</div>}));
vi.mock('./pages/Dashboard',()=>({default:()=><div>PANO-EKRANI</div>}));
vi.mock('./pages/platform/PlatformDashboard',()=>({default:()=><div>PLATFORM-OZETI</div>}));

import App from './App';

afterEach(()=>{cleanup();operator=false;window.history.pushState({},'','/')});

it('/yedekler operatörü /platform/yedekler ekranına yönlendirir',async()=>{
 operator=true;
 window.history.pushState({},'','/yedekler');
 render(<App/>);
 expect(await screen.findByText('YEDEK-EKRANI')).toBeTruthy();
 expect(window.location.pathname).toBe('/platform/yedekler');
});

it('operatör olmayan /yedekler ile de /platform ile de panoya düşer',async()=>{
 window.history.pushState({},'','/yedekler');
 render(<App/>);
 expect(await screen.findByText('PANO-EKRANI')).toBeTruthy();
 expect(window.location.pathname).toBe('/');
 cleanup();
 window.history.pushState({},'','/platform');
 render(<App/>);
 await waitFor(()=>expect(window.location.pathname).toBe('/'));
 expect(screen.queryByText('PLATFORM-OZETI')).toBeNull();
});

it('operatör /platform panosunu açar',async()=>{
 operator=true;
 window.history.pushState({},'','/platform');
 render(<App/>);
 expect(await screen.findByText('PLATFORM-OZETI')).toBeTruthy();
});
