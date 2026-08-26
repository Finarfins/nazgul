import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Decimal from 'decimal.js';
import TransactionDialog from './TransactionDialog';

const get=vi.fn();
const post=vi.fn();
const put=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:(...args:any[])=>post(...args),put:(...args:any[])=>put(...args)},
 money:(v:Decimal.Value)=>`${new Decimal(v).toFixed(2)} ₺`,
 policyOverrideHeaders:(reason:string)=>({'X-Policy-Override-Reason':reason}),
}));
vi.mock('../AuthContext',()=>({useAuth:()=>({user:{role:'admin'}})}));
vi.mock('./EntityDialog',()=>({default:()=>null}));
vi.mock('./ProductDialog',()=>({default:()=>null}));

const product={id:12,name:'Hidrolik Pompa',product_code:'HP-100',sale_price:1500,purchase_price:1000,vat_rate:20,unit:'adet',warehouse_stock:40};

beforeEach(()=>{
 get.mockReset();
 post.mockReset();
 put.mockReset();
 post.mockResolvedValue({data:{id:1}});
 get.mockImplementation((url:string)=>{
  if(url==='/customers')return Promise.resolve({data:[{id:4,name:'Test Müşteri'},{id:7,name:'Ali Çiftçi'}]});
  if(url==='/customers/7')return Promise.resolve({data:{summary:null}});
  if(url==='/orders/last-sale-price')return Promise.reject({response:{status:404}});
  if(url==='/warehouses')return Promise.resolve({data:[{id:3,name:'Merkez Depo'}]});
  if(url==='/products')return Promise.resolve({data:[product]});
  if(url==='/payments/accounts')return Promise.resolve({data:[]});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

function mount(props:any={}){
 return render(<ThemeProvider theme={createTheme()}>
  <TransactionDialog open kind="sale" onClose={()=>{}} onSaved={()=>{}} {...props}/>
 </ThemeProvider>);
}

const priceInput=()=>screen.getAllByLabelText("Birim Fiyat")[0] as HTMLInputElement;

// Diyalogun ilk yüklemesi ÜÇ AYRI React commit'inden geçer:
//   1) Promise.all([/customers,/warehouses,/payments/accounts]) -> setWarehouseId
//   2) warehouseId etkisi -> GET /products -> setProducts
//   3) products etkisi -> selectProduct(0,preset) -> setLines -> useAutoTrailingLine
//      son satırı dolu görüp altına BOŞ SATIRI ekler
//
// ÖLÇÜLEN KUSUR: doğrudan "iki satır var mı" beklemek bu ÜÇ geçişin tamamını
// TEK bir waitFor bütçesine sıkıştırıyordu (RTL varsayılanı 1000 ms). Bütçeye
// giren süreler, yüksüz, 16 çekirdek, 20 yineleme:
//     bacak A  render sonrası -> /products istendi   p50  48 ms  max 127
//     bacak B  /products      -> önseçim uygulandı   p50  68 ms  max 131
//     bacak C  önseçim        -> boş satır eklendi   p50  99 ms  max 116
//     BÜTÜN ZİNCİR                                   p50 223 ms  max 368
// Yani tek bütçeye kalan pay 1000/368 = 2.7x; en kötü TEK bacağa 1000/131 = 7.6x.
// Pay daraldıkça düşen küme büyüyor (bütçe 400 ms: 0/10, 300 ms: 1 test 10/10,
// 200 ms: 3 test 10/10) ve hata her defasında `waitFor`un yeniden fırlattığı
// `expected [...] to have a length of 2 but got 1` oluyor. CI kutusunda aynı
// ağaç üzerinde düşen kümenin 3/2/1/0 diye değişmesi de bu.
//
// BÜTÇE UZATILMADI — hiçbir timeout değeri değişmedi. Beklenen ŞEY düzeltildi:
// her geçiş KENDİ koşulu olarak beklenir, böylece tek bir bekleme birden fazla
// bağımsız geçişi kapsamaz. Aynı dosyadaki 'son satır dolunca...' testi bunu
// zaten yapıyordu ve pay daralmasında hiç düşmedi; kontrol odur.
async function bosSatirHazir(){
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',expect.anything()));
 await screen.findByDisplayValue(/Hidrolik Pompa/);
 await waitFor(()=>expect(screen.getAllByLabelText('Birim Fiyat')).toHaveLength(2));
}

it('initialProductId verilmediğinde ilk satır boş kalır',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',expect.anything()));
 expect(screen.queryByDisplayValue(/Hidrolik Pompa/)).toBeNull();
 await waitFor(()=>expect(priceInput().value).toBe('0'));
});

it('satışta ürünü önseçer ve satış fiyatını uygular',async()=>{
 mount({initialProductId:12});
 expect(await screen.findByDisplayValue(/Hidrolik Pompa/)).toBeInTheDocument();
 // Mevcut selectProduct mantığı yeniden kullanıldığı için fiyat satış fiyatından gelir.
 await waitFor(()=>expect(priceInput().value).toBe('1500'));
});

it('alışta aynı ürün için alış fiyatını uygular',async()=>{
 mount({kind:'purchase',initialProductId:12});
 expect(await screen.findByDisplayValue(/Hidrolik Pompa/)).toBeInTheDocument();
 await waitFor(()=>expect(priceInput().value).toBe('1000'));
});

it('depoda bulunmayan ürün önseçimi sessizce yok sayılır',async()=>{
 mount({initialProductId:999});
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',expect.anything()));
 expect(screen.queryByDisplayValue(/Hidrolik Pompa/)).toBeNull();
});

it('son satır dolunca altına boş satır kendiliğinden açılır',async()=>{
 mount();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',expect.anything()));
 expect(screen.getAllByLabelText('Birim Fiyat')).toHaveLength(1);
 cleanup();
 mount({initialProductId:12});
 expect(await screen.findByDisplayValue(/Hidrolik Pompa/)).toBeInTheDocument();
 await waitFor(()=>expect(screen.getAllByLabelText('Birim Fiyat')).toHaveLength(2));
 const prices=screen.getAllByLabelText('Birim Fiyat') as HTMLInputElement[];
 expect(prices[0].value).toBe('1500');
 expect(prices[1].value).toBe('0');
});

it('otomatik açılan boş satır kayda gitmez',async()=>{
 mount({initialEntityId:7,initialProductId:12});
 await bosSatirHazir();
 fireEvent.click(screen.getByRole('button',{name:/SATIŞI TAMAMLA/}));
 await waitFor(()=>expect(post).toHaveBeenCalled());
 const payload=post.mock.calls[0][1];
 expect(payload.items).toHaveLength(1);
 expect(payload.items[0].product_id).toBe(12);
});

it('otomatik boş satır elle genel toplam iskontosuna karışmaz',async()=>{
 post.mockResolvedValue({data:{id:82,final_total:'1400.00'}});
 mount({initialEntityId:7,initialProductId:12});
 await bosSatirHazir();
 fireEvent.change(screen.getByLabelText('Elle genel toplam'),{target:{value:'1400'}});
 expect(screen.getByText('-100.00 ₺')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/SATIŞI TAMAMLA/}));
 await waitFor(()=>expect(post).toHaveBeenCalledWith('/orders',expect.objectContaining({
  discount_amount:'100',
  discount_percent:'6.6667',
  items:[expect.objectContaining({product_id:12,unit_price:'1500'})],
 }),expect.any(Object)));
});

it('otomatik boş satırın silme düğmesi pasiftir, dolu satırınki aktiftir',async()=>{
 mount({initialProductId:12});
 await bosSatirHazir();
 const deleteButtons=screen.getAllByRole('button',{name:'Satırı sil'});
 expect(deleteButtons[0]).not.toBeDisabled();
 expect(deleteButtons[1]).toBeDisabled();
});

it('mevcut belge düzenlenirken önseçim uygulanmaz',async()=>{
 get.mockImplementation((url:string)=>{
  if(url==='/warehouses')return Promise.resolve({data:[{id:3,name:'Merkez Depo'}]});
  if(url==='/products')return Promise.resolve({data:[product]});
  if(url==='/payments/accounts')return Promise.resolve({data:[]});
  if(url==='/orders/5')return Promise.resolve({data:{document:{entity_id:1,customer_name:'Cari',transaction_date:'2026-07-10',warehouse_id:3},items:[]}});
  return Promise.resolve({data:[]});
 });
 mount({id:5,initialProductId:12});
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/orders/5'));
 expect(screen.queryByDisplayValue(/Hidrolik Pompa/)).toBeNull();
});

it('elle genel toplamı fiyatları ezmeden sabit iskonto olarak gönderir',async()=>{
 post.mockResolvedValue({data:{id:81,final_total:'1400.00'}});
 mount({initialProductId:12});
 // Aynı yarış, daha hafif hâli: bu bekleme A+B bacaklarını tek bütçede
 // kapsıyordu. Ürün listesinin geldiği ayrı bir koşul olarak beklenir.
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',expect.anything()));
 expect(await screen.findByDisplayValue(/Hidrolik Pompa/)).toBeInTheDocument();
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'Müşteri'}));
 fireEvent.click(await screen.findByText('Test Müşteri'));
 fireEvent.change(screen.getByLabelText('Elle genel toplam'),{target:{value:'1400'}});
 expect(screen.getByText('-100.00 ₺')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/SATIŞI TAMAMLA/}));
 await waitFor(()=>expect(post).toHaveBeenCalledWith('/orders',expect.objectContaining({
  discount_amount:'100',
  discount_percent:'6.6667',
  items:[expect.objectContaining({unit_price:'1500'})],
 }),expect.any(Object)));
});

it('elle toplamlı belge düzenlenirken kesin iskonto tutarını korur',async()=>{
 get.mockImplementation((url:string)=>{
  if(url==='/warehouses')return Promise.resolve({data:[{id:3,name:'Merkez Depo'}]});
  if(url==='/products')return Promise.resolve({data:[product]});
  if(url==='/payments/accounts')return Promise.resolve({data:[]});
  if(url==='/orders/6')return Promise.resolve({data:{
   document:{entity_id:4,customer_name:'Test Müşteri',transaction_date:'2026-07-10',warehouse_id:3,grand_total:'32261.71',discount_percent:'0.81',discount_amount:'261.71',final_total:'32000.00'},
   items:[{product_id:12,product_name:'Hidrolik Pompa',quantity:1,unit_price:'32261.71',vat_rate:20,discount_percent:0}],
  }});
  return Promise.resolve({data:[]});
 });
 put.mockResolvedValue({data:{id:6,final_total:'32000.00'}});
 mount({id:6});
 expect(await screen.findByLabelText('Elle genel toplam')).toHaveValue('32000.00');
 const saveButton=screen.getByRole('button',{name:/SATIŞI TAMAMLA/});
 await waitFor(()=>expect(saveButton).toBeEnabled());
 fireEvent.click(saveButton);
 await waitFor(()=>expect(put).toHaveBeenCalledWith('/orders/6',expect.objectContaining({
  discount_amount:'261.71',
 }),expect.any(Object)));
});
