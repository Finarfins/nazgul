import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {api} from '../api';
import {DespatchNotePanel,type EDespatchStatus} from './DespatchNotePanel';

vi.mock('../api',()=>({
 api:{get:vi.fn(),post:vi.fn()},
 apiDetail:()=>undefined,
 errorDetail:(_error:unknown,fallback:string)=>fallback,
 money:(value:unknown)=>String(value??''),
}));

// E4b-3: panel artık `useAuth().can`e bakıyor (yanıt bölmesi `sales`
// istiyor). Varsayılan SATIŞ kullanıcısıdır; izinsiz dal
// `DespatchNotePanel.kismi.test.tsx`te ölçülüyor.
vi.mock('../AuthContext',()=>({useAuth:()=>({can:()=>true})}));

const ETTN='11111111-2222-3333-4444-555555555555';

const irsaliye=(status:EDespatchStatus,lastError:string|null=null)=>({
 id:7,invoice_id:17,despatch_uuid:ETTN,despatch_number:'IRS-FTR-1',
 issue_date:'2026-09-13',
 edespatch_status:status,edespatch_gib_status_code:null,
 edespatch_provider_uuid:null,edespatch_last_error:lastError,
 response_status:null,
 driver_name:'Ahmet Yilmaz',vehicle_plate:'34ABC123',
});

// E4b-3: panel TEK istek atmıyor artık — liste, `despatchable-items` ve her
// irsaliyenin detayı. Sahte `get` URL'e BAKARAK cevap veriyor; tek bir
// `mockResolvedValue` üç ucu birden yanlış beslerdi.
const kur=(notes:ReturnType<typeof irsaliye>[],items:unknown[]=[],complete=false)=>{
 vi.mocked(api.get).mockImplementation(((url:string)=>{
  if(url.startsWith('/despatch-notes?'))
   return Promise.resolve({data:{items:notes,total:notes.length}});
  if(url.includes('/despatchable-items'))
   return Promise.resolve({data:{invoice_id:17,items,complete}});
  const id=Number(url.split('/')[2]);
  return Promise.resolve({data:notes.find(note=>note.id===id)??{}});
 }) as never);
};

const KALEM={invoice_item_id:101,invoice_line_no:1,description:'Fren Balatası',
 product_id:5,invoiced:'10.0000',despatched:'0.0000',remaining:'10.0000'};

describe('InvoiceDetail e-İrsaliye paneli',()=>{
 beforeEach(()=>vi.clearAllMocks());
 afterEach(()=>cleanup());

 it.each([
  ['NONE','e-İrsaliye: gönderilmedi'],
  ['QUEUED','e-İrsaliye: kuyrukta'],
  ['PROCESSING','e-İrsaliye: işleniyor'],
  ['SIGNED','e-İrsaliye: imzalandı'],
  ['SENT','e-İrsaliye: gönderildi'],
  // E4b-2: TESLİM son durak değil; etiket yanıtın beklendiğini söylüyor.
  ['DELIVERED','e-İrsaliye: teslim edildi — yanıt bekleniyor'],
  ['FAILED','e-İrsaliye: başarısız'],
  ['UNKNOWN','e-İrsaliye: durum bilinmiyor'],
 ] as const)('%s durumunu doğru rozetle gösterir',async(status,label)=>{
  kur([irsaliye(status)],[KALEM]);
  render(<DespatchNotePanel invoiceId={17}/>);
  expect(await screen.findByText(label)).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith('/despatch-notes?invoice_id=17');
 });

 it('irsaliye yokken OLUSTUR butonu gosterir, gonder/sorgula GOSTERMEZ',async()=>{
  kur([],[KALEM]);
  render(<DespatchNotePanel invoiceId={17}/>);
  expect(await screen.findByRole('button',{name:/e-İrsaliye Oluştur/})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Gönder'})).toBeNull();
  expect(screen.queryByRole('button',{name:/Durumu Sorgula/})).toBeNull();
 });

 it('UNKNOWN durumda GONDER kapali ve uyari gorunuyor',async()=>{
  // Sunucudaki `edespatch.GONDERIM_KAPALI` ile AYNI kural. Buton acik
  // olsaydi kullanici her basista 409 alirdi; kapali olmasi cift irsaliye
  // riskini kullaniciya ANLATIYOR.
  kur([irsaliye('UNKNOWN')],[KALEM]);
  render(<DespatchNotePanel invoiceId={17}/>);
  expect(await screen.findByText('e-İrsaliye: durum bilinmiyor')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Gönder'})).toBeDisabled();
  expect(screen.getByText(/Çift irsaliye riskine karşı/)).toBeInTheDocument();
  // Cikis yolu ACIK: sorgulama butonu etkin.
  expect(screen.getByRole('button',{name:/Durumu Sorgula/})).toBeEnabled();
 });

 it.each(['NONE','FAILED'] as const)(
  '%s durumunda GONDER acik',
  async status=>{
   kur([irsaliye(status)],[KALEM]);
   render(<DespatchNotePanel invoiceId={17}/>);
   await screen.findByText(/e-İrsaliye:/);
   expect(screen.getByRole('button',{name:'Gönder'})).toBeEnabled();
  },
 );

 it.each(['QUEUED','SENT','DELIVERED'] as const)(
  '%s durumunda GONDER kapali',
  async status=>{
   kur([irsaliye(status)],[KALEM]);
   render(<DespatchNotePanel invoiceId={17}/>);
   await screen.findByText(/e-İrsaliye:/);
   expect(screen.getByRole('button',{name:'Gönder'})).toBeDisabled();
  },
 );

 it('PDF butonu HIC YOK — saglayici sozlesmesi dogrulanmadi',async()=>{
  // Uc 501 donuyor (kesif §2.4). Gorunmeyen bir buton, her basista hata
  // veren bir butondan iyidir.
  kur([irsaliye('SENT')],[KALEM]);
  render(<DespatchNotePanel invoiceId={17}/>);
  await screen.findByText('e-İrsaliye: gönderildi');
  expect(screen.getByRole('link',{name:'XML'})).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:'PDF'})).toBeNull();
  expect(screen.queryByRole('button',{name:'PDF'})).toBeNull();
 });

 it('dialog ZORUNLU alanlar dolana kadar OLUSTUR kapali tutuyor',async()=>{
  kur([],[KALEM]);
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByRole('button',{name:/e-İrsaliye Oluştur/}));

  expect(screen.getByRole('button',{name:'Oluştur'})).toBeDisabled();

  const yaz=(etiket:RegExp,deger:string)=>
   fireEvent.change(screen.getByLabelText(etiket),{target:{value:deger}});
  yaz(/Fiili Sevk Zamanı/,'2026-09-13T08:30');
  yaz(/Şoför Adı Soyadı/,'Ahmet Yilmaz');
  // ONCE EKSIK TCKN: 10 hane yetmez, 11 gerekir.
  yaz(/Şoför T\.C\. Kimlik No/,'1111111111');
  yaz(/Araç Plakası/,'34ABC123');
  yaz(/Teslim Adresi/,'Depo Yolu 7');
  yaz(/Teslim Posta Kodu/,'34710');
  expect(screen.getByRole('button',{name:'Oluştur'})).toBeDisabled();

  yaz(/Şoför T\.C\. Kimlik No/,'11111111110');
  expect(screen.getByRole('button',{name:'Oluştur'})).toBeEnabled();
 });

 it('OLUSTUR govdeyi dogru gonderiyor, bos dorseyi NULL yapiyor ve LINES GONDERMIYOR',async()=>{
  kur([],[KALEM]);
  vi.mocked(api.post).mockResolvedValue({data:irsaliye('NONE')} as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByRole('button',{name:/e-İrsaliye Oluştur/}));

  const yaz=(etiket:RegExp,deger:string)=>
   fireEvent.change(screen.getByLabelText(etiket),{target:{value:deger}});
  yaz(/Fiili Sevk Zamanı/,'2026-09-13T08:30');
  yaz(/Şoför Adı Soyadı/,'Ahmet Yilmaz');
  yaz(/Şoför T\.C\. Kimlik No/,'11111111110');
  yaz(/Araç Plakası/,'34ABC123');
  yaz(/Teslim Adresi/,'Depo Yolu 7');
  yaz(/Teslim Posta Kodu/,'34710');
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  // DÜZ SEVKTE `lines` YOK: gövdesiz istek sunucuda "her kalemin kalanı"
  // demektir (E4a davranışı) ve boş bir `lines` 422 olurdu.
  await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/despatch-notes',{
   invoice_id:17,
   actual_shipment_at:'2026-09-13T08:30',
   driver_name:'Ahmet Yilmaz',
   driver_national_id:'11111111110',
   vehicle_plate:'34ABC123',
   // BOS DORSE `null` gider, bos dize DEGIL: bos dize saklanirsa UBL'de
   // bos bir `DORSEPLAKA` elemani dogardi.
   trailer_plate:null,
   delivery_address:'Depo Yolu 7',
   // GİB şematronu zorunlu tutuyor (sandbox'ta ölçüldü).
   delivery_postal_code:'34710',
  }));
  expect(await screen.findByText(/kaydı oluşturuldu/)).toBeInTheDocument();
 });

 it('GONDER ve SORGULA dogru uclari cagiriyor',async()=>{
  kur([irsaliye('NONE')],[KALEM]);
  vi.mocked(api.post).mockResolvedValue({data:irsaliye('QUEUED')} as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Gönder'}));
  await waitFor(()=>expect(api.post)
   .toHaveBeenCalledWith('/despatch-notes/7/edespatch/submit'));
  expect(await screen.findByText('e-İrsaliye: kuyrukta')).toBeInTheDocument();

  // `sync` LİSTEYİ TAZELİYOR (kalanlar ve `complete` değişmiş olabilir),
  // o yüzden sahte liste de sorgudan sonraki durumu vermeli.
  kur([irsaliye('SENT')],[KALEM]);
  vi.mocked(api.post).mockResolvedValue({data:irsaliye('SENT')} as never);
  fireEvent.click(screen.getByRole('button',{name:/Durumu Sorgula/}));
  await waitFor(()=>expect(api.post)
   .toHaveBeenCalledWith('/despatch-notes/7/edespatch/sync'));
  expect(await screen.findByText('e-İrsaliye: gönderildi')).toBeInTheDocument();
 });

 it('sunucu hatasini gosteriyor',async()=>{
  kur([irsaliye('NONE')],[KALEM]);
  vi.mocked(api.post).mockRejectedValue(new Error('409'));
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Gönder'}));
  expect(await screen.findByText('e-İrsaliye gönderilemedi.')).toBeInTheDocument();
 });
});
