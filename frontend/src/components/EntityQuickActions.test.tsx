import React from 'react';
import {cleanup,render,screen,waitFor,fireEvent} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {beforeEach,describe,expect,it,vi} from 'vitest';
import EntityQuickActions from './EntityQuickActions';

const authState=vi.hoisted(()=>({canPayments:true}));
vi.mock('../AuthContext',()=>({
 useAuth:()=>({can:(permission:string)=>permission==='payments'&&authState.canPayments}),
}));
const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
const get=vi.fn();
const txnProps=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:vi.fn()},
 money:(v:any)=>`${Number(v).toFixed(2)} ₺`,
}));
vi.mock('./EntityDialog',()=>({default:()=>null}));
vi.mock('./TransactionDialog',()=>({default:(props:any)=>{txnProps(props);return null}}));

function mount(){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter><EntityQuickActions open type="customer" entity={{id:7,name:'Test Müşteri'}} onClose={vi.fn()} onChanged={vi.fn()}/></MemoryRouter></ThemeProvider>);
}

describe('EntityQuickActions',()=>{
 beforeEach(()=>{cleanup();authState.canPayments=true;navigate.mockReset();get.mockReset();txnProps.mockReset();get.mockImplementation((url:string)=>{
  if(url==='/customers/7') return Promise.resolve({data:{entity:{id:7,name:'Test Müşteri'},summary:{current_balance:1250,overdue_amount:250,last_activity:'2026-07-11',risk_limit:1000,risk_exceeded:true,risk_usage_percent:125},documents:[{id:33,document_no:'S-33',transaction_date:'2026-07-11',status:'completed',final_total:1250}]}});
  if(url==='/payments/accounts') return Promise.resolve({data:[]});
  return Promise.reject(new Error('unexpected '+url));
 })});
 it('summary içindeki bakiye, gecikme ve risk bilgisini gösterir',async()=>{
  mount();
  expect(await screen.findByText('Mevcut Bakiye: 1250.00 ₺')).toBeInTheDocument();
  expect(screen.getByText('Vadesi Geçen: 250.00 ₺')).toBeInTheDocument();
  expect(screen.getByText('Son işlem: 2026-07-11')).toBeInTheDocument();
  expect(screen.getByText('Risk %125')).toBeInTheDocument();
 });
 it('belgeye tıklanınca query string değil router state openId kullanır',async()=>{
  mount();
  fireEvent.click(await screen.findByText('S-33'));
  await waitFor(()=>expect(txnProps).toHaveBeenLastCalledWith(expect.objectContaining({open:true,kind:'sale',id:33})));
  expect(navigate).not.toHaveBeenCalledWith('/satislar',expect.anything());
 });
 it('payments izni yoksa tahsilat aksiyonunu ve diyaloğunu göstermez',async()=>{
  authState.canPayments=false;
  mount();
  await screen.findByText('Mevcut Bakiye: 1250.00 ₺');
  expect(screen.queryByRole('button',{name:'Tahsilat'})).not.toBeInTheDocument();
  expect(screen.queryByText(/Tahsilat Ekle/)).not.toBeInTheDocument();
  expect(get).not.toHaveBeenCalledWith('/payments/accounts',expect.anything());
 });
});
