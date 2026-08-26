import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Products from './Products';

const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:vi.fn()},
 money:(value:any)=>String(value),
 openAuthenticated:vi.fn(),
 policyOverrideHeaders:vi.fn(),
 policyOverrideRequired:()=>false,
}));
vi.mock('../AuthContext',()=>({useAuth:()=>({user:{role:'admin'}})}));
vi.mock('../components/ExcelImportDialog',()=>({default:()=>null}));
vi.mock('../components/ProductDialog',()=>({default:()=>null}));
vi.mock('../components/ResponsiveTable',()=>({
 default:({rows,columns}:any)=><div>{rows.map((row:any)=>{
  const nameColumn=columns.find((column:any)=>column.field==='name');
  return <span key={row.id}>{nameColumn.renderCell({value:row.name,row})}</span>;
 })}</div>,
}));

beforeEach(()=>{
 get.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/warehouses')return Promise.resolve({data:[{id:1,name:'Merkez'}]});
  if(url==='/search/parts')return Promise.resolve({data:{items:[{id:7,name:'OCR Bulunan Parça',match_type:'oem_ocr'}]}});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

it('ürün sorgusunu akıllı parça arama ucuna gönderir',async()=>{
 render(<ThemeProvider theme={createTheme()}><MemoryRouter><Products/></MemoryRouter></ThemeProvider>);
 expect(screen.getByRole('heading',{name:'Ürünler / Stok'})).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Yeni Ürün'})).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('Ürün, kod veya barkod ara'),{target:{value:'845I-2763'}});
 expect(await screen.findByText('OCR Bulunan Parça')).toBeInTheDocument();
 expect(screen.getByText('Benzer / OCR eşleşmesi')).toBeInTheDocument();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/search/parts',{params:{q:'845I-2763',sort:'name_asc',limit:300}}));
});
