import React from 'react';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Dashboard from './Dashboard';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
const get=vi.fn();
let financeAllowed=true;
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 money:(v:any)=>`${Number(v).toFixed(2)} ₺`,
 errorDetail:(_e:any,fallback:string)=>fallback,
}));
vi.mock('../AuthContext',()=>({useAuth:()=>({can:(permission:string)=>permission==='finance'&&financeAllowed})}));
vi.mock('recharts',()=>({
 ResponsiveContainer:({children}:any)=><div>{children}</div>,
 AreaChart:({children}:any)=><div>{children}</div>,
 Area:()=>null,CartesianGrid:()=>null,Tooltip:()=>null,XAxis:()=>null,YAxis:()=>null,
}));

const dashboard={
 as_of:'2026-07-12',today_sales:100,today_collections:50,month_sales:1000,month_collections:500,
 month_purchases:300,month_expenses:100,month_profit:600,stock_value:2000,cash_bank_total:1500,
 customer_receivables:700,supplier_payables:400,customer_count:1,product_count:1,critical_stock_count:1,
 overdue_count:1,overdue_total:250,
 recent_sales:[{id:11,order_date:'2026-07-12',customer_name:'Son Satış Müşterisi',document_no:'S-11',final_total:100}],
 critical_products:[{id:22,name:'Kritik Ürün',stock:-2,unit:'adet'}],
 overdue_receivables:[{id:33,customer_id:44,customer_name:'Geciken Müşteri',days_overdue:10,document_no:'S-33',remaining:250}],
 finance_accounts:[{id:55,name:'Merkez Kasa',account_type:'cash',currency:'TRY',balance:1500}],
 sales_trend:[],
 top_products:[
  {name:'Bagli Urun',product_id:121,quantity:9,total:900},
  {name:'Belirsiz Urun',product_id:null,quantity:4,total:400},
 ],
 recent_activity:[
  {type:'sale',id:66,title:'Son Hareket Satışı',date:'2026-07-12',amount:90,entity_id:44,entity_type:'customer'},
  {type:'collection',id:77,title:'Tahsilat Müşterisi',date:'2026-07-12',amount:40,entity_id:88,entity_type:'customer'},
  {type:'payment',id:78,title:'Ödeme Tedarikçisi',date:'2026-07-12',amount:30,entity_id:99,entity_type:'supplier'},
  {type:'finance',id:79,title:'Kasa Hareketi',date:'2026-07-12',amount:20,account_id:55},
 ],
};

beforeEach(()=>{navigate.mockReset();get.mockReset();financeAllowed=true;get.mockResolvedValue({data:dashboard})});
afterEach(()=>cleanup());

function mount(){return render(<ThemeProvider theme={createTheme()}><MemoryRouter><Dashboard/></MemoryRouter></ThemeProvider>)}

it('son satış satırını router state openId ile açar',async()=>{
 mount();
 fireEvent.click(await screen.findByText('Son Satış Müşterisi'));
 expect(navigate).toHaveBeenCalledWith('/satislar',{state:{openId:11}});
});

it('son hareketleri ilgili detay ekranlarına yönlendirir',async()=>{
 mount();
 await screen.findByText('Son Hareket Satışı');
 fireEvent.click(screen.getByText('Son Hareket Satışı'));
 expect(navigate).toHaveBeenCalledWith('/satislar',{state:{openId:66}});
 fireEvent.click(screen.getByText('Tahsilat Müşterisi'));
 expect(navigate).toHaveBeenCalledWith('/musteriler/88');
 fireEvent.click(screen.getByText('Ödeme Tedarikçisi'));
 expect(navigate).toHaveBeenCalledWith('/tedarikciler/99');
 fireEvent.click(screen.getByText('Kasa Hareketi'));
 expect(navigate).toHaveBeenCalledWith('/nakit-yonetimi',{state:{accountId:55,transactionId:79}});
});

it('en çok satan ürün satırını product_id ile ürün kartına yönlendirir',async()=>{
 mount();
 fireEvent.click(await screen.findByText('Bagli Urun'));
 expect(navigate).toHaveBeenCalledWith('/urunler/121');
});

it('product_id olmayan ürün satırı gezinme tetiklemez',async()=>{
 mount();
 fireEvent.click(await screen.findByText('Belirsiz Urun'));
 expect(navigate).not.toHaveBeenCalled();
});

it('dashboard hatasını düz metin olarak güvenle gösterir',async()=>{
 get.mockReset();
 get.mockRejectedValue({response:{status:500,data:{detail:{msg:'nesne'}}}});
 mount();
 expect(await screen.findByText('Dashboard verileri yüklenemedi.')).toBeTruthy();
});

it('kritik stok, geciken alacak ve finans hesabı kartları tıklanabilir',async()=>{
 mount();
 await screen.findByText('Kritik Ürün');
 fireEvent.click(screen.getByText('Kritik Ürün'));
 expect(navigate).toHaveBeenCalledWith('/urunler/22');
 fireEvent.click(screen.getByText('Geciken Müşteri'));
 expect(navigate).toHaveBeenCalledWith('/musteriler/44');
 fireEvent.click(screen.getByText('Merkez Kasa'));
 expect(navigate).toHaveBeenCalledWith('/nakit-yonetimi',{state:{accountId:55}});
});

it('finans yetkisi olmayan kullanıcıdan hazine bileşenlerini gizler',async()=>{
 financeAllowed=false;
 mount();
 await screen.findByText('Acil Aksiyonlar');
 expect(screen.queryByText('Kasa / Banka')).toBeNull();
 expect(screen.queryByText('Merkez Kasa')).toBeNull();
});

it('acil aksiyon kartları hedef ekranları hazır filtreyle açar',async()=>{
 mount();
 await screen.findByText('Acil Aksiyonlar');
 // Ayni basliklar asagidaki detay bloklarinda da geciyor; ilk esleme Acil Aksiyonlar kartidir.
 fireEvent.click(screen.getAllByText('Kritik / Negatif Stok')[0]);
 expect(navigate).toHaveBeenCalledWith('/urunler?critical=1');
 fireEvent.click(screen.getAllByText('Vadesi Geçen Alacak')[0]);
 expect(navigate).toHaveBeenCalledWith('/satislar?status=overdue');
});
