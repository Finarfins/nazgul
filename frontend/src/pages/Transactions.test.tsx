import React from 'react';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {expect,it,vi} from 'vitest';
import Transactions from './Transactions';

const get=vi.fn();
vi.mock('../api',()=>({api:{get:(...args:any[])=>get(...args),delete:vi.fn(),post:vi.fn()},money:(v:any)=>String(v),openAuthenticated:vi.fn(),policyOverrideHeaders:(reason:string)=>({'X-Policy-Override':'true','X-Policy-Override-Reason':reason}),policyOverrideRequired:()=>false}));
vi.mock('../AuthContext',()=>({useAuth:()=>({user:{role:'admin'}})}));
const dialogProps=vi.fn();
vi.mock('../components/TransactionDialog',()=>({default:(props:any)=>{dialogProps(props);return null}}));
const tableProps=vi.fn();
vi.mock('../components/ResponsiveTable',()=>({default:(props:any)=>{tableProps(props);return null}}));

it('kart görünümü için Düzenle ve Sil aksiyonlarını lg kırılımıyla geçirir',async()=>{
 tableProps.mockReset();
 const row={id:7,document_no:'S-7',customer_name:'Kart Cari',transaction_date:'2026-07-11',status:'completed',final_total:120,remaining_amount:0};
 get.mockImplementation((url:string)=>Promise.resolve({data:url==='/orders'?[row]:[]}));
 render(<ThemeProvider theme={createTheme()}><MemoryRouter><Transactions kind="sale"/></MemoryRouter></ThemeProvider>);
 await waitFor(()=>expect(tableProps).toHaveBeenCalled());
 const props=tableProps.mock.calls[tableProps.mock.calls.length-1][0];
 // Tablet genişliğinde de kart görünümü kullanılmalı.
 expect(props.cardBreakpoint).toBe('lg');
 expect(props.cardActions.map((a:any)=>a.label)).toEqual(['Düzenle','Sil']);
 // Silme aksiyonu yalnızca o satır silinirken kilitlenir.
 expect(props.cardActions[1].disabled(row)).toBe(false);
});

it('detay modalındaki müşteri bağlantısı modalı kapatıp cari sayfasına gider',async()=>{
 get.mockImplementation((url:string)=>{
  if(url==='/orders/42') return Promise.resolve({data:{document:{id:42,document_no:'S-42',customer_id:9,customer_name:'Test Cari',transaction_date:'2026-07-11',status:'completed',final_total:120},items:[]}});
  if(url==='/orders') return Promise.resolve({data:[]});
  return Promise.resolve({data:[]});
 });
 render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[{pathname:'/satislar',state:{openId:42}}]}><Routes><Route path="/satislar" element={<Transactions kind="sale"/>}/><Route path="/musteriler/:id" element={<div>Cari sayfası</div>}/></Routes></MemoryRouter></ThemeProvider>);
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/orders/42'));
 expect(await screen.findByText('Satış S-42')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Test Cari'}));
 expect(await screen.findByText('Cari sayfası')).toBeInTheDocument();
 expect(screen.queryByText('Satış S-42')).toBeNull();
});

it('satış listesinin Müşteri kolonu cari bağlantısı üretir',async()=>{
 tableProps.mockReset();
 const row={id:7,entity_id:9,customer_name:'Liste Cari'};
 get.mockImplementation((url:string)=>Promise.resolve({data:url==='/orders'?[row]:[]}));
 render(<ThemeProvider theme={createTheme()}><MemoryRouter><Transactions kind="sale"/></MemoryRouter></ThemeProvider>);
 await waitFor(()=>expect(tableProps).toHaveBeenCalled());
 const props=tableProps.mock.calls[tableProps.mock.calls.length-1][0];
 const column=props.columns.find((item:any)=>item.headerName==='Müşteri');
 const cell=column.renderCell({row,value:row.customer_name});
 render(<MemoryRouter>{cell}</MemoryRouter>);
 expect(screen.getByRole('link',{name:'Liste Cari'})).toHaveAttribute('href','/musteriler/9');
});

it('API ISO vade tarihini detayda GG.AA.YYYY biçiminde gösterir',async()=>{
 get.mockImplementation((url:string)=>{
  if(url==='/orders/43') return Promise.resolve({data:{document:{id:43,document_no:'S-43',customer_name:'Vadeli Cari',transaction_date:'2026-07-11',due_date:'2026-12-31',status:'completed',final_total:120},items:[]}});
  if(url==='/orders') return Promise.resolve({data:[]});
  return Promise.resolve({data:[]});
 });
 render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[{pathname:'/satislar',state:{openId:43}}]}><Routes><Route path="/satislar" element={<Transactions kind="sale"/>}/></Routes></MemoryRouter></ThemeProvider>);
 expect(await screen.findByText(/Vade: 31\.12\.2026/)).toBeInTheDocument();
});

it('komut paleti / dashboard ?new=1 parametresiyle satış formunu doğrudan açar',async()=>{
 dialogProps.mockReset();
 get.mockImplementation(()=>Promise.resolve({data:[]}));
 render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={['/satislar?new=1']}><Transactions kind="sale"/></MemoryRouter></ThemeProvider>);
 await waitFor(()=>{
  const props=dialogProps.mock.calls[dialogProps.mock.calls.length-1][0];
  expect(props.open).toBe(true);
  expect(props.kind).toBe('sale');
  expect(props.id).toBeNull();
 });
});

it('alış ekranında da ?new=1 parametresini karşılar',async()=>{
 dialogProps.mockReset();
 get.mockImplementation(()=>Promise.resolve({data:[]}));
 render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={['/alislar?new=1']}><Transactions kind="purchase"/></MemoryRouter></ThemeProvider>);
 await waitFor(()=>{
  const props=dialogProps.mock.calls[dialogProps.mock.calls.length-1][0];
  expect(props.open).toBe(true);
  expect(props.kind).toBe('purchase');
 });
});

it('parametresiz açılışta belge formunu kendiliğinden açmaz',async()=>{
 dialogProps.mockReset();
 get.mockImplementation(()=>Promise.resolve({data:[]}));
 render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={['/satislar']}><Transactions kind="sale"/></MemoryRouter></ThemeProvider>);
 await waitFor(()=>expect(dialogProps).toHaveBeenCalled());
 const props=dialogProps.mock.calls[dialogProps.mock.calls.length-1][0];
 expect(props.open).toBe(false);
});

it('dashboard ?status=overdue kısayolunu açık bakiyeli belge filtresine çevirir',async()=>{
 tableProps.mockReset();
 const rows=[
  {id:1,document_no:'S-1',customer_name:'Açık Bakiye',transaction_date:'2026-07-11',status:'completed',final_total:100,remaining_amount:40},
  {id:2,document_no:'S-2',customer_name:'Kapalı Belge',transaction_date:'2026-07-11',status:'completed',final_total:100,remaining_amount:0},
  {id:3,document_no:'S-3',customer_name:'İptal Belge',transaction_date:'2026-07-11',status:'cancelled',final_total:100,remaining_amount:90},
 ];
 get.mockImplementation((url:string)=>Promise.resolve({data:url==='/orders'?rows:[]}));
 render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={['/satislar?status=overdue']}><Transactions kind="sale"/></MemoryRouter></ThemeProvider>);
 // Overdue sunucu tarafinda bir durum degeri degildir; status parametresi API'ye gonderilmez.
 await waitFor(()=>expect(get).toHaveBeenCalledWith('/orders',{params:{q:'',status:undefined,date_from:undefined,date_to:undefined,sort:'date_desc'}}));
 await waitFor(()=>{
  const props=tableProps.mock.calls[tableProps.mock.calls.length-1][0];
  expect(props.rows.map((row:any)=>row.id)).toEqual([1]);
 });
});
