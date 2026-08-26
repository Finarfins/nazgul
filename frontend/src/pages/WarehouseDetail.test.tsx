import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import WarehouseDetail from './WarehouseDetail';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate,useParams:()=>({id:'3'})};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...a:any[])=>get(...a)},
 money:(v:any)=>`${Number(v).toFixed(2)} ₺`,
 errorDetail:(_e:any,fallback:string)=>fallback,
}));
const transferProps=vi.fn();
vi.mock('../components/TransferDialog',()=>({default:(props:any)=>{transferProps(props);return props.open?<div data-testid="transfer-open"/>:null}}));
const countProps=vi.fn();
vi.mock('../components/InventoryCountDialog',()=>({default:(props:any)=>{countProps(props);return props.open?<div data-testid="count-open"/>:null}}));

const detail={
 warehouse:{id:3,name:'Merkez Depo',code:'MRK',branch_name:'Ana Şube',is_default:true,is_active:true},
 stats:{product_count:4,total_quantity:120,critical_count:1,zero_count:1,negative_count:1},
 products:[
  {product_id:10,name:'Normal Ürün',product_code:'N-1',unit:'adet',quantity:50,critical_stock:5,location:'A-1',last_movement_date:'2026-07-10'},
  {product_id:11,name:'Kritik Ürün',product_code:'K-1',unit:'adet',quantity:3,critical_stock:10,location:'A-2',last_movement_date:'2026-07-09'},
  {product_id:12,name:'Sıfır Ürün',product_code:'Z-1',unit:'adet',quantity:0,critical_stock:5,location:'A-3',last_movement_date:null},
  {product_id:13,name:'Negatif Ürün',product_code:'X-1',unit:'adet',quantity:-4,critical_stock:5,location:'A-4',last_movement_date:'2026-07-01'},
 ],
};
const countRows=[
 {id:41,count_date:'2026-07-20',warehouse_id:3,warehouse_name:'Merkez Depo',item_count:8,changed_count:3,total_absolute_variance:5},
 {id:42,count_date:'2026-07-19',warehouse_id:4,warehouse_name:'Şube Depo',item_count:4,changed_count:1,total_absolute_variance:1},
];

beforeEach(()=>{
 navigate.mockReset();get.mockReset();transferProps.mockReset();countProps.mockReset();
 get.mockImplementation((url:string,config?:any)=>{
  if(url==='/warehouses/3')return Promise.resolve({data:detail});
  if(url==='/warehouses')return Promise.resolve({data:[{id:3,name:'Merkez Depo'},{id:4,name:'Şube Depo'}]});
  if(url==='/warehouses/counts')return Promise.resolve({data:countRows.filter(row=>!config?.params?.warehouse_id||row.warehouse_id===config.params.warehouse_id)});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

function mount(){return render(<ThemeProvider theme={createTheme()}><MemoryRouter><WarehouseDetail/></MemoryRouter></ThemeProvider>)}

it('yüklenirken ilerleme göstergesi gösterir',()=>{
 get.mockReturnValue(new Promise(()=>{}));
 mount();
 expect(screen.getByLabelText('Depo yükleniyor')).toBeInTheDocument();
});

it('hata durumunu düz metin olarak gösterir',async()=>{
 get.mockImplementation((url:string)=>url==='/warehouses/3'
  ?Promise.reject({response:{status:500,data:{detail:{msg:'x'}}}})
  :Promise.resolve({data:[]}));
 mount();
 expect(await screen.findByText('Depo bilgileri yüklenemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});

it('başlık istatistiklerini ve ürünleri gösterir',async()=>{
 mount();
 expect(await screen.findByText('Merkez Depo')).toBeInTheDocument();
 expect(screen.getByText('Varsayılan Depo')).toBeInTheDocument();
 expect(screen.getByText('Kritik Ürün')).toBeInTheDocument();
 expect(screen.getByText('Negatif Ürün')).toBeInTheDocument();
});

it('boş depoda bilgi mesajı gösterir',async()=>{
 get.mockImplementation((url:string,config?:any)=>url==='/warehouses/3'
  ?Promise.resolve({data:{...detail,products:[]}})
  :url==='/warehouses/counts'?Promise.resolve({data:countRows.filter(row=>row.warehouse_id===config?.params?.warehouse_id)}):Promise.resolve({data:[]}));
 mount();
 expect(await screen.findByText('Bu depoda ürün stoğu bulunmuyor.')).toBeInTheDocument();
});

it('ürün satırından ürün detayına gider (kart görünümü)',async()=>{
 window.matchMedia=((query:string)=>({matches:true,media:query,onchange:null,
  addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn()})) as any;
 mount();
 await screen.findByText('Kritik Ürün');
 fireEvent.click(screen.getByRole('button',{name:'Kritik Ürün — Ürün Detayı'}));
 expect(navigate).toHaveBeenCalledWith('/urunler/11');
});

it('buradan transfer aksiyonu depoyu kaynak olarak önseçer',async()=>{
 mount();
 await screen.findByText('Merkez Depo');
 fireEvent.click(screen.getByRole('button',{name:'Buradan Transfer Et'}));
 await waitFor(()=>{
  const props=transferProps.mock.calls[transferProps.mock.calls.length-1][0];
  expect(props.open).toBe(true);
  expect(props.initialSourceId).toBe(3);
 });
});

it('fiziksel sayım aksiyonu mevcut depoyu önseçer',async()=>{
 mount();
 await screen.findByText('Merkez Depo');
 fireEvent.click(screen.getByRole('button',{name:'Fiziksel Sayım'}));
 await waitFor(()=>{
  const props=countProps.mock.calls[countProps.mock.calls.length-1][0];
  expect(props.open).toBe(true);
  expect(props.initialWarehouseId).toBe(3);
 });
});

it('yalnız mevcut depoya ait sayımları sunucudan ister ve belgeye gider',async()=>{
 window.matchMedia=((query:string)=>({matches:true,media:query,onchange:null,
  addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn()})) as any;
 mount();
 expect(await screen.findByText('Sayım #41')).toBeInTheDocument();
 expect(screen.queryByText('Sayım #42')).toBeNull();
 expect(get).toHaveBeenCalledWith('/warehouses/counts',{params:{warehouse_id:3,limit:10}});
 fireEvent.click(screen.getByRole('button',{name:'Sayım #41 — Sayımı Aç'}));
 expect(navigate).toHaveBeenCalledWith('/stok-sayimlari/41');
});

it('tüm sayımlara depo bağlamıyla gider',async()=>{
 mount();
 await screen.findByText('Merkez Depo');
 fireEvent.click(screen.getByRole('button',{name:'Tüm Sayımlar'}));
 expect(navigate).toHaveBeenCalledWith('/stok-sayimlari',{state:{warehouseId:3}});
});

it('sayım geçmişi hata nesnesini düz metin olarak gösterir',async()=>{
 get.mockImplementation((url:string)=>{
  if(url==='/warehouses/3')return Promise.resolve({data:detail});
  if(url==='/warehouses')return Promise.resolve({data:[]});
  if(url==='/warehouses/counts')return Promise.reject({response:{status:500,data:{detail:[{msg:'x'}]}}});
  return Promise.resolve({data:[]});
 });
 mount();
 expect(await screen.findByText('Sayım geçmişi yüklenemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});

it('stok hareketleri butonu depo bağlamıyla gider',async()=>{
 mount();
 await screen.findByText('Merkez Depo');
 fireEvent.click(screen.getByRole('button',{name:/Stok Hareketleri/}));
 expect(navigate).toHaveBeenCalledWith('/stok-hareketleri',{state:{warehouseId:3}});
});
