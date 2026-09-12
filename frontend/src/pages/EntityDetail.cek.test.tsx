/**
 * CS4 — cari kartının tahsilat formunda çek/senet yöntemi.
 *
 * CS2'den sonra çıplak `POST /api/payments` çek/senet yönteminde 422'dir;
 * bu form o yöntemde CS3'ün "Yeni Çek / Senet" penceresini ön dolgulu açar
 * ve evrak `payment_olustur:true` ile yazılır (ödeme satırını sunucu doğurur).
 */
import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import EntityDetail from './EntityDetail';

vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<Record<string,unknown>>('react-router-dom');
 return {...actual,useNavigate:()=>vi.fn(),useParams:()=>({id:'7'})};
});
const get=vi.fn();
const post=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...a:unknown[])=>get(...a),post:(...a:unknown[])=>post(...a),put:vi.fn(),delete:vi.fn()},
 money:(v:unknown)=>`${Number(v).toFixed(2)} ₺`,
 errorDetail:(e:unknown,fallback:string)=>{
  const detay=(e as {response?:{data?:{detail?:unknown}}})?.response?.data?.detail;
  return typeof detay==='string'&&detay?detay:fallback;
 },
}));
let izinler:string[]=[];
vi.mock('../AuthContext',()=>({useAuth:()=>({can:(izin:string)=>izinler.includes(izin)})}));
vi.mock('../components/NotificationConsentPanel',()=>({default:()=>null}));
vi.mock('../components/EntityDialog',()=>({default:()=>null}));
vi.mock('../components/TransactionDialog',()=>({default:()=>null}));
vi.mock('../components/EntityLedgerOverview',()=>({default:()=>null}));
vi.mock('../components/EntityStatementDialog',()=>({default:()=>null}));
vi.mock('../components/EntityDocumentList',()=>({default:()=>null}));
vi.mock('../components/SupplierPriceHistory',()=>({default:()=>null,SupplierPriceHistoryTab:()=>null}));
vi.mock('../components/CustomerMachines',()=>({default:()=>null,CustomerMachinesTab:()=>null}));

const detay=(ad:string)=>({
 entity:{id:7,name:ad,is_active:1},
 summary:{current_balance:'0.00',document_total:'0.00',payment_total:'0.00',overdue_amount:'0.00',
  risk_limit:'0',risk_usage_percent:0,risk_exceeded:false,document_count:0,last_activity:null},
 documents:[],payments:[],products:[],contacts:[],tasks:[],notes:[],history:[],charge_documents:[],
});

beforeEach(()=>{
 cleanup();izinler=['read','payments','purchases','sales'];get.mockReset();post.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/customers/7')return Promise.resolve({data:detay('CS4 Müşteri')});
  if(url==='/suppliers/7')return Promise.resolve({data:detay('CS4 Tedarikçi')});
  return Promise.resolve({data:[]});
 });
 post.mockResolvedValue({data:{}});
});
afterEach(()=>cleanup());

const mount=(type:'customer'|'supplier'='customer')=>render(
 <ThemeProvider theme={createTheme()}><MemoryRouter><EntityDetail type={type}/></MemoryRouter></ThemeProvider>);

const sec=(etiket:string,secenek:string)=>{
 fireEvent.mouseDown(screen.getByRole('combobox',{name:etiket}));
 fireEvent.click(within(screen.getByRole('listbox')).getByRole('option',{name:secenek}));
};

/** Tahsilat penceresini açıp tutar/tarih/not doldurur ve yöntemi çek yapar. */
const cekFormu=async(type:'customer'|'supplier'='customer')=>{
 mount(type);
 await screen.findByText(type==='customer'?'CS4 Müşteri':'CS4 Tedarikçi');
 fireEvent.click(screen.getByRole('button',{name:type==='customer'?'Tahsilat Al':'Ödeme Yap'}));
 await screen.findByRole('dialog');
 fireEvent.change(screen.getByLabelText('Tutar'),{target:{value:'2400'}});
 fireEvent.change(screen.getByLabelText('Tarih'),{target:{value:'2026-09-20'}});
 fireEvent.change(screen.getByLabelText('Not'),{target:{value:'Ekim vadesi'}});
 sec('Ödeme Yöntemi','Çek');
};

describe('CS4 — EntityDetail çek/senet',()=>{
 it('Çek seçip Kaydet: POST /payments ÇAĞRILMAZ, pencere ön dolgulu açılır',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  expect(post).not.toHaveBeenCalledWith('/payments',expect.anything());
  expect(within(pencere).getByRole('combobox',{name:'Evrak Türü'})).toHaveTextContent('Çek');
  expect(within(pencere).getByRole('combobox',{name:'Yön'})).toHaveTextContent('Alınan (müşteriden)');
  expect(within(pencere).getByLabelText('Müşteri')).toHaveValue('CS4 Müşteri');
  expect(within(pencere).getByLabelText('Tutar')).toHaveValue(2400);
  expect(within(pencere).getByLabelText('Vade')).toHaveValue('2026-09-20');
  expect(within(pencere).getByLabelText('Not')).toHaveValue('Ekim vadesi');
 });

 it('tedarikçi kartında yön VERİLEN gelir',async()=>{
  await cekFormu('supplier');
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  expect(within(pencere).getByRole('combobox',{name:'Yön'})).toHaveTextContent('Verilen (tedarikçiye)');
  expect(within(pencere).getByLabelText('Tedarikçi')).toHaveValue('CS4 Tedarikçi');
 });

 it('kaydedilen evrak `payment_olustur:true` gider ve cari yeniden yüklenir',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:'C-3'}});
  const onceki=get.mock.calls.filter(([u])=>u==='/customers/7').length;
  post.mockResolvedValueOnce({data:{id:21}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler',{
   tur:'cek',yon:'alinan',tutar:'2400',vade:'2026-09-20',seri_no:'C-3',customer_id:7,
   notlar:'Ekim vadesi',payment_olustur:true,odeme_tarihi:'2026-09-20',
  }));
  expect(post).not.toHaveBeenCalledWith('/payments',expect.anything());
  await waitFor(()=>expect(get.mock.calls.filter(([u])=>u==='/customers/7').length).toBeGreaterThan(onceki));
 });

 it('Vazgeç hiçbir şey yazmaz',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  fireEvent.click(within(pencere).getByRole('button',{name:'Vazgeç'}));
  await waitFor(()=>expect(screen.queryByRole('dialog',{name:'Yeni Çek / Senet'})).toBeNull());
  expect(post).not.toHaveBeenCalled();
 });

 it('sunucunun 422 evrak hatası pencerede görünür',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:'C-3'}});
  const metin='Çek/senet ile ödemede evrak bilgisi (cek_senet: vade, seri_no) zorunludur';
  post.mockRejectedValueOnce({response:{status:422,data:{detail:metin}}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  expect(await within(pencere).findByText(metin)).toBeInTheDocument();
 });

 it('`payments` taşımayan rol çek/senet seçeneğini HİÇ göremez',async()=>{
  izinler=['read','sales','purchases'];
  mount();
  await screen.findByText('CS4 Müşteri');
  // Tahsilat penceresi bu rolde hiç çizilmiyor; seçenek de evrak penceresi de yok.
  expect(screen.queryByRole('button',{name:'Tahsilat Al'})).toBeNull();
  expect(screen.queryByRole('combobox',{name:'Ödeme Yöntemi'})).toBeNull();
  expect(screen.queryByRole('dialog',{name:'Yeni Çek / Senet'})).toBeNull();
 });
});
