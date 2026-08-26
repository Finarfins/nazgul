import React from 'react';
import {cleanup,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';

import PurchaseDashboard from './PurchaseDashboard';

// Pano yalnızca sunucudan geleni gösterir. Testler bu sözleşmeyi çiviler:
// hiçbir tutar tarayıcıda hesaplanmaz, yetkisiz kullanıcı veri görmez, boş
// tenant hata değil boş durum alır ve ekran sipariş oluşturma yolu sunmaz.
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
 PieChart:({children}:any)=><div>{children}</div>,
 Pie:()=>null,Cell:()=>null,Legend:()=>null,Bar:()=>null,
 CartesianGrid:()=>null,Tooltip:()=>null,XAxis:()=>null,YAxis:()=>null,
}));

const SPEND={
 date_from:null,date_to:null,excluded_statuses:['draft','cancelled'],vat_included:true,
 totals:{total_try:'2450.00',line_total_try:'2500.00',purchase_count:3,supplier_count:2,average_purchase_try:'816.67'},
 monthly:[
  {month:'2026-05',total_try:'2000.00',purchase_count:2},
  {month:'2026-06',total_try:'450.00',purchase_count:1},
 ],
 by_supplier:[
  {supplier_id:11,supplier_name:'Alfa Tedarik',total_try:'1450.00',share_percent:'59.1837',purchase_count:2,last_purchase_date:'2026-06-05'},
  {supplier_id:12,supplier_name:'Beta Tedarik',total_try:'1000.00',share_percent:'40.8163',purchase_count:1,last_purchase_date:'2026-05-20'},
 ],
 other_supplier_total_try:'0.00',has_other_suppliers:false,
 by_product:[
  {product_id:21,product_name:'Motor Yagi',quantity:'12.0000',total_try:'1300.00',share_percent:'52.0000'},
  {product_id:null,product_name:'Serbest Satir',quantity:'1.0000',total_try:'200.00',share_percent:'8.0000'},
 ],
 other_product_total_try:'0.00',has_other_products:false,
 by_category:[
  {category:'Yag',total_try:'1300.00',share_percent:'52.0000'},
  {category:null,total_try:'200.00',share_percent:'8.0000'},
 ],
 other_category_total_try:'0.00',has_other_categories:false,
};
const SCORECARD={
 evaluated_product_count:3,candidate_product_count:3,truncated:false,
 comparable_product_count:2,single_source_product_count:1,unpriced_product_count:0,
 alt_threshold_percent:'5',
 suppliers:[
  {supplier_id:11,supplier_name:'Alfa Tedarik',product_count:3,priced_product_count:3,best_count:2,worst_count:0,alternative_count:0},
  {supplier_id:12,supplier_name:'Beta Tedarik',product_count:2,priced_product_count:2,best_count:0,worst_count:2,alternative_count:1},
 ],
};
const REORDER={items:[{
 product_id:21,product_code:'MY-1',product_name:'Motor Yagi',current_stock:'2.0000',
 reorder_point:'10.0000',suggested_quantity:'20.0000',lead_time_days:5,
 best_supplier:{supplier_id:11,supplier_name:'Alfa Tedarik',effective_price_in_try:'100.0000',catalog_cost_try_total:'2000.0000'},
}]};
const REORDER_SUMMARY={
 below_reorder_point_count:1,unpriced_count:0,total_suggested_cost_try:'2000.0000',
 demand_window_days:90,by_supplier:[],largest_deficits:[],
};

const EMPTY_SPEND={
 ...SPEND,
 totals:{total_try:'0.00',line_total_try:'0.00',purchase_count:0,supplier_count:0,average_purchase_try:null},
 monthly:[],by_supplier:[],by_product:[],by_category:[],
};

function route(url:string){
 if(url==='/purchase-comparison/spend-analytics')return {data:SPEND};
 if(url==='/purchase-comparison/supplier-scorecard')return {data:SCORECARD};
 if(url==='/purchase-comparison/reorder-suggestions')return {data:REORDER};
 if(url==='/purchase-comparison/dashboard')return {data:REORDER_SUMMARY};
 throw new Error(`beklenmeyen istek: ${url}`);
}

beforeEach(()=>{
 navigate.mockReset();get.mockReset();
 get.mockImplementation((url:string)=>Promise.resolve(route(url)));
});
afterEach(()=>cleanup());

function mount(){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter><PurchaseDashboard/></MemoryRouter></ThemeProvider>);
}

// Aynı tedarikçi/ürün adı birden fazla bölümde geçer (bu panonun amacı zaten
// aynı varlığı farklı açılardan göstermek), bu yüzden sorgular kart başlığına
// göre daraltılır.
function card(title:string){
 const container=screen.getByText(title).closest('.MuiCard-root');
 if(!container)throw new Error(`kart bulunamadı: ${title}`);
 return within(container as HTMLElement);
}

it('özet kartlarını ve aylık trendi sunucudan gelen tutarlarla gösterir',async()=>{
 mount();
 // Toplam sunucunun total_try'ı; tarayıcı aylık değerleri toplayarak üretmez.
 expect(await screen.findByText('2450.00 ₺')).toBeTruthy();
 expect(screen.getByText('816.67 ₺')).toBeTruthy();
 expect(screen.getByText('Toplam Alış (KDV dahil)')).toBeTruthy();
 expect(screen.getByText('Taslak ve iptal belgeler hariç')).toBeTruthy();
});

it('tedarikçi kırılımını payıyla listeler ve tedarikçi kartına yönlendirir',async()=>{
 mount();
 await screen.findByText('Tedarikçi Bazlı Harcama');
 const spendCard=card('Tedarikçi Bazlı Harcama');
 expect(spendCard.getByText('1450.00 ₺')).toBeTruthy();
 // Pay sunucudan gelen share_percent; frontend bölme yapmaz.
 expect(spendCard.getByText('%59.2')).toBeTruthy();
 spendCard.getByText('Alfa Tedarik').click();
 expect(navigate).toHaveBeenCalledWith('/tedarikciler/11');
});

it('"diğer" satırını sunucunun bayrağına göre gösterir, tutarı Number ile eşiklemez',async()=>{
 // Kuyruk var: bayrak true, tutar sunucudan gelen string olarak basılır.
 get.mockImplementation((url:string)=>Promise.resolve(
  url==='/purchase-comparison/spend-analytics'
   ?{data:{...SPEND,other_supplier_total_try:'1234.50',has_other_suppliers:true}}
   :route(url)
 ));
 mount();
 await screen.findByText('Tedarikçi Bazlı Harcama');
 expect(card('Tedarikçi Bazlı Harcama').getByText('Diğer tedarikçiler')).toBeTruthy();
 expect(card('Tedarikçi Bazlı Harcama').getByText('1234.50 ₺')).toBeTruthy();
 cleanup();

 // Kuyruk yok: bayrak false olduğu için satır hiç çizilmez. Tutar string'i
 // "0.00" gibi Number'a çevrilebilir bir değer olmasa da karar değişmez.
 get.mockImplementation((url:string)=>Promise.resolve(
  url==='/purchase-comparison/spend-analytics'
   ?{data:{...SPEND,other_supplier_total_try:'0.0000',has_other_suppliers:false}}
   :route(url)
 ));
 mount();
 await screen.findByText('Tedarikçi Bazlı Harcama');
 expect(screen.queryByText('Diğer tedarikçiler')).toBeNull();
});

it('fiyat karşılaştırma özetinde en iyi ve en pahalı tedarikçiyi işaretler',async()=>{
 mount();
 expect(await screen.findByText('En iyi: Alfa Tedarik (2)')).toBeTruthy();
 expect(screen.getByText('En pahalı: Beta Tedarik (2)')).toBeTruthy();
 expect(screen.getByText('2 karşılaştırılabilir ürün')).toBeTruthy();
 expect(screen.getByText('1 tek kaynak')).toBeTruthy();
});

it('yeniden sipariş listesini salt okunur gösterir, sipariş düğmesi sunmaz',async()=>{
 mount();
 expect(await screen.findByText('Yeniden Sipariş Önerileri')).toBeTruthy();
 expect(screen.getByText(/bu ekran sipariş oluşturmaz/i)).toBeTruthy();
 expect(screen.queryByRole('button',{name:/sipariş/i})).toBeNull();
});

it('ürün kırılımında product_id olmayan satır bağlantısız kalır',async()=>{
 mount();
 await screen.findByText('Ürün Bazlı Harcama');
 const productCard=card('Ürün Bazlı Harcama');
 productCard.getByText('Motor Yagi').click();
 expect(navigate).toHaveBeenCalledWith('/urunler/21');
 navigate.mockReset();
 productCard.getByText('Serbest Satir').click();
 expect(navigate).not.toHaveBeenCalled();
});

it('boş tenant için hata değil boş durum gösterir',async()=>{
 get.mockImplementation((url:string)=>Promise.resolve(
  url==='/purchase-comparison/spend-analytics'?{data:EMPTY_SPEND}
  :url==='/purchase-comparison/supplier-scorecard'?{data:{...SCORECARD,suppliers:[],comparable_product_count:0,single_source_product_count:0}}
  :url==='/purchase-comparison/reorder-suggestions'?{data:{items:[]}}
  :{data:{...REORDER_SUMMARY,below_reorder_point_count:0,total_suggested_cost_try:'0.0000'}}
 ));
 mount();
 expect(await screen.findByText(/alış belgesi bulunmuyor/i)).toBeTruthy();
 expect(screen.getByText('Grafik için alış verisi yok.')).toBeTruthy();
 expect(screen.getByText('Karşılaştırılacak tedarikçi fiyatı yok.')).toBeTruthy();
 expect(screen.getByText('Sipariş noktasının altına düşen ürün yok.')).toBeTruthy();
 // Ortalama yoksa uydurulmuş 0 değil, "-" gösterilir.
 expect(screen.getByText('-')).toBeTruthy();
});

it('yetkisiz kullanıcıya veri değil izin uyarısı gösterir',async()=>{
 get.mockImplementation(()=>Promise.reject({response:{status:403}}));
 mount();
 expect(await screen.findByText(/satın alma yetkisi gerektirir/i)).toBeTruthy();
 await waitFor(()=>{
  expect(screen.queryByText('Toplam Alış (KDV dahil)')).toBeNull();
 });
 expect(screen.queryByText('Alfa Tedarik')).toBeNull();
});

it('403 dışı hatada genel hata mesajı gösterir',async()=>{
 get.mockImplementation(()=>Promise.reject({response:{status:500}}));
 mount();
 expect(await screen.findByText('Satın alma panosu yüklenemedi.')).toBeTruthy();
});
