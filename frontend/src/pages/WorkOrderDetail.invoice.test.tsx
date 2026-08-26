import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {api} from '../api';
import WorkOrderDetail from './WorkOrderDetail';

let salesPermission=true;
vi.mock('../AuthContext',()=>({useAuth:()=>({can:(permission:string)=>permission==='sales'&&salesPermission,user:{role:'admin'}})}));
vi.mock('../api',()=>({
 api:{get:vi.fn(),post:vi.fn(),patch:vi.fn(),delete:vi.fn(),put:vi.fn()},
 errorDetail:(error:any,fallback:string)=>error?.response?.data?.detail||fallback,
 money:(value:unknown)=>`${String(value)} ₺`,
 openAuthenticated:vi.fn(),
}));

const workOrder=(status:string)=>({
 id:42,work_order_no:'IE-42',status,priority:'NORMAL',customer_id:5,customer_name:'Bereket Tarım',
 machine_id:3,machine_brand:'Sungur',machine_model:'X',technician_name:'Ali',opened_at:'2026-07-30',
 actual_hours:'2',labor_rate:'100',warranty_type:'NONE',warranty_percent:'0',
});
const summary={
 customer:{id:5,name:'Bereket Tarım'},totals:{labor:'200.00',parts:'50.00',tax:'5.00',discount:'0.00',grand_total:'250.00',labor_source:'header'},
 warranty:{type:'NONE',coverage_percent:'0',customer_amount:'250.00',warranty_amount:'0.00'},
};
const renderPage=()=>render(<MemoryRouter initialEntries={['/is-emirleri/42']}><Routes><Route path="/is-emirleri/:id" element={<WorkOrderDetail/>}/><Route path="/faturalar/:id" element={<div>Fatura detayına gidildi</div>}/></Routes></MemoryRouter>);

describe('WorkOrderDetail faturalandırma',()=>{
 beforeEach(()=>{vi.clearAllMocks();salesPermission=true});
 afterEach(cleanup);

 const mockLoads=(status:string)=>{
  vi.mocked(api.get).mockImplementation(async url=>{
   if(url==='/work-orders/42')return {data:workOrder(status)} as any;
   if(url==='/work-orders/42/invoice')return status==='COMPLETED'?{data:summary} as any:Promise.reject({response:{status:409}});
   if(String(url).includes('/parts'))return {data:{items:[]}} as any;
   if(String(url).includes('/labor-lines'))return {data:{items:[]}} as any;
   return {data:[]} as any;
  });
 };

 it.each([
  ['OPEN',true,false],
  ['COMPLETED',false,false],
  ['COMPLETED',true,true],
 ] as const)('%s durumunda sales=%s için buton görünürlüğü %s',async(status,allowed,visible)=>{
  salesPermission=allowed;mockLoads(status);renderPage();
  await screen.findByText('IE-42');
  await waitFor(()=>expect(Boolean(screen.queryByRole('button',{name:'Faturalandır'}))).toBe(visible));
  if(status==='OPEN')expect(screen.getByText(/İş emri faturalandırmaya hazır değil/)).toBeInTheDocument();
 });

 it('onaydan sonra generate çağrısı yapar ve detaya yönlendirir',async()=>{
  mockLoads('COMPLETED');
  vi.mocked(api.post).mockResolvedValue({data:{id:91}} as any);
  renderPage();
  fireEvent.click(await screen.findByRole('button',{name:'Faturalandır'}));
  expect(screen.getAllByText('Bereket Tarım')).toHaveLength(2);
  expect(screen.getAllByText('250.00 ₺').length).toBeGreaterThanOrEqual(3);
  fireEvent.click(screen.getByRole('button',{name:'Faturayı Oluştur'}));
  await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/invoices/generate',{work_order_id:42}));
  expect(await screen.findByText('Fatura detayına gidildi')).toBeInTheDocument();
 });

 it('duplicate 409 durumunda mevcut faturaya yönlendirir',async()=>{
  mockLoads('COMPLETED');
  vi.mocked(api.post).mockRejectedValue({response:{status:409,data:{detail:'Bu iş emri için fatura zaten oluşturulmuş.'}}});
  vi.mocked(api.get).mockImplementation(async url=>{
   if(url==='/work-orders/42')return {data:workOrder('COMPLETED')} as any;
   if(url==='/work-orders/42/invoice')return {data:summary} as any;
   if(url==='/invoices')return {data:{items:[{id:77}],pages:1}} as any;
   if(url==='/invoices/77')return {data:{id:77,work_order_id:42}} as any;
   if(String(url).includes('/parts')||String(url).includes('/labor-lines'))return {data:{items:[]}} as any;
   return {data:[]} as any;
  });
  renderPage();
  fireEvent.click(await screen.findByRole('button',{name:'Faturalandır'}));
  fireEvent.click(screen.getByRole('button',{name:'Faturayı Oluştur'}));
  expect(await screen.findByText('Fatura detayına gidildi')).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith('/invoices',{params:{page:1,page_size:200}});
 });
});
