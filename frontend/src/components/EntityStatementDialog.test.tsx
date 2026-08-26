import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import EntityStatementDialog from './EntityStatementDialog';

const get=vi.fn();
const openAuthenticated=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 errorDetail:(_:unknown,fallback:string)=>fallback,
 money:(value:any)=>`${value} ₺`,
 openAuthenticated:(...args:any[])=>openAuthenticated(...args),
}));

const statement={
 entity_type:'customer',
 entity:{id:7,name:'Ekstre Çiftçi',tax_number:'1234567890',address:null,phone:null,email:null,owner_name:null},
 date_from:'2026-07-01',
 date_to:'2026-07-31',
 opening_balance:'250.00',
 closing_balance:'430.00',
 total_debit:'300.00',
 total_credit:'120.00',
 line_count:2,
 truncated:false,
 lines:[
  {entry_date:'2026-07-05',kind:'document',label:'Satış',document_no:'EKS-002',reference_id:2,debit:'300.00',credit:'0.00',balance:'550.00'},
  {entry_date:'2026-07-10',kind:'payment',label:'Tahsilat (Nakit)',document_no:null,reference_id:5,debit:'0.00',credit:'120.00',balance:'430.00'},
 ],
};

const renderDialog=()=>render(<ThemeProvider theme={createTheme()}>
 <EntityStatementDialog open type="customer" id={7} onClose={vi.fn()}/>
</ThemeProvider>);

describe('EntityStatementDialog',()=>{
 // Vitest globals kapalı olduğu için RTL otomatik temizlik yapmaz; MUI Dialog
 // portal'ı document.body'de kalır ve sonraki testler aynı metni iki kez bulur.
 afterEach(()=>cleanup());
 beforeEach(()=>{get.mockReset();openAuthenticated.mockReset()});

 it('devir, yürüyen bakiye ve kapanışı gösterir',async()=>{
  get.mockResolvedValue({data:statement});
  renderDialog();

  expect(await screen.findByText('Ekstre Çiftçi')).toBeInTheDocument();
  expect(get).toHaveBeenCalledWith('/customers/7/statement',expect.objectContaining({
   params:expect.objectContaining({date_from:expect.any(String),date_to:expect.any(String)}),
  }));
  expect(screen.getByText('Devir (açılış bakiyesi)')).toBeInTheDocument();
  expect(screen.getByText('Satış')).toBeInTheDocument();
  expect(screen.getByText('Tahsilat (Nakit)')).toBeInTheDocument();
  // Yürüyen bakiye satır satır ilerler, kapanış son satırla aynıdır.
  expect(screen.getByText('550.00 ₺')).toBeInTheDocument();
  expect(screen.getAllByText('430.00 ₺').length).toBeGreaterThan(1);
  expect(screen.getByText('05.07.2026')).toBeInTheDocument();
 });

 it('hareketsiz dönemde boş durum gösterir',async()=>{
  get.mockResolvedValue({data:{...statement,lines:[],line_count:0,closing_balance:'250.00',total_debit:'0.00',total_credit:'0.00'}});
  renderDialog();
  expect(await screen.findByText('Bu dönemde hareket yok.')).toBeInTheDocument();
 });

 it('seçili dönemle PDF indirir',async()=>{
  get.mockResolvedValue({data:statement});
  renderDialog();
  await screen.findByText('Ekstre Çiftçi');

  fireEvent.click(screen.getByRole('button',{name:'PDF İndir / Yazdır'}));
  await waitFor(()=>expect(openAuthenticated).toHaveBeenCalledTimes(1));
  const [url,filename]=openAuthenticated.mock.calls[0];
  expect(url).toMatch(/^\/customers\/7\/statement\.pdf\?date_from=\d{4}-\d{2}-\d{2}&date_to=\d{4}-\d{2}-\d{2}$/);
  expect(filename).toMatch(/^ekstre-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}\.pdf$/);
 });

 it('hata durumunda mesaj gösterir',async()=>{
  get.mockRejectedValue(new Error('boom'));
  renderDialog();
  expect(await screen.findByText('Ekstre yüklenemedi.')).toBeInTheDocument();
 });
});
