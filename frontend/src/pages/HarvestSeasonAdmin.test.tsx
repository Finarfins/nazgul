import {cleanup,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {ThemeProvider,createTheme} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import HarvestSeasonAdmin,{previewWarning} from './HarvestSeasonAdmin';

const get=vi.fn();
const post=vi.fn();
const put=vi.fn();
const del=vi.fn();
let finance=true;

vi.mock('../api',()=>({
 api:{
  get:(...args:unknown[])=>get(...args),
  post:(...args:unknown[])=>post(...args),
  put:(...args:unknown[])=>put(...args),
  delete:(...args:unknown[])=>del(...args),
 },
 money:(value:unknown)=>`${value} TL`,
 errorDetail:(error:unknown,fallback:string)=>{
  const detail=(error as {response?:{data?:{detail?:string}}})?.response?.data?.detail;
  return detail||fallback;
 },
}));
vi.mock('../AuthContext',()=>({
 useAuth:()=>({can:(permission:string)=>permission==='read'||(permission==='finance'&&finance)}),
}));

const REGIONS=[
 {id:1,company_id:1,code:'MERKEZ',name:'Merkez Ova',active:true},
 {id:2,company_id:1,code:'YAYLA',name:'Yayla',active:true},
];
const CALENDARS=[
 {
  id:10,company_id:1,season_code:'PANCAR_SOKUMU',name:'Pancar Sökümü 2026',
  season_year:2026,region_id:null,product_category:null,due_date:'2026-11-30',
  active:true,created_at:'2026-07-01T10:00:00Z',updated_at:'2026-07-01T10:00:00Z',
 },
 {
  id:11,company_id:1,season_code:'YEDEK_SEZON',name:'Yedek Sezonu 2026',
  season_year:2026,region_id:1,product_category:'Yedek',due_date:'2026-10-31',
  active:false,created_at:'2026-07-01T10:00:00Z',updated_at:'2026-07-01T10:00:00Z',
 },
];
// 20 kategorisiz (TÜM kategorilere uyar), 21 Tohum kategorili — ikisi de
// kapsam 50 ve eşit öncelik, yani motor Tohum satışında sıralayamaz. Naif
// kategori-eşitliği bu çifti kaçırırdı; sunucu analizi yakalar.
const RULES=[
 {
  id:20,company_id:1,customer_id:7,region_id:null,product_category:null,
  season_code:'PANCAR_SOKUMU',priority:0,effective_from:'2026-01-01',
  effective_to:'2026-12-31',active:true,scope:50,
 },
 {
  id:21,company_id:1,customer_id:7,region_id:null,product_category:'Tohum',
  season_code:'PANCAR_SOKUMU',priority:0,effective_from:null,
  effective_to:null,active:true,scope:50,
 },
 // Farklı öncelik: motor sıralayabiliyor, çakışma listesinde yok.
 {
  id:22,company_id:1,customer_id:8,region_id:null,product_category:null,
  season_code:'PANCAR_SOKUMU',priority:5,effective_from:null,
  effective_to:null,active:true,scope:50,
 },
];
// Sunucunun motor mantığıyla hesapladığı çakışma; UI bunu olduğu gibi gösterir.
const CONFLICTS=[
 {
  rule_ids:[20,21],scope:50,priority:0,customer_id:7,region_id:null,
  product_category:'tohum',sample_date:'2026-03-01',
  detail:'Aynı öncelikte birden fazla harman vade kuralı eşleşti',
 },
];
const CUSTOMERS=[{id:7,name:'Bereket Çiftliği'},{id:8,name:'Ova Tarım'}];
const PRODUCTS=[
 {id:31,name:'Pancar Tohumu',category:'Tohum'},
 {id:32,name:'Yedek Parça',category:'Yedek'},
];
const PREVIEW={
 due_date:'2026-11-30',calendar_id:10,season_code:'PANCAR_SOKUMU',
 season_name:'Pancar Sökümü 2026',season_year:2026,mode:'rule',customer_id:7,
 customer_region_id:1,transaction_date:'2026-08-01',
 product_categories:['tohum'],rule_ids:[20],
};

function route(url:string){
 if(url==='/harvest-scheduling/regions')return {data:REGIONS};
 if(url==='/harvest-scheduling/calendars')return {data:CALENDARS};
 if(url==='/harvest-scheduling/rules')return {data:RULES};
 if(url==='/harvest-scheduling/rules/conflicts')return {data:CONFLICTS};
 if(url==='/customers')return {data:CUSTOMERS};
 if(url==='/products')return {data:PRODUCTS};
 if(url.startsWith('/harvest-scheduling/preview'))return {data:PREVIEW};
 throw new Error(`Beklenmeyen GET: ${url}`);
}

function mount(){
 return render(<ThemeProvider theme={createTheme()}><MemoryRouter>
  <HarvestSeasonAdmin/>
 </MemoryRouter></ThemeProvider>);
}

const httpError=(status:number,detail:string)=>({response:{status,data:{detail}}});

beforeEach(()=>{
 finance=true;
 get.mockReset();post.mockReset();put.mockReset();del.mockReset();
 get.mockImplementation((url:string)=>Promise.resolve(route(url)));
 post.mockResolvedValue({data:{}});
 put.mockResolvedValue({data:{}});
 del.mockResolvedValue({data:null});
});
afterEach(()=>cleanup());

describe('Harman Sezon Takvimi yönetimi',()=>{
 it('finance yetkisi olmayan kullanıcıya yazma kontrollerini göstermez',async()=>{
  finance=false;
  mount();
  expect(await screen.findByText('MERKEZ')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Yeni'})).toBeNull();
  expect(screen.queryByRole('button',{name:'Düzenle'})).toBeNull();
  expect(screen.queryByRole('button',{name:'Sil'})).toBeNull();
  expect(screen.getByText(/finans\/yönetici rollerine aittir/)).toBeInTheDocument();
 });

 it('finance yetkisiyle bölgeleri listeler ve yeni bölge oluşturur',async()=>{
  const user=userEvent.setup();
  mount();
  expect(await screen.findByText('Merkez Ova')).toBeInTheDocument();

  await user.click(screen.getByRole('button',{name:'Yeni'}));
  const dialog=await screen.findByRole('dialog');
  await user.type(within(dialog).getByRole('textbox',{name:'Bölge Kodu'}),'DERE');
  await user.type(within(dialog).getByRole('textbox',{name:'Bölge Adı'}),'Dere Boyu');
  await user.click(within(dialog).getByRole('button',{name:'Kaydet'}));

  await waitFor(()=>expect(post).toHaveBeenCalledWith(
   '/harvest-scheduling/regions',
   {code:'DERE',name:'Dere Boyu',active:true},
  ));
 });

 it('sezon takvimini vade tarihiyle gösterir ve düzenlemeyi PUT ile gönderir',async()=>{
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('tab',{name:/Sezon Takvimleri/}));

  expect(await screen.findByText('Pancar Sökümü 2026')).toBeInTheDocument();
  expect(screen.getByText('2026-11-30')).toBeInTheDocument();

  await user.click(screen.getAllByRole('button',{name:'Düzenle'})[0]);
  const dialog=await screen.findByRole('dialog');
  expect(within(dialog).getByRole('textbox',{name:'Sezon Adı'})).toHaveValue('Pancar Sökümü 2026');
  await user.click(within(dialog).getByRole('button',{name:'Kaydet'}));

  await waitFor(()=>expect(put).toHaveBeenCalledWith(
   '/harvest-scheduling/calendars/10',
   expect.objectContaining({
    season_code:'PANCAR_SOKUMU',name:'Pancar Sökümü 2026',
    season_year:2026,due_date:'2026-11-30',active:true,
   }),
  ));
 });

 it('sezon yılı ile vade yılı çakışırsa kaydetmeyi engeller',async()=>{
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('tab',{name:/Sezon Takvimleri/}));
  await user.click(screen.getAllByRole('button',{name:'Düzenle'})[0]);
  const dialog=await screen.findByRole('dialog');

  const year=within(dialog).getByRole('spinbutton',{name:'Sezon Yılı'});
  await user.clear(year);
  await user.type(year,'2027');

  expect(await within(dialog).findByText('Vade tarihi sezon yılıyla aynı yılda olmalıdır.'))
   .toBeInTheDocument();
  expect(within(dialog).getByRole('button',{name:'Kaydet'})).toBeDisabled();
  expect(put).not.toHaveBeenCalled();
 });

 it('çözümleyici önceliğini gösterir',async()=>{
  mount();
  expect(await screen.findByText('1. Müşteriye özel')).toBeInTheDocument();
  expect(screen.getByText('5. Genel (varsayılan)')).toBeInTheDocument();
 });

 it('kategorisiz + kategorili çifti sunucu çakışması olarak işaretler',async()=>{
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('tab',{name:/Kurallar/}));

  // Sunucunun bildirdiği çift işaretlenir: 20 kategorisiz, 21 Tohum.
  expect(await screen.findByText(/Motor 1 kural çiftini sıralayamıyor/))
   .toBeInTheDocument();
  const summary=screen.getByRole('listitem');
  expect(summary).toHaveTextContent('Kural #20 ↔ #21');
  expect(summary).toHaveTextContent('tohum kategorisi');
  expect(summary).toHaveTextContent('örnek tarih 2026-03-01');
  // Sunucunun bildirmediği 22 numaralı kural işaretlenmez.
  expect(screen.getAllByText(/Aynı öncelikte çakışma/)).toHaveLength(2);
 });

 it('çakışma listesini kendi mantığıyla türetmez; sunucu boş dönerse uyarmaz',async()=>{
  // Kurallar aynı kapsam+öncelikte kalsa bile, sunucu çakışma bildirmediği
  // sürece ekran uyarı üretmez: naif istemci mantığı kaldırıldı.
  get.mockImplementation((url:string)=>{
   if(url==='/harvest-scheduling/rules/conflicts')return Promise.resolve({data:[]});
   return Promise.resolve(route(url));
  });
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('tab',{name:/Kurallar/}));

  expect((await screen.findAllByText('PANCAR_SOKUMU')).length).toBe(3);
  expect(screen.queryByText(/sıralayamıyor/)).toBeNull();
  expect(screen.queryByText(/Aynı öncelikte çakışma/)).toBeNull();
 });

 it('önizleme müşteri+ürün+tarih ile çözümlenen vadeyi gösterir',async()=>{
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('tab',{name:'Önizleme'}));

  await user.click(await screen.findByRole('combobox',{name:'Müşteri'}));
  await user.click(await screen.findByRole('option',{name:'Bereket Çiftliği'}));
  await user.click(screen.getByRole('combobox',{name:'Ürünler'}));
  await user.click(await screen.findByRole('option',{name:/Pancar Tohumu/}));
  await user.keyboard('{Escape}');

  await user.click(screen.getByRole('button',{name:'Vadeyi Çözümle'}));

  expect(await screen.findByText('2026-11-30')).toBeInTheDocument();
  expect(screen.getByText('Kural eşleşmesi')).toBeInTheDocument();

  const previewCall=get.mock.calls.map(call=>String(call[0]))
   .find(url=>url.startsWith('/harvest-scheduling/preview'));
  expect(previewCall).toContain('customer_id=7');
  expect(previewCall).toContain('product_ids=31');
  // Salt-okuma: önizleme hiçbir yazma çağrısı tetiklemez.
  expect(post).not.toHaveBeenCalled();
  expect(put).not.toHaveBeenCalled();
  expect(del).not.toHaveBeenCalled();
 });

 it('önizleme fail-closed 409 çakışmasını anlaşılır biçimde anlatır',async()=>{
  get.mockImplementation((url:string)=>{
   if(url.startsWith('/harvest-scheduling/preview'))return Promise.reject(
    httpError(409,'Aynı öncelikte birden fazla harman vade kuralı eşleşti'),
   );
   return Promise.resolve(route(url));
  });
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('tab',{name:'Önizleme'}));
  await user.click(await screen.findByRole('combobox',{name:'Müşteri'}));
  await user.click(await screen.findByRole('option',{name:'Bereket Çiftliği'}));
  await user.click(screen.getByRole('combobox',{name:'Ürünler'}));
  await user.click(await screen.findByRole('option',{name:/Pancar Tohumu/}));
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('button',{name:'Vadeyi Çözümle'}));

  expect(await screen.findByText('Aynı öncelikte birden fazla harman vade kuralı eşleşti'))
   .toBeInTheDocument();
  expect(screen.getByText(/önceliğini değiştirin veya birini pasifleştirin/))
   .toBeInTheDocument();
 });

 it('kullanımdaki takvim silinemediğinde sunucu gerekçesini gösterir',async()=>{
  del.mockRejectedValue(httpError(409,'Satışta kullanılan sezon takvimi silinemez'));
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('tab',{name:/Sezon Takvimleri/}));
  await user.click(screen.getAllByRole('button',{name:'Sil'})[0]);

  const dialog=await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button',{name:'Sil'}));

  expect(await screen.findByText('Satışta kullanılan sezon takvimi silinemez'))
   .toBeInTheDocument();
  expect(del).toHaveBeenCalledWith('/harvest-scheduling/calendars/10');
 });

 it('kural silmeyi onay sonrası DELETE ile gönderir',async()=>{
  const user=userEvent.setup();
  mount();
  await user.click(await screen.findByRole('tab',{name:/Kurallar/}));
  await user.click(screen.getAllByRole('button',{name:'Sil'})[0]);
  const dialog=await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button',{name:'Sil'}));

  await waitFor(()=>expect(del).toHaveBeenCalledWith('/harvest-scheduling/rules/20'));
 });

 it('tanımlar yüklenemezse hata gösterir',async()=>{
  get.mockImplementation((url:string)=>{
   if(url.startsWith('/harvest-scheduling/'))return Promise.reject(
    httpError(500,'Harman sezon tanımları yüklenemedi.'),
   );
   return Promise.resolve(route(url));
  });
  mount();
  expect(await screen.findByText('Harman sezon tanımları yüklenemedi.')).toBeInTheDocument();
 });
});

describe('previewWarning',()=>{
 it('karma sezon 409 uyarısını ayrıştırır',()=>{
  const warning=previewWarning(409,'Satış kalemleri farklı harman sezonlarına düşüyor; tek sezon seçin');
  expect(warning.severity).toBe('warning');
  expect(warning.hint).toMatch(/tek sezona ayırın/);
 });

 it('sezon tanımlı değil 422 uyarısını ayrıştırır',()=>{
  const warning=previewWarning(422,'Bu müşteri ve ürünler için uygun harman sezonu tanımlı değil');
  expect(warning.severity).toBe('error');
  expect(warning.hint).toMatch(/Kural ekleyin/);
 });

 it('aktif takvim yok 422 uyarısını ayrıştırır',()=>{
  const warning=previewWarning(422,'PANCAR sezonu için satış tarihinden sonra aktif takvim yok');
  expect(warning.severity).toBe('error');
  expect(warning.hint).toMatch(/Takvim ekleyin/);
 });

 it('bilinmeyen durumda güvenli varsayılana düşer',()=>{
  const warning=previewWarning(undefined,'');
  expect(warning.severity).toBe('error');
  expect(warning.title).toBe('Vade çözümlenemedi.');
 });
});
