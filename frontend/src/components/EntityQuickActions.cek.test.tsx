/**
 * CS4 — hızlı işlem merkezinin tahsilat formunda çek/senet yöntemi.
 *
 * CS2'den sonra çıplak `POST /api/payments` çek/senet yönteminde 422'dir;
 * bu form o yöntemde CS3'ün "Yeni Çek / Senet" penceresini ön dolgulu açar.
 */
import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import EntityQuickActions from './EntityQuickActions';

const authState=vi.hoisted(()=>({canPayments:true}));
vi.mock('../AuthContext',()=>({
 useAuth:()=>({can:(izin:string)=>izin==='payments'&&authState.canPayments}),
}));
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<Record<string,unknown>>('react-router-dom');
 return {...actual,useNavigate:()=>vi.fn()};
});
const get=vi.fn();
const post=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...a:unknown[])=>get(...a),post:(...a:unknown[])=>post(...a)},
 money:(v:unknown)=>`${Number(v).toFixed(2)} ₺`,
}));
vi.mock('./EntityDialog',()=>({default:()=>null}));
vi.mock('./TransactionDialog',()=>({default:()=>null}));

const onChanged=vi.fn();
const mount=(type:'customer'|'supplier'='customer')=>render(
 <ThemeProvider theme={createTheme()}><MemoryRouter>
  <EntityQuickActions open type={type} entity={{id:7,name:type==='customer'?'Test Müşteri':'Test Tedarikçi'}} onClose={vi.fn()} onChanged={onChanged}/>
 </MemoryRouter></ThemeProvider>);

beforeEach(()=>{
 cleanup();authState.canPayments=true;get.mockReset();post.mockReset();onChanged.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/customers/7'||url==='/suppliers/7')return Promise.resolve({data:{entity:{id:7,name:'Test Müşteri'},summary:{current_balance:1250},documents:[]}});
  if(url==='/payments/accounts')return Promise.resolve({data:[]});
  return Promise.reject(new Error('unexpected '+url));
 });
 post.mockResolvedValue({data:{}});
});
afterEach(()=>cleanup());

const sec=(etiket:string,secenek:string)=>{
 fireEvent.mouseDown(screen.getByRole('combobox',{name:etiket}));
 fireEvent.click(within(screen.getByRole('listbox')).getByRole('option',{name:secenek}));
};

/** Tahsilat penceresini açıp tutar/tarih/not doldurur ve yöntemi çek yapar. */
const cekFormu=async(type:'customer'|'supplier'='customer')=>{
 mount(type);
 fireEvent.click(await screen.findByRole('button',{name:type==='customer'?'Tahsilat':'Ödeme'}));
 await screen.findByText(/Tahsilat Ekle|Ödeme Ekle/);
 fireEvent.change(screen.getByLabelText('Tutar'),{target:{value:'750'}});
 fireEvent.change(screen.getByLabelText('Tarih'),{target:{value:'2026-09-20'}});
 fireEvent.change(screen.getByLabelText('Not'),{target:{value:'Vadeli çek'}});
 sec('Ödeme Yöntemi','Çek');
};

describe('CS4 — EntityQuickActions çek/senet',()=>{
 it('Çek seçip Kaydet: POST /payments ÇAĞRILMAZ, pencere ön dolgulu açılır',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  expect(post).not.toHaveBeenCalledWith('/payments',expect.anything());
  expect(within(pencere).getByRole('combobox',{name:'Evrak Türü'})).toHaveTextContent('Çek');
  expect(within(pencere).getByRole('combobox',{name:'Yön'})).toHaveTextContent('Alınan (müşteriden)');
  expect(within(pencere).getByLabelText('Müşteri')).toHaveValue('Test Müşteri');
  expect(within(pencere).getByLabelText('Tutar')).toHaveValue(750);
  expect(within(pencere).getByLabelText('Vade')).toHaveValue('2026-09-20');
  expect(within(pencere).getByLabelText('Not')).toHaveValue('Vadeli çek');
 });

 it('tedarikçi kolunda yön VERİLEN gelir',async()=>{
  await cekFormu('supplier');
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  expect(within(pencere).getByRole('combobox',{name:'Yön'})).toHaveTextContent('Verilen (tedarikçiye)');
  expect(within(pencere).getByLabelText('Tedarikçi')).toHaveValue('Test Tedarikçi');
 });

 it('kaydedilen evrak `payment_olustur:true` gider ve cari yeniden yüklenir',async()=>{
  await cekFormu();
  fireEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  const pencere=await screen.findByRole('dialog',{name:'Yeni Çek / Senet'});
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:'B-7'}});
  const onceki=get.mock.calls.filter(([u])=>u==='/customers/7').length;
  post.mockResolvedValueOnce({data:{id:12}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/cek-senetler',{
   tur:'cek',yon:'alinan',tutar:'750',vade:'2026-09-20',seri_no:'B-7',customer_id:7,
   notlar:'Vadeli çek',payment_olustur:true,odeme_tarihi:'2026-09-20',
  }));
  expect(post).not.toHaveBeenCalledWith('/payments',expect.anything());
  await waitFor(()=>expect(get.mock.calls.filter(([u])=>u==='/customers/7').length).toBeGreaterThan(onceki));
  expect(onChanged).toHaveBeenCalled();
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
  fireEvent.change(within(pencere).getByLabelText('Seri No'),{target:{value:'B-7'}});
  const metin='Çek/senet ile ödemede evrak bilgisi (cek_senet: vade, seri_no) zorunludur';
  post.mockRejectedValueOnce({response:{status:422,data:{detail:metin}}});
  fireEvent.click(within(pencere).getByRole('button',{name:'Kaydet'}));
  expect(await within(pencere).findByText(metin)).toBeInTheDocument();
 });

 it('`payments` taşımayan rol çek/senet seçeneğini HİÇ göremez',async()=>{
  authState.canPayments=false;
  mount();
  await screen.findByText('Mevcut Bakiye: 1250.00 ₺');
  // Tahsilat penceresinin KENDİSİ bu rolde yok; seçenek de, evrak penceresi de
  // hiç çizilmiyor.
  expect(screen.queryByRole('button',{name:'Tahsilat'})).toBeNull();
  expect(screen.queryByRole('combobox',{name:'Ödeme Yöntemi'})).toBeNull();
  expect(screen.queryByRole('dialog',{name:'Yeni Çek / Senet'})).toBeNull();
 });
});
