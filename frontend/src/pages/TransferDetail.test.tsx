import React from 'react';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import TransferDetail from './TransferDetail';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate,useParams:()=>({id:'9'})};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...a:any[])=>get(...a)},
 errorDetail:(_e:any,fallback:string)=>fallback,
}));

const payload={
 transfer:{id:9,transfer_date:'2026-07-20',source_warehouse_id:1,source_name:'Merkez Depo',target_warehouse_id:2,target_name:'Şube Depo',status:'completed',note:'Acil sevkiyat',created_at:'2026-07-20T10:30:00Z',item_count:1,total_quantity:3},
 items:[{id:1,product_id:12,product_name:'Hidrolik Pompa',product_code:'HP-100',unit:'Adet',quantity:3}],
 movements:[
  {id:20,product_id:12,product_name:'Hidrolik Pompa',movement_type:'transfer_out',quantity:-3,movement_date:'2026-07-20',warehouse_id:1,warehouse_name:'Merkez Depo',note:'Acil sevkiyat'},
  {id:21,product_id:12,product_name:'Hidrolik Pompa',movement_type:'transfer_in',quantity:3,movement_date:'2026-07-20',warehouse_id:2,warehouse_name:'Şube Depo',note:'Acil sevkiyat'},
 ],
};

function forceCards(){
 window.matchMedia=((query:string)=>({matches:true,media:query,onchange:null,
  addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn()})) as any;
}

beforeEach(()=>{
 navigate.mockReset();get.mockReset();
 get.mockResolvedValue({data:payload});
});
afterEach(()=>cleanup());

function mount(){return render(<ThemeProvider theme={createTheme()}><MemoryRouter><TransferDetail/></MemoryRouter></ThemeProvider>)}

it('transfer üst bilgisini, ürünleri ve stok izlerini gösterir',async()=>{
 forceCards();
 mount();
 expect(await screen.findByText('Depo Transferi #9')).toBeInTheDocument();
 expect(get).toHaveBeenCalledWith('/warehouse-transfers/9');
 expect(screen.getByText('Merkez Depo → Şube Depo')).toBeInTheDocument();
 expect(screen.getAllByText('Hidrolik Pompa').length).toBeGreaterThan(0);
 expect(screen.getAllByText(/^Transfer Çıkışı ·/).length).toBeGreaterThan(0);
 expect(screen.getAllByText(/^Transfer Girişi ·/).length).toBeGreaterThan(0);
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});

it('ürün detayına satır bağlamıyla gider',async()=>{
 forceCards();
 mount();
 await screen.findByText('Depo Transferi #9');
 const buttons=screen.getAllByRole('button',{name:/Hidrolik Pompa — Ürün Detayı/});
 fireEvent.click(buttons[0]);
 expect(navigate).toHaveBeenCalledWith('/urunler/12');
});

it('stok hareketlerine geri döner',async()=>{
 mount();
 await screen.findByText('Depo Transferi #9');
 fireEvent.click(screen.getByRole('button',{name:'Stok Hareketlerine Dön'}));
 expect(navigate).toHaveBeenCalledWith('/stok-hareketleri');
});

it('hata yanıtını güvenli düz metin olarak gösterir',async()=>{
 get.mockRejectedValue({response:{status:500,data:{detail:[{msg:'x'}]}}});
 mount();
 expect(await screen.findByText('Depo transferi yüklenemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});
