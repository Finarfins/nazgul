import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {api,apiDetail} from '../api';
import {
 DespatchNotePanel,EDESPATCH_STATUSES,STATUS_VIEW,type EDespatchStatus,
} from './DespatchNotePanel';

vi.mock('../api',()=>({
 api:{get:vi.fn(),post:vi.fn()},
 apiDetail:vi.fn(),
 errorDetail:(_error:unknown,fallback:string)=>fallback,
 money:(value:unknown)=>String(value??''),
}));

// İZİN ANAHTARI: panel yanıt bölmesini `can('sales')` ile kapatıyor ve
// depo/rapor kullanıcısı burada `false` alıyor.
let satisIzni=true;
vi.mock('../AuthContext',()=>({
 useAuth:()=>({can:(permission:string)=>permission==='sales'&&satisIzni}),
}));

const TAMAMLANDI='Faturanın bütün mal kalemleri sevk edildi.';

const ETTN='11111111-2222-3333-4444-555555555555';

type SahteIrsaliye={id:number;edespatch_status:EDespatchStatus;[key:string]:unknown};

const irsaliye=(id:number,status:EDespatchStatus,ek:Record<string,unknown>={}):SahteIrsaliye=>({
 id,invoice_id:17,despatch_uuid:`${ETTN}-${id}`,despatch_number:`IRS-FTR-${id}`,
 issue_date:'2026-09-13',
 edespatch_status:status,edespatch_gib_status_code:null,
 edespatch_provider_uuid:null,edespatch_last_error:null,response_status:null,
 driver_name:'Ahmet Yilmaz',vehicle_plate:'34ABC123',
 lines:[{line_no:1,item_name:'Fren Balatası',quantity:'4.0000'}],
 ...ek,
});

// `despatchable-items` HİZMET KALEMİ TAŞIMAZ — sunucu LABOR'ı zaten eliyor.
const kalem=(id:number,description:string,despatched:string,remaining:string)=>({
 invoice_item_id:id,invoice_line_no:id-100,description,product_id:id,
 invoiced:'10.0000',despatched,remaining,
});
const BALATA=kalem(101,'Fren Balatası','0.0000','10.0000');
const FILTRE=kalem(102,'Yağ Filtresi','2.0000','3.0000');

type Kurulum={
 notes?:SahteIrsaliye[];
 items?:unknown[];
 complete?:boolean;
 response?:unknown;
};

const kur=({notes=[],items=[],complete=false,response=null}:Kurulum={})=>{
 vi.mocked(api.get).mockImplementation(((url:string)=>{
  if(url.startsWith('/despatch-notes?'))
   return Promise.resolve({data:{items:notes,total:notes.length}});
  if(url.includes('/despatchable-items'))
   return Promise.resolve({data:{invoice_id:17,items,complete}});
  if(url.endsWith('/response'))return Promise.resolve({data:response});
  const id=Number(url.split('/')[2]);
  return Promise.resolve({data:notes.find(note=>note.id===id)??{}});
 }) as never);
};

// Kısmi sevk diyalogunu açıp ZORUNLU şoför/plaka alanlarını doldurur.
const diyalogAc=async(buton:RegExp|string='Kısmi sevk')=>{
 fireEvent.click(await screen.findByRole('button',{name:buton}));
 const yaz=(etiket:RegExp,deger:string)=>
  fireEvent.change(screen.getByLabelText(etiket),{target:{value:deger}});
 yaz(/Fiili Sevk Zamanı/,'2026-09-13T08:30');
 yaz(/Şoför Adı Soyadı/,'Ahmet Yilmaz');
 yaz(/Şoför T\.C\. Kimlik No/,'11111111110');
 yaz(/Araç Plakası/,'34ABC123');
 yaz(/Teslim Adresi/,'Depo Yolu 7');
 yaz(/Teslim Posta Kodu/,'34710');
};

const miktarKutusu=(itemId:number)=>within(screen.getByTestId(`sevk-satiri-${itemId}`))
 .getByRole('spinbutton');

describe('E4b-3 kısmi sevk diyalogu',()=>{
 beforeEach(()=>{satisIzni=true;vi.clearAllMocks();vi.mocked(apiDetail).mockReturnValue(undefined)});
 afterEach(()=>cleanup());

 it('diyalog YALNIZ sevk edilebilir kalemleri kalanlarıyla listeliyor',async()=>{
  // HİZMET KALEMİ SUNUCUDAN GELMİYOR; arayüz ikinci bir süzgeç yazmıyor.
  kur({items:[BALATA,FILTRE]});
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  expect(api.get).toHaveBeenCalledWith('/invoices/17/despatchable-items');
  expect(screen.getByTestId('sevk-satiri-101')).toBeInTheDocument();
  const filtre=within(screen.getByTestId('sevk-satiri-102'));
  // `quantityDecimal(...).toFixed()` — projenin miktar biçimi (yeni bir
  // biçimlendirici YOK): sondaki sıfırlar taşınmaz, `PartiMutabakati`yle AYNI.
  expect(filtre.getByText('2')).toBeInTheDocument();
  expect(filtre.getByText('3')).toBeInTheDocument();
  // VARSAYILAN KALANDIR.
  expect(miktarKutusu(102)).toHaveValue(3);
 });

 it('gövde TAM: `lines` DIZE miktarlarla, SIFIR satır YOK',async()=>{
  kur({items:[BALATA,FILTRE]});
  vi.mocked(api.post).mockResolvedValue({data:irsaliye(7,'NONE')} as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  fireEvent.change(miktarKutusu(101),{target:{value:'2.5'}});
  // SIFIR = "bu kalemi sevk etme"; gövdeye GİRMEZ.
  fireEvent.change(miktarKutusu(102),{target:{value:'0'}});
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/despatch-notes',{
   invoice_id:17,
   actual_shipment_at:'2026-09-13T08:30',
   driver_name:'Ahmet Yilmaz',
   driver_national_id:'11111111110',
   vehicle_plate:'34ABC123',
   trailer_plate:null,
   delivery_address:'Depo Yolu 7',
   delivery_postal_code:'34710',
   // MİKTAR DİZE, `Number` DEĞİL: dört haneli ölçek float'a çevrilseydi
   // sunucunun kalan hesabına ikili kalıntı sızardı.
   lines:[{invoice_item_id:101,quantity:'2.5'}],
  }));
 });

 it('her satır BOŞ/SIFIRken OLUSTUR kapalı — SEVK_KALEMI_YOK istemcide',async()=>{
  kur({items:[BALATA,FILTRE]});
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  fireEvent.change(miktarKutusu(101),{target:{value:'0'}});
  fireEvent.change(miktarKutusu(102),{target:{value:''}});
  expect(screen.getByRole('button',{name:'Oluştur'})).toBeDisabled();
  expect(api.post).not.toHaveBeenCalled();
 });

 it('SEVK_MIKTAR_ASIMI mesajı DOĞRU SATIRIN altında',async()=>{
  kur({items:[BALATA,FILTRE]});
  vi.mocked(api.post).mockRejectedValue(new Error('422'));
  vi.mocked(apiDetail).mockReturnValue({
   code:'SEVK_MIKTAR_ASIMI',invoice_item_id:102,remaining:'3.0000',requested:'9.0000',
  });
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  fireEvent.change(miktarKutusu(102),{target:{value:'9'}});
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  const hatali=within(screen.getByTestId('sevk-satiri-102'));
  expect(await hatali.findByText(/Sevk miktarı kalan miktarı aşıyor/)).toBeInTheDocument();
  expect(hatali.getByText(/Kalan: 3\./)).toBeInTheDocument();
  // MESAJ SUÇLU SATIRDA: temiz satır temiz kalır.
  expect(within(screen.getByTestId('sevk-satiri-101'))
   .queryByText(/kalan miktarı aşıyor/)).toBeNull();
 });

 it('409 IRSALIYE_TAMAMLANDI → toast ve LİSTE TAZELENİYOR',async()=>{
  kur({items:[BALATA]});
  vi.mocked(api.post).mockRejectedValue(new Error('409'));
  vi.mocked(apiDetail).mockReturnValue({code:'IRSALIYE_TAMAMLANDI'});
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  const oncekiListeCagrilari=vi.mocked(api.get).mock.calls
   .filter(([url])=>String(url).startsWith('/despatch-notes?')).length;
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  expect(await screen.findByText(TAMAMLANDI))
   .toBeInTheDocument();
  await waitFor(()=>expect(vi.mocked(api.get).mock.calls
   .filter(([url])=>String(url).startsWith('/despatch-notes?')).length)
   .toBeGreaterThan(oncekiListeCagrilari));
 });

 it('hepsi sevk edildiğinde İKİ BUTON DA kapalı ve sebebi yazıyor',async()=>{
  kur({items:[kalem(101,'Fren Balatası','10.0000','0.0000')],complete:true});
  render(<DespatchNotePanel invoiceId={17}/>);
  expect(await screen.findByRole('button',{name:'Kısmi sevk'})).toBeDisabled();
  expect(screen.getByRole('button',{name:/e-İrsaliye Oluştur/})).toBeDisabled();
  expect(screen.getAllByLabelText('Tüm kalemler sevk edildi').length).toBeGreaterThan(0);
 });
});

describe('E4b-3 sunucunun adı konmuş 422 kodları',()=>{
 beforeEach(()=>{satisIzni=true;vi.clearAllMocks();vi.mocked(apiDetail).mockReturnValue(undefined)});
 afterEach(()=>cleanup());

 // İstemci KODA bakıyor, metne değil; bu yüzden reddin gövdesi
 // `routers/despatch_notes.py::_hata` ile AYNI biçimde kuruluyor.
 const reddet=(code:string,ek:Record<string,unknown>={})=>{
  vi.mocked(api.post).mockRejectedValue(new Error('422'));
  vi.mocked(apiDetail).mockReturnValue({code,message:'sunucu metni',...ek});
 };

 it('HIZMET_SATIRI_SEVK_EDILMEZ mesajı SUÇLU SATIRIN altında',async()=>{
  // Liste hizmet kalemi taşımaz; bu 422 ancak liste BAYATLADIĞINDA gelir
  // (kalem arada hizmete döndü) — kimlik hâlâ tabloda, mesaj da orada.
  kur({items:[BALATA,FILTRE]});
  reddet('HIZMET_SATIRI_SEVK_EDILMEZ',{invoice_item_id:102});
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  expect(await within(screen.getByTestId('sevk-satiri-102'))
   .findByText(/Hizmet kalemi e-İrsaliye ile sevk edilmez/)).toBeInTheDocument();
  expect(within(screen.getByTestId('sevk-satiri-101'))
   .queryByText(/Hizmet kalemi/)).toBeNull();
 });

 it('SEVK_SATIRI_TEKRAR mesajı SUÇLU SATIRIN altında',async()=>{
  kur({items:[BALATA,FILTRE]});
  reddet('SEVK_SATIRI_TEKRAR',{invoice_item_id:101});
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  expect(await within(screen.getByTestId('sevk-satiri-101'))
   .findByText(/iki kez yazılamaz/)).toBeInTheDocument();
 });

 it('FATURA_KALEMI_YOK: kimlik TABLODA YOK → mesaj DİYALOĞUN başında',async()=>{
  // KRİTİK: bu kodun kimliği TANIMI GEREĞİ bu faturanın değil. Satır
  // altına yazılsaydı öyle bir satır olmadığı için HİÇBİR ŞEY görünmez,
  // kullanıcı sessizce başarısız bir "Oluştur"la kalırdı.
  kur({items:[BALATA,FILTRE]});
  reddet('FATURA_KALEMI_YOK',{invoice_item_id:999});
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  expect(await screen.findByText(/Bu kalem faturaya ait değil/)).toBeInTheDocument();
  // Diyalog AÇIK kalıyor: kullanıcı listeyi yenileyip tekrar deneyecek.
  expect(screen.getByTestId('sevk-satiri-101')).toBeInTheDocument();
 });

 it('TANINMAYAN kimlik taşıyan HERHANGİ bir satır kodu da diyaloğa düşüyor',async()=>{
  // Kural koda değil KİMLİĞİN EŞLEŞMESİNE bakıyor: sunucu yarın başka bir
  // kodda da tanınmayan kimlik yollarsa mesaj yine GÖRÜNÜR.
  kur({items:[BALATA]});
  reddet('SEVK_MIKTAR_ASIMI',{invoice_item_id:404,remaining:'3.0000'});
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  expect(await screen.findByText(/Sevk miktarı kalan miktarı aşıyor\. Kalan: 3\./))
   .toBeInTheDocument();
 });

 it('SUNUCU SEVK_KALEMI_YOK (tam sevk yolu) diyalogda gösteriliyor',async()=>{
  // Tam sevkte istemci tarafı kapı YOK — gövde `lines` taşımıyor ve
  // "faturada mal kalemi yok" kararı YALNIZ sunucuda verilebiliyor.
  kur({items:[BALATA]});
  reddet('SEVK_KALEMI_YOK');
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc(/e-İrsaliye Oluştur/);
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  expect(await screen.findByText(
   'Sevk edilecek kalem seçilmedi; en az bir satıra miktar yazın.',
  )).toBeInTheDocument();
  // TAM SEVKTE `lines` GÖNDERİLMEZ.
  expect(vi.mocked(api.post).mock.calls[0][1]).not.toHaveProperty('lines');
 });
});

describe('E4b-3 liste tazeleme ve dayanıklılık',()=>{
 beforeEach(()=>{satisIzni=true;vi.clearAllMocks();vi.mocked(apiDetail).mockReturnValue(undefined)});
 afterEach(()=>cleanup());

 it('detay isteği DÜŞERSE satır LİSTE VERİSİYLE çiziliyor',async()=>{
  // `.catch(()=>note)`: tek bir irsaliyenin detayı 500 verdi diye BÜTÜN
  // panelin boşalması, kullanıcının elindeki bilgiyi de yok ederdi.
  kur({notes:[irsaliye(7,'SENT')],items:[BALATA]});
  const listeliGet=vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(((url:string)=>
   url==='/despatch-notes/7'
    ?Promise.reject(new Error('500'))
    :listeliGet(url)) as never);

  render(<DespatchNotePanel invoiceId={17}/>);
  const satir=within(await screen.findByTestId('irsaliye-7'));
  expect(satir.getByText(/IRS-FTR-7 · 13\.09\.2026/)).toBeInTheDocument();
  expect(satir.getByText('e-İrsaliye: gönderildi')).toBeInTheDocument();
  // Panel de ayakta: genel hata uyarısı YOK.
  expect(screen.queryByText('e-İrsaliye durumu yüklenemedi.')).toBeNull();
 });

 it('oluşturulan irsaliye POST GÖVDESİNDEN listede beliriyor',async()=>{
  // Liste tazelemesi (`load(true)`) BEKLETİLİYOR: kayıt, tazeleme dönmeden
  // de görünmeli — yoksa kullanıcı bir tur boş listeye bakar.
  kur({items:[BALATA]});
  const listeliGet=vi.mocked(api.get).getMockImplementation()!;
  let tazelemeyiBirak=()=>{};
  let ilkListeAlindi=false;
  vi.mocked(api.get).mockImplementation(((url:string)=>{
   if(url.startsWith('/despatch-notes?')){
    if(!ilkListeAlindi){ilkListeAlindi=true;return listeliGet(url)}
    return new Promise(resolve=>{tazelemeyiBirak=()=>resolve({data:{
     items:[irsaliye(7,'NONE',{despatch_number:'IRS-LISTE-7'})],total:1}})});
   }
   return listeliGet(url);
  }) as never);
  vi.mocked(api.post).mockResolvedValue({
   data:irsaliye(7,'NONE',{despatch_number:'IRS-POST-7'}),
  } as never);

  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  // POST GÖVDESİ: liste tazelemesi HENÜZ DÖNMEDİ.
  expect(await screen.findByText(/IRS-POST-7/)).toBeInTheDocument();
  expect(screen.getByText(/e-İrsaliye kaydı oluşturuldu/)).toBeInTheDocument();
  tazelemeyiBirak();
  expect(await screen.findByText(/IRS-LISTE-7/)).toBeInTheDocument();
 });

 it('409 sonrası TAZELEME butonları KAPATIYOR ve sebebini yazıyor',async()=>{
  // 409 yalnız bir toast değil: kaynağın durumu DEĞİŞTİ ve ekranın da
  // değişmesi gerekiyor — açık kalan bir "Kısmi sevk" butonu yeni bir 409
  // davetiyesidir.
  kur({items:[BALATA]});
  vi.mocked(api.post).mockRejectedValue(new Error('409'));
  vi.mocked(apiDetail).mockReturnValue({code:'IRSALIYE_TAMAMLANDI'});
  render(<DespatchNotePanel invoiceId={17}/>);
  await diyalogAc();

  // Tazeleme artık `complete` dönüyor (diyalog açıkken arka plan
  // butonları `aria-hidden`; kapı 409'DAN SONRA ölçülüyor).
  kur({items:[kalem(101,'Fren Balatası','10.0000','0.0000')],complete:true});
  fireEvent.click(screen.getByRole('button',{name:'Oluştur'}));

  expect(await screen.findByText(TAMAMLANDI)).toBeInTheDocument();
  await waitFor(()=>expect(screen.getByRole('button',{name:'Kısmi sevk'})).toBeDisabled());
  expect(screen.getByRole('button',{name:/e-İrsaliye Oluştur/})).toBeDisabled();
  expect(screen.getAllByLabelText('Tüm kalemler sevk edildi').length).toBeGreaterThan(0);
 });
});

describe('E4b-3 çoklu irsaliye ve yanıt görünümü',()=>{
 beforeEach(()=>{satisIzni=true;vi.clearAllMocks();vi.mocked(apiDetail).mockReturnValue(undefined)});
 afterEach(()=>cleanup());

 it('FATURA BAŞINA ÇOK İRSALİYE: hepsi kendi durumu ve satır sayısıyla',async()=>{
  // E4a `note?.` varsayımı düştü (E4b-1 göç 0087: UNIQUE kalktı).
  kur({notes:[irsaliye(7,'SENT'),irsaliye(9,'NONE')],items:[BALATA]});
  render(<DespatchNotePanel invoiceId={17}/>);
  expect(await screen.findByTestId('irsaliye-7')).toBeInTheDocument();
  expect(screen.getByTestId('irsaliye-9')).toBeInTheDocument();
  expect(within(screen.getByTestId('irsaliye-7'))
   .getByText(/IRS-FTR-7 · 13\.09\.2026 · 1 satır/)).toBeInTheDocument();
  expect(within(screen.getByTestId('irsaliye-7'))
   .getByText('e-İrsaliye: gönderildi')).toBeInTheDocument();
  expect(within(screen.getByTestId('irsaliye-9'))
   .getByText('e-İrsaliye: gönderilmedi')).toBeInTheDocument();
  // HER SATIR KENDİ EYLEMLERİNİ TAŞIR.
  expect(within(screen.getByTestId('irsaliye-9'))
   .getByRole('button',{name:'Gönder'})).toBeEnabled();
  expect(within(screen.getByTestId('irsaliye-7'))
   .getByRole('button',{name:'Gönder'})).toBeDisabled();
 });

 it('KISMI_KABUL yanıtı: satırlar dökülüyor, REDDEDİLEN vurgulanıyor',async()=>{
  kur({
   notes:[irsaliye(7,'PARTIALLY_ACCEPTED')],items:[BALATA],
   response:{
    despatch_id:7,edespatch_status:'PARTIALLY_ACCEPTED',response_status:'KISMI_KABUL',
    response_received_at:'2026-09-16T10:00:00+00:00',
    implicit_accept_due_at:'2026-09-20T08:30:00+00:00',
    responses_count:2,
    response:{response_uuid:'u-1',response_number:'YNT-3',response_type:'KISMI_KABUL',
     issue_date:'2026-09-16',notes:'İki koli hasarlı',created_at:'2026-09-16T10:00:00+00:00'},
    lines:[
     {despatch_line_id:1,line_no:1,product_id:5,item_name:'Fren Balatası',
      despatched_quantity:'4.0000',received_quantity:'4.0000',
      rejected_quantity:'0.0000',reject_reason:null},
     {despatch_line_id:2,line_no:2,product_id:6,item_name:'Yağ Filtresi',
      despatched_quantity:'3.0000',received_quantity:'1.0000',
      rejected_quantity:'2.0000',reject_reason:'Hasarlı'},
    ],
   },
  });
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByText('Yanıt'));

  expect(await screen.findByText('Kısmi kabul')).toBeInTheDocument();
  expect(screen.getByText('16.09.2026')).toBeInTheDocument();
  expect(screen.getByText('İki koli hasarlı')).toBeInTheDocument();
  expect(screen.getByText('2 yanıt, ilki (geçerli olan) gösteriliyor')).toBeInTheDocument();
  // REDDEDİLEN SATIR VURGULU, temiz satır DEĞİL.
  expect(screen.getByTestId('yanit-satiri-2')).toHaveAttribute('data-redli','1');
  expect(screen.getByTestId('yanit-satiri-1')).toHaveAttribute('data-redli','0');
  expect(within(screen.getByTestId('yanit-satiri-2')).getByText('Hasarlı'))
   .toBeInTheDocument();
  // YANIT GELDİ → ZIMNİ KABUL SAYACI GİZLİ (yoksa "hâlâ bekleniyor"
  // diye okunurdu).
  expect(screen.queryByText(/Zımni kabul/)).toBeNull();
 });

 it('yanıt YOKKEN zımni kabul tarihi gösteriliyor',async()=>{
  kur({
   notes:[irsaliye(7,'DELIVERED')],items:[BALATA],
   response:{
    despatch_id:7,edespatch_status:'DELIVERED',response_status:null,
    response_received_at:null,implicit_accept_due_at:'2026-09-20T08:30:00+00:00',
    responses_count:0,response:null,lines:[],
   },
  });
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByText('Yanıt'));
  expect(await screen.findByText('Zımni kabul: 20.09.2026')).toBeInTheDocument();
 });

 it('DEPO kullanıcısı yanıt bölmesini HİÇ görmüyor',async()=>{
  // Uç `sales` istiyor; görünüp 403 veren bir bölme yerine GÖRÜNMEYEN.
  satisIzni=false;
  kur({notes:[irsaliye(7,'ACCEPTED')],items:[BALATA]});
  render(<DespatchNotePanel invoiceId={17}/>);
  await screen.findByTestId('irsaliye-7');
  expect(screen.queryByText('Yanıt')).toBeNull();
  expect(vi.mocked(api.get).mock.calls
   .some(([url])=>String(url).endsWith('/response'))).toBe(false);
 });

 it('sync sonrası özet toast\'ta ve YANIT yeniden sorulacak',async()=>{
  kur({
   notes:[irsaliye(7,'DELIVERED')],items:[BALATA],
   response:{
    despatch_id:7,edespatch_status:'DELIVERED',response_status:null,
    response_received_at:null,implicit_accept_due_at:null,
    responses_count:0,response:null,lines:[],
   },
  });
  vi.mocked(api.post).mockResolvedValue({data:{
   ...irsaliye(7,'ACCEPTED'),changed:true,
   receipt_advice:{asked:true,found:2,recorded:1,skipped:1},
  }} as never);
  render(<DespatchNotePanel invoiceId={17}/>);
  fireEvent.click(await screen.findByText('Yanıt'));
  await screen.findByText(/henüz ticari yanıt gelmedi/);
  const oncekiYanitCagrilari=vi.mocked(api.get).mock.calls
   .filter(([url])=>String(url).endsWith('/response')).length;

  kur({notes:[irsaliye(7,'ACCEPTED')],items:[BALATA],response:{
   despatch_id:7,edespatch_status:'ACCEPTED',response_status:'KABUL',
   response_received_at:'2026-09-16T10:00:00+00:00',implicit_accept_due_at:null,
   responses_count:1,
   response:{response_uuid:'u-1',response_number:'YNT-1',response_type:'KABUL',
    issue_date:'2026-09-16',notes:null,created_at:'2026-09-16T10:00:00+00:00'},
   lines:[],
  }});
  fireEvent.click(screen.getByRole('button',{name:/Durumu Sorgula/}));

  expect(await screen.findByText(
   'Durum sorgulandı — değişiklik var · kaydedilen 1 · atlanan 1',
  )).toBeInTheDocument();
  // YANIT DA TAZELENDİ: açık bir bölme eski gövdeyi göstermeye devam
  // edemez (sync yeni bir yanıt belgesi kaydetmiş olabilir).
  await waitFor(()=>expect(vi.mocked(api.get).mock.calls
   .filter(([url])=>String(url).endsWith('/response')).length)
   .toBeGreaterThan(oncekiYanitCagrilari));
  expect(await screen.findByText('Kabul')).toBeInTheDocument();
 });
});

describe('E4b-3 durum birliği kapısı',()=>{
 beforeEach(()=>{satisIzni=true;vi.clearAllMocks();vi.mocked(apiDetail).mockReturnValue(undefined)});
 afterEach(()=>cleanup());

 // `app/einvoice/edespatch.py::BILINEN` ON BİR ad taşıyor ve göç
 // `20260915_0089`un CHECK kısıtı da onları. Şema `edespatch_status`ı düz
 // `string` üretiyor (`types.gen.ts`te numaralandırma YOK), o yüzden
 // adlar BURADA çakılı: arka uç bir durum eklerse bu test kırılır ve
 // panelin sessizce `NONE`a düşmesi ÖNLENİR.
 //
 // KARŞILAŞTIRMA ELLE KOPYALANMIŞ BİR LİSTEYE DEĞİL, KODUN İKİNCİ
 // KAYNAĞINA: `STATUS_VIEW`in anahtarları. Elle kopya, `EDESPATCH_STATUSES`
 // ile birlikte güncellenip `STATUS_VIEW`in unutulduğu durumu YEŞİL
 // GEÇİRİRDİ — oysa panelin rozeti oradan okunuyor.
 it('birlik `STATUS_VIEW`in anahtar kümesiyle BİREBİR aynı',()=>{
  expect([...EDESPATCH_STATUSES].sort()).toEqual(Object.keys(STATUS_VIEW).sort());
  // ON BİR: sunucudaki `BILINEN` sayısı. Sayı kayarsa iki taraf da
  // aynı anda kaymış olabilir; bu satır onu da yakalar.
  expect(EDESPATCH_STATUSES).toHaveLength(11);
 });

 it.each(EDESPATCH_STATUSES)('%s panelde adı konmuş bir rozet alıyor',async status=>{
  // Sözlükte olmayan bir durum `NONE`a düşerdi: "gönderilmedi" yazan bir
  // REDDEDİLDİ, bu ekranın anlatabileceği EN KÖTÜ yalan.
  kur({notes:[irsaliye(7,status as EDespatchStatus)],items:[BALATA]});
  render(<DespatchNotePanel invoiceId={17}/>);
  const rozet=await within(await screen.findByTestId('irsaliye-7'))
   .findByText(/^e-İrsaliye: /);
  if(status!=='NONE')expect(rozet).not.toHaveTextContent('gönderilmedi');
 });

 it.each([
  ['ACCEPTED','e-İrsaliye: kabul edildi','MuiChip-colorSuccess'],
  ['PARTIALLY_ACCEPTED','e-İrsaliye: kısmen kabul edildi','MuiChip-colorWarning'],
  ['REJECTED','e-İrsaliye: reddedildi','MuiChip-colorError'],
 ] as const)('%s rozeti doğru renkte',async(status,label,sinif)=>{
  kur({notes:[irsaliye(7,status)],items:[BALATA]});
  render(<DespatchNotePanel invoiceId={17}/>);
  const etiket=await screen.findByText(label);
  expect(etiket.closest('.MuiChip-root')).toHaveClass(sinif);
 });
});
