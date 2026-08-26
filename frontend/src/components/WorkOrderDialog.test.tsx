import React from 'react';
import {cleanup,render,screen,fireEvent,waitFor} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import WorkOrderDialog,{MACHINE_OPTIONS_LIMIT,MACHINE_SEARCH_DEBOUNCE_MS,PART_SEARCH_DEBOUNCE_MS} from './WorkOrderDialog';
import {api} from '../api';

vi.mock('../api',()=>({
 api:{get:vi.fn(),post:vi.fn(),put:vi.fn()},
 money:(v:unknown)=>String(v),
}));

// Düzenleme (edit) PUT'u tam-değiştirme olduğu için WorkOrderUpdate'in 15
// düzenlenebilir alanının TAMAMINI taşımalı — özellikle kullanıcıya
// gösterilmeyen opened_at ve scheduled_date alanlarını da mevcut değerleriyle.
// Bu test alan listesini backend sözleşmesine sabitler; bir alan düşerse
// regresyon olarak yakalanır.
const EDITABLE_FIELDS=[
 'machine_id','customer_id','technician_id','opened_at','scheduled_date','priority','complaint',
 'diagnosis','repair_summary','technician_notes','estimated_hours','actual_hours',
 'labor_rate','warranty_type','warranty_percent',
];

const workOrder={
 id:1,machine_id:5,customer_id:9,technician_id:3,work_order_no:'ISE-0001',
 opened_at:'2026-07-01T08:00:00Z',scheduled_date:null,priority:'HIGH',complaint:'Arıza',diagnosis:'Teşhis',
 repair_summary:'Onarım',technician_notes:'Not',estimated_hours:'2.00',actual_hours:'1.00',
 labor_rate:'100.00',warranty_type:'NONE',warranty_percent:null,
};

describe('WorkOrderDialog',()=>{
 beforeEach(()=>{vi.clearAllMocks()});
 afterEach(cleanup);

 // Canlıda yakalanan hata: dialog limit=1000 gönderiyordu, GET /api/machines
 // ise limit'i 1..500 ile sınırlıyor (Query(le=500)) ve 422 dönüyordu. İstek
 // uç sözleşmesinin içinde kalmalı; aksi halde makine kutusu hiç dolmaz.
 it('makine listesini GET /machines sözleşmesinin sınırları içinde ister',async()=>{
  const mock=api.get as unknown as ReturnType<typeof vi.fn>;
  mock.mockResolvedValue({data:[]});

  render(<ThemeProvider theme={createTheme()}><WorkOrderDialog open onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);

  await waitFor(()=>expect(mock).toHaveBeenCalledWith('/machines',expect.anything()));
  const [,config]=mock.mock.calls.find(call=>call[0]==='/machines')!;
  const params=(config as {params:{active:string;limit:number}}).params;
  expect(params.active).toBe('all');
  expect(params.limit).toBe(MACHINE_OPTIONS_LIMIT);
  // Uç sözleşmesi: 1 ≤ limit ≤ 500. Bu sınır aşılırsa backend 422 döner.
  expect(params.limit).toBeGreaterThanOrEqual(1);
  expect(params.limit).toBeLessThanOrEqual(500);
 });

 it('500 dışındaki makineyi debounce edilmiş sunucu q aramasıyla getirir',async()=>{
  const mock=api.get as unknown as ReturnType<typeof vi.fn>;
  mock.mockImplementation((url:string,config?:{params?:{q?:string}})=>{
   if(url==='/machines'&&config?.params?.q==='501')return Promise.resolve({data:[{id:501,brand:'Sungur',model:'501',serial_number:'SN-501'}]});
   return Promise.resolve({data:[]});
  });

  render(<ThemeProvider theme={createTheme()}><WorkOrderDialog open onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);

  const input=screen.getByRole('combobox',{name:'Makine'});
  fireEvent.change(input,{target:{value:'501'}});
  await waitFor(()=>expect(mock).toHaveBeenCalledWith('/machines',expect.objectContaining({
   params:{active:'all',limit:MACHINE_OPTIONS_LIMIT,q:'501'},
  })),{timeout:MACHINE_SEARCH_DEBOUNCE_MS+1000});
  expect(await screen.findByText('Sungur 501 · SN-501')).toBeTruthy();
 });

 // Hata sessizce yutulduğu için kullanıcı 422 yerine boş bir "No options"
 // kutusu görüyordu. Dropdown artık boş sonuçtan ayrı hata metni göstermeli.
 it('makine araması yüklenemezse dropdown hata gösterir, No options göstermez',async()=>{
  const mock=api.get as unknown as ReturnType<typeof vi.fn>;
  mock.mockImplementation((url:string,config?:{params?:{q?:string}})=>{
   if(url==='/machines'&&config?.params?.q)return Promise.reject({response:{status:503}});
   return Promise.resolve({data:[]});
  });

  render(<ThemeProvider theme={createTheme()}><WorkOrderDialog open onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);

  const input=screen.getByRole('combobox',{name:'Makine'});
  fireEvent.change(input,{target:{value:'arıza'}});
  expect(await screen.findByText('Makineler yüklenemedi — tekrar deneyin',{},{
   timeout:MACHINE_SEARCH_DEBOUNCE_MS+1000,
  })).toBeTruthy();
  expect(screen.queryByText('No options')).toBeNull();
  expect(screen.queryByText('Makine bulunamadı')).toBeNull();
 });

 it('düzenleme PUT gövdesi opened_at dahil 14 alanı da gönderir',async()=>{
  const mock=api.get as unknown as ReturnType<typeof vi.fn>;
  mock.mockImplementation((url:string)=>{
   if(url==='/machines')return Promise.resolve({data:[{id:5,brand:'Sungur',model:'X',serial_number:'S1',customer_id:9}]});
   if(url==='/customers')return Promise.resolve({data:[{id:9,name:'Müşteri A'}]});
   if(url==='/work-orders/technicians')return Promise.resolve({data:[{id:3,display_name:'Tekniker',username:'tek'}]});
   if(url==='/work-orders/1')return Promise.resolve({data:workOrder});
   return Promise.resolve({data:[]});
  });
  (api.put as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({data:workOrder});

  render(<ThemeProvider theme={createTheme()}><WorkOrderDialog open id={1} onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);

  // Kayıtlı veri yüklenip zorunlu alanlar dolunca Kaydet etkinleşir.
  const saveButton=await screen.findByRole('button',{name:'Kaydet'});
  await waitFor(()=>expect(saveButton).not.toBeDisabled());
  fireEvent.click(saveButton);

  await waitFor(()=>expect(api.put).toHaveBeenCalledTimes(1));
  const [url,payload]=(api.put as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(url).toBe('/work-orders/1');
  expect(Object.keys(payload as Record<string,unknown>).sort()).toEqual([...EDITABLE_FIELDS].sort());
  // opened_at değiştirilmeden geri gönderilir; planlanmamış iş emrinde
  // scheduled_date null olarak taşınır (tam-değiştirme sözleşmesi).
  expect((payload as Record<string,unknown>).opened_at).toBe('2026-07-01T08:00:00Z');
  expect((payload as Record<string,unknown>).scheduled_date).toBeNull();
 });

 it('iş emri ve parçaları tek atomik istekte gönderir',async()=>{
  const get=api.get as unknown as ReturnType<typeof vi.fn>;
  const post=api.post as unknown as ReturnType<typeof vi.fn>;
  get.mockImplementation((url:string)=>{
   if(url==='/machines')return Promise.resolve({data:[{id:5,brand:'Sungur',model:'X',customer_id:9}]});
   if(url==='/customers')return Promise.resolve({data:[{id:9,name:'Müşteri A'}]});
   if(url==='/work-orders/technicians')return Promise.resolve({data:[{id:3,display_name:'Tekniker'}]});
   if(url==='/warehouses')return Promise.resolve({data:[{id:2,name:'Servis Deposu'}]});
   if(url==='/products')return Promise.resolve({data:{items:[{id:12,name:'Filtre',product_code:'F-1',sale_price:250,purchase_price:10,warehouse_stock:5,reserved_quantity:2,available_stock:3,unit:'adet'}],has_more:false}});
   return Promise.resolve({data:[]});
  });
  post.mockResolvedValue({data:{id:77,work_order_no:'ISE-77'}});

  render(<ThemeProvider theme={createTheme()}><WorkOrderDialog open onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);
  fireEvent.mouseDown(await screen.findByRole('combobox',{name:/Makine/}));
  fireEvent.click(await screen.findByText(/Sungur X/));
  fireEvent.mouseDown(screen.getByRole('combobox',{name:/Teknisyen/}));
  fireEvent.click(await screen.findByText('Tekniker'));
  await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',{params:{limit:2000,warehouse_id:2,include_meta:true}}));
  fireEvent.mouseDown(screen.getByRole('combobox',{name:/Parça ara/}));
  fireEvent.click(await screen.findByText(/kullanılabilir 3 adet/));
  await waitFor(()=>expect(screen.getAllByLabelText('Miktar')).toHaveLength(2));

  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/work-orders',expect.objectContaining({
   parts:[expect.objectContaining({product_id:12,warehouse_id:2,quantity:'1',unit_price:'250'})],
  })));
  expect(post).toHaveBeenCalledTimes(1);
 });

 // Canlıda yakalanan hata: parça İŞ EMRİNE ALIŞ fiyatıyla giriyordu; faturada
 // müşteriye maliyet yansıyor, kâr marjı kalkıyordu. Parça artık SATIŞ
 // fiyatından gelmeli, kullanıcı override edebilmeli ve toplamlar görünmeli.
 it('parça birim fiyatını satış fiyatından alır, override eder ve toplamları hesaplar',async()=>{
  const get=api.get as unknown as ReturnType<typeof vi.fn>;
  const post=api.post as unknown as ReturnType<typeof vi.fn>;
  get.mockImplementation((url:string)=>{
   if(url==='/machines')return Promise.resolve({data:[{id:5,brand:'Sungur',model:'X',customer_id:9}]});
   if(url==='/customers')return Promise.resolve({data:[{id:9,name:'Müşteri A'}]});
   if(url==='/work-orders/technicians')return Promise.resolve({data:[{id:3,display_name:'Tekniker'}]});
   if(url==='/warehouses')return Promise.resolve({data:[{id:2,name:'Servis Deposu'}]});
   if(url==='/products')return Promise.resolve({data:{items:[{id:12,name:'Filtre',product_code:'F-1',sale_price:250,purchase_price:10,warehouse_stock:5,available_stock:3,unit:'adet'}],has_more:false}});
   return Promise.resolve({data:[]});
  });
  post.mockResolvedValue({data:{id:77,work_order_no:'ISE-77'}});

  render(<ThemeProvider theme={createTheme()}><WorkOrderDialog open onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);
  fireEvent.mouseDown(await screen.findByRole('combobox',{name:/Makine/}));
  fireEvent.click(await screen.findByText(/Sungur X/));
  fireEvent.mouseDown(screen.getByRole('combobox',{name:/Teknisyen/}));
  fireEvent.click(await screen.findByText('Tekniker'));
  fireEvent.mouseDown(await screen.findByRole('combobox',{name:/Parça ara/}));
  fireEvent.click(await screen.findByText(/kullanılabilir 3 adet/));

  // Birim fiyat SATIŞ fiyatından (250) gelir — alış fiyatından (10) DEĞİL.
  const priceInput=(await screen.findAllByLabelText('Birim Fiyat (₺)'))[0] as HTMLInputElement;
  await waitFor(()=>expect(priceInput.value).toBe('250'));
  expect(screen.getByText('Parça Ara Toplamı:',{exact:false}).textContent).toContain('250.00');

  // Override: kullanıcı birim fiyatı 300 yapınca satır ve ara toplam güncellenir.
  fireEvent.change(priceInput,{target:{value:'300'}});
  await waitFor(()=>expect(screen.getByText('Parça Ara Toplamı:',{exact:false}).textContent).toContain('300.00'));
  expect(screen.getByText('Genel Toplam:',{exact:false}).textContent).toContain('300.00');

  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/work-orders',expect.objectContaining({
   // Decimal-STRING sözleşmesi: para/miktar alanları string gider (float değil).
   parts:[expect.objectContaining({product_id:12,unit_price:'300',quantity:'1',discount:'0',tax_rate:'0'})],
  })));
 });

 // Satış fiyatı tanımsız ürün: birim fiyat 0 yazılır, satırda uyarı çıkar VE
 // kayıt engellenir (0 TL parça faturalanamaz — gelir kaybı riski). Fiyat
 // girilince kayıt tekrar açılır; bu da 0 fiyatın tek engel olduğunu kanıtlar.
 it('0 fiyatlı parçayla kaydetmeyi engeller, fiyat girilince açılır',async()=>{
  const get=api.get as unknown as ReturnType<typeof vi.fn>;
  get.mockImplementation((url:string)=>{
   if(url==='/machines')return Promise.resolve({data:[{id:5,brand:'Sungur',model:'X',customer_id:9}]});
   if(url==='/work-orders/technicians')return Promise.resolve({data:[{id:3,display_name:'Tekniker'}]});
   if(url==='/warehouses')return Promise.resolve({data:[{id:2,name:'Servis Deposu'}]});
   if(url==='/products')return Promise.resolve({data:{items:[{id:99,name:'Conta',product_code:'C-1',purchase_price:40,warehouse_stock:5,available_stock:3,unit:'adet'}],has_more:false}});
   return Promise.resolve({data:[]});
  });

  render(<ThemeProvider theme={createTheme()}><WorkOrderDialog open onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);
  fireEvent.mouseDown(await screen.findByRole('combobox',{name:/Makine/}));
  fireEvent.click(await screen.findByText(/Sungur X/));
  fireEvent.mouseDown(screen.getByRole('combobox',{name:/Teknisyen/}));
  fireEvent.click(await screen.findByText('Tekniker'));
  fireEvent.mouseDown(await screen.findByRole('combobox',{name:/Parça ara/}));
  fireEvent.click(await screen.findByText(/kullanılabilir 3 adet/));

  const priceInput=(await screen.findAllByLabelText('Birim Fiyat (₺)'))[0] as HTMLInputElement;
  await waitFor(()=>expect(priceInput.value).toBe('0'));
  expect(await screen.findByText('Satış fiyatı tanımsız — birim fiyatı elle girin.')).toBeTruthy();
  // Diğer zorunlu alanlar dolu; tek engel 0 birim fiyat → Kaydet devre dışı.
  expect(await screen.findByText('Birim fiyatı 0 olan parça kaydedilemez; satış fiyatını girin.')).toBeTruthy();
  expect(screen.getByRole('button',{name:'Kaydet'})).toBeDisabled();

  fireEvent.change(priceInput,{target:{value:'175'}});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Kaydet'})).not.toBeDisabled());
 });

 it('ürün cevabı has_more ise eksik liste uyarısını gösterir',async()=>{
  const get=api.get as unknown as ReturnType<typeof vi.fn>;
  get.mockImplementation((url:string)=>{
   if(url==='/warehouses')return Promise.resolve({data:[{id:2,name:'Servis Deposu'}]});
   if(url==='/products')return Promise.resolve({data:{items:[],has_more:true}});
   return Promise.resolve({data:[]});
  });

 render(<ThemeProvider theme={createTheme()}><WorkOrderDialog open onClose={vi.fn()} onSaved={vi.fn()}/></ThemeProvider>);

  expect(await screen.findByText('Ürün listesi eksik; aramayı daraltın.')).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox',{name:/Parça ara/}),{target:{value:'filtre'}});
  await waitFor(()=>expect(get).toHaveBeenCalledWith('/products',{params:{
   limit:2000,warehouse_id:2,include_meta:true,q:'filtre',
  }}),{timeout:PART_SEARCH_DEBOUNCE_MS+1000});
 });
});
