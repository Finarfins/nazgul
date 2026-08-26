import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import EntityDetail from './EntityDetail';

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

// Money and quantity arrive as strings from the API; the charge rows must cope
// with the real serialization, not a friendlier test-only shape.
const detail={
 entity:{id:7,name:'Servis Müşterisi'},
 summary:{
  current_balance:'1500.00',document_total:'1000.00',payment_total:'0.00',
  overdue_amount:'0.00',risk_limit:'0',risk_usage_percent:0,risk_exceeded:false,
  document_count:1,
 },
 documents:[],payments:[],products:[],contacts:[],tasks:[],notes:[],history:[],
 charge_documents:[
  {id:31,charge_type:'service_fee',document_no:'SE-90-R1',transaction_date:'2026-07-01',
   due_date:'2026-07-15',gross_amount:'900.00',applied:'0.00',remaining:'900.00',
   work_order_id:90},
  {id:32,charge_type:'late_fee',document_no:'VF-32-R1',transaction_date:'2026-07-01',
   due_date:'2026-07-10',gross_amount:'600.00',applied:'0.00',remaining:'600.00',
   work_order_id:null},
 ],
};

const renderDetail=async()=>{
 render(
  <ThemeProvider theme={createTheme()}>
   <MemoryRouter><EntityDetail type="customer"/></MemoryRouter>
  </ThemeProvider>,
 );
 await waitFor(()=>expect(screen.getByText('Servis Müşterisi')).toBeTruthy());
 // The charge breakdown lives under the sales/documents tab.
 fireEvent.click(screen.getByRole('tab',{name:/Satış/}));
 return await screen.findByText('SE-90-R1');
};

beforeEach(()=>{
 navigate.mockReset();
 get.mockReset();
 get.mockImplementation((url:string)=>
  url==='/customers/7'?Promise.resolve({data:detail}):Promise.resolve({data:[]}));
});
afterEach(cleanup);

it('opens the work order behind a service fee',async()=>{
 const service=await renderDetail();
 const row=service.closest('[role="button"]');
 expect(row).toBeTruthy();
 fireEvent.click(row as Element);
 // The work order id -- never the charge document id (31), which belongs to a
 // different table and would resolve to an unrelated record.
 expect(navigate).toHaveBeenCalledWith('/is-emirleri/90');
});

it('activates a service fee row from the keyboard',async()=>{
 const service=await renderDetail();
 fireEvent.keyDown(service.closest('[role="button"]') as Element,{key:'Enter'});
 expect(navigate).toHaveBeenCalledWith('/is-emirleri/90');
});

it('leaves a late fee row non-navigable',async()=>{
 await renderDetail();
 const late=await screen.findByText('VF-32-R1');
 // No work order stands behind a late fee, so the row must not offer a dead
 // click or steal a tab stop.
 expect(late.closest('[role="button"]')).toBeNull();
 fireEvent.click(late);
 expect(navigate).not.toHaveBeenCalled();
});
