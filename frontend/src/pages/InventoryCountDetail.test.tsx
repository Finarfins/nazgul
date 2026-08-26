import React from 'react';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import InventoryCountDetail,{inventoryCountDetailCsv} from './InventoryCountDetail';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate,useParams:()=>({id:'41'})};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 errorDetail:(_error:any,fallback:string)=>fallback,
}));

const payload={
 count:{id:41,count_date:'2026-07-20',warehouse_id:1,warehouse_name:'Merkez Depo',note:'Raf; "A" sayımı',item_count:2,changed_count:2,total_absolute_variance:4},
 items:[
  {id:41,product_id:10,product_name:'Hidrolik Pompa',product_code:'HP-10',unit:'Adet',system_quantity:10,counted_quantity:8,variance:-2,current_quantity:8,critical_stock:2},
  {id:42,product_id:11,product_name:'Mazot Filtresi',product_code:'MF-11',unit:'Adet',system_quantity:5,counted_quantity:7,variance:2,current_quantity:7,critical_stock:1},
 ],
};

function forceCards(){
 window.matchMedia=((query:string)=>({matches:true,media:query,onchange:null,
  addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn()})) as any;
}

beforeEach(()=>{navigate.mockReset();get.mockReset();get.mockResolvedValue({data:payload});});
afterEach(()=>cleanup());

function mount(){return render(<ThemeProvider theme={createTheme()}><MemoryRouter><InventoryCountDetail/></MemoryRouter></ThemeProvider>)}

it('sayım özeti ile sistem-sayılan-fark satırlarını gösterir',async()=>{
 forceCards();
 mount();
 expect(await screen.findByText('Fiziksel Stok Sayımı #41')).toBeInTheDocument();
 expect(get).toHaveBeenCalledWith('/warehouses/counts/41');
 expect(screen.getByText('Merkez Depo')).toBeInTheDocument();
 expect(screen.getByText('Raf; "A" sayımı')).toBeInTheDocument();
 expect(screen.getAllByText('Hidrolik Pompa').length).toBeGreaterThan(0);
 expect(screen.getAllByText('-2').length).toBeGreaterThan(0);
 expect(screen.getAllByText('+2').length).toBeGreaterThan(0);
});

it('ürün, depo, hareket ve sayım listesi bağlamlarına gider',async()=>{
 forceCards();
 mount();
 await screen.findByText('Fiziksel Stok Sayımı #41');
 fireEvent.click(screen.getAllByRole('button',{name:/Hidrolik Pompa — Ürün Detayı/})[0]);
 expect(navigate).toHaveBeenCalledWith('/urunler/10');
 fireEvent.click(screen.getByRole('button',{name:'Depoya Git'}));
 expect(navigate).toHaveBeenCalledWith('/depolar/1');
 fireEvent.click(screen.getByRole('button',{name:'Stok Hareketleri'}));
 expect(navigate).toHaveBeenCalledWith('/stok-hareketleri',{state:{warehouseId:1}});
 fireEvent.click(screen.getByRole('button',{name:'Tüm Sayımlara Dön'}));
 expect(navigate).toHaveBeenCalledWith('/stok-sayimlari',{state:{warehouseId:1}});
});

it('sayım belgesi CSV raporunu metadata ve satırlarla üretir',()=>{
 const csv=inventoryCountDetailCsv(payload.count,payload.items);
 expect(csv.startsWith('\uFEFF')).toBe(true);
 expect(csv).toContain('"Fiziksel Stok Sayımı";"#41"');
 expect(csv).toContain('"Açıklama";"Raf; ""A"" sayımı"');
 expect(csv).toContain('"Ürün ID";"Ürün";"Kod"');
 expect(csv).toContain('"10";"Hidrolik Pompa";"HP-10"');
});

it('CSV metinlerinde formül enjeksiyonunu engeller ve sayıları korur',()=>{
 const csv=inventoryCountDetailCsv(
  {id:9,warehouse_name:'=Merkez',count_date:'2026-07-20',note:'  @Tehlikeli',item_count:1,changed_count:1,total_absolute_variance:2},
  [{product_id:5,product_name:'+Pompa',product_code:'-HP',unit:'@Adet',system_quantity:4,counted_quantity:2,variance:-2,current_quantity:2,critical_stock:1}],
 );
 expect(csv).toContain('"Depo";"\'=Merkez"');
 expect(csv).toContain('"Açıklama";"\'  @Tehlikeli"');
 expect(csv).toContain('"5";"\'+Pompa";"\'-HP";"\'@Adet"');
 expect(csv).toContain('"4";"2";"-2";"2";"1"');
});

it('sayım belgesini tarayıcı yazdırmasına gönderir',async()=>{
 const print=vi.spyOn(window,'print').mockImplementation(()=>{});
 mount();
 await screen.findByText('Fiziksel Stok Sayımı #41');
 fireEvent.click(screen.getByRole('button',{name:'Yazdır'}));
 expect(print).toHaveBeenCalledTimes(1);
 print.mockRestore();
});

it('hata nesnesini güvenli düz metne dönüştürür',async()=>{
 get.mockRejectedValue({response:{status:500,data:{detail:[{msg:'x'}]}}});
 mount();
 expect(await screen.findByText('Stok sayımı yüklenemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'Tüm Sayımlara Dön'}));
 expect(navigate).toHaveBeenCalledWith('/stok-sayimlari');
});
