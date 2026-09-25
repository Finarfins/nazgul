/**
 * F10-1d — cari kartında WhatsApp bağlantısı ve bildirim izinleri iki taraf
 * tipinde de çizilir (keşif §6.4: çiftçinin makbuz/avans yüzü tedarikçidir).
 *
 * Paneller burada SAHTEDİR: bu dosya EntityDetail'in onlara hangi taraf tipini,
 * kimliği ve telefonu verdiğini ölçer. Kartın kendi davranışı
 * `components/WhatsAppTarafKarti.test.tsx`te.
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
vi.mock('../AuthContext',()=>({useAuth:()=>({can:()=>true})}));
type PanelProps={partyType?:string;partyId:number;defaultRecipient?:string|null;defaultPhone?:string|null};
vi.mock('../components/NotificationConsentPanel',()=>({
 default:(p:PanelProps)=><div data-testid="izin-paneli">{`${p.partyType}|${p.partyId}|${p.defaultRecipient??'∅'}`}</div>,
}));
vi.mock('../components/WhatsAppTarafKarti',()=>({
 default:(p:PanelProps)=><div data-testid="wa-karti">{`${p.partyType}|${p.partyId}|${p.defaultPhone??'∅'}`}</div>,
}));
vi.mock('../components/EntityDialog',()=>({default:()=>null}));
vi.mock('../components/TransactionDialog',()=>({default:()=>null}));
vi.mock('../components/EntityLedgerOverview',()=>({default:()=>null}));
vi.mock('../components/EntityStatementDialog',()=>({default:()=>null}));
vi.mock('../components/EntityDocumentList',()=>({default:()=>null}));
vi.mock('../components/SupplierPriceHistory',()=>({default:()=>null,SupplierPriceHistoryTab:()=>null}));
vi.mock('../components/CustomerMachines',()=>({default:()=>null,CustomerMachinesTab:()=>null}));

const detay=(ad:string,phone:string|null)=>({
 entity:{id:7,name:ad,is_active:1,phone},
 summary:{current_balance:'0.00',document_total:'0.00',payment_total:'0.00',overdue_amount:'0.00',
  risk_limit:'0',risk_usage_percent:0,risk_exceeded:false,document_count:0,last_activity:null},
 documents:[],payments:[],products:[],contacts:[],tasks:[],notes:[],history:[],charge_documents:[],
});

let telefon:string|null='05321112233';
beforeEach(()=>{
 cleanup();telefon='05321112233';get.mockReset();
 get.mockImplementation((url:string)=>{
  if(url==='/customers/7')return Promise.resolve({data:detay('F10 Müşteri',telefon)});
  if(url==='/suppliers/7')return Promise.resolve({data:detay('F10 Tedarikçi',telefon)});
  return Promise.resolve({data:[]});
 });
});
afterEach(()=>cleanup());

const mount=(type:'customer'|'supplier')=>render(
 <ThemeProvider theme={createTheme()}><MemoryRouter><EntityDetail type={type}/></MemoryRouter></ThemeProvider>);

describe('F10-1d — EntityDetail panelleri',()=>{
 it('müşteri kartı: iki panel CUSTOMER ve cari telefonuyla çizilir',async()=>{
  mount('customer');
  expect((await screen.findByTestId('wa-karti')).textContent).toBe('CUSTOMER|7|05321112233');
  expect(screen.getByTestId('izin-paneli').textContent).toBe('CUSTOMER|7|05321112233');
 });

 it('tedarikçi kartı: iki panel de SUPPLIER ile çizilir (§6.4)',async()=>{
  mount('supplier');
  expect((await screen.findByTestId('wa-karti')).textContent).toBe('SUPPLIER|7|05321112233');
  expect(screen.getByTestId('izin-paneli').textContent).toBe('SUPPLIER|7|05321112233');
 });

 it('maskeli telefon panellere ön dolgu olarak GEÇMEZ',async()=>{
  telefon='05** *** ** 33';
  mount('supplier');
  expect((await screen.findByTestId('wa-karti')).textContent).toBe('SUPPLIER|7|∅');
  expect(screen.getByTestId('izin-paneli').textContent).toBe('SUPPLIER|7|∅');
 });
});
