/**
 * F10-1d düzeltme 1 — bildirim izin paneli `notifications` kapısının arkasında.
 *
 * Panel GERÇEKTİR (sahte değil): kapı kalkarsa depo/rapor rolü
 * GET /notifications/consents atar, 403 "yetkiniz yok" uyarısı ve iki
 * "İzin yok" anahtarı görür. Rol kümeleri `backend/app/auth.py` ROLE_PERMISSIONS.
 */
import React from 'react';
import {cleanup,render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import EntityDetail from './EntityDetail';

vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<Record<string,unknown>>('react-router-dom');
 return {...actual,useNavigate:()=>vi.fn(),useParams:()=>({id:'7'})};
});
const get=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...a:unknown[])=>get(...a),post:vi.fn(),put:vi.fn(),delete:vi.fn()},
 money:(v:unknown)=>`${Number(v).toFixed(2)} ₺`,
 errorDetail:(_e:unknown,fallback:string)=>fallback,
}));
let izinler:string[]=[];
vi.mock('../AuthContext',()=>({useAuth:()=>({can:(izin:string)=>izinler.includes('*')||izinler.includes(izin)})}));
vi.mock('../components/WhatsAppTarafKarti',()=>({default:()=><div data-testid="wa-karti"/>}));
vi.mock('../components/EntityDialog',()=>({default:()=>null}));
vi.mock('../components/TransactionDialog',()=>({default:()=>null}));
vi.mock('../components/EntityLedgerOverview',()=>({default:()=>null}));
vi.mock('../components/EntityStatementDialog',()=>({default:()=>null}));
vi.mock('../components/EntityDocumentList',()=>({default:()=>null}));
vi.mock('../components/SupplierPriceHistory',()=>({default:()=>null,SupplierPriceHistoryTab:()=>null}));
vi.mock('../components/CustomerMachines',()=>({default:()=>null,CustomerMachinesTab:()=>null}));

const DEPO=['read','stock','purchases','supplier_prices.view','farm.view','farm.inputs','herd.view'];
const RAPOR=['read','reports','farm.view','herd.view'];

const detay={
 entity:{id:7,name:'F10 Cari',is_active:1,phone:'05321112233'},
 summary:{current_balance:'0.00',document_total:'0.00',payment_total:'0.00',overdue_amount:'0.00',
  risk_limit:'0',risk_usage_percent:0,risk_exceeded:false,document_count:0,last_activity:null},
 documents:[],payments:[],products:[],contacts:[],tasks:[],notes:[],history:[],charge_documents:[],
};

beforeEach(()=>{
 cleanup();get.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/customers/7'||url==='/suppliers/7')return Promise.resolve({data:detay});
  if(url==='/notifications/consents'&&!izinler.includes('*')&&!izinler.includes('notifications'))
   return Promise.reject({response:{status:403,data:{detail:'Bu işlem için yetkiniz yok'}}});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

const mount=(type:'customer'|'supplier')=>render(
 <ThemeProvider theme={createTheme()}><MemoryRouter><EntityDetail type={type}/></MemoryRouter></ThemeProvider>);
const izinCagrisi=()=>get.mock.calls.filter(c=>c[0]==='/notifications/consents');

describe('F10-1d — izin paneli notifications kapısı',()=>{
 it.each([
  ['depo','supplier',DEPO],
  ['rapor','customer',RAPOR],
 ] as const)('%s rolü %s kartında paneli görmez, izin ucu çağrılmaz',async(_rol,tip,rolIzinleri)=>{
  izinler=[...rolIzinleri];
  mount(tip);
  await screen.findByTestId('wa-karti');
  await new Promise(r=>setTimeout(r,0));
  expect(screen.queryByText('Bildirim izinleri (KVKK)')).toBeNull();
  expect(screen.queryByText(/yetkiniz yok|İzin kayıtları yüklenemedi/)).toBeNull();
  expect(screen.queryByText('İzin yok')).toBeNull();
  expect(izinCagrisi()).toHaveLength(0);
 });

 it.each(['customer','supplier'] as const)('yönetici (*) %s kartında paneli görür ve izinleri okur',async tip=>{
  izinler=['*'];
  mount(tip);
  expect(await screen.findByText('Bildirim izinleri (KVKK)')).toBeTruthy();
  expect(izinCagrisi()).toHaveLength(1);
  expect(izinCagrisi()[0][1]).toEqual({params:{party_type:tip==='customer'?'CUSTOMER':'SUPPLIER',party_id:7}});
 });
});
