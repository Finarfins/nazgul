import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import {ThemeProvider} from '@mui/material/styles';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

import {ALL_NAV_ITEMS,NAV_GROUPS,NAV_LABELS,PINNED_ITEMS} from '../navigation';
import {getAppTheme} from '../theme';
import AppShell from './AppShell';

const navigate=vi.fn();
vi.mock('react-router-dom',async()=>{
 const actual=await vi.importActual<any>('react-router-dom');
 return {...actual,useNavigate:()=>navigate};
});
vi.mock('../api',()=>({api:{get:vi.fn(()=>Promise.resolve({data:{items:[]}})),post:vi.fn()}}));
vi.mock('../ThemeContext',()=>({useAppTheme:()=>({mode:'light',toggle:vi.fn()})}));

// Rol izinleri backend/app/auth.py ROLE_PERMISSIONS ile birebir aynıdır.
const ROLE_PERMISSIONS:Record<string,string[]>={
 admin:['*'],
 yonetici:['read','sales','purchases','payments','finance','stock','reports','users','machines'],
 muhasebe:['read','sales','purchases','payments','finance','reports'],
 satis:['read','sales','payments'],
 depo:['read','stock','purchases'],
 rapor:['read','reports'],
};
let permissions:string[]=['*'];
// AuthContext.can ile aynı kural: `platform` hiçbir rolden (`*` dahil) gelmez,
// yalnız `is_platform_operator` bayrağından.
let operator=false;
vi.mock('../AuthContext',()=>({
 useAuth:()=>({
  user:{id:1,username:'test',display_name:'Test Kullanıcı',role:'admin',must_change_password:false},
  companies:[],activeCompany:null,setActiveCompany:vi.fn(),logout:vi.fn(),
  can:(permission:string)=>permission==='platform'?operator:permissions.includes('*')||permissions.includes(permission),
 }),
}));

afterEach(()=>{cleanup();navigate.mockReset();permissions=['*'];operator=false});
beforeEach(()=>{vi.clearAllMocks()});

function mount(role='admin',route='/'){
 permissions=ROLE_PERMISSIONS[role];
 return render(
  <ThemeProvider theme={getAppTheme('light')}>
   <MemoryRouter initialEntries={[route]}>
    <Routes><Route element={<AppShell/>}><Route path="*" element={<div>içerik</div>}/></Route></Routes>
   </MemoryRouter>
  </ThemeProvider>,
 );
}

// Etiketler ÜRETİM KAYNAĞINDAN okunur (NAV_LABELS / navigation.tsx). Kopya
// literal tutulmaz: bir grubun adı değişince üretim ve test birlikte değişir,
// test "eski adı" doğrulamaya devam edemez.
const GROUP_LABELS=NAV_GROUPS.map(group=>group.label);
const PINNED_LABELS=PINNED_ITEMS.map(item=>item.label);

/**
 * Sorguları kenar çubuğuyla sınırlar. Üst çubuktaki POS düğmesinin görünür
 * metni de "Satış" olduğu için menü sayımları kapsam gerektirir.
 */
const sidebar=()=>document.querySelector('.MuiDrawer-paper') as HTMLElement;
const inSidebar=(label:string)=>within(sidebar()).queryAllByText(label).length>0;
/** Grup başlıkları `data-nav-group` ile kesin seçilir (etikete bağlı değil). */
const groupHeaderEl=(id:string)=>sidebar().querySelector(`[data-nav-group="${id}"]`) as HTMLElement;
/** Açma/kapama başlıkta değil, yanındaki ok düğmesindedir. */
const groupToggleEl=(id:string)=>sidebar().querySelector(`[data-nav-toggle="${id}"]`) as HTMLElement;
const groupExpanded=(id:string)=>groupToggleEl(id).getAttribute('aria-expanded')==='true';
const topLevelLabels=()=>[...PINNED_LABELS,...GROUP_LABELS].filter(inSidebar);

describe('AppShell gruplu menü',()=>{
 it('11 üst düzey madde çizer: 2 sabit + 9 grup',async()=>{
  mount('admin');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  // 9 → 10: Tarla grubu eklendi (mobil-erp#2), hiçbir grup çıkarılmadı.
  // 10 → 11: Hayvancılık grubu eklendi (mobil-erp#17), yine çıkarılan yok.
  expect(topLevelLabels()).toHaveLength(11);
  // Platform Yönetimi (PP3) operatör olmayan admin'de GÖRÜNMEZ.
  for(const label of GROUP_LABELS.filter(label=>label!==NAV_LABELS.groupPlatform))expect(screen.getAllByText(label).length).toBeGreaterThan(0);
  expect(inSidebar(NAV_LABELS.groupPlatform)).toBe(false);
 });

 it('PP3: Platform Yönetimi grubu yalnız platform operatörüne görünür',async()=>{
  operator=true;
  mount('rapor','/platform/sirketler');
  await waitFor(()=>expect(inSidebar(NAV_LABELS.groupPlatform)).toBe(true));
  // Aktif sayfa grubun içinde: grup açık ve yedi maddesi görünür.
  expect(groupExpanded('platform')).toBe(true);
  for(const label of [NAV_LABELS.platformDashboard,NAV_LABELS.platformCompanies,NAV_LABELS.platformUsers,
   NAV_LABELS.platformOutbox,NAV_LABELS.platformSecurity,NAV_LABELS.platformEDocuments,NAV_LABELS.backups]){
   expect(inSidebar(label)).toBe(true);
  }
  fireEvent.click(groupHeaderEl('platform'));
  expect(navigate).toHaveBeenCalledWith('/platform');
 });

 it('gruplar varsayılan olarak kapalıdır; alt maddeler görünmez',async()=>{
  mount('admin','/');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  // "Depolar" yalnız Stok & Ürünler grubu açıkken görünür.
  expect(screen.queryByText(NAV_LABELS.warehouses)).toBeNull();
  expect(screen.queryByText(NAV_LABELS.allocations)).toBeNull();
 });

 it('ok düğmesine tıklayınca açılır ve tekrar tıklayınca kapanır',async()=>{
  mount('admin','/');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  const toggle=groupToggleEl('inventory');
  fireEvent.click(toggle);
  expect(await screen.findByText(NAV_LABELS.warehouses)).toBeTruthy();
  expect(screen.getByText(NAV_LABELS.stockMovements)).toBeTruthy();
  fireEvent.click(toggle);
  await waitFor(()=>expect(screen.queryByText(NAV_LABELS.warehouses)).toBeNull());
 });

 // Tek tık gezinme sözleşmesi: grup başlığı yalnız akordiyon değildir, grubun
 // ana sayfasına da götürür. Hedef, o roldeki İLK görünür maddedir; gezinme
 // grubu aktif yaptığı için görünürlük türetmesi grubu ayrıca açar.
 it('grup başlığına tıklamak grubun ana sayfasına gider',async()=>{
  mount('admin','/');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  const landings:Record<string,string>={
   sales:'/satislar',customers:'/musteriler',inventory:'/urunler',purchasing:'/alislar',
   service:'/is-emirleri',finance:'/odemeler',admin:'/raporlar',
  };
  for(const [id,path] of Object.entries(landings)){
   navigate.mockClear();
   fireEvent.click(groupHeaderEl(id));
   expect(navigate).toHaveBeenCalledWith(path);
  }
 });

 // Rol filtresi sonrası ilk madde değişebilir: grubun ana sayfası o rolde
 // GÖRÜNMÜYORSA başlık, görünür İLK maddeye gitmelidir.
 //
 // 2026-09-09 (SEC-3): bu test ESKİDEN `depo` + Finans grubu ile yazılıydı —
 // grubun tek görünür maddesi Tahsis Defteri (`read`) idi. /tahsis-defteri
 // `payments`a taşınınca depo Finans grubunu TAMAMEN kaybetti ve senaryo
 // ORTADAN KALKTI. Testi silmek yerine AYNI OLGUYU hâlâ sergileyen bir role
 // taşıdık: `rapor` Finans grubunda YALNIZ Alacak Yaşlandırma'yı (`reports`)
 // görüyor, grubun ana sayfası /odemeler ise ona kapalı. Ölçülen davranış
 // değişmedi; ölçüldüğü yer değişti.
 it('başlık, o rolde görünür İLK maddeye gider',async()=>{
  mount('rapor','/');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  fireEvent.click(groupHeaderEl('finance'));
  expect(navigate).toHaveBeenCalledWith('/raporlar/alacak-yaslandirma');
 });

 // Başlık gezindiği için mobil çekmece kapanmalı; ok düğmesi gezinmediği için
 // çekmeceyi açık bırakmalı. (Aynı `drawer` JSX'i iki dalda da kullanılıyor.)
 it('ok düğmesi gezinmez',async()=>{
  mount('admin','/');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  navigate.mockClear();
  fireEvent.click(groupToggleEl('inventory'));
  expect(navigate).not.toHaveBeenCalled();
 });

 // E2E sözleşmesi: grup başlıklarının erişilebilir adı TAM olarak etikettir.
 // Playwright'ta ad sorgusu alt dize eşlediği için "Satış" grubu, "Satışlar"
 // maddesi ve "Hızlı Satış" düğmesi birbirine karışabiliyor; e2e tarafında
 // `exact: true` kullanılıyor ve bu test o varsayımı burada kilitliyor.
 it('grup başlıklarının erişilebilir adı tam olarak etiketin kendisidir',async()=>{
  // TÜM grup başlıkları ölçülür: Platform Yönetimi yalnız operatörde çizilir.
  operator=true;
  mount('admin');
  await waitFor(()=>expect(inSidebar(NAV_LABELS.home)).toBe(true));
  for(const label of GROUP_LABELS){
   // getByRole adı testing-library'de tam eşleşir: ad fazladan metin içeriyorsa
   // (ikon başlığı, sayaç vb.) bu sorgu bulamaz.
   expect(within(sidebar()).getByRole('button',{name:label})).toBeTruthy();
  }
 });

 it('aktif sayfanın grubu otomatik açık gelir',async()=>{
  mount('admin','/depolar');
  // Stok & Ürünler grubu açık olduğu için kardeş maddeler de görünür.
  expect(await screen.findByText(NAV_LABELS.warehouses)).toBeTruthy();
  expect(screen.getByText(NAV_LABELS.inventoryCounts)).toBeTruthy();
  // Başka bir grubun maddesi hâlâ gizli.
  expect(screen.queryByText(NAV_LABELS.allocations)).toBeNull();
 });

 // Aktif grup sözleşmesi: `openGroups` yalnız kullanıcının açtıklarını tutar,
 // görünürlük `openGroups.has(id) || activeGroup===id` ile TÜRETİLİR; aktif
 // gruba tıklamak onu kümeye EKLEMEZ, yalnız çıkarır.
 //
 // Bu senaryonun tamamı TEK bir AppShell ömründe koşar: yeniden mount etmek
 // `openGroups` durumunu sıfırlayacağı için asıl kanıtlanmak istenen şeyi
 // (durumun gezinme boyunca nasıl taşındığını) gizlerdi. Gezinme SPA içi
 // bağlantı tıklamalarıyla yapılır.
 it('aktif grup elle kapatılınca görünür kalır, aktiflik bitince kapanır',async()=>{
  mount('admin','/depolar');
  // 1) Aktif grup (Stok & Ürünler) açık geldi.
  expect(await screen.findByText(NAV_LABELS.warehouses)).toBeTruthy();
  expect(groupExpanded('inventory')).toBe(true);

  // 2) Kullanıcı aktif grubun ok düğmesine basar: "elle açık" kümesinden çıkar
  //    ama aktif olduğu için görsel olarak açık kalır.
  fireEvent.click(groupToggleEl('inventory'));
  expect(groupExpanded('inventory')).toBe(true);
  expect(screen.getByText(NAV_LABELS.warehouses)).toBeTruthy();

  // 3) Aynı grup içinde SPA geçişi (/urunler): hâlâ aktif → hâlâ açık.
  fireEvent.click(within(sidebar()).getByRole('link',{name:NAV_LABELS.products}));
  await waitFor(()=>expect(groupExpanded('inventory')).toBe(true));
  expect(screen.getByText(NAV_LABELS.warehouses)).toBeTruthy();

  // 4) Başka bir gruba SPA geçişi: Finans'ı elle aç, içindeki bağlantıya git.
  fireEvent.click(groupToggleEl('finance'));
  fireEvent.click(within(sidebar()).getByRole('link',{name:NAV_LABELS.allocations}));

  // 5) Stok grubu artık ne aktif ne de "elle açık" → KAPANDI.
  //    (aria-expanded anında döner; DOM'dan düşmesi Collapse animasyonu
  //    bittiğinde olur, o yüzden metinler de waitFor ile beklenir.)
  await waitFor(()=>expect(groupExpanded('inventory')).toBe(false));
  await waitFor(()=>expect(screen.queryByText(NAV_LABELS.warehouses)).toBeNull());
  expect(screen.queryByText(NAV_LABELS.products)).toBeNull();
 });

 it('elle açılan ikinci grup, başka sayfaya geçilse de açık kalır',async()=>{
  mount('admin','/depolar');
  await screen.findByText(NAV_LABELS.warehouses);
  // Aktif OLMAYAN ikinci bir grubu elle aç: kümeye girer.
  fireEvent.click(groupToggleEl('finance'));
  expect(groupExpanded('finance')).toBe(true);

  // Aynı AppShell ömründe SPA geçişi: elle açılan grup açık kalır.
  fireEvent.click(within(sidebar()).getByRole('link',{name:NAV_LABELS.products}));
  await waitFor(()=>expect(groupExpanded('finance')).toBe(true));
  expect(screen.getByText(NAV_LABELS.allocations)).toBeTruthy();
 });

 it('detay rotasında da üst maddesinin grubu açılır',async()=>{
  mount('admin','/makineler/7');
  expect(await screen.findByText(NAV_LABELS.machines)).toBeTruthy();
  expect(screen.getByText(NAV_LABELS.workOrders)).toBeTruthy();
 });

 it('iş grubuna dağıtılan rapor kendi grubunu açar',async()=>{
  // /raporlar/satin-alma-panosu bilinçli olarak Satın Alma grubunda (§2.2);
  // /raporlar öneki yüzünden Yönetim grubuna düşmemeli.
  mount('admin','/raporlar/satin-alma-panosu');
  expect(await screen.findByText(NAV_LABELS.purchaseDashboard)).toBeTruthy();
  expect(screen.getByText(NAV_LABELS.suppliers)).toBeTruthy();
  expect(screen.queryByText(NAV_LABELS.users)).toBeNull();
 });
});

describe('AppShell rol bazlı görünürlük',()=>{
 // 8 -> 7 (SEC-3): `satis` ALIŞ grubunu kaybetti. Grubun beş maddesinin
 // hiçbiri satis'e açık değil: /alislar ve /tedarikciler `purchases`a taşındı,
 // kalan üçü zaten `reports` / `supplier_prices.view` istiyordu. Kabul edilen
 // A-2 kararının AppShell'deki karşılığı. Hızlı Satış ve Satış grubu
 // DOKUNULMADAN duruyor — satis rolünün asli yüzeyi kesilmedi.
 it('satis rolü 7 üst düzey madde görür, Yönetim ve Alış gruplarını görmez',async()=>{
  mount('satis');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  expect(topLevelLabels()).toHaveLength(7);
  expect(screen.queryByText(NAV_LABELS.groupAdmin)).toBeNull();
  expect(screen.queryByText(NAV_LABELS.groupPurchasing)).toBeNull();
  expect(screen.getAllByText(NAV_LABELS.pos).length).toBeGreaterThan(0);
  // 'Satış' metni üst çubuktaki POS düğmesinde de geçiyor; grup başlığı
  // olarak varlığını üst düzey etiket listesinden okuyoruz.
  expect(topLevelLabels()).toContain(NAV_LABELS.groupSales);
 });

 // 7 -> 6 (SEC-3): `depo` FİNANS grubunu kaybetti (bir alttaki teste bakın).
 // Kaybın SINIRI de burada: Alış grubu KALIYOR, çünkü depo `purchases`
 // taşıyor — SEC-3'ün alış tarafı daralması depo'yu HİÇ etkilemiyor.
 it('depo rolü 6 üst düzey madde görür, Hızlı Satış sabitini görmez',async()=>{
  mount('depo');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  expect(topLevelLabels()).toHaveLength(6);
  expect(screen.queryByText(NAV_LABELS.pos)).toBeNull();
  expect(screen.queryByText(NAV_LABELS.groupAdmin)).toBeNull();
  expect(screen.getByText(NAV_LABELS.groupPurchasing)).toBeTruthy();
  expect(screen.getByText(NAV_LABELS.groupInventory)).toBeTruthy();
 });

 // İDDİA TERSİNE DÖNDÜ (SEC-3) ve bu bilinçli. Finans grubu depo'ya
 // ESKİDEN yalnız Tahsis Defteri'nin `read` olması sayesinde görünüyordu —
 // yani depo, kasa/banka/alacak maddelerinin HİÇBİRİNİ açamadığı hâlde
 // başlığı görüyordu. Defter `payments`a taşınınca o tek dayanak kalktı.
 // Depo için tahsis defteri bir stok işi DEĞİLDİR; kaybolan şey ölü bir
 // menü başlığıdır.
 it('depo rolünde Finans grubu ARTIK HİÇ görünmez',async()=>{
  mount('depo');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  expect(screen.queryByText(NAV_LABELS.groupFinance)).toBeNull();
  expect(screen.queryByText(NAV_LABELS.allocations)).toBeNull();
  expect(screen.queryByText(NAV_LABELS.payments)).toBeNull();
  expect(screen.queryByText(NAV_LABELS.cashManagement)).toBeNull();
 });

 it('rapor rolü Yönetim grubunu görür ama Hızlı Satış sabitini görmez',async()=>{
  mount('rapor');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  expect(topLevelLabels()).toHaveLength(8);
  expect(screen.getByText(NAV_LABELS.groupAdmin)).toBeTruthy();
  expect(screen.queryByText(NAV_LABELS.pos)).toBeNull();
 });
});

describe('AppShell üst çubuk Satış düğmesi',()=>{
 // Erişilebilir adı "Hızlı Satış"; görünür metni ("Satış") adın içinde geçer.
 const posButton=()=>screen.findByRole('button',{name:NAV_LABELS.pos});

 it('sales izni olan rolde görünür ve POS ekranına götürür',async()=>{
  mount('satis','/musteriler');
  fireEvent.click(await posButton());
  expect(navigate).toHaveBeenCalledWith('/hizli-satis');
 });

 it('sales izni olmayan rolde hiç çizilmez',async()=>{
  mount('depo','/musteriler');
  await waitFor(()=>expect(inSidebar(NAV_LABELS.home)).toBe(true));
  expect(screen.queryByRole('button',{name:NAV_LABELS.pos})).toBeNull();
 });

 it('dokunmatik için en az 44px yüksekliğindedir',async()=>{
  mount('satis','/musteriler');
  expect(parseFloat(getComputedStyle(await posButton()).minHeight)).toBeGreaterThanOrEqual(44);
 });
});

describe('AppShell kenar çubuğu teması',()=>{
 it('arka planını sabit hex yerine tema token’ından alır',async()=>{
  const theme=getAppTheme('light');
  const {container}=mount('admin');
  await waitFor(()=>expect(screen.getAllByText(NAV_LABELS.home).length).toBeGreaterThan(0));
  const drawer=container.querySelector('.MuiDrawer-paper > .MuiBox-root');
  expect(drawer).not.toBeNull();
  const background=getComputedStyle(drawer as Element).backgroundColor;
  // Premium tema tokenı kullanılır; bileşen içinde ayrı bir sabit renk yoktur.
  expect(background).toBe('rgb(7, 30, 65)');
  expect(theme.palette.sidebar).toBe('#071e41');
 });
});

describe('AppShell mevcut rotaları kırmaz',()=>{
 it('menü maddelerinin tamamı gerçek bir bağlantı olarak çizilir',async()=>{
  // Tamamı = Platform Yönetimi dahil; o grup yalnız operatörde çizilir.
  operator=true;
  mount('admin');
  await waitFor(()=>expect(inSidebar(NAV_LABELS.home)).toBe(true));
  // Her grubu aç, sonra href'lerin rota yollarıyla eşleştiğini doğrula.
  for(const group of NAV_GROUPS)fireEvent.click(groupToggleEl(group.id));
  const hrefs=within(sidebar()).getAllByRole('link').map(link=>link.getAttribute('href'));
  // 36 → 43: Tarla grubunun yedi maddesi.
  // 43 → 47: Hayvancılık grubunun dört maddesi (mobil-erp#17).
  // 47 → 48: Süt & Besi ekranı (FAZ 6).
  // 48 → 49: Maliyet Oranları (mobil-erp#24).
  // 49 → 50: Olay Kuyruğu (FIELD_STOK_OUTBOX açılış koşulu 2) — Tarla
  // grubunda; menüde GÖRÜNÜR, yani parsel/hayvan detayı gibi "listeden
  // açılan" bir ekran DEĞİL. Kuyruğu okumak için bir kayıt seçmek gerekmez.
  // 50 → 51: BKÜ Kataloğu (göç 20260901_0063) — Tarla grubunda, menüde
  // GÖRÜNÜR: katalog kayıt başına değil, firma başına yönetilen bir liste.
  // 51 → 57 (PP3): Platform Yönetimi'nin yedi maddesi, /yedekler çıktı.
  // 57 → 58: Çek / Senet Portföyü (CS3) — Finans grubunda, `payments`.
  // 58 → 59: Parti Mutabakatı (1B-H) — Stok & Ürünler grubunda, `read`.
  expect(new Set(hrefs).size).toBe(59);
  for(const path of ALL_NAV_ITEMS.map(item=>item.path))expect(hrefs).toContain(path);
 });
});
