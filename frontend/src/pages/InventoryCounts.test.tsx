import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import InventoryCounts,{inventoryCountsCsv} from './InventoryCounts';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate,useLocation:()=>({state:{warehouseId:2}})};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 errorDetail:(_e:any,fallback:string)=>fallback,
}));
const countDialogProps=vi.fn();
vi.mock('../components/InventoryCountDialog',()=>({default:(props:any)=>{
 countDialogProps(props);
 return props.open?<div data-testid="count-dialog-open"/>:null;
}}));

const rows=[
 {id:41,count_date:'2026-07-20',warehouse_id:2,warehouse_name:'Şube Depo',item_count:3,changed_count:2,unchanged_count:1,increase_count:1,decrease_count:1,total_absolute_variance:4},
 {id:42,count_date:'2026-07-19',warehouse_id:2,warehouse_name:'Şube; "Yedek"',item_count:2,changed_count:0,unchanged_count:2,increase_count:0,decrease_count:0,total_absolute_variance:0},
];

function forceCards(){
 window.matchMedia=((query:string)=>({matches:true,media:query,onchange:null,
  addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn()})) as any;
}

beforeEach(()=>{
 navigate.mockReset();get.mockReset();countDialogProps.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/warehouses')return Promise.resolve({data:[{id:1,name:'Merkez Depo'},{id:2,name:'Şube Depo'}]});
  if(url==='/warehouses/counts')return Promise.resolve({data:rows});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

function mount(entry='/stok-sayimlari'){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[entry]}><InventoryCounts/></MemoryRouter></ThemeProvider>);
}

it('depo bağlamını korur ve sayım özetlerini gösterir',async()=>{
 forceCards();
 mount();
 expect(await screen.findByText('Sayım #41')).toBeInTheDocument();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/warehouses/counts',{params:{
  warehouse_id:2,date_from:undefined,date_to:undefined,changed_only:undefined,
 }}));
 expect(screen.getByText('Sayım belgesi: 2')).toBeInTheDocument();
 expect(screen.getByText('Sayılan satır: 5')).toBeInTheDocument();
 expect(screen.getByText('Değişen satır: 2')).toBeInTheDocument();
 expect(screen.getByText('Toplam mutlak fark: 4')).toBeInTheDocument();
});

it('sayım kartından belge detayına gider',async()=>{
 forceCards();
 mount();
 await screen.findByText('Sayım #41');
 fireEvent.click(screen.getByRole('button',{name:'Sayım #41 — Sayımı Aç'}));
 expect(navigate).toHaveBeenCalledWith('/stok-sayimlari/41');
});

it('fark bulunmayan sayımları istemci tarafında ayırır',async()=>{
 forceCards();
 mount();
 await screen.findByText('Sayım #41');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'Fark durumu'}));
 fireEvent.click(await screen.findByRole('option',{name:'Fark bulunmayanlar'}));
 await waitFor(()=>expect(screen.queryByText('Sayım #41')).toBeNull());
 expect(screen.getByText('Sayım #42')).toBeInTheDocument();
 expect(screen.getByText('Sayım belgesi: 1')).toBeInTheDocument();
});

it('yeni sayım penceresine seçili depoyu aktarır',async()=>{
 mount();
 await screen.findByText('Stok Sayımları');
 fireEvent.click(screen.getByRole('button',{name:'Yeni Fiziksel Sayım'}));
 await waitFor(()=>{
  const props=countDialogProps.mock.calls[countDialogProps.mock.calls.length-1][0];
  expect(props.open).toBe(true);
  expect(props.initialWarehouseId).toBe(2);
 });
});

it('komut paleti parametresiyle yeni sayımı doğrudan açar',async()=>{
 mount('/stok-sayimlari?new=1');
 await waitFor(()=>{
  const props=countDialogProps.mock.calls[countDialogProps.mock.calls.length-1][0];
  expect(props.open).toBe(true);
  expect(props.initialWarehouseId).toBe(2);
 });
});

it('CSV raporunu Excel uyumlu BOM, ayraç ve kaçışlarla üretir',()=>{
 const csv=inventoryCountsCsv(rows);
 expect(csv.startsWith('\uFEFF')).toBe(true);
 expect(csv).toContain('"Sayım No";"Tarih";"Depo"');
 expect(csv).toContain('"41";"2026-07-20";"Şube Depo"');
 expect(csv).toContain('"Şube; ""Yedek"""');
});

it('hata nesnesini güvenli düz metin olarak gösterir',async()=>{
 get.mockImplementation((url:string)=>url==='/warehouses/counts'
  ?Promise.reject({response:{status:500,data:{detail:[{msg:'x'}]}}})
  :Promise.resolve({data:[]}));
 mount();
 expect(await screen.findByText('Stok sayımları yüklenemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});