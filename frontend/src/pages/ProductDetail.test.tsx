import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import ProductDetail,{movementTarget} from './ProductDetail';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate,useParams:()=>({id:'12'})};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 money:(v:any)=>`${Number(v).toFixed(2)} ₺`,
 errorDetail:(_e:any,fallback:string)=>fallback,
 openAuthenticated:vi.fn(),
}));
vi.mock('../components/ProductDialog',()=>({default:()=>null}));
const txnProps=vi.fn();
vi.mock('../components/TransactionDialog',()=>({default:(props:any)=>{txnProps(props);return null}}));
const transferProps=vi.fn();
vi.mock('../components/TransferDialog',()=>({default:(props:any)=>{transferProps(props);return null}}));

const baseProduct={
 id:12,name:'Hidrolik Pompa',product_code:'HP-100',barcode:'869001',category:'Hidrolik',
 unit:'adet',stock:40,critical_stock:10,minimum_stock:0,sale_price:1500,purchase_price:1000,
 vat_rate:20,active:true,location:'A-1',oem_number:'OEM-9',brand:'Marka',
};

function payload(overrides:any={}){
 return {
  product:{...baseProduct,...(overrides.product||{})},
  commercial_summary:{total_sold_quantity:12,total_purchased_quantity:20,last_sale_price:1500,last_purchase_price:1000,...(overrides.commercial_summary||{})},
  warehouse_stocks:overrides.warehouse_stocks??[{warehouse_id:3,name:'Merkez Depo',quantity:40,critical_stock:10}],
  movements:overrides.movements??[
   {id:101,movement_type:'sale',quantity:-2,movement_date:'2026-07-10',reference_type:'order',reference_id:55,warehouse_name:'Merkez Depo',note:'Demo satış'},
   {id:102,movement_type:'purchase',quantity:5,movement_date:'2026-07-09',reference_type:'purchases',reference_id:77,warehouse_name:'Merkez Depo',note:null},
   {id:103,movement_type:'transfer_out',quantity:-1,movement_date:'2026-07-08',reference_type:'transfer',reference_id:9,warehouse_name:'Şube Depo',note:null},
  ],
  sales_history:overrides.sales_history??[
   {id:55,document_no:'S-55',order_date:'2026-07-10',customer_id:41,customer_name:'Tarım A.Ş.',quantity:2,unit_price:1500,discount_percent:0,line_total:3000},
  ],
  purchase_history:overrides.purchase_history??[
   {id:77,document_no:'A-77',purchase_date:'2026-07-09',supplier_id:61,supplier_name:'Pompa Ltd.',quantity:5,unit_price:1000,discount_percent:0,line_total:5000},
  ],
 };
}

beforeEach(()=>{navigate.mockReset();get.mockReset();txnProps.mockReset();get.mockResolvedValue({data:payload()})});
afterEach(()=>cleanup());

function mount(){return render(<ThemeProvider theme={createTheme()}><MemoryRouter><ProductDetail/></MemoryRouter></ThemeProvider>)}
const openTab=async(name:RegExp)=>fireEvent.click(await screen.findByRole('tab',{name}));

describe('movementTarget',()=>{
 it('tekil ve çoğul referans tiplerini aynı rotaya çözer',()=>{
  expect(movementTarget('order',5)).toEqual({path:'/satislar',openId:5});
  expect(movementTarget('orders',5)).toEqual({path:'/satislar',openId:5});
  expect(movementTarget('purchase',6)).toEqual({path:'/alislar',openId:6});
  expect(movementTarget('purchases',6)).toEqual({path:'/alislar',openId:6});
  expect(movementTarget('transfer',9)).toEqual({path:'/depo-transferleri/9',openId:9});
 });
 it('rotası olmayan veya kimliksiz referansları çözmez',()=>{
  expect(movementTarget('product',1)).toBeNull();
  expect(movementTarget('quotes',3)).toBeNull();
  expect(movementTarget('order',null)).toBeNull();
  expect(movementTarget('order',0)).toBeNull();
  expect(movementTarget(null,5)).toBeNull();
 });
});

it('yüklenirken ilerleme göstergesi gösterir',()=>{
 get.mockReturnValue(new Promise(()=>{}));
 mount();
 expect(screen.getByLabelText('Ürün yükleniyor')).toBeInTheDocument();
});

it('hata durumunu ham nesne yerine düz metin olarak gösterir',async()=>{
 get.mockRejectedValue({response:{status:422,data:{detail:[{msg:'bozuk',loc:['body']}]}}});
 mount();
 expect(await screen.findByText('Ürün bilgileri yüklenemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});

it('normal stokta operasyonel özeti gösterir',async()=>{
 mount();
 expect(await screen.findByText('Hidrolik Pompa')).toBeInTheDocument();
 expect(screen.getByText('Stok durumu: Normal')).toBeInTheDocument();
 expect(screen.getAllByText('40 adet').length).toBeGreaterThan(0);
 expect(screen.getByText('Son satış: 2026-07-10')).toBeInTheDocument();
 expect(screen.getByText('Son alış: 2026-07-09')).toBeInTheDocument();
});

it('kritik stokta eksik miktarı ve alış çağrısını gösterir',async()=>{
 get.mockResolvedValue({data:payload({product:{stock:4,critical_stock:10}})});
 mount();
 expect(await screen.findByText('Stok durumu: Kritik')).toBeInTheDocument();
 expect(screen.getByText(/Stok kritik seviyede/)).toBeInTheDocument();
 expect(screen.getByText(/Eksik 6 adet/)).toBeInTheDocument();
});

it('negatif stoku istisnai durum olarak işaretler ve sıfıra çekmez',async()=>{
 get.mockResolvedValue({data:payload({product:{stock:-3,critical_stock:10}})});
 mount();
 expect(await screen.findByText('Stok durumu: Negatif Stok')).toBeInTheDocument();
 expect(screen.getByText(/Negatif stok/)).toBeInTheDocument();
 expect(screen.getByText('-3 adet')).toBeInTheDocument();
});

it('kritik eşiği tanımsızken pozitif stoğu kritik saymaz',async()=>{
 get.mockResolvedValue({data:payload({product:{stock:5,critical_stock:0,minimum_stock:0}})});
 mount();
 expect(await screen.findByText('Stok durumu: Normal')).toBeInTheDocument();
});

it('depo bazında stok satırlarını durumuyla listeler',async()=>{
 mount();
 await screen.findByText('Hidrolik Pompa');
 expect(screen.getByText('Merkez Depo')).toBeInTheDocument();
 expect(screen.getAllByText('Kritik seviye: 10 adet').length).toBeGreaterThan(0);
});

it('depo stoğu yokken boş durum mesajı gösterir',async()=>{
 get.mockResolvedValue({data:payload({warehouse_stocks:[]})});
 mount();
 expect(await screen.findByText('Depo bazında stok bilgisi bulunmuyor.')).toBeInTheDocument();
});

describe('stok hareketleri',()=>{
 it('iş diline çevirir ve yönü gösterir',async()=>{
  mount();
  await openTab(/Stok Hareketleri/);
  expect(screen.getByText(/^Satış · Merkez Depo$/)).toBeInTheDocument();
  expect(screen.getByText(/^Transfer Çıkışı · Şube Depo$/)).toBeInTheDocument();
  expect(screen.getAllByText('Çıkış').length).toBeGreaterThan(0);
 });

 it('satış hareketinden satış belgesine gider',async()=>{
  mount();
  await openTab(/Stok Hareketleri/);
  fireEvent.click(screen.getByRole('button',{name:/Satış hareketinin belgesini aç/}));
  expect(navigate).toHaveBeenCalledWith('/satislar',{state:{openId:55}});
 });

 it('alış hareketinden alış belgesine gider',async()=>{
  mount();
  await openTab(/Stok Hareketleri/);
  fireEvent.click(screen.getByRole('button',{name:/Alış hareketinin belgesini aç/}));
  expect(navigate).toHaveBeenCalledWith('/alislar',{state:{openId:77}});
 });

 it('klavyeyle de belgeyi açar',async()=>{
  mount();
  await openTab(/Stok Hareketleri/);
  fireEvent.keyDown(screen.getByRole('button',{name:/Satış hareketinin belgesini aç/}),{key:'Enter'});
  expect(navigate).toHaveBeenCalledWith('/satislar',{state:{openId:55}});
 });

 it('transfer hareketinden transfer belgesine gider',async()=>{
  mount();
  await openTab(/Stok Hareketleri/);
  fireEvent.click(screen.getByRole('button',{name:/Transfer Çıkışı hareketinin belgesini aç/}));
  expect(navigate).toHaveBeenCalledWith('/depo-transferleri/9',{state:{openId:9}});
 });

 it('hareket yokken boş durum mesajı gösterir',async()=>{
  get.mockResolvedValue({data:payload({movements:[]})});
  mount();
  await openTab(/Stok Hareketleri/);
  expect(screen.getByText('Bu ürün için henüz stok hareketi bulunmuyor.')).toBeInTheDocument();
 });
});

describe('ticari geçmiş',()=>{
 it('satış belgesine ve müşteri kartına ayrı ayrı gider',async()=>{
  mount();
  await openTab(/Satış Geçmişi/);
  fireEvent.click(screen.getByRole('button',{name:'S-55 satış belgesini aç'}));
  expect(navigate).toHaveBeenCalledWith('/satislar',{state:{openId:55}});
  fireEvent.click(screen.getByRole('button',{name:'Tarım A.Ş. müşteri kartını aç'}));
  expect(navigate).toHaveBeenCalledWith('/musteriler/41');
 });

 it('alış belgesine ve tedarikçi kartına ayrı ayrı gider',async()=>{
  mount();
  await openTab(/Alış Geçmişi/);
  fireEvent.click(screen.getByRole('button',{name:'A-77 alış belgesini aç'}));
  expect(navigate).toHaveBeenCalledWith('/alislar',{state:{openId:77}});
  fireEvent.click(screen.getByRole('button',{name:'Pompa Ltd. tedarikçi kartını aç'}));
  expect(navigate).toHaveBeenCalledWith('/tedarikciler/61');
 });

 it('customer_id gelmediğinde müşteri adı bağlantısız kalır',async()=>{
  get.mockResolvedValue({data:payload({sales_history:[
   {id:55,document_no:'S-55',order_date:'2026-07-10',customer_id:null,customer_name:'Kimliksiz Cari',quantity:2,unit_price:1500,discount_percent:0,line_total:3000},
  ]})});
  mount();
  await openTab(/Satış Geçmişi/);
  expect(screen.getByText('Kimliksiz Cari')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/Kimliksiz Cari/})).toBeNull();
 });

 it('satış ve alış geçmişi boşken ayrı mesajlar gösterir',async()=>{
  get.mockResolvedValue({data:payload({sales_history:[],purchase_history:[]})});
  mount();
  await openTab(/Satış Geçmişi/);
  expect(screen.getByText('Bu ürün henüz satılmamış.')).toBeInTheDocument();
  await openTab(/Alış Geçmişi/);
  expect(screen.getByText('Bu ürün için alış kaydı bulunmuyor.')).toBeInTheDocument();
 });
});

describe('hızlı işlemler',()=>{
 it('Satış Oluştur ürünü önseçili satış ekranını açar',async()=>{
  mount();
  fireEvent.click(await screen.findByRole('button',{name:'Satış Oluştur'}));
  await waitFor(()=>{
   const props=txnProps.mock.calls[txnProps.mock.calls.length-1][0];
   expect(props.open).toBe(true);
   expect(props.kind).toBe('sale');
   expect(props.initialProductId).toBe(12);
  });
 });

 it('Alış Oluştur ürünü önseçili alış ekranını açar',async()=>{
  mount();
  fireEvent.click(await screen.findByRole('button',{name:'Alış Oluştur'}));
  await waitFor(()=>{
   const props=txnProps.mock.calls[txnProps.mock.calls.length-1][0];
   expect(props.open).toBe(true);
   expect(props.kind).toBe('purchase');
   expect(props.initialProductId).toBe(12);
  });
 });

 it('kritik stok uyarısından alış oluşturulabilir',async()=>{
  get.mockResolvedValue({data:payload({product:{stock:4,critical_stock:10}})});
  mount();
  await screen.findByText('Stok durumu: Kritik');
  const buttons=screen.getAllByRole('button',{name:'Alış Oluştur'});
  fireEvent.click(buttons[buttons.length-1]);
  await waitFor(()=>{
   const props=txnProps.mock.calls[txnProps.mock.calls.length-1][0];
   expect(props.open).toBe(true);
   expect(props.kind).toBe('purchase');
  });
 });
});
