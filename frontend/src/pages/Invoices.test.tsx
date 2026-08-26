import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {api} from '../api';
import Invoices from './Invoices';

vi.mock('../api',()=>({
 api:{get:vi.fn()},
 errorDetail:(_error:unknown,fallback:string)=>fallback,
 money:(value:unknown)=>`${String(value)} ₺`,
}));
vi.mock('../components/ResponsiveTable',()=>({
 default:({rows}:{rows:any[]})=><div>{rows.map(row=><div key={row.id}>{row.invoice_number} · {row.customer?.name} · {row.totals?.grand_total} · {row.einvoice_status}</div>)}</div>,
}));

describe('Invoices',()=>{
 beforeEach(()=>vi.clearAllMocks());
 afterEach(cleanup);

 it('fatura satırlarını ve e-Fatura durumunu gösterir',async()=>{
  vi.mocked(api.get).mockImplementation(async url=>{
   if(url==='/invoices')return {data:{items:[{id:7,invoice_number:'FTR-2026-7',status:'ISSUED',currency:'TRY',customer:{name:'Sungur Çiftliği'},totals:{grand_total:'1250.40'},created_at:'2026-07-31'}],page:1,pages:1,total:1}} as any;
   return {data:{einvoice_status:'SENT'}} as any;
  });
  render(<MemoryRouter><Invoices/></MemoryRouter>);
  expect(await screen.findByText(/FTR-2026-7 · Sungur Çiftliği · 1250.40 · SENT/)).toBeInTheDocument();
  expect(screen.getByText('1')).toBeInTheDocument();
 });

 it('boş durumu açıklayıcı metinle gösterir',async()=>{
  vi.mocked(api.get).mockResolvedValue({data:{items:[],page:1,pages:0,total:0}} as any);
  render(<MemoryRouter><Invoices/></MemoryRouter>);
  expect(await screen.findByText('Henüz fatura bulunmuyor.')).toBeInTheDocument();
  expect(screen.getByText('Tamamlanan bir iş emrini faturalandırdığınızda burada görünecek.')).toBeInTheDocument();
  await waitFor(()=>expect(api.get).toHaveBeenCalledWith('/invoices',{params:expect.objectContaining({page:1,page_size:25})}));
 });
});
