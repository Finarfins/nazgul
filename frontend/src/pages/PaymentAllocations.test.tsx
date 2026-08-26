import {cleanup,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import PaymentAllocations from './PaymentAllocations';

const get=vi.fn();
const post=vi.fn();
let finance=true;

vi.mock('../api',()=>({
 api:{
  get:(...args:unknown[])=>get(...args),
  post:(...args:unknown[])=>post(...args),
 },
 money:(value:unknown)=>`${value} TL`,
 errorDetail:(error:unknown,fallback:string)=>{
  const detail=(error as {response?:{data?:{detail?:string}}})?.response?.data?.detail;
  return detail||fallback;
 },
}));
vi.mock('../AuthContext',()=>({
 useAuth:()=>({can:(permission:string)=>permission==='read'||(permission==='finance'&&finance)}),
}));

const ALLOCATIONS=[
 {
  id:31,payment_id:11,order_id:101,amount:'60.00',allocation_type:'manual',
  effective_date:'2026-07-16',recorded_at:'2026-07-16T10:00:00Z',created_by:1,
  reversed_at:null,reversed_by:null,reversal_allocation_id:null,
  reversal_of_allocation_id:null,note:'İlk tahsis',net_amount:'40.00',status:'partial',
 },
 {
  id:32,payment_id:11,order_id:101,amount:'20.00',allocation_type:'manual',
  effective_date:'2026-07-17',recorded_at:'2026-07-17T10:00:00Z',created_by:1,
  reversed_at:null,reversed_by:null,reversal_allocation_id:null,
  reversal_of_allocation_id:31,note:'Kısmi geri alma',net_amount:'-20.00',status:'reversal',
 },
 {
  id:33,payment_id:11,order_id:101,amount:'15.00',allocation_type:'manual',
  effective_date:'2026-07-18',recorded_at:'2026-07-18T10:00:00Z',created_by:1,
  reversed_at:'2026-07-19T10:00:00Z',reversed_by:1,reversal_allocation_id:34,
  reversal_of_allocation_id:null,note:'Tam geri alınmış',net_amount:'0.00',status:'reversed',
 },
];
const RECONCILIATION=[
 {
  company_id:1,order_id:101,customer_id:7,document_no:'S-101',
  stored_paid_amount:'0.00',linked_payment_total:'0.00',
  unlinked_customer_payment_total:'100.00',linked_return_total:'0.00',
  unlinked_customer_return_total:'0.00',active_allocation_total:'40.00',
  frozen_residual_amount:'0.00',document_allocation_excess:'0.00',
  payment_allocation_excess:'0.00',candidate_residual:'0.00',
  evidence_source:'linked_payment_match',confidence:'high',status:'confirmed',
  reason:'Stored paid_amount bağlı ödeme toplamıyla eşleşiyor',
 },
 {
  company_id:1,order_id:102,customer_id:8,document_no:'S-102',
  stored_paid_amount:'50.00',linked_payment_total:'0.00',
  unlinked_customer_payment_total:'0.00',linked_return_total:'0.00',
  unlinked_customer_return_total:'0.00',active_allocation_total:'0.00',
  frozen_residual_amount:'0.00',document_allocation_excess:'0.00',
  payment_allocation_excess:'0.00',candidate_residual:'50.00',
  evidence_source:'unexplained_paid_amount',confidence:'unknown',status:'quarantined',
  reason:'Ödeme hareketiyle açıklanamayan paid_amount inceleme gerektiriyor',
 },
];
const PAYMENTS=[
 {
  id:11,entity_type:'customer',entity_id:7,entity_name:'Bereket Çiftliği',
  amount:'100.00',payment_date:'2026-07-16',reference_type:null,reference_id:null,
 },
];
const RECEIVABLES=[
 {
  id:101,customer_id:7,customer_name:'Bereket Çiftliği',document_no:'S-101',
  due_date:'2026-08-01',total:'150.00',paid:'0.00',outstanding:'110.00',status:'ACIK',
 },
];
const RESULT={
 operation:'manual_allocation',payment_id:11,order_id:101,allocation_id:33,
 source_allocation_id:null,reversal_allocation_id:null,amount:'10.00',net_amount:'10.00',
};

function route(url:string){
 // Motor durumu salt-okuma sinyali: mevcut testler AÇIK yapılandırmayı
 // kurguluyor, dolayısıyla geçmiş görünümü bugünkü davranışını korur.
 if(url==='/payment-allocations/engine-state')return {data:{enabled:true}};
 if(url==='/payment-allocations/reconciliation')return {data:RECONCILIATION};
 if(url==='/payments')return {data:PAYMENTS};
 if(url==='/receivables')return {data:{receivables:RECEIVABLES}};
 if(url==='/payment-allocations/payments/11')return {data:ALLOCATIONS};
 throw new Error(`Beklenmeyen GET: ${url}`);
}

function mount(){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter>
  <PaymentAllocations/>
 </MemoryRouter></ThemeProvider>);
}

async function openManual(user:ReturnType<typeof userEvent.setup>){
 await user.click(await screen.findByRole('button',{name:'Manuel Tahsis'}));
 const dialog=await screen.findByRole('dialog');
 await user.click(within(dialog).getByRole('combobox',{name:'Müşteri Tahsilatı'}));
 await user.click(await screen.findByRole('option',{name:/#11.*Bereket Çiftliği/}));
 await user.click(within(dialog).getByRole('combobox',{name:'Hedef Alacak Belgesi'}));
 await user.click(await screen.findByRole('option',{name:/S-101/}));
 await user.type(within(dialog).getByRole('spinbutton',{name:'Tahsis Tutarı'}),'10.00');
 await user.type(within(dialog).getByRole('textbox',{name:'İşlem Gerekçesi'}),'Müşteri talebi');
}

beforeEach(()=>{
 finance=true;get.mockReset();post.mockReset();
 get.mockImplementation((url:string)=>Promise.resolve(route(url)));
 post.mockResolvedValue({data:RESULT});
});
afterEach(()=>cleanup());

describe('Tahsis Defteri',()=>{
 it('read rolüne geçmişi gösterir, finance işlemlerini ve reconciliation sekmesini gizler',async()=>{
  finance=false;
  const user=userEvent.setup();
  mount();
  expect(screen.queryByRole('button',{name:'Manuel Tahsis'})).toBeNull();
  expect(screen.queryByRole('tab',{name:/Mutabakat/})).toBeNull();
  expect(get).not.toHaveBeenCalledWith('/payment-allocations/reconciliation');

  await user.type(screen.getByLabelText('Ödeme No'),'11');
  await user.click(screen.getByRole('button',{name:'Geçmişi Getir'}));

  expect(await screen.findByText('Kısmen Geri Alındı')).toBeInTheDocument();
  expect(screen.getByText('Geri Alma Kaydı')).toBeInTheDocument();
  expect(screen.getByText('Kısmi geri alma')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Geri Al'})).toBeNull();
 });

 it('finance rolüne mutation kontrollerini ve salt-okunur mutabakat raporunu gösterir',async()=>{
  const user=userEvent.setup();
  mount();
  expect(await screen.findByRole('button',{name:'Manuel Tahsis'})).toBeInTheDocument();
  await user.click(screen.getByRole('tab',{name:/Mutabakat/}));
  expect(await screen.findByText('S-101')).toBeInTheDocument();
  expect(screen.getByText('S-102')).toBeInTheDocument();
  expect(screen.queryByText('2 karantina kaydı')).not.toBeInTheDocument();
  expect(screen.getByText('1 karantina kaydı')).toBeInTheDocument();
  expect(screen.getByText(/Bu rapor salt okunurdur/)).toBeInTheDocument();
 });

 it('zorunlu gerekçe ve ikinci onaydan sonra manuel tahsis yapıp sunucu toplamlarını gösterir',async()=>{
  const user=userEvent.setup();
  mount();
  await openManual(user);

  await user.click(screen.getByRole('button',{name:'Onaya Geç'}));
  expect(await screen.findByText('İşlemi Onaylayın')).toBeInTheDocument();
  expect(screen.getByText(/kalıcı kayıt ekler/)).toBeInTheDocument();
  await user.click(screen.getByRole('button',{name:'Onayla ve Uygula'}));

  await waitFor(()=>expect(post).toHaveBeenCalledTimes(1));
  const [url,payload,config]=post.mock.calls[0];
  expect(url).toBe('/payment-allocations/manual');
  expect(payload).toEqual({
   payment_id:11,order_id:101,amount:'10',effective_date:expect.any(String),
   note:'Müşteri talebi',
  });
  expect(config.headers['Idempotency-Key']).toBeTruthy();

  expect(await screen.findByText('İşlem Tamamlandı')).toBeInTheDocument();
  const dialog=within(screen.getByRole('dialog'));
  expect(dialog.getByText(/Ödeme toplamı:/)).toHaveTextContent('100.00 TL');
  expect(dialog.getByText(/Belge toplamı:/)).toHaveTextContent('150.00 TL');
  expect(dialog.getByText(/Belgenin açık kalanı:/)).toHaveTextContent('110.00 TL');
  expect(dialog.getByText(/sunucudan yeniden okunmuştur/)).toBeInTheDocument();
 });

 it('karantinadaki belgeyi hedef listesinde seçilemez yapar',async()=>{
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('button',{name:'Manuel Tahsis'}));
  const dialog=await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('combobox',{name:'Hedef Alacak Belgesi'}));
  const quarantined=await screen.findByRole('option',{name:/S-102.*KARANTİNA/});
  expect(quarantined).toHaveAttribute('aria-disabled','true');
 });

 it('flag-off 409 mesajını net gösterir ve okuma görünümünü kapatmaz',async()=>{
  const user=userEvent.setup();
  post.mockRejectedValue({
   response:{status:409,data:{detail:'Tahsis motoru devre dışı'}},
  });
  mount();
  await openManual(user);
  await user.click(screen.getByRole('button',{name:'Onaya Geç'}));
  await user.click(screen.getByRole('button',{name:'Onayla ve Uygula'}));

  expect(await screen.findAllByText(
   /Tahsis motoru devre dışı.*Okuma ve mutabakat ekranları çalışmaya devam eder/,
  )).not.toHaveLength(0);
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(screen.getByText('Tahsis Defteri')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Onayla ve Uygula'})).toBeDisabled();

  await user.click(screen.getByRole('button',{name:'Geri Dön'}));
  expect(screen.getByRole('button',{name:'Onaya Geç'})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:'Vazgeç'}));
  expect(screen.getByRole('button',{name:'Manuel Tahsis'})).toBeDisabled();

  await user.type(screen.getByLabelText('Ödeme No'),'11');
  await user.click(screen.getByRole('button',{name:'Geçmişi Getir'}));
  expect(await screen.findByText('Kısmen Geri Alındı')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Geri Al'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Yeniden Tahsis'})).toBeDisabled();
 });

 it('aktif tahsiste tam/kısmi reversal ve reallocation girişlerini açar',async()=>{
  const user=userEvent.setup();
  mount();
  await user.type(screen.getByLabelText('Ödeme No'),'11');
  await user.click(screen.getByRole('button',{name:'Geçmişi Getir'}));
  expect(await screen.findByText('Kısmen Geri Alındı')).toBeInTheDocument();
  expect(screen.getAllByRole('button',{name:'Geri Al'})).toHaveLength(1);
  expect(screen.getAllByRole('button',{name:'Yeniden Tahsis'})).toHaveLength(1);

  await user.click(screen.getByRole('button',{name:'Geri Al'}));
  expect(await screen.findByText('Tahsis Geri Alma')).toBeInTheDocument();
  expect(within(screen.getByRole('dialog')).getByRole('spinbutton',{name:'Geri Alınacak Tutar'})).toHaveValue(40);
  await user.click(screen.getByRole('button',{name:'Vazgeç'}));

  await user.click(screen.getByRole('button',{name:'Yeniden Tahsis'}));
  expect(await screen.findByRole('heading',{name:'Yeniden Tahsis'})).toBeInTheDocument();
  expect(screen.getByText(/Tahsis #31/)).toBeInTheDocument();
 });
});
