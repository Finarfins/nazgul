import React from 'react';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import StockMovements from './StockMovements';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...a:any[])=>get(...a)},
 errorDetail:(_e:any,fallback:string)=>fallback,
}));

const movements=[
 {id:1,product_id:10,product_name:'Satılan Ürün',warehouse_name:'Merkez',movement_date:'2026-07-10',movement_type:'sale',quantity:-2,reference_type:'order',reference_id:55,note:'x'},
 {id:2,product_id:11,product_name:'Alınan Ürün',warehouse_name:'Merkez',movement_date:'2026-07-09',movement_type:'purchase',quantity:5,reference_type:'purchases',reference_id:77,note:null},
 {id:3,product_id:12,product_name:'Transfer Ürün',warehouse_name:'Şube',movement_date:'2026-07-08',movement_type:'transfer_out',quantity:-1,reference_type:'transfer',reference_id:9,note:null},
];

function forceCards(){
 window.matchMedia=((query:string)=>({matches:true,media:query,onchange:null,
  addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn()})) as any;
}

beforeEach(()=>{
 navigate.mockReset();get.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/warehouses')return Promise.resolve({data:[{id:1,name:'Merkez'}]});
  if(url==='/products/stock/movements/all')return Promise.resolve({data:movements});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

function mount(){return render(<ThemeProvider theme={createTheme()}><MemoryRouter><StockMovements/></MemoryRouter></ThemeProvider>)}

it('hareket türlerini iş diline çevirir',async()=>{
 forceCards();
 mount();
 expect(await screen.findByText('Transfer Ürün')).toBeInTheDocument();
 expect(screen.getAllByText('Satış').length).toBeGreaterThan(0);
 expect(screen.getAllByText('Transfer Çıkışı').length).toBeGreaterThan(0);
});

it('satış hareketinden satış belgesine gider',async()=>{
 forceCards();
 mount();
 await screen.findByText('Satılan Ürün');
 fireEvent.click(screen.getByRole('button',{name:'Satılan Ürün — Belgeyi Aç'}));
 expect(navigate).toHaveBeenCalledWith('/satislar',{state:{openId:55}});
});

it('alış hareketinden alış belgesine gider',async()=>{
 forceCards();
 mount();
 await screen.findByText('Alınan Ürün');
 fireEvent.click(screen.getByRole('button',{name:'Alınan Ürün — Belgeyi Aç'}));
 expect(navigate).toHaveBeenCalledWith('/alislar',{state:{openId:77}});
});

it('transfer hareketinden transfer detayına gider',async()=>{
 forceCards();
 mount();
 await screen.findByText('Transfer Ürün');
 fireEvent.click(screen.getByRole('button',{name:'Transfer Ürün — Belgeyi Aç'}));
 expect(navigate).toHaveBeenCalledWith('/depo-transferleri/9',{state:{openId:9}});
});

it('boş sonuçta bilgi mesajı gösterir',async()=>{
 get.mockImplementation((url:string)=>url==='/products/stock/movements/all'
  ?Promise.resolve({data:[]}):Promise.resolve({data:[]}));
 mount();
 expect(await screen.findByText('Bu filtreye uygun stok hareketi bulunmuyor.')).toBeInTheDocument();
});

it('hata durumunu düz metin olarak gösterir',async()=>{
 get.mockImplementation((url:string)=>url==='/products/stock/movements/all'
  ?Promise.reject({response:{status:500,data:{detail:[{msg:'x'}]}}}):Promise.resolve({data:[]}));
 mount();
 expect(await screen.findByText('Stok hareketleri yüklenemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});
