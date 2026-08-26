import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import Pos from './Pos';
import {api} from '../api';
import fixtures from '../test/fixtures/pos-api.json';

vi.mock('../api',()=>({api:{get:vi.fn(),post:vi.fn()},errorDetail:(_error:unknown,fallback:string)=>fallback,money:(value:unknown)=>`${Number(value||0).toFixed(2)} TL`,policyOverrideHeaders:vi.fn(),policyOverrideRequired:()=>false}));

// Bu dosyanın mock verisi ELLE YAZILMAZ. pos-api.json, gerçek backend'den
// backend/tools/capture_frontend_fixtures.py ile yakalanır; CI drift kontrolü
// fixture'ı backend'in gerçek JSON şekliyle senkron tutar. Prod POS çökmesi
// (d.replace is not a function) elle yazılmış string mock'lar gerçek API'nin
// sayı gönderdiğini gizlediği için CI'dan kaçmıştı — bu katman o sınıfı kapatır.
const MONEY_RE=/^-?\d+\.\d{2}$/;
const QUANTITY_RE=/^-?\d+\.\d{4}$/;

describe('Hızlı Satış — gerçek API fixtureları',()=>{
 afterEach(cleanup);
 beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url)=>Promise.resolve({data:url==='/warehouses'?[{id:fixtures.pos_lookup.warehouse_id,name:'Merkez Depo',is_default:true}]:url==='/quick-pick'?fixtures.quick_pick:fixtures.pos_lookup}) as never);
 });

 it('fixture para/miktar alanları kanonik sabit ölçekli string (kontrat bekçisi)',()=>{
  // Backend sayı üretmeye dönerse fixture yeniden üretimi burada kırmızı yanar.
  for(const pick of fixtures.quick_pick){
   expect(pick.unit_price).toMatch(MONEY_RE);
   expect(pick.current_stock).toMatch(QUANTITY_RE);
   expect(pick.total_quantity).toMatch(QUANTITY_RE);
  }
  expect(fixtures.pos_lookup.unit_price).toMatch(MONEY_RE);
  expect(fixtures.pos_lookup.stock).toMatch(QUANTITY_RE);
  for(const field of ['subtotal','vat_total','grand_total','discount_amount','final_total','paid_amount','remaining_amount'] as const){
   expect(fixtures.pos_sale[field]).toMatch(MONEY_RE);
  }
 });

 it('gerçek quick-pick yanıtıyla çip -> sepet -> genel toplam akışı çalışır',async()=>{
  render(<Pos/>);
  const pick=await screen.findByText('Yağ Filtresi · YF-8');
  fireEvent.click(pick);
  expect(await screen.findByText('Yağ Filtresi')).toBeInTheDocument();
  expect(screen.getByLabelText('Yağ Filtresi miktar')).toHaveValue('1');
  expect(screen.getAllByText('18.00 TL').length).toBeGreaterThan(0);
  fireEvent.click(pick);
  expect(screen.getByLabelText('Yağ Filtresi miktar')).toHaveValue('2');
  expect(screen.getAllByText('36.00 TL').length).toBeGreaterThan(0);
 });

 it('gerçek lookup + satış yanıtıyla barkod satışı tamamlanır',async()=>{
  vi.mocked(api.post).mockResolvedValue({data:fixtures.pos_sale} as never);
  render(<Pos/>);
  await screen.findByText(/Merkez Depo/);
  const barcode=screen.getByLabelText('Barkod');
  fireEvent.change(barcode,{target:{value:'8690000000086'}});
  fireEvent.keyDown(barcode,{key:'Enter'});
  expect(await screen.findByText('Yağ Filtresi')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Yağ Filtresi miktar'),{target:{value:'2'}});
  fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
  await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',{items:[{product_id:fixtures.pos_lookup.id,quantity:'2',unit_price:fixtures.pos_lookup.unit_price,discount_percent:'0'}],payment_type:'cash',note:undefined,customer_id:null,warehouse_id:fixtures.pos_lookup.warehouse_id,discount_percent:'0'},expect.objectContaining({headers:expect.objectContaining({'Idempotency-Key':expect.any(String)})})));
  expect(await screen.findByText(new RegExp(`Satış #${fixtures.pos_sale.sale_id} tamamlandı`))).toBeInTheDocument();
 });
});
