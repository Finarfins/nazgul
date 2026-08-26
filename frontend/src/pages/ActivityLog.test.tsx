import {cleanup,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import ActivityLog from './ActivityLog';

const get=vi.fn();
const post=vi.fn();
let role='admin';

vi.mock('../api',()=>({
 api:{
  get:(...args:unknown[])=>get(...args),
  post:(...args:unknown[])=>post(...args),
 },
 errorDetail:(error:unknown,fallback:string)=>{
  const detail=(error as {response?:{data?:{detail?:string}}})?.response?.data?.detail;
  return detail||fallback;
 },
}));
vi.mock('../AuthContext',()=>({
 useAuth:()=>({user:{id:1,username:'admin',display_name:'Ana Yönetici',role}}),
}));

const CATALOG={
 action_types:[
  {value:'sale.cancel',label:'Satış iptali'},
  {value:'payment.delete',label:'Ödeme silme'},
 ],
 resource_types:['sale','payment'],
 max_limit:100,
};
const USERS=[
 {id:1,username:'admin',display_name:'Ana Yönetici',role:'admin'},
 {id:5,username:'satisci',display_name:'Satış Görevlisi X',role:'satis'},
];
const ROWS=[
 {
  id:31,company_id:1,user_id:5,action_type:'sale.cancel',action_label:'Satış iptali',
  resource_type:'sale',resource_id:123,
  summary:'ORD-123 satışını iptal etti — 4.500,00 TL, müşteri: Ahmet Y.',
  details:{cancelled:{document_no:'ORD-123',final_total:'4500.00'}},
  correlation_id:'abc123',created_at:'2026-07-25T09:30:00Z',
  archived_at:null,archived_by:null,archive_reason:null,is_archived:false,
 },
 {
  id:30,company_id:1,user_id:5,action_type:'payment.delete',action_label:'Ödeme silme',
  resource_type:'payment',resource_id:77,
  summary:'#77 numaralı tahsilatı sildi — 250,50 TL, cari: Ahmet Y.',
  details:{changes:{amount:{old:'250.50',new:null}}},
  correlation_id:'def456',created_at:'2026-07-25T08:00:00Z',
  archived_at:null,archived_by:null,archive_reason:null,is_archived:false,
 },
];

const respond=(rows=ROWS,total=rows.length)=>{
 get.mockImplementation((url:string)=>{
  if(url==='/activity-logs/catalog')return Promise.resolve({data:CATALOG});
  if(url==='/users')return Promise.resolve({data:USERS});
  if(url==='/activity-logs')return Promise.resolve({data:{items:rows,total,limit:50,offset:0}});
  return Promise.reject(new Error(`beklenmeyen istek: ${url}`));
 });
};

const view=()=>render(
 <ThemeProvider theme={createTheme()}>
  <MemoryRouter><ActivityLog/></MemoryRouter>
 </ThemeProvider>,
);

describe('Aktivite paneli',()=>{
 beforeEach(()=>{
  role='admin';
  get.mockReset();post.mockReset();
  respond();
 });
 afterEach(cleanup);

 it('sunucudan gelen Türkçe özeti ve kullanıcıyı listeler',async()=>{
  view();
  expect(await screen.findByText(/ORD-123 satışını iptal etti/)).toBeInTheDocument();
  expect(screen.getAllByText('Satış Görevlisi X').length).toBeGreaterThan(0);
  // Tutar sunucuda biçimlenmiştir; istemci Number()'a çevirmez.
  expect(screen.getByText(/4\.500,00 TL/)).toBeInTheDocument();
  expect(screen.getByText(/250,50 TL/)).toBeInTheDocument();
 });

 it('filtreler sunucu tarafı sorguya çevrilir ve sayfa başa döner',async()=>{
  view();
  await screen.findByText(/ORD-123/);
  await userEvent.click(screen.getByLabelText('İşlem tipi'));
  await userEvent.click(await screen.findByRole('option',{name:'Ödeme silme'}));
  await waitFor(()=>{
   const calls=get.mock.calls.filter(call=>call[0]==='/activity-logs');
   const last=calls[calls.length-1][1] as {params:Record<string,unknown>};
   expect(last.params.action_type).toBe('payment.delete');
   expect(last.params.offset).toBe(0);
   expect(last.params.limit).toBe(50);
  });
 });

 it('detay çekmecesi eski/yeni değerleri ham metin olarak gösterir',async()=>{
  view();
  await userEvent.click(await screen.findByText(/#77 numaralı tahsilatı sildi/));
  const drawer=await screen.findByRole('presentation');
  expect(within(drawer).getByText('Değişen alanlar')).toBeInTheDocument();
  expect(within(drawer).getByText('amount')).toBeInTheDocument();
  expect(within(drawer).getByText('250.50')).toBeInTheDocument();
  expect(within(drawer).getByText('def456',{exact:false})).toBeInTheDocument();
 });

 it('admin gerekçeyle arşivler; gerekçe kısa ise buton kapalıdır',async()=>{
  post.mockResolvedValue({data:{}});
  view();
  await screen.findByText(/ORD-123/);
  const archiveAction=screen.getByRole('button',{name:`${ROWS[0].summary} — Arşivle`});
  await userEvent.click(archiveAction);
  const confirm=await screen.findByRole('button',{name:'Arşivle'});
  expect(confirm).toBeDisabled();
  await userEvent.type(screen.getByLabelText('Gerekçe'),'mükerrer kayıt');
  await waitFor(()=>expect(confirm).toBeEnabled());
  await userEvent.click(confirm);
  await waitFor(()=>expect(post).toHaveBeenCalledWith(
   '/activity-logs/31/archive',{reason:'mükerrer kayıt'},
  ));
 });

 it('yönetici arşiv yüzeyini hiç görmez',async()=>{
  role='yonetici';
  view();
  await screen.findByText(/ORD-123/);
  expect(screen.queryByLabelText('Arşivlenenleri göster')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/ — Arşivle$/})).not.toBeInTheDocument();
 });

 it('boş sonuç ve hata durumları ayrı ayrı ele alınır',async()=>{
  respond([],0);
  view();
  expect(await screen.findByText('Seçilen filtrelerde aktivite kaydı yok.')).toBeInTheDocument();

  cleanup();
  get.mockImplementation((url:string)=>{
   if(url==='/activity-logs/catalog')return Promise.resolve({data:CATALOG});
   if(url==='/users')return Promise.resolve({data:USERS});
   return Promise.reject({response:{data:{detail:'Aktivite kayıtlarını görüntüleme yetkiniz yok'}}});
  });
  view();
  expect(await screen.findByText('Aktivite kayıtlarını görüntüleme yetkiniz yok')).toBeInTheDocument();
 });
});
