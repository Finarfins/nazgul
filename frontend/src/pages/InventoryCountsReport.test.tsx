import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import InventoryCountsReport,{inventoryCountsCsv} from './InventoryCountsReport';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate,useLocation:()=>({state:{warehouseId:2}})};
});
const get=vi.fn();
const openAuthenticated=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 errorDetail:(_error:any,fallback:string)=>fallback,
 openAuthenticated:(...args:any[])=>openAuthenticated(...args),
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

beforeEach(()=>{
 navigate.mockReset();get.mockReset();openAuthenticated.mockReset();countDialogProps.mockReset();
 openAuthenticated.mockResolvedValue(undefined);
 get.mockImplementation((url:string)=>{
  if(url==='/warehouses')return Promise.resolve({data:[{id:1,name:'Merkez Depo'},{id:2,name:'Şube Depo'}]});
  if(url==='/warehouses/counts')return Promise.resolve({data:rows});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

function mount(entry='/stok-sayimlari'){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[entry]}><InventoryCountsReport/></MemoryRouter></ThemeProvider>);
}

it('depo bağlamını korur ve sayım özetlerini yükler',async()=>{
 mount();
 expect(await screen.findByText('Sayım belgesi: 2')).toBeInTheDocument();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/warehouses/counts',{params:{
  warehouse_id:2,date_from:undefined,date_to:undefined,changed_only:undefined,
 }}));
 expect(screen.getByText('Sayılan satır: 5')).toBeInTheDocument();
 expect(screen.getByText('Toplam mutlak fark: 4')).toBeInTheDocument();
});

it('komut paleti parametresiyle yeni sayımı seçili depoda açar',async()=>{
 mount('/stok-sayimlari?new=1');
 await waitFor(()=>{
  const props=countDialogProps.mock.calls[countDialogProps.mock.calls.length-1][0];
  expect(props.open).toBe(true);
  expect(props.initialWarehouseId).toBe(2);
 });
});

it('CSV çıktısını BOM ve formül güvenli ayraçlarla üretir',()=>{
 const csv=inventoryCountsCsv(rows);
 expect(csv.startsWith('\uFEFF')).toBe(true);
 expect(csv).toContain('"Sayım No";"Tarih";"Depo"');
 expect(csv).toContain('"Şube; ""Yedek"""');
});

it('asgari belge mutlak farkıyla düşük etkili belgeleri ve KPI etkisini filtreler',async()=>{
 mount();
 await screen.findByText('Sayım belgesi: 2');
 fireEvent.change(screen.getByLabelText('Asgari belge mutlak farkı'),{target:{value:'1'}});
 await waitFor(()=>expect(screen.getByText('Sayım belgesi: 1')).toBeInTheDocument());
 expect(screen.getByText('Sayılan satır: 3')).toBeInTheDocument();
 expect(screen.getByText('Değişen satır: 2')).toBeInTheDocument();
 expect(screen.getByText('Toplam mutlak fark: 4')).toBeInTheDocument();
 expect(screen.getByText('Belge önem eşiği: 1')).toBeInTheDocument();
});

it('yüksek belge eşiğinde boş durumu gösterir',async()=>{
 mount();
 await screen.findByText('Sayım belgesi: 2');
 fireEvent.change(screen.getByLabelText('Asgari belge mutlak farkı'),{target:{value:'5'}});
 await waitFor(()=>expect(screen.getByText('Sayım belgesi: 0')).toBeInTheDocument());
 expect(screen.getByText('Seçilen filtrelerde stok sayımı bulunmuyor.')).toBeInTheDocument();
});

it('seçili filtreleri ve belge eşiğini Excel raporuna taşır',async()=>{
 mount();
 await screen.findByText('Stok Sayımları');
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'Fark durumu'}));
 fireEvent.click(await screen.findByRole('option',{name:'Fark bulunanlar'}));
 fireEvent.change(screen.getByLabelText('Başlangıç'),{target:{value:'2026-07-01'}});
 fireEvent.change(screen.getByLabelText('Bitiş'),{target:{value:'2026-07-20'}});
 fireEvent.change(screen.getByLabelText('Asgari belge mutlak farkı'),{target:{value:'2.5'}});
 fireEvent.click(screen.getByRole('button',{name:'Excel’e Aktar'}));
 await waitFor(()=>expect(openAuthenticated).toHaveBeenCalledWith(
  '/exports/warehouse-count-variance.xlsx?warehouse_id=2&date_from=2026-07-01&date_to=2026-07-20&changed_only=true&min_absolute_variance=2.5',
  'stok-sayim-farklari.xlsx',
 ));
});

it('filtre temizleme belge eşiğini de kaldırır',async()=>{
 mount();
 await screen.findByText('Sayım belgesi: 2');
 fireEvent.change(screen.getByLabelText('Asgari belge mutlak farkı'),{target:{value:'5'}});
 await waitFor(()=>expect(screen.getByText('Sayım belgesi: 0')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'Filtreleri Temizle'}));
 await waitFor(()=>expect(screen.getByText('Sayım belgesi: 2')).toBeInTheDocument());
 expect(screen.queryByText(/Belge önem eşiği:/)).toBeNull();
});

it('rapor hatasını güvenli düz metin olarak gösterir',async()=>{
 openAuthenticated.mockRejectedValue({response:{status:500,data:{detail:[{msg:'x'}]}}});
 mount();
 await screen.findByText('Sayım belgesi: 2');
 fireEvent.click(screen.getByRole('button',{name:'Excel’e Aktar'}));
 expect(await screen.findByText('Stok sayım fark raporu indirilemedi.')).toBeInTheDocument();
 expect(screen.queryByText(/\[object Object\]/)).toBeNull();
});
