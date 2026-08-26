import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {ThemeProvider} from '@mui/material/styles';
import Decimal from 'decimal.js';

import {api} from '../api';
import {getAppTheme} from '../theme';
import Pos,{POS_TOUCH_MIN} from './Pos';

vi.mock('../api',()=>({
 api:{get:vi.fn(),post:vi.fn()},
 errorDetail:(error:any,fallback:string)=>error?.response?.data?.detail||fallback,
 money:(value:Decimal.Value)=>`${new Decimal(value||0).toFixed(2)} TL`,
 policyOverrideHeaders:(reason:string)=>({'X-Policy-Override':'true','X-Policy-Override-Reason':reason}),
 policyOverrideRequired:(error:any)=>error?.response?.status===409&&String(error?.response?.data?.detail||'').includes('Yönetici onayıyla'),
}));
// Mock'lar gerçek API'yi yansıtmalı: Decimal alanlar JSON'da SAYI olarak
// serileşiyor (18, 6, 16.5) — string değil. Prod'daki "d.replace is not a
// function" çökmesi, mock'ların string kullanması yüzünden CI'dan kaçtı;
// bu dosya bir daha string'e döndürülmemeli.
const quickPick={product_id:8,product_name:'Yağ Filtresi',product_code:'YF-8',unit_price:18,current_stock:6,times_sold:12,total_quantity:16.5};
const customers=[{id:5,name:'Mehmet Çiftçi'},{id:6,name:'Ayşe Tarım'}];
const warehouses=[{id:1,name:'Merkez Depo',is_default:true}];
const product={id:7,name:'Filtre',code:'FLT-7',unit_price:12.5,stock:8,warehouse_id:1};
const route=(url:unknown)=>url==='/warehouses'?warehouses:url==='/quick-pick'?[quickPick]:url==='/customers'?customers:product;

describe('Hızlı Satış',()=>{afterEach(cleanup);beforeEach(()=>{vi.clearAllMocks();vi.mocked(api.get).mockImplementation((url)=>Promise.resolve({data:route(url)}) as never)});it('dokunmatik klavye oturum içinde kapatılabilir',async()=>{
 render(<Pos/>);
 fireEvent.click(screen.getByRole('button',{name:'Dokunmatik klavye açık'}));
 fireEvent.focus(screen.getByLabelText('Barkod'));
 expect(screen.queryByRole('region',{name:'Ekran klavyesi'})).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Dokunmatik klavye kapalı'})).toBeInTheDocument();
});it('barkod Enter ile ürünü sepete ekler ve satışı gönderir',async()=>{
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:42,final_total:'25.00',paid_amount:'25.00',remaining_amount:'0.00',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);await screen.findByText(/Merkez Depo/);const barcode=screen.getByLabelText('Barkod');fireEvent.change(barcode,{target:{value:'869000000007'}});fireEvent.keyDown(barcode,{key:'Enter'});expect(await screen.findByText('Filtre')).toBeInTheDocument();fireEvent.change(screen.getByLabelText('Filtre miktar'),{target:{value:'2'}});fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 // Müşteri seçilmedi -> customer_id null, yani "Perakende Satış" sistem carisi.
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',{items:[{product_id:7,quantity:'2',unit_price:'12.5',discount_percent:'0'}],payment_type:'cash',note:undefined,customer_id:null,warehouse_id:1,discount_percent:'0'},expect.objectContaining({headers:expect.objectContaining({'Idempotency-Key':expect.any(String)})})));expect(await screen.findByText(/Satış #42 tamamlandı/)).toBeInTheDocument();
});it('öğrenilmiş hızlı seçime dokununca ürünü sepete ekler ve toplam hesaplanır',async()=>{
 // numeric(<number>) yolu: sayı unit_price ile sepete ekle -> çip 2 kez ->
 // 2 x 18 = 36.00 TL genel toplam görünmeli (çökme yok).
 render(<Pos/>);const pick=await screen.findByText('Yağ Filtresi · YF-8');fireEvent.click(pick);expect(await screen.findByText('Yağ Filtresi')).toBeInTheDocument();expect(screen.getByLabelText('Yağ Filtresi miktar')).toHaveValue('1');expect(screen.getAllByText('18.00 TL').length).toBeGreaterThan(0);fireEvent.click(pick);expect(screen.getByLabelText('Yağ Filtresi miktar')).toHaveValue('2');expect(screen.getAllByText('36.00 TL').length).toBeGreaterThan(0);
 expect(screen.getByRole('combobox',{name:'Depo'})).toHaveAttribute('aria-disabled','true');
});it('elle OCR araması adayı açıkça etiketler ve tıklanmadan sepete eklemez',async()=>{
 vi.mocked(api.get).mockImplementation((url)=>Promise.resolve({data:url==='/warehouses'?warehouses:url==='/quick-pick'?[]:url==='/customers'?[]:url==='/search/parts'?{items:[{id:9,name:'OCR Filtre',product_code:'OQ-10',sale_price:'20.00',warehouse_stock:'4.0000',match_type:'product_code_ocr'}]}:product}) as never);
 render(<Pos/>);await screen.findByText(/Merkez Depo/);
 fireEvent.change(screen.getByLabelText('Elle ürün / parça ara'),{target:{value:'0Q-1O'}});
 fireEvent.click(screen.getByRole('button',{name:'Ara'}));
 expect(await screen.findByText(/Benzer \/ OCR/)).toBeInTheDocument();
 expect(screen.queryByLabelText('OCR Filtre miktar')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/OCR Filtre/}));
 expect(await screen.findByLabelText('OCR Filtre miktar')).toHaveValue('1');
 expect(api.get).toHaveBeenCalledWith('/search/parts',{params:{q:'0Q-1O',limit:20,warehouse_id:1}});
});it('belirsiz barkodda aday seçmeden anlaşılır hata gösterir',async()=>{
 vi.mocked(api.get).mockImplementation((url)=>url==='/pos/lookup'?Promise.reject({response:{data:{detail:{code:'AMBIGUOUS_BARCODE',candidate_count:2}}}}):Promise.resolve({data:route(url)}) as never);
 render(<Pos/>);await screen.findByText(/Merkez Depo/);
 const barcode=screen.getByLabelText('Barkod');fireEvent.change(barcode,{target:{value:'869'}});fireEvent.keyDown(barcode,{key:'Enter'});
 expect(await screen.findByText(/2 aktif ürünle eşleşiyor/)).toBeInTheDocument();
 expect(screen.queryByText('Filtre')).not.toBeInTheDocument();
});it('sayı fiyatlı hızlı seçim satışı tamamlanır',async()=>{
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:43,final_total:'36.00',paid_amount:'36.00',remaining_amount:'0.00',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));fireEvent.click(await screen.findByRole('button',{name:'Satışı Tamamla'}));
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',{items:[{product_id:8,quantity:'1',unit_price:'18',discount_percent:'0'}],payment_type:'cash',note:undefined,customer_id:null,warehouse_id:1,discount_percent:'0'},expect.objectContaining({headers:expect.objectContaining({'Idempotency-Key':expect.any(String)})})));expect(await screen.findByText(/Satış #43 tamamlandı/)).toBeInTheDocument();
});it('satış geçmişi yoksa sakin bir boş durum gösterir',async()=>{
 vi.mocked(api.get).mockImplementation((url)=>Promise.resolve({data:url==='/warehouses'?warehouses:url==='/quick-pick'?[]:url==='/customers'?[]:{}}) as never);render(<Pos/>);expect(await screen.findByText('Henüz hızlı seçim oluşturacak satış geçmişi yok.')).toBeInTheDocument();
});it('birim fiyat satır bazında düzenlenebilir ve toplam ona göre hesaplanır',async()=>{
 // Liste fiyatı 18; tezgâhta 15'e pazarlık edildi -> satır toplamı 2 x 15 = 30.
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:44,final_total:'30.00',paid_amount:'30.00',remaining_amount:'0.00',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 fireEvent.change(screen.getByLabelText('Yağ Filtresi birim fiyat'),{target:{value:'15'}});
 fireEvent.change(screen.getByLabelText('Yağ Filtresi miktar'),{target:{value:'2'}});
 expect(screen.getAllByText('30.00 TL').length).toBeGreaterThan(0);
 // Düzenlenen fiyat liste fiyatından farklıysa liste fiyatı bilgi olarak kalır.
 expect(screen.getByText(/Liste: 18\.00 TL/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',expect.objectContaining({items:[{product_id:8,quantity:'2',unit_price:'15',discount_percent:'0'}]}),expect.any(Object)));
});it('satır ve belge iskontosu birlikte uygulanır',async()=>{
 // 2 x 18 = 36 -> satır %10 = 32.40 -> belge %25 = 24.30 (sunucudaki
 // compute_line + apply_global_discount sırasının birebir aynısı).
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:45,final_total:'24.30',paid_amount:'24.30',remaining_amount:'0.00',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 fireEvent.change(screen.getByLabelText('Yağ Filtresi miktar'),{target:{value:'2'}});
 fireEvent.change(screen.getByLabelText('Yağ Filtresi iskonto'),{target:{value:'10'}});
 expect(screen.getAllByText('32.40 TL').length).toBeGreaterThan(0);
 fireEvent.change(screen.getByLabelText('Belge iskontosu'),{target:{value:'25'}});
 expect(screen.getAllByText('24.30 TL').length).toBeGreaterThan(0);
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',expect.objectContaining({items:[{product_id:8,quantity:'2',unit_price:'18',discount_percent:'10'}],discount_percent:'25'}),expect.any(Object)));
});it('elle genel toplamı sabit belge iskontosu olarak gönderir ve sunucu kuruşunu doğrular',async()=>{
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:145,final_total:'17.50',paid_amount:'17.50',remaining_amount:'0.00',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 fireEvent.change(screen.getByLabelText('Elle genel toplam'),{target:{value:'17,50'}});
 expect(screen.getByText(/Belge iskontosu −0\.50 TL/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',expect.objectContaining({
  discount_percent:'2.7778',
  discount_amount:'0.5',
 }),expect.any(Object)));
 expect(await screen.findByText(/Satış #145 tamamlandı/)).toBeInTheDocument();
});it('elle toplam satır toplamından büyükse satışı engeller',async()=>{
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 fireEvent.change(screen.getByLabelText('Elle genel toplam'),{target:{value:'18.01'}});
 expect(await screen.findByText(/fiyat farkı için iskonto kullanılamaz/)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Satışı Tamamla'})).toBeDisabled();
});it('sunucu toplamı elle girilen toplamdan saparsa farkı görünür gösterir',async()=>{
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:146,final_total:'17.49',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 fireEvent.change(screen.getByLabelText('Elle genel toplam'),{target:{value:'17.50'}});
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 expect(await screen.findByText(/Fark: 0\.01 TL \(eksik\).*tekrar göndermeyin/)).toBeInTheDocument();
});it('sunucu ödeme alanlarını döndürmezse client hesabıyla fiş üretmez',async()=>{
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:147,final_total:'18.00',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 expect(await screen.findByText('Satış tamamlanamadı.')).toBeInTheDocument();
 expect(screen.queryByRole('dialog',{name:'Satış tamamlandı'})).not.toBeInTheDocument();
});it('satır iskontosu 100 üstündeyse alanı işaretler, toplamı sıfıra kırpar ve satışı bloklar',async()=>{
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 const discount=screen.getByLabelText('Yağ Filtresi iskonto');
 fireEvent.change(discount,{target:{value:'111'}});
 expect(discount).toHaveAttribute('aria-invalid','true');
 expect(screen.getByText('iskonto 0–100 arası olmalı')).toBeInTheDocument();
 expect(screen.getAllByText('0.00 TL').length).toBeGreaterThan(0);
 const complete=screen.getByRole('button',{name:'Satışı Tamamla'});
 expect(complete).toBeDisabled();
 fireEvent.click(complete);
 expect(api.post).not.toHaveBeenCalled();
});it('belge iskontosu aralık dışındaysa alanı işaretler ve satışı bloklar',async()=>{
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 const discount=screen.getByLabelText('Belge iskontosu');
 fireEvent.change(discount,{target:{value:'-1'}});
 expect(discount).toHaveAttribute('aria-invalid','true');
 expect(screen.getByText('iskonto 0–100 arası olmalı')).toBeInTheDocument();
 const complete=screen.getByRole('button',{name:'Satışı Tamamla'});
 expect(complete).toBeDisabled();
 fireEvent.click(complete);
 expect(api.post).not.toHaveBeenCalled();
});it('müşteri seçilince satış o cariye yazılır',async()=>{
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:46,final_total:'18.00',paid_amount:'18.00',remaining_amount:'0.00',customer_name:'Mehmet Çiftçi'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 const picker=await screen.findByLabelText('Müşteri');fireEvent.mouseDown(picker);fireEvent.change(picker,{target:{value:'Mehmet'}});
 fireEvent.click(await screen.findByText('Mehmet Çiftçi'));
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',expect.objectContaining({customer_id:5}),expect.any(Object)));
 expect((await screen.findAllByText(/Mehmet Çiftçi/)).length).toBeGreaterThan(0);
});it('kısmi tahsilatı müşterinin bakiyesine yazar ve fiş yazdırır',async()=>{
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:49,final_total:'18.00',paid_amount:'14.00',remaining_amount:'4.00',customer_name:'Mehmet Çiftçi'}} as never);
 const print=vi.spyOn(window,'print').mockImplementation(()=>{});
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 const picker=await screen.findByLabelText('Müşteri');fireEvent.change(picker,{target:{value:'Mehmet'}});
 fireEvent.click(await screen.findByText('Mehmet Çiftçi'));
 fireEvent.change(screen.getByLabelText('Alınan tutar'),{target:{value:'14'}});
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',expect.objectContaining({customer_id:5,paid_amount:'14.00'}),expect.any(Object)));
 expect(await screen.findByText('Kalan bakiye')).toBeInTheDocument();
 expect(screen.getByText('4.00 TL')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Fiş Yazdır'}));
 expect(print).toHaveBeenCalledTimes(1);
 print.mockRestore();
});it('veresiye müşterisiz tamamlanamaz',async()=>{
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 // MUI Select bir <div role="combobox">; label'ı getByLabelText ile eşleşmiyor.
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'Ödeme'}));
 fireEvent.click(await screen.findByRole('option',{name:'Veresiye'}));
 // Backend 400 döner; kullanıcıyı oraya kadar götürmeden burada durduruyoruz.
 expect(await screen.findByText('Veresiye satış için müşteri seçin.')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Satışı Tamamla'})).toBeDisabled();
 expect(api.post).not.toHaveBeenCalled();
});it('fiyat değişikliğini yönetici onayı veya gerekçe olmadan gönderir',async()=>{
 vi.mocked(api.post).mockResolvedValueOnce({data:{sale_id:47,final_total:'15.00',paid_amount:'15.00',remaining_amount:'0.00',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 fireEvent.change(screen.getByLabelText('Yağ Filtresi birim fiyat'),{target:{value:'15'}});
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 await waitFor(()=>expect(api.post).toHaveBeenCalledTimes(1));
 expect(api.post).toHaveBeenLastCalledWith(
  '/pos/sale',
  expect.objectContaining({items:[expect.objectContaining({unit_price:'15'})]}),
  {headers:expect.objectContaining({'Idempotency-Key':expect.any(String)})},
 );
 expect(screen.getByRole('dialog',{name:'Satış tamamlandı'})).toBeInTheDocument();
});it('depo değişince eski arama sonucu yeni depoyla sepete girmez',async()=>{
 // Regresyon: A deposunda arama yapılır, sepet BOŞken B deposuna geçilir.
 // Eski sonuç temizlenmezse ona tıklamak A'nın stoğunu B etiketiyle sepete
 // sokuyordu: ekranda A'nın stoğu görünür, satış B'den düşerdi.
 const twoWarehouses=[{id:1,name:'Merkez Depo',is_default:true},{id:2,name:'Şube Depo',is_default:false}];
 const partsA={items:[{id:9,name:'A Deposu Filtresi',product_code:'AD-9',sale_price:20,warehouse_stock:4,match_type:'product_code'}]};
 vi.mocked(api.get).mockImplementation((url)=>Promise.resolve({data:url==='/warehouses'?twoWarehouses:url==='/quick-pick'?[]:url==='/customers'?[]:url==='/search/parts'?partsA:product}) as never);

 render(<Pos/>);await screen.findByText(/Merkez Depo/);
 fireEvent.change(screen.getByLabelText('Elle ürün / parça ara'),{target:{value:'AD-9'}});
 fireEvent.click(screen.getByRole('button',{name:'Ara'}));
 expect(await screen.findByRole('button',{name:/A Deposu Filtresi/})).toBeInTheDocument();

 // Sepet boş olduğu için depo seçici açıktır; B deposuna geç. (MUI Select
 // yerli <select> değildir: listeyi açıp seçeneği tıklamak gerekir.)
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'Depo'}));
 fireEvent.click(within(screen.getByRole('listbox')).getByText('Şube Depo'));

 // Eski sonuç DERHAL ekrandan kalkmalı: artık tıklanabilir bile olmamalı.
 await waitFor(()=>expect(screen.queryByRole('button',{name:/A Deposu Filtresi/})).not.toBeInTheDocument());
 // Ve hiçbir şekilde sepete girmemiş olmalı.
 expect(screen.queryByLabelText('A Deposu Filtresi miktar')).not.toBeInTheDocument();
});it('geç gelen arama yanıtı eski depoyla sepete eklenmez',async()=>{
 // Uçuştaki istek A deposu için başlatılır, yanıt gelmeden B'ye geçilir.
 // Geç yanıt ekrana basılmamalı; bassa bile ekleme reddedilmeli.
 const twoWarehouses=[{id:1,name:'Merkez Depo',is_default:true},{id:2,name:'Şube Depo',is_default:false}];
 let releaseSearch:(value:unknown)=>void=()=>{};
 const pendingSearch=new Promise(resolve=>{releaseSearch=resolve});
 vi.mocked(api.get).mockImplementation((url)=>{
  if(url==='/search/parts')return pendingSearch as never;
  return Promise.resolve({data:url==='/warehouses'?twoWarehouses:url==='/quick-pick'?[]:url==='/customers'?[]:product}) as never;
 });

 render(<Pos/>);await screen.findByText(/Merkez Depo/);
 fireEvent.change(screen.getByLabelText('Elle ürün / parça ara'),{target:{value:'AD-9'}});
 fireEvent.click(screen.getByRole('button',{name:'Ara'}));
 // Yanıt gelmeden depo değişir.
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'Depo'}));
 fireEvent.click(within(screen.getByRole('listbox')).getByText('Şube Depo'));
 // Şimdi A deposunun sonucu geç gelir.
 releaseSearch({data:{items:[{id:9,name:'A Deposu Filtresi',product_code:'AD-9',sale_price:20,warehouse_stock:4,match_type:'product_code'}]}});

 await waitFor(()=>expect(screen.getByRole('combobox',{name:'Depo'})).toHaveTextContent('Şube Depo'));
 expect(screen.queryByRole('button',{name:/A Deposu Filtresi/})).not.toBeInTheDocument();
 expect(screen.queryByLabelText('A Deposu Filtresi miktar')).not.toBeInTheDocument();
});it('sayısal ekran klavyesi yalnız odaklanan alanı düzenler ve ondalığı API biçimine çevirir',async()=>{
 vi.mocked(api.post).mockResolvedValue({data:{sale_id:48,final_total:'45.00',paid_amount:'45.00',remaining_amount:'0.00',customer_name:'Perakende Satış'}} as never);
 render(<Pos/>);fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
 const quantity=screen.getByLabelText('Yağ Filtresi miktar');
 const price=screen.getByLabelText('Yağ Filtresi birim fiyat');
 fireEvent.focus(quantity);
 expect(screen.getByRole('region',{name:'Ekran klavyesi'})).toBeInTheDocument();
 expect(screen.getByText('Aktif alan: Yağ Filtresi miktar')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Tuş 2'}));
 fireEvent.click(screen.getByRole('button',{name:'Tuş ,'}));
 fireEvent.click(screen.getByRole('button',{name:'Tuş 5'}));
 expect(quantity).toHaveValue('2,5');
 expect(price).toHaveValue('18');
 fireEvent.click(screen.getByRole('button',{name:'Enter'}));
 expect(screen.queryByRole('region',{name:'Ekran klavyesi'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Satışı Tamamla'}));
 await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/pos/sale',expect.objectContaining({items:[expect.objectContaining({quantity:'2.5',unit_price:'18'})]}),expect.any(Object)));
});it('metin alanına geçince Türkçe QWERTY açılır ve müşteri/not girişini destekler',async()=>{
 render(<Pos/>);await screen.findByText(/Merkez Depo/);
 const search=screen.getByLabelText('Elle ürün / parça ara');
 fireEvent.focus(search);
 expect(screen.getByRole('region',{name:'Ekran klavyesi'})).toBeInTheDocument();
 expect(screen.getByText('Aktif alan: Ürün / parça arama')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Tuş ş'}));
 fireEvent.click(screen.getByRole('button',{name:'Tuş ü'}));
 expect(search).toHaveValue('şü');
 expect(screen.queryByText('Sayısal tuş takımı')).not.toBeInTheDocument();
 const customerPicker=screen.getByLabelText('Müşteri');
 fireEvent.focus(customerPicker);
 expect(screen.getByRole('region',{name:'Ekran klavyesi'})).toBeInTheDocument();
 expect(screen.getByText('Aktif alan: Müşteri arama')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Tuş m'}));
 expect(customerPicker).toHaveValue('m');
 const note=screen.getByLabelText('Not (isteğe bağlı)');
 fireEvent.focus(note);
 fireEvent.click(screen.getByRole('button',{name:'Tuş ç'}));
 fireEvent.click(screen.getByRole('button',{name:'Büyük küçük harf'}));
 fireEvent.click(screen.getByRole('button',{name:'Tuş İ'}));
 expect(note).toHaveValue('çİ');
})});

// #163'te ekran klavyesi için kurulan 44px sözleşmesinin POS'un tamamına
// genişletilmiş hali. Uygulama teması masaüstü yoğunluğu için 40px veriyor;
// bu test POS ölçeğinin onu gerçekten geçersiz kıldığını 320px'lik dar bir
// POS ekranında doğrular.
describe('Hızlı Satış dokunmatik hedefleri',()=>{
 afterEach(()=>{cleanup();vi.unstubAllGlobals()});
 beforeEach(()=>{vi.clearAllMocks();vi.mocked(api.get).mockImplementation((url)=>Promise.resolve({data:route(url)}) as never)});

 const atLeast44=(element:Element,label:string)=>{
  const style=getComputedStyle(element);
  expect({label,minHeight:parseFloat(style.minHeight)||0}).toEqual({label,minHeight:expect.any(Number)});
  expect(parseFloat(style.minHeight)||0).toBeGreaterThanOrEqual(POS_TOUCH_MIN);
 };

 it('320px ekranda sepet satırı ve ödeme kontrollerini en az 44px tutar',async()=>{
  vi.stubGlobal('innerWidth',320);
  window.dispatchEvent(new Event('resize'));
  render(<ThemeProvider theme={getAppTheme('light')}><Pos/></ThemeProvider>);

  fireEvent.click(await screen.findByText('Yağ Filtresi · YF-8'));
  await screen.findByText('Yağ Filtresi');

  // Sepet satırı: fiyat / miktar / iskonto girişleri ve sil düğmesi.
  for(const label of ['Yağ Filtresi birim fiyat','Yağ Filtresi miktar','Yağ Filtresi iskonto']){
   const input=screen.getByLabelText(label);
   const root=input.closest('.MuiOutlinedInput-root');
   expect(root).not.toBeNull();
   atLeast44(root as Element,label);
  }
  atLeast44(screen.getByRole('button',{name:'Yağ Filtresi sil'}),'sil düğmesi');

  // Ödeme şeridi ve birincil aksiyon.
  atLeast44(screen.getByLabelText('Belge iskontosu').closest('.MuiOutlinedInput-root') as Element,'belge iskontosu');
  atLeast44(screen.getByRole('button',{name:'Satışı Tamamla'}),'Satışı Tamamla');
  atLeast44(screen.getByRole('button',{name:'Ara'}),'Ara');
 });

 it('hızlı seçim çipleri de dokunulabilir boyuttadır',async()=>{
  vi.stubGlobal('innerWidth',320);
  window.dispatchEvent(new Event('resize'));
  render(<ThemeProvider theme={getAppTheme('light')}><Pos/></ThemeProvider>);
  const chip=(await screen.findByText('Yağ Filtresi · YF-8')).closest('.MuiChip-root');
  expect(chip).not.toBeNull();
  atLeast44(chip as Element,'hızlı seçim çipi');
 });
});
