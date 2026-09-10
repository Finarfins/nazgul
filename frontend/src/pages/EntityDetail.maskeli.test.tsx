import React from 'react';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import EntityDetail from './EntityDetail';

// SEC-3b — cari kartı başlığındaki üç kısayol (tel:, wa.me, mailto:) ham değeri
// bir PROTOKOL BAĞLANTISINA çeviriyor. Maskeli rolde değer yıldızlı gelir
// (`backend/app/alan_maskeleme.py`) ve kısayol SESSİZCE YANLIŞ çalışır:
// `tel:05** *** ** 12` geçersizdir, `wa.me` rakam dışını atınca `0512` gibi
// BAŞKA bir numaraya gider, `mailto:a***@ornek.com` var olmayan adrese açar.
// `maskeli()` koruması bu kısayolları gizler; bu dosya o korumayı ölçer.
// Çalışma zamanı merceği tam bu testi yazıp silmişti: kod tabanında `maskeli`
// adına dokunan başka test YOKTU.

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate,useParams:()=>({id:'7'})};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:vi.fn(),put:vi.fn(),delete:vi.fn()},
 money:(v:any)=>`${Number(v).toFixed(2)} ₺`,
 errorDetail:(_e:any,fallback:string)=>fallback,
}));
vi.mock('../AuthContext',()=>({useAuth:()=>({can:()=>false})}));
vi.mock('../components/NotificationConsentPanel',()=>({default:()=>null}));
vi.mock('../components/EntityDialog',()=>({default:()=>null}));
vi.mock('../components/TransactionDialog',()=>({default:()=>null}));
vi.mock('../components/EntityLedgerOverview',()=>({default:()=>null}));
vi.mock('../components/EntityStatementDialog',()=>({default:()=>null}));
vi.mock('../components/SupplierPriceHistory',()=>({
 default:()=>null,
 SupplierPriceHistoryTab:()=>null,
}));
vi.mock('../components/CustomerMachines',()=>({
 default:()=>null,
 CustomerMachinesTab:()=>null,
}));

const HAM_TELEFON='05551234512';
const HAM_EPOSTA='ahmet@ornek.com';
const MASKE_TELEFON='05** *** ** 12';
const MASKE_EPOSTA='a***@ornek.com';
const TAM_MASKE='***';

const detail=(entity:Record<string,unknown>)=>({
 entity:{id:7,name:'SEC3B Müşteri',is_active:1,...entity},
 summary:{
  current_balance:'0.00',document_total:'0.00',payment_total:'0.00',
  overdue_amount:'0.00',risk_limit:'0',risk_usage_percent:0,risk_exceeded:false,
  document_count:0,last_activity:null,
 },
 documents:[],payments:[],products:[],contacts:[],tasks:[],notes:[],history:[],
 charge_documents:[],
});

const renderWith=async(entity:Record<string,unknown>)=>{
 get.mockImplementation((url:string)=>
  url==='/customers/7'?Promise.resolve({data:detail(entity)}):Promise.resolve({data:[]}));
 render(
  <ThemeProvider theme={createTheme()}>
   <MemoryRouter><EntityDetail type="customer"/></MemoryRouter>
  </ThemeProvider>,
 );
 await waitFor(()=>expect(screen.getByText('SEC3B Müşteri')).toBeTruthy());
};

// Başlıktaki üç kısayol `IconButton component="a"` — yani `href`li bağlantı.
const hrefs=()=>Array.from(document.querySelectorAll('a[href]'))
 .map(a=>a.getAttribute('href')||'');
const kisayollar=()=>({
 tel:hrefs().filter(h=>h.startsWith('tel:')),
 wa:hrefs().filter(h=>h.includes('wa.me/')),
 mailto:hrefs().filter(h=>h.startsWith('mailto:')),
});

beforeEach(()=>{navigate.mockReset();get.mockReset();});
afterEach(cleanup);

it('ham telefon ve e-posta için üç kısayol da doğru hedefle görünür',async()=>{
 await renderWith({phone:HAM_TELEFON,email:HAM_EPOSTA});
 const k=kisayollar();
 expect(k.tel).toEqual([`tel:${HAM_TELEFON}`]);
 expect(k.wa).toEqual([`https://wa.me/${HAM_TELEFON}`]);
 expect(k.mailto).toEqual([`mailto:${HAM_EPOSTA}`]);
});

it('maskeli telefonda tel: ve wa.me kısayolları gizlenir, değer metin olarak kalır',async()=>{
 await renderWith({phone:MASKE_TELEFON,email:HAM_EPOSTA});
 const k=kisayollar();
 expect(k.tel).toEqual([]);
 expect(k.wa).toEqual([]);
 // Veri kaybolmuyor: maskeli değer başlıkta hâlâ okunuyor; kalkan yalnız eylem.
 expect(screen.getByText(new RegExp(MASKE_TELEFON.replace(/\*/g,'\\*')))).toBeTruthy();
 // Maskeli telefon e-posta kısayolunu ETKİLEMEZ: koruma alan bazlıdır.
 expect(k.mailto).toEqual([`mailto:${HAM_EPOSTA}`]);
});

it('maskeli e-postada mailto: gizlenir, telefon kısayolları etkilenmez',async()=>{
 await renderWith({phone:HAM_TELEFON,email:MASKE_EPOSTA});
 const k=kisayollar();
 expect(k.mailto).toEqual([]);
 expect(k.tel).toEqual([`tel:${HAM_TELEFON}`]);
 expect(k.wa).toEqual([`https://wa.me/${HAM_TELEFON}`]);
});

it('tam maske (***) her iki alanda üç kısayolu da gizler',async()=>{
 await renderWith({phone:TAM_MASKE,email:TAM_MASKE});
 const k=kisayollar();
 expect(k.tel).toEqual([]);
 expect(k.wa).toEqual([]);
 expect(k.mailto).toEqual([]);
});

it('wa.me hedefi maskeli değerden ASLA türetilmez (0512 tuzağı)',async()=>{
 // Koruma olmasaydı `05** *** ** 12` rakam dışı atılıp `wa.me/0512` olurdu:
 // görünüşte çalışan, BAŞKA bir numaraya giden bir bağlantı.
 await renderWith({phone:MASKE_TELEFON,email:null});
 expect(hrefs().some(h=>h.includes('wa.me/0512'))).toBe(false);
 expect(kisayollar().wa).toEqual([]);
});

it('boş telefon ve e-posta kısayol üretmez ve maskeli sayılmaz',async()=>{
 await renderWith({phone:null,email:null});
 const k=kisayollar();
 expect(k.tel).toEqual([]);
 expect(k.wa).toEqual([]);
 expect(k.mailto).toEqual([]);
 expect(screen.getByText(/Telefon yok/)).toBeTruthy();
});
