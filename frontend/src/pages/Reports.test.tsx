import React from 'react';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Reports from './Reports';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 money:(v:any)=>`${Number(v).toFixed(2)} ₺`,
 errorDetail:(_e:any,fallback:string)=>fallback,
}));
vi.mock('recharts',()=>({
 ResponsiveContainer:({children}:any)=><div>{children}</div>,
 BarChart:({children}:any)=><div>{children}</div>,
 Bar:()=>null,CartesianGrid:()=>null,Tooltip:()=>null,XAxis:()=>null,YAxis:()=>null,
}));

const summary={
 sales_total:1000,purchases_total:400,collections:600,payments:200,gross_difference:600,
 sales_count:3,purchases_count:2,
 top_customers:[
  {customer_id:41,name:'Ciro Müşterisi',total:900,count:3},
  {customer_id:42,name:'İkinci Müşteri',total:100,count:1},
 ],
 top_products:[
  {product_name:'Bagli Urun',product_id:121,quantity:9,total:900},
  {product_name:'Belirsiz Urun',product_id:null,quantity:4,total:400},
 ],
 monthly_sales:[{month:'2026-05',total:1000}],
};

beforeEach(()=>{navigate.mockReset();get.mockReset();get.mockResolvedValue({data:summary})});
afterEach(()=>cleanup());

function mount(){return render(<ThemeProvider theme={createTheme()}><MemoryRouter><Reports/></MemoryRouter></ThemeProvider>)}

it('en yüksek ciro müşterisini customer_id ile cari detayına yönlendirir',async()=>{
 mount();
 fireEvent.click(await screen.findByText('Ciro Müşterisi'));
 expect(navigate).toHaveBeenCalledWith('/musteriler/41');
});

it('en çok satan ürünü product_id ile ürün kartına yönlendirir',async()=>{
 mount();
 fireEvent.click(await screen.findByText('Bagli Urun'));
 expect(navigate).toHaveBeenCalledWith('/urunler/121');
});

it('product_id olmayan ürün satırı gezinme tetiklemez',async()=>{
 mount();
 fireEvent.click(await screen.findByText('Belirsiz Urun'));
 expect(navigate).not.toHaveBeenCalled();
});

it('klavye ile ürün satırından ürün kartına gidilebilir',async()=>{
 mount();
 fireEvent.keyDown(await screen.findByText('Bagli Urun'),{key:'Enter'});
 expect(navigate).toHaveBeenCalledWith('/urunler/121');
});
