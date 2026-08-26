import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import MachineDetail,{MACHINE_TABS,MACHINE_TAB_LABELS,tabIndexForSlug} from './MachineDetail';

// Makine 360 sekme sözleşmesi (docs/ux-yenileme-f0-tasarim.md §4).
//
// Etiketler ÜRETİM KAYNAĞINDAN (`MACHINE_TAB_LABELS`) okunur; kopya literal
// tutmak, etiket değişince testin sessizce yeşil kalmasına yol açardı.
//
// ResponsiveTable masaüstünde DataGrid'e düşer ve jsdom'da sanallaştırma
// yüzünden hücreler güvenilir okunmaz; bu yüzden testlerin çoğu KART (mobil)
// dalında koşar — mobil/masaüstü veri eşitliği de böyle doğrulanır.

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 // useSearchParams KASTEN gerçek: `?sekme=` sözleşmesi test edilecek şeyin
 // ta kendisi, mock'lanırsa test kendi kendini doğrular.
 return {...actual,useNavigate:()=>navigate,useParams:()=>({id:'7'})};
});

const get=vi.fn();
const patch=vi.fn();
vi.mock('../api',()=>({
 api:{get:(...args:any[])=>get(...args),post:vi.fn(),patch:(...args:any[])=>patch(...args)},
 money:(v:any)=>`${Number(v||0).toFixed(2)} ₺`,
 errorDetail:(_e:any,fallback:string)=>fallback,
}));

let canWrite=true;
vi.mock('../AuthContext',()=>({useAuth:()=>({can:(permission:string)=>permission==='machines'?canWrite:true})}));
vi.mock('../components/MachineDialog',()=>({default:()=>null,MACHINE_STATUS_LABELS:{active:'Aktif'}}));

function useCardLayout(matches:boolean){
 window.matchMedia=((query:string)=>({
  matches,media:query,onchange:null,
  addListener:vi.fn(),removeListener:vi.fn(),
  addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn(),
 })) as any;
}

const machine={
 id:7,brand:'John Deere',model:'6120M',manufacturer:'Deere',variant:'Premium',
 serial_number:'SER-1',chassis_number:'SASI-1',registration_number:'34 ABC 01',engine_number:'MTR-1',
 model_year:2019,working_hours:'1250.50',status:'active',is_active:true,
 customer_id:41,customer_name:'Tarım A.Ş.',created_at:'2024-03-05T10:00:00',notes:'Yıllık bakım yapıldı',
};
const readings=[
 {id:1,reading_date:'2026-07-10T09:30:00',reading_hours:'1250.50',reading_type:'normal',source:'manual',reason:null,created_by_name:'Ali Usta'},
 {id:2,reading_date:'2026-06-01T08:00:00',reading_hours:'1100.00',reading_type:'correction',source:'work_order',work_order_id:55,work_order_no:'IE-55',reason:'Sayaç düzeltmesi',created_by_name:'Veli Usta'},
];
const ownership=[
 {id:3,transfer_date:'2025-01-20',from_customer_id:40,from_customer_name:'Önceki Sahip A.Ş.',to_customer_id:41,to_customer_name:'Tarım A.Ş.',reason:'Satış',created_by_name:'Muhasebe'},
];
const orders=[
 {id:55,work_order_no:'IE-55',status:'OPEN',priority:'normal',opened_at:'2026-07-01',complaint:'Hidrolik kaçak',labor_total:'500',parts_total:'250',grand_total:'750'},
 {id:56,work_order_no:'IE-56',status:'OPEN',priority:'high',opened_at:'2026-05-01',complaint:'Fren ayarı',labor_total:'100',parts_total:'0',grand_total:'100'},
];

/** Verilen sayıda sahte sayaç okuması; tam kat sınırlarını kurmak için. */
const makeReadings=(count:number)=>Array.from({length:count},(_unused,index)=>({
 ...readings[0],id:1000+index,reading_hours:`${1000+index}.00`,
}));

type Overrides={
 orders?:any[];readings?:any[];ownership?:any[];
 fail?:string;
 /** `undefined` verilirse uç o alanı HİÇ göndermiyor demektir. */
 ordersTotal?:number;ordersPages?:number;omitOrdersMeta?:boolean;
 ordersByPage?:Record<number,any[]>;
};
function respond(overrides:Overrides={}){
 get.mockImplementation((url:string,config?:any)=>{
  if(overrides.fail&&url.includes(overrides.fail))return Promise.reject(new Error('boom'));
  if(url==='/machines/7')return Promise.resolve({data:machine});
  if(url==='/work-orders'){
   const page=Number(config?.params?.page??1);
   const items=overrides.ordersByPage?.[page]??overrides.orders??orders;
   if(overrides.omitOrdersMeta)return Promise.resolve({data:{items,page,page_size:25}});
   const total=overrides.ordersTotal??items.length;
   const pages=overrides.ordersPages??1;
   return Promise.resolve({data:{items,page,page_size:25,total,pages}});
  }
  if(url==='/machines/7/hour-readings'){
   // GERÇEK uç gibi davran: limit/offset penceresini uygula. Sayfa sınırı
   // hatalarının (tam kat) ancak böyle görünür olduğu yer burası.
   const limit=Number(config?.params?.limit??100);
   const offset=Number(config?.params?.offset??0);
   const dataset=overrides.readings??readings;
   return Promise.resolve({data:dataset.slice(offset,offset+limit)});
  }
  if(url==='/machines/7/ownership-history')return Promise.resolve({data:overrides.ownership??ownership});
  return Promise.resolve({data:[]});
 });
}

beforeEach(()=>{navigate.mockReset();get.mockReset();patch.mockReset();canWrite=true;useCardLayout(true);respond()});
afterEach(()=>cleanup());

function mount(entry='/makineler/7'){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter initialEntries={[entry]}><MachineDetail/></MemoryRouter></ThemeProvider>);
}
const openTab=async(label:string)=>fireEvent.click(await screen.findByRole('tab',{name:new RegExp(label)}));
const workOrderCalls=()=>get.mock.calls.filter(call=>call[0]==='/work-orders');
const readingCalls=()=>get.mock.calls.filter(call=>call[0]==='/machines/7/hour-readings');

describe('Makine 360 sekme kabuğu',()=>{
 it('dört sekmeyi tanımlı sırada çizer ve Kimlik ile açılır',async()=>{
  mount();
  const tabs=await screen.findAllByRole('tab');
  expect(tabs).toHaveLength(MACHINE_TABS.length);
  MACHINE_TABS.forEach((label,index)=>expect(tabs[index]).toHaveTextContent(label));
  expect(tabs[0]).toHaveAttribute('aria-selected','true');
 });

 it('Kimlik sekmesi seri / şasi / motor / plaka alanlarını gösterir',async()=>{
  mount();
  expect(await screen.findByText('SER-1')).toBeInTheDocument();
  expect(screen.getByText('SASI-1')).toBeInTheDocument();
  expect(screen.getByText('MTR-1')).toBeInTheDocument();
  expect(screen.getByText('34 ABC 01')).toBeInTheDocument();
  expect(screen.getByText('Yıllık bakım yapıldı')).toBeInTheDocument();
 });

 it('sekmeler arasında gezinince yalnız ilgili blok görünür',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.readings);
  expect(await screen.findByText('1250.50 saat')).toBeInTheDocument();
  expect(screen.queryByText('SASI-1')).toBeNull();

  await openTab(MACHINE_TAB_LABELS.ownership);
  expect(await screen.findByText('Önceki Sahip A.Ş.')).toBeInTheDocument();
  expect(screen.queryByText('1250.50 saat')).toBeNull();

  await openTab(MACHINE_TAB_LABELS.service);
  expect(await screen.findByText('IE-56')).toBeInTheDocument();
  expect(screen.queryByText('Önceki Sahip A.Ş.')).toBeNull();
 });

 it('Ekler sekmesi yoktur ve hiçbir ek isteği atılmaz',async()=>{
  mount();
  await screen.findByText('SER-1');
  expect(screen.queryByRole('tab',{name:/Ekler/})).toBeNull();
  expect(get.mock.calls.filter(call=>String(call[0]).includes('attachment'))).toHaveLength(0);
 });

 it('sayacı kesin bilinen sekmeler sayıyı yazar, bilinmeyen yazmaz',async()=>{
  mount();
  // Sahiplik ucunda sınır yok → kesin sayı. İş emri ucu total döndürür → kesin.
  expect(await screen.findByRole('tab',{name:new RegExp(`${MACHINE_TAB_LABELS.ownership} \\(1\\)`)})).toBeInTheDocument();
  expect(screen.getByRole('tab',{name:new RegExp(`${MACHINE_TAB_LABELS.service} \\(2\\)`)})).toBeInTheDocument();
  // Sayaç ucu toplam DÖNDÜRMEZ; yüklenen sayfa uzunluğu toplam gibi yazılmaz.
  expect(screen.queryByRole('tab',{name:new RegExp(`${MACHINE_TAB_LABELS.readings} \\(`)})).toBeNull();
 });

 it('masaüstü dalında da sekmeler çalışır (DataGrid kolonlarına düşer)',async()=>{
  useCardLayout(false);
  mount();
  await openTab(MACHINE_TAB_LABELS.service);
  expect(await screen.findByText('Genel Toplam')).toBeInTheDocument();
 });
});

// ---------------------------------------------------------------------------
// 1. madde: sekme başına TEK durum makinesi. Üç bitiş birbirini dışlar.
// ---------------------------------------------------------------------------
describe('Durum makinesi: loading → (error | empty | data)',()=>{
 const EMPTY_TEXTS=[
  'Henüz sayaç okuması kaydedilmedi.',
  'Sahiplik kaydı yok.',
  'Bu makine için henüz iş emri kaydı yok.',
 ];

 it('her sekme istek uçarken yükleme göstergesi çizer',async()=>{
  // Üç uç da askıda: hiçbiri boş-durum metnine düşmemeli.
  get.mockImplementation((url:string)=>url==='/machines/7'
   ?Promise.resolve({data:machine})
   :new Promise(()=>{}));
  mount();
  for(const label of [MACHINE_TAB_LABELS.readings,MACHINE_TAB_LABELS.ownership,MACHINE_TAB_LABELS.service]){
   await openTab(label);
   expect(await screen.findByLabelText('Yükleniyor')).toBeInTheDocument();
   EMPTY_TEXTS.forEach(text=>expect(screen.queryByText(text)).toBeNull());
  }
 });

 it.each([
  ['hour-readings',MACHINE_TAB_LABELS.readings,'Sayaç geçmişi yüklenemedi.'],
  ['ownership-history',MACHINE_TAB_LABELS.ownership,'Sahiplik tarihçesi yüklenemedi.'],
  ['work-orders',MACHINE_TAB_LABELS.service,'Servis geçmişi yüklenemedi.'],
 ])('%s hatası: hata metni var, boş-durum metni YOK',async(endpoint,label,message)=>{
  respond({fail:endpoint});
  mount();
  await openTab(label);
  expect(await screen.findByText(message)).toBeInTheDocument();
  // Kritik: "yüklenemedi" ile "kayıt yok" aynı anda görünemez.
  EMPTY_TEXTS.forEach(text=>expect(screen.queryByText(text)).toBeNull());
 });

 it('bir sekmenin hatası diğer sekmeleri düşürmez',async()=>{
  respond({fail:'hour-readings'});
  mount();
  await openTab(MACHINE_TAB_LABELS.readings);
  expect(await screen.findByText('Sayaç geçmişi yüklenemedi.')).toBeInTheDocument();
  await openTab(MACHINE_TAB_LABELS.ownership);
  expect(await screen.findByText('Önceki Sahip A.Ş.')).toBeInTheDocument();
 });

 it('gerçekten boş liste yalnız boş-durum metnini çizer',async()=>{
  respond({readings:[],ownership:[],orders:[]});
  mount();
  await openTab(MACHINE_TAB_LABELS.readings);
  expect(await screen.findByText(EMPTY_TEXTS[0])).toBeInTheDocument();
  expect(screen.queryByRole('alert')).toBeNull();
  await openTab(MACHINE_TAB_LABELS.ownership);
  expect(await screen.findByText(EMPTY_TEXTS[1])).toBeInTheDocument();
  await openTab(MACHINE_TAB_LABELS.service);
  expect(await screen.findByText(EMPTY_TEXTS[2])).toBeInTheDocument();
  expect(screen.queryByRole('alert')).toBeNull();
 });
});

// ---------------------------------------------------------------------------
// 3. madde: mobil kart = masaüstü tablosunun veri olarak ÜST kümesi.
// ---------------------------------------------------------------------------
describe('Mobil kart ↔ masaüstü kolon eşitliği',()=>{
 it('servis kartı sekiz kolonun tamamını taşır (Öncelik dahil)',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.service);
  const card=(await screen.findByText('IE-55')).closest('.MuiCard-root') as HTMLElement;
  const inCard=within(card);
  expect(inCard.getByText('IE-55')).toBeInTheDocument();          // İş Emri No
  expect(inCard.getByText('Açık')).toBeInTheDocument();           // Durum
  expect(inCard.getByText('Öncelik')).toBeInTheDocument();        // Öncelik
  expect(inCard.getByText('normal')).toBeInTheDocument();
  expect(inCard.getByText('2026-07-01')).toBeInTheDocument();     // Açılış
  expect(inCard.getByText('Hidrolik kaçak')).toBeInTheDocument(); // Şikayet
  expect(inCard.getByText('500.00 ₺')).toBeInTheDocument();       // İşçilik
  expect(inCard.getByText('250.00 ₺')).toBeInTheDocument();       // Parça
  expect(inCard.getByText('750.00 ₺')).toBeInTheDocument();       // Genel Toplam
 });

 it('sayaç kartındaki iş emri bağlantısı gerçek ve tıklanabilir',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.readings);
  const link=await screen.findByRole('link',{name:'IE-55'});
  expect(link).toHaveAttribute('href','/is-emirleri/55');
  // Devre dışı CardActionArea `pointer-events:none` verip bağlantıyı ölü
  // bırakıyordu; kart gövdesi artık düz CardContent.
  expect(link.closest('.Mui-disabled')).toBeNull();
 });

 it('sahiplik kartındaki iki müşteri bağlantısı da tıklanabilir',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.ownership);
  const from=await screen.findByRole('link',{name:'Önceki Sahip A.Ş.'});
  // Başlık kartında da aynı müşteri bağlantısı var; sorgu KARTA daraltılır.
  const card=from.closest('.MuiCard-root') as HTMLElement;
  const to=within(card).getByRole('link',{name:'Tarım A.Ş.'});
  expect(from).toHaveAttribute('href','/musteriler/40');
  expect(to).toHaveAttribute('href','/musteriler/41');
  expect(from.closest('.Mui-disabled')).toBeNull();
  expect(to.closest('.Mui-disabled')).toBeNull();
 });

 it('servis satırına tıklamak iş emrini açar',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.service);
  fireEvent.click(await screen.findByText('IE-55'));
  expect(navigate).toHaveBeenCalledWith('/is-emirleri/55');
 });
});

// ---------------------------------------------------------------------------
// 4. madde: sessiz kesme yok. Tavan varsa görünür, yoksa toplam yazılır.
// ---------------------------------------------------------------------------
describe('Sayfalama',()=>{
 it('iş emri sekmesi sabit 200 tavanı yerine sayfa ister',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.service);
  await screen.findByText('IE-55');
  const params=workOrderCalls()[0][1].params;
  expect(params.page).toBe(1);
  expect(params.page_size).toBe(25);
  expect(params.machine_id).toBe('7');
 });

 it('birden fazla sayfa varsa toplam ve sayfa no gösterilir, Sonraki ikinci sayfayı getirir',async()=>{
  const second=[{...orders[0],id:99,work_order_no:'IE-99'}];
  respond({ordersByPage:{1:orders,2:second},ordersTotal:26,ordersPages:2});
  mount();
  await openTab(MACHINE_TAB_LABELS.service);
  expect(await screen.findByText('Toplam 26 kayıt · Sayfa 1/2')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button',{name:'Sonraki'}));
  expect(await screen.findByText('IE-99')).toBeInTheDocument();
  await waitFor(()=>expect(workOrderCalls()[1][1].params.page).toBe(2));
  expect(screen.getByText('Toplam 26 kayıt · Sayfa 2/2')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Sonraki'})).toBeDisabled();
 });

 it('tek sayfaya sığan iş emri listesinde toplam yazılır, sayfa düğmesi çıkmaz',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.service);
  expect(await screen.findByText('Toplam 2 kayıt')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Sonraki'})).toBeNull();
 });

 it('sayaç ucu toplam döndürmediği için sayfa no yazılır, Sonraki ikinci sayfayı getirir',async()=>{
  respond({readings:makeReadings(26)});
  mount();
  await openTab(MACHINE_TAB_LABELS.readings);
  expect(await screen.findByText('Sayfa 1 · toplam bilinmiyor')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button',{name:'Sonraki'}));
  await waitFor(()=>expect(readingCalls()[1][1].params.offset).toBe(25));
  expect(await screen.findByText('Sayfa 2 · toplam bilinmiyor')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Sonraki'})).toBeDisabled();
 });

 // --- Sayfa sınırı: TAM KAT sayılar ---------------------------------------
 // Eski `rows.length===PAGE_SIZE` testi tam katlarda yanılıyordu: son sayfa
 // dolu olduğu için "sonraki var" sanıp kullanıcıyı BOŞ sayfaya götürüyordu.
 // "+1 deseni" (bir fazla kayıt iste, fazlalığı çizme) bunu kapatır.
 it('tam 25 okuma: tek sayfadır, Sonraki hiç çıkmaz',async()=>{
  respond({readings:makeReadings(25)});
  mount();
  await openTab(MACHINE_TAB_LABELS.readings);
  expect(await screen.findByText('Toplam 25 kayıt')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Sonraki'})).toBeNull();
  // Fazlalık istenir ama ÇİZİLMEZ: sayfa boyutu 25 kalır.
  expect(readingCalls()[0][1].params.limit).toBe(26);
 });

 it('tam 50 okuma: ikinci sayfa son sayfadır, orada Sonraki kapalıdır',async()=>{
  respond({readings:makeReadings(50)});
  mount();
  await openTab(MACHINE_TAB_LABELS.readings);
  expect(await screen.findByText('Sayfa 1 · toplam bilinmiyor')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Sonraki'})).toBeEnabled();

  fireEvent.click(screen.getByRole('button',{name:'Sonraki'}));
  expect(await screen.findByText('Sayfa 2 · toplam bilinmiyor')).toBeInTheDocument();
  // 50 kayıt tam iki sayfa: üçüncü sayfa YOK.
  expect(screen.getByRole('button',{name:'Sonraki'})).toBeDisabled();
 });

 // --- Metadata eksikse uydurma ---------------------------------------------
 it('uç total/pages göndermezse "Toplam 0 kayıt" YAZILMAZ',async()=>{
  const fullPage=Array.from({length:25},(_unused,index)=>({...orders[0],id:200+index,work_order_no:`IE-${200+index}`}));
  respond({orders:fullPage,omitOrdersMeta:true});
  mount();
  await openTab(MACHINE_TAB_LABELS.service);
  await screen.findByText('IE-200');
  // Satırlar ekrandayken "0 kayıt" yazmak eksik veriyi YANLIŞ göstermektir.
  expect(screen.queryByText(/Toplam 0 kayıt/)).toBeNull();
  expect(screen.getByText('Sayfa 1 · toplam bilinmiyor')).toBeInTheDocument();
  // Sekme başlığına da uydurma sayı yazılmaz.
  expect(screen.queryByRole('tab',{name:new RegExp(`${MACHINE_TAB_LABELS.service} \\(`)})).toBeNull();
 });

 it('metadata yoksa ama liste tek sayfaya sığıyorsa toplam TÜRETİLİR',async()=>{
  respond({omitOrdersMeta:true});
  mount();
  await openTab(MACHINE_TAB_LABELS.service);
  // 2 satır geldi ve sayfa dolmadı → toplam kesin olarak 2'dir (uydurma değil).
  expect(await screen.findByText('Toplam 2 kayıt')).toBeInTheDocument();
 });

 it('sahiplik ucunda tavan yok: sayfalama şeridi hiç çizilmez',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.ownership);
  await screen.findByText('Önceki Sahip A.Ş.');
  expect(screen.queryByRole('button',{name:'Sonraki'})).toBeNull();
  expect(screen.queryByText(/Sayfa/)).toBeNull();
 });
});

// ---------------------------------------------------------------------------
// 5. madde: ?sekme= — paylaşılabilir, yenilemeye dayanıklı, geçmişi kirletmez.
// ---------------------------------------------------------------------------
describe('?sekme= sorgu parametresi',()=>{
 it('geçersiz ve eksik slug ilk sekmeye düşer (hata değil)',()=>{
  expect(tabIndexForSlug(null)).toBe(0);
  expect(tabIndexForSlug('')).toBe(0);
  expect(tabIndexForSlug('yokboyle')).toBe(0);
  expect(tabIndexForSlug('servis')).toBe(3);
 });

 it('derin bağlantı doğrudan ilgili sekmeyi açar',async()=>{
  mount('/makineler/7?sekme=servis');
  const tabs=await screen.findAllByRole('tab');
  expect(tabs[3]).toHaveAttribute('aria-selected','true');
  expect(await screen.findByText('IE-55')).toBeInTheDocument();
 });

 it('geçersiz slug ile açılan sayfa Kimlik sekmesini gösterir',async()=>{
  mount('/makineler/7?sekme=uzay');
  const tabs=await screen.findAllByRole('tab');
  expect(tabs[0]).toHaveAttribute('aria-selected','true');
  expect(await screen.findByText('SASI-1')).toBeInTheDocument();
 });

 it('sekme değişimi rota değiştirmez (navigate çağrılmaz)',async()=>{
  mount();
  await openTab(MACHINE_TAB_LABELS.readings);
  await openTab(MACHINE_TAB_LABELS.ownership);
  expect(navigate).not.toHaveBeenCalled();
 });
});

describe('Yetki',()=>{
 it('yazma izni olmayan kullanıcı eylem düğmelerini görmez',async()=>{
  canWrite=false;
  mount();
  await screen.findByText('SER-1');
  expect(screen.queryByRole('button',{name:'Düzenle'})).toBeNull();
  await openTab(MACHINE_TAB_LABELS.readings);
  expect(screen.queryByRole('button',{name:'Yeni Okuma'})).toBeNull();
  await openTab(MACHINE_TAB_LABELS.ownership);
  expect(screen.queryByRole('button',{name:'Sahiplik Devret'})).toBeNull();
 });
});
