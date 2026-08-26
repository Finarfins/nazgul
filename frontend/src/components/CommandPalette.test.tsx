import React from 'react';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,describe,expect,it,vi} from 'vitest';
import CommandPalette from './CommandPalette';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
vi.mock('../api',()=>({api:{get:vi.fn()}}));

// Rol izinleri backend/app/auth.py ROLE_PERMISSIONS ile birebir aynıdır.
const ROLE_PERMISSIONS:Record<string,string[]>={
 admin:['*'],
 yonetici:['read','sales','purchases','payments','finance','stock','reports','users','machines'],
 muhasebe:['read','sales','purchases','payments','finance','reports'],
 satis:['read','sales','payments'],
 depo:['read','stock','purchases'],
 rapor:['read','reports'],
};
let permissions:string[]=['*'];
vi.mock('../AuthContext',()=>({
 useAuth:()=>({can:(permission:string)=>permissions.includes('*')||permissions.includes(permission)}),
}));

afterEach(()=>{cleanup();navigate.mockReset();permissions=['*'];});

function mount(role='admin',onClose=vi.fn()){
 permissions=ROLE_PERMISSIONS[role];
 render(<ThemeProvider theme={createTheme()}><MemoryRouter><CommandPalette open onClose={onClose}/></MemoryRouter></ThemeProvider>);
 return onClose;
}

it('stok sayımları çalışma alanını açar',()=>{
 const close=mount();
 fireEvent.click(screen.getByText('Stok sayımlarını aç'));
 expect(close).toHaveBeenCalled();
 expect(navigate).toHaveBeenCalledWith('/stok-sayimlari');
});

it('yeni fiziksel sayım komutunu hızlı başlangıç parametresiyle açar',()=>{
 const close=mount();
 fireEvent.click(screen.getByText('Yeni fiziksel sayım'));
 expect(close).toHaveBeenCalled();
 expect(navigate).toHaveBeenCalledWith('/stok-sayimlari?new=1');
});

// Palet, menüyle aynı tek-kaynak izin haritasını kullanmalıdır: menüde
// gizlenen bir sayfa Ctrl+K ile de açılamamalı.
describe('izin filtresi',()=>{
 it('depo rolü satış eylemlerini görmez, stok eylemlerini görür',()=>{
  mount('depo');
  expect(screen.queryByText('Yeni satış oluştur')).toBeNull();
  expect(screen.queryByText('Raporları aç')).toBeNull();
  expect(screen.queryByText('Akıllı analizleri aç')).toBeNull();
  expect(screen.getByText('Yeni fiziksel sayım')).toBeTruthy();
  expect(screen.getByText('Müşterileri aç')).toBeTruthy();
 });

 it('satis rolü stok sayımı ve rapor eylemlerini görmez',()=>{
  mount('satis');
  expect(screen.queryByText('Yeni fiziksel sayım')).toBeNull();
  expect(screen.queryByText('Stok sayımlarını aç')).toBeNull();
  expect(screen.queryByText('Raporları aç')).toBeNull();
  expect(screen.getByText('Yeni satış oluştur')).toBeTruthy();
 });

 it('rapor rolü raporları görür, satış ve sayım eylemlerini görmez',()=>{
  mount('rapor');
  expect(screen.getByText('Raporları aç')).toBeTruthy();
  expect(screen.getByText('Akıllı analizleri aç')).toBeTruthy();
  expect(screen.queryByText('Yeni satış oluştur')).toBeNull();
  expect(screen.queryByText('Yeni fiziksel sayım')).toBeNull();
 });

 it('admin tüm hızlı işlemleri görür',()=>{
  mount('admin');
  for(const title of ['Yeni satış oluştur','Yeni alış oluştur','Yeni fiziksel sayım','Stok sayımlarını aç','Müşterileri aç','Ürünleri aç','Raporları aç','Akıllı analizleri aç']){
   expect(screen.getByText(title)).toBeTruthy();
  }
 });
});
