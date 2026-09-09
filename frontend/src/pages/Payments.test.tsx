import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Payments from './Payments';

const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:vi.fn(),put:vi.fn(),delete:vi.fn()},
 money:(value:any)=>String(value),
}));
vi.mock('../components/ExcelImportDialog',()=>({default:()=>null}));
vi.mock('../components/ResponsiveTable',()=>({default:()=>null}));

// SEC-3 / BAYRAK A-1 — sayfa artık `can('purchases')` soruyor. İzinler
// testten TAK EDİLİYOR: gerçek `AuthProvider` bir `/api/auth/me` turu ister
// ve bu dosyanın ölçtüğü şey o değil.
let izinler:string[]=[];
vi.mock('../AuthContext',()=>({
 useAuth:()=>({can:(permission:string)=>izinler.includes(permission)}),
}));

beforeEach(()=>{
 // Varsayılan: tedarikçi kolunu görebilen bir rol (muhasebe/depo/yönetici).
 izinler=['read','purchases','payments','sales'];
 get.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/payments')return Promise.resolve({data:[]});
  if(url==='/payments/summary')return Promise.resolve({data:{customer_total:0,supplier_total:0,manual_total:0,document_total:0,movement_count:0}});
  if(url==='/payments/accounts')return Promise.resolve({data:[]});
  if(url==='/customers')return Promise.resolve({data:[{id:1,name:'Test Müşteri'}]});
  if(url==='/suppliers')return Promise.resolve({data:[{id:2,name:'Test Tedarikçi'}]});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

function mount(entry='/odemeler'){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[entry]}><Payments/></MemoryRouter></ThemeProvider>);
}

it('dashboard tahsilat kısayolunu müşteri tahsilatı formuyla açar',async()=>{
 mount('/odemeler?new=customer');
 expect(await screen.findByText('Yeni Tahsilat / Ödeme')).toBeInTheDocument();
 // Islem turu musteri tahsilati olarak on secili gelmeli; cari listesi de musterilerden yuklenmeli.
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/customers'));
 expect(screen.getByRole('combobox',{name:'İşlem Türü'})).toHaveTextContent('Müşteri Tahsilatı');
});

it('tedarikçi kısayolunu ödeme formuyla açar',async()=>{
 mount('/odemeler?new=supplier');
 expect(await screen.findByText('Yeni Tahsilat / Ödeme')).toBeInTheDocument();
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/suppliers'));
 expect(screen.getByRole('combobox',{name:'İşlem Türü'})).toHaveTextContent('Tedarikçi Ödemesi');
});

it('parametresiz açılışta formu kendiliğinden açmaz',async()=>{
 mount();
 await screen.findByRole('heading',{name:'Tahsilat / Ödeme'});
 expect(screen.queryByText('Yeni Tahsilat / Ödeme')).toBeNull();
});

it('yeni hareket düğmesi formu müşteri tahsilatı olarak açar',async()=>{
 mount();
 fireEvent.click(await screen.findByRole('button',{name:'Yeni Hareket'}));
 expect(await screen.findByText('Yeni Tahsilat / Ödeme')).toBeInTheDocument();
 expect(screen.getByRole('combobox',{name:'İşlem Türü'})).toHaveTextContent('Müşteri Tahsilatı');
});

/**
 * SEC-3 / BAYRAK A-1 — `satis` rolünün `/odemeler` sayfası.
 *
 * `/odemeler` nav izni `payments`tır ve `satis` onu TAŞIR, yani sayfa satis'e
 * AÇIKTIR ve öyle KALMALIDIR — müşteri tahsilatı satış rolünün asli işidir.
 * Ama tedarikçi seçici `GET /api/suppliers` çağırır ve o uç SEC-3'te
 * `purchases`a taşındı. Karar: `satis`e `purchases` VERİLMEDİ; tedarikçi
 * kolu sayfada `can('purchases')` arkasına alındı.
 *
 * Aşağıdaki iki test o kararın İKİ YARISINI ölçüyor: seçenek GÖRÜNMÜYOR ve
 * istek ATILMIYOR. İkincisi olmadan birincisi yeterli olmazdı — menüden
 * gizlenmiş ama URL'den (`?new=supplier`) tetiklenebilen bir çağrı sayfayı
 * yine 403'e sokardı; `/alacaklar`da bir kez yaşanan kusurun aynısı.
 */
it('SEC-3/A-1: purchases taşımayan rol tedarikçi seçeneğini GÖRMEZ',async()=>{
 izinler=['read','sales','payments','notifications']; // `satis`
 mount();
 fireEvent.click(await screen.findByRole('button',{name:'Yeni Hareket'}));
 expect(await screen.findByText('Yeni Tahsilat / Ödeme')).toBeInTheDocument();
 const secici=screen.getByRole('combobox',{name:'İşlem Türü'});
 expect(secici).toHaveTextContent('Müşteri Tahsilatı');
 fireEvent.mouseDown(secici);
 expect(screen.getByRole('option',{name:'Müşteri Tahsilatı'})).toBeInTheDocument();
 expect(screen.queryByRole('option',{name:'Tedarikçi Ödemesi'})).toBeNull();
});

it('SEC-3/A-1: ?new=supplier kısayolu purchases olmadan /suppliers ÇAĞIRMAZ',async()=>{
 izinler=['read','sales','payments','notifications']; // `satis`
 mount('/odemeler?new=supplier');
 expect(await screen.findByText('Yeni Tahsilat / Ödeme')).toBeInTheDocument();
 // Kısayol müşteri koluna DÜŞÜYOR: sayfa açılıyor, form çalışıyor, ama
 // tedarikçi ucu hiç denenmiyor.
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/customers'));
 expect(get).not.toHaveBeenCalledWith('/suppliers');
 expect(screen.getByRole('combobox',{name:'İşlem Türü'})).toHaveTextContent('Müşteri Tahsilatı');
});
