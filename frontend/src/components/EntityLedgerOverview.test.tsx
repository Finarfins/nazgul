import React from 'react';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import EntityLedgerOverview from './EntityLedgerOverview';

const get=vi.fn();
const openAuthenticated=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args)},
 errorDetail:(_:unknown,fallback:string)=>fallback,
 money:(value:any)=>`${value} ₺`,
 openAuthenticated:(...args:any[])=>openAuthenticated(...args),
}));

describe('EntityLedgerOverview',()=>{
 beforeEach(()=>{get.mockReset();openAuthenticated.mockReset()});

 it('satışı genişletip ürün kalemlerini gösterir ve PDF yazdırır',async()=>{
  const onOpenProduct=vi.fn();
  get.mockResolvedValue({data:{items:[{id:9,product_id:21,product_name:'Filtre',quantity:'2.000',unit_price:'50.00',line_total:'100.00'}]}});
  render(<ThemeProvider theme={createTheme()}><EntityLedgerOverview
   type="customer"
   documents={[{id:12,document_no:'S-12',transaction_date:'2026-07-24',final_total:'100.00'}]}
   payments={[]}
   onOpenDocument={vi.fn()}
   onOpenProduct={onOpenProduct}
  /></ThemeProvider>);

  fireEvent.click(screen.getByText('S-12'));
  expect(await screen.findByText('Filtre')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/Filtre/}));
  expect(onOpenProduct).toHaveBeenCalledWith(21);
  expect(get).toHaveBeenCalledWith('/orders/12');
  fireEvent.click(screen.getByRole('button',{name:'Yazdır'}));
  await waitFor(()=>expect(openAuthenticated).toHaveBeenCalledWith('/documents/orders/12/pdf'));
 });

 it('servis ve gecikme bedellerini aynı bölümde listeler',()=>{
  render(<ThemeProvider theme={createTheme()}><EntityLedgerOverview
   type="customer"
   documents={[]}
   payments={[]}
   chargeDocuments={[{id:7,charge_type:'service_fee',document_no:'SE-7-R1',due_date:'2026-07-30',applied:'20.00',remaining:'80.00'}]}
   onOpenDocument={vi.fn()}
  /></ThemeProvider>);

  expect(screen.getByText('Servis / Gecikme Bedelleri')).toBeInTheDocument();
  expect(screen.getByText('SE-7-R1')).toBeInTheDocument();
  expect(screen.getByText('Vade 2026-07-30')).toBeInTheDocument();
  expect(screen.getByText('80.00 ₺')).toBeInTheDocument();
 });
});
