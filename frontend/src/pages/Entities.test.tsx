import React from 'react';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import Entities from './Entities';

const navigate=vi.fn();
const get=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
vi.mock('../api',()=>({api:{get:(...args:any[])=>get(...args),patch:vi.fn(),delete:vi.fn()},money:(value:any)=>String(value)}));
vi.mock('../components/ResponsiveTable',()=>({
 default:({rows,onRowClick}:any)=><button onClick={()=>onRowClick(rows[0])}>Test müşterisini aç</button>,
}));
vi.mock('../components/EntityQuickActions',()=>({default:()=>null}));
vi.mock('../components/ExcelImportDialog',()=>({default:()=>null}));
vi.mock('../components/EntityDialog',()=>({default:()=>null}));

describe('Entities customer navigation',()=>{
 beforeEach(()=>{
  navigate.mockReset();get.mockReset();
  get.mockResolvedValue({data:[{id:7,name:'Test Müşteri'}]});
 });

 it('satır tıklamasında hızlı işlem popupı yerine doğrudan cari karta gider',async()=>{
  render(<ThemeProvider theme={createTheme()}><MemoryRouter><Entities type="customer"/></MemoryRouter></ThemeProvider>);
  await waitFor(()=>expect(get).toHaveBeenCalled());
  expect(screen.getByRole('heading',{name:'Müşteriler'})).toBeTruthy();
  expect(screen.getByRole('button',{name:'Yeni Müşteri'})).toBeTruthy();
  expect(screen.getByLabelText('Müşteri ara')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Test müşterisini aç'}));
  expect(navigate).toHaveBeenCalledWith('/musteriler/7');
 });
});
