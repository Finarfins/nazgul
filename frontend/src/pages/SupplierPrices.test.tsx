import {cleanup,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import SupplierPrices from './SupplierPrices';

const get=vi.fn();
const post=vi.fn();
const put=vi.fn();
const remove=vi.fn();
let permissions:string[]=[];
vi.mock('../api',()=>({
 api:{
  get:(...args:unknown[])=>get(...args),post:(...args:unknown[])=>post(...args),
  put:(...args:unknown[])=>put(...args),delete:(...args:unknown[])=>remove(...args),
 },
 errorDetail:(error:any,fallback:string)=>error?.response?.data?.detail||fallback,
}));
vi.mock('../AuthContext',()=>({useAuth:()=>({can:(permission:string)=>permissions.includes(permission)})}));

const summary={id:12,status:'DRY_RUN_READY',source_filename:'liste.xlsx',parsed_row_count:2,created_at:'2026-07-30T09:00:00Z'};
const profile={
 id:7,supplier_id:3,supplier_name:'TAMPAR',name:'TAMPAR Excel',source_type:'xlsx',
 currency_mode:'inline',term_days:60,vat_source:'fixed:20',warning_threshold:'25.00',
 blocked_threshold:'100.00',created_at:'2026-07-30T08:00:00Z',import_count:2,
};
const profileDetail={
 ...profile,sheet_selector:'.*',header_row_strategy:'detect',
 column_map:{code:'Kod',price_cash:'Peşin',price_term:'Vadeli'},page_sections:[],
};
const report={
 ...summary,report_digest:'digest',expected_revision:'digest',
 lines:[
  {line_no:1,supplier_code:'ABC',description:'Rulman',state:'OK',old_price_cash:'10.0000',price_cash:'12.5000',old_price_term:'11.0000',price_term:'13.5000',currency:'TRY',deviation_pct:'25.00',match_source:'xref'},
  {line_no:2,supplier_code:'BLOCK',state:'BLOCKED',price_cash:'99.9900',price_term:'100.0000',currency:'EUR',deviation_pct:'125.00'},
 ],
};

describe('Tedarikçi fiyat importları',()=>{
 afterEach(cleanup);
 beforeEach(()=>{
  permissions=['supplier_prices.view','supplier_prices.import','supplier_prices.apply','supplier_prices.override_block'];
  get.mockReset();post.mockReset();put.mockReset();remove.mockReset();
  get.mockImplementation((url:string)=>{
   if(url==='/supplier-prices/imports')return Promise.resolve({data:[summary]});
   if(url==='/supplier-prices/imports/12')return Promise.resolve({data:report});
   if(url==='/supplier-prices/profiles')return Promise.resolve({data:[profile]});
   if(url==='/supplier-prices/profiles/7')return Promise.resolve({data:profileDetail});
   if(url==='/suppliers')return Promise.resolve({data:[{id:3,name:'TAMPAR'}]});
   return Promise.reject(new Error(`beklenmeyen istek: ${url}`));
  });
 });

 it('son importları listeler, satırdan raporu açar ve Decimal-string değerleri aynen gösterir',async()=>{
  render(<SupplierPrices/>);
  const row=await screen.findByRole('row',{name:/Import #12/});
  expect(within(row).getByText('2')).toBeInTheDocument();
  await userEvent.click(row);
  expect(await screen.findByText(/10\.0000 → 12\.5000/)).toBeInTheDocument();
  expect(screen.getByText(/11\.0000 → 13\.5000/)).toBeInTheDocument();
  expect(get).toHaveBeenCalledWith('/supplier-prices/imports/12');
 });

 it('boş durumda ilk importun nasıl oluşturulacağını açıklar',async()=>{
  get.mockResolvedValueOnce({data:[]});
  render(<SupplierPrices/>);
  expect(await screen.findByText(/Henüz tedarikçi fiyat importu yok/)).toBeInTheDocument();
 });

 it('profil seçicisindeki profile_id ile multipart import oluşturur',async()=>{
  post.mockResolvedValueOnce({data:{id:12}});
  render(<SupplierPrices/>);
  await screen.findByText('Son importlar');
  expect(screen.getByLabelText('Eşleme profili')).toHaveTextContent('TAMPAR Excel');
  const input=document.querySelector('input[type="file"]') as HTMLInputElement;
  await userEvent.upload(input,new File(['xlsx'],'fiyat.xlsx',{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
  await userEvent.click(screen.getByRole('button',{name:'Dry-run Oluştur'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/supplier-prices/imports',expect.any(FormData)));
  const form=post.mock.calls[0][1] as FormData;
  expect(form.get('profile_id')).toBe('7');
 });

 it('profil listesini ve boş durum çağrısını gösterir',async()=>{
  render(<SupplierPrices/>);
  await userEvent.click(screen.getByRole('tab',{name:'Eşleme Profilleri'}));
  expect(await screen.findByText('TAMPAR Excel')).toBeInTheDocument();
  expect(screen.getByText('TAMPAR')).toBeInTheDocument();
  cleanup();
  get.mockImplementation((url:string)=>{
   if(url==='/supplier-prices/imports')return Promise.resolve({data:[]});
   if(url==='/supplier-prices/profiles')return Promise.resolve({data:[]});
   return Promise.resolve({data:[]});
  });
  render(<SupplierPrices/>);
  await userEvent.click(screen.getByRole('tab',{name:'Eşleme Profilleri'}));
  expect(await screen.findByText(/Henüz eşleme profili yok/)).toBeInTheDocument();
 });

 it('xlsx profil oluşturma payloadını gerçek eşleme alanlarıyla gönderir',async()=>{
  post.mockResolvedValueOnce({data:{id:8}});
  render(<SupplierPrices/>);
  await userEvent.click(screen.getByRole('tab',{name:'Eşleme Profilleri'}));
  await userEvent.click(await screen.findByRole('button',{name:'Yeni profil'}));
  await userEvent.click(screen.getByLabelText(/Tedarikçi/));
  await userEvent.click(screen.getByRole('option',{name:'TAMPAR'}));
  await userEvent.type(screen.getByLabelText(/Profil adı/),'Yeni Excel');
  await userEvent.type(screen.getByLabelText(/Ürün \/ tedarikçi kodu başlığı/),'Kod');
  await userEvent.type(screen.getByLabelText(/Peşin fiyat başlığı/),'Peşin');
  await userEvent.type(screen.getByLabelText(/Vadeli fiyat başlığı/),'Vadeli');
  await userEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/supplier-prices/profiles',expect.objectContaining({
   supplier_id:3,name:'Yeni Excel',source_type:'xlsx',
   column_map:expect.objectContaining({code:'Kod',price_cash:'Peşin',price_term:'Vadeli'}),
  })));
 });

 it('profil detayını düzenleme payloadıyla PUT eder',async()=>{
  put.mockResolvedValueOnce({data:profileDetail});
  render(<SupplierPrices/>);
  await userEvent.click(screen.getByRole('tab',{name:'Eşleme Profilleri'}));
  await userEvent.click(await screen.findByLabelText('TAMPAR Excel profilini düzenle'));
  const name=await screen.findByLabelText(/Profil adı/);
  await userEvent.clear(name);await userEvent.type(name,'TAMPAR Güncel');
  await userEvent.click(screen.getByRole('button',{name:'Kaydet'}));
  await waitFor(()=>expect(put).toHaveBeenCalledWith('/supplier-prices/profiles/7',expect.objectContaining({name:'TAMPAR Güncel'})));
 });

 it('silme 409 mesajını kullanıcıya gösterir',async()=>{
  remove.mockRejectedValueOnce({response:{data:{detail:'Bu profille yapılmış içe aktarımlar var; profil silinemez.'}}});
  render(<SupplierPrices/>);
  await userEvent.click(screen.getByRole('tab',{name:'Eşleme Profilleri'}));
  await userEvent.click(await screen.findByLabelText('TAMPAR Excel profilini sil'));
  await userEvent.click(screen.getByRole('button',{name:'Sil'}));
  expect(await screen.findByText('Bu profille yapılmış içe aktarımlar var; profil silinemez.')).toBeInTheDocument();
 });

 it('uygula işlemini yetki ve onayla gönderir',async()=>{
  post.mockResolvedValue({data:{ok:true}});
  render(<SupplierPrices/>);
  await userEvent.click(await screen.findByRole('row',{name:/Import #12/}));
  await userEvent.click(await screen.findByRole('button',{name:'Uygula'}));
  await userEvent.click(screen.getByRole('button',{name:'Onayla'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/supplier-prices/imports/12/apply',{expected_revision:'digest',report_digest:'digest'}));
 });

 it('bloklu satır override işlemini gerekçe ve onayla gönderir',async()=>{
  post.mockResolvedValue({data:{ok:true}});
  render(<SupplierPrices/>);
  await userEvent.click(await screen.findByRole('row',{name:/Import #12/}));
  await userEvent.click(screen.getByRole('tab',{name:/Bloklu/}));
  await userEvent.click(await screen.findByRole('button',{name:'Satırı kabul et'}));
  await userEvent.type(await screen.findByRole('textbox'),'tedarikçi teyidi');
  await userEvent.click(screen.getByRole('button',{name:'Onayla ve kabul et'}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith('/supplier-prices/imports/12/lines/2/override',{reason:'tedarikçi teyidi'}));
 });

 it('yazma yetkileri yoksa yükleme ve aksiyonları göstermez',async()=>{
  permissions=['supplier_prices.view'];
  render(<SupplierPrices/>);
  await userEvent.click(await screen.findByRole('row',{name:/Import #12/}));
  expect(screen.queryByText('Yeni import oluştur')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Uygula'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('tab',{name:/Bloklu/}));
  expect(screen.queryByRole('button',{name:'Satırı kabul et'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('tab',{name:'Eşleme Profilleri'}));
  expect(screen.queryByRole('button',{name:'Yeni profil'})).not.toBeInTheDocument();
 });
});
