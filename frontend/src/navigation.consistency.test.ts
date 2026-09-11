import {describe,expect,it} from 'vitest';

// Rota listesinin tek doğrulanabilir kaynağı `App.tsx`'in kendisidir; kaynak
// metni Vite'ın `?raw` yükleyicisiyle okunur (Node builtin'i gerekmez).
import appSource from './App.tsx?raw';
import {ALL_NAV_ITEMS,NAV_GROUPS,NAV_LABELS,PINNED_ITEMS,ROUTE_PERMISSIONS,PUBLIC_PATHS,groupIdForPath,permissionForPath,type Permission} from './navigation';

const menuItems=ALL_NAV_ITEMS.map(item=>[item.path] as const);

/**
 * MENÜ URL SÖZLEŞMESİ — bilinçli olarak ELLE yazılmış, konfigürasyondan
 * TÜRETİLMEMİŞ liste.
 *
 * Amaç: kullanıcıların yer imlerine aldığı ve dış sistemlerin derin bağlantı
 * verdiği 32 menü adresinin tarihsel kaydı. `ALL_NAV_ITEMS`'tan türetilseydi
 * test kendi kendini doğrular, bir yol sessizce değiştiğinde yeşil kalırdı.
 * Bu haliyle rota + konfigürasyon + test aynı anda değiştirilse bile bu
 * listenin BİLİNÇLİ olarak güncellenmesi gerekir.
 *
 * Buraya bir satır eklemek/çıkarmak = kullanıcı yer imini kırmak. Önce bunun
 * kabul edildiğinden emin ol.
 *
 * 2026-07-28 (Bildirimler FAZ-1): /bildirimler ve /bildirimler/sablonlar
 * YENİ adres olarak eklendi — 31 tarihsel adresin hiçbiri değişmedi ya da
 * silinmedi; yeni ekleme mevcut yer imlerini kırmaz.
 *
 * 2026-08-07 (Tarla Yönetimi V1, mobil-erp#2): /tarla ve dört alt adresi
 * YENİ olarak eklendi. Mevcut 36 adresin hiçbirine dokunulmadı — özellikle
 * `/saha` OLDUĞU GİBİ duruyor: o bayinin servis iş emirleri ekranı, tarla
 * modülüyle ilgisi yok ve yer imleri kırılmamalı.
 *
 * 2026-08-08 (Hayvancılık V1, mobil-erp#17): /hayvancilik ve üç alt adresi
 * YENİ olarak eklendi. Mevcut adreslerin hiçbirine dokunulmadı.
 *
 * 2026-08-27 (outbox okuma yüzeyi, FIELD_STOK_OUTBOX açılış koşulu 2):
 * /tarla/olay-kuyrugu YENİ adres olarak eklendi. Mevcut adreslerin hiçbiri
 * değişmedi ya da silinmedi — yani hiçbir yer imi kırılmıyor.
 *
 * 2026-09-11 (PP3, platform yönetim paneli): /yedekler MENÜDEN çıktı ve
 * /platform/yedekler oldu; altı yeni /platform adresi eklendi. /yedekler YER
 * İMİ KIRILMADI: App.tsx'te /platform/yedekler'e yönlendiren bir rota olarak
 * duruyor (bkz. App.platform.test.tsx). Adres artık menüde olmadığı için bu
 * listeden çıktı; yönlendirme testi onun bekçisidir.
 */
const BOOKMARKED_MENU_URLS=[
 '/',
 '/hizli-satis',
 '/satislar',
 '/faturalar',
 '/belge-akislari',
 '/musteriler',
 '/urunler',
 '/stok-hareketleri',
 '/depolar',
 '/stok-sayimlari',
 '/sube-transfer',
 '/sezonsal-stok-plani',
 '/parca-supersession',
 '/alislar',
 '/tedarikciler',
 '/raporlar/satin-alma-panosu',
 '/raporlar/tedarikci-karsilastirma',
 '/is-emirleri',
 '/makineler',
 '/odemeler',
 '/alacaklar',
 '/tahsis-defteri',
 '/nakit-yonetimi',
 '/raporlar/alacak-yaslandirma',
 '/tanimlar/harman-sezon',
 '/raporlar',
 '/analizler',
 '/raporlar/emilim-orani',
 '/kullanicilar',
 '/firmalar',
 '/aktivite',
 '/islem-gecmisi',
 '/bildirimler',
 '/bildirimler/sablonlar',
 '/tedarikci-fiyatlari',
 '/tarla',
 '/tarla/ciftlikler',
 '/tarla/sezonlar',
 '/tarla/faaliyetler',
 '/tarla/bku-katalogu',
 '/tarla/gorevler',
 '/tarla/hasat',
 '/tarla/hizli-giris',
 '/tarla/olay-kuyrugu',
 '/hayvancilik',
 '/hayvancilik/hayvanlar',
 '/hayvancilik/saglik',
 '/hayvancilik/doller',
 '/hayvancilik/verim',
 '/tanimlar/maliyet-oranlari',
 '/platform',
 '/platform/sirketler',
 '/platform/kullanicilar',
 '/platform/kuyruk',
 '/platform/guvenlik',
 '/platform/e-belgeler',
 '/platform/yedekler',
];

/**
 * Menü ile route izinlerinin bir daha ayrışmamasını garanti eden testler.
 *
 * Ayrışma gerçek bir hataya yol açmıştı: `/firmalar` menüde `read` ile
 * listeleniyor, route ise `users` istiyordu; yetkisiz roller maddeyi görüp
 * tıklayınca sessizce Ana Sayfa'ya atılıyordu.
 */

/** `App.tsx` içindeki `<Route path="...">` değerleri (kabuk dışı olanlar hariç). */
function routePathsFromApp():string[]{
 const paths=[...appSource.matchAll(/<Route\s+path="([^"]+)"/g)].map(match=>`/${match[1]}`);
 // `<Route index .../>` kök rotadır; `path="*"` yakalayıcı yönlendirmedir.
 const declared=['/',...paths];
 const publicPaths=new Set<string>(PUBLIC_PATHS);
 return [...new Set(declared)].filter(path=>path!=='/*'&&!publicPaths.has(path));
}

/**
 * backend/app/auth.py `ROLE_PERMISSIONS`'ın ELLE kopyası.
 *
 * 2026-08-07 — TARLA EKLENİRKEN BU KOPYANIN BAYAT OLDUĞU GÖRÜLDÜ ve tam
 * senkrona çekildi. Eksik olanlar: `field_service` (yonetici, satis),
 * `notifications*` (yonetici, muhasebe, satis), `supplier_prices.*` (üç rol).
 *
 * Sonuç değişmemişti çünkü o izinlerin bağlı olduğu menü maddeleri, aynı
 * grupta başka görünür madde bulunan gruplarda duruyor — yani grup sayıları
 * tesadüfen doğru çıkıyordu. Yani test yeşildi ama YANLIŞ BİR ROL TABLOSU
 * üzerinden yeşildi; bir sonraki değişiklikte sessizce yanlış cevap verebilirdi.
 *
 * Kalıcı çözüm (backlog): izin matrisini backend'den makine-okunur biçimde
 * dışa aktarıp kontrat kapısına bağlamak. Bu PR'ın kapsamı değil.
 */
const ROLE_PERMISSIONS:Record<string,string[]>={
 admin:['*'],
 yonetici:['read','sales','field_service','purchases','payments','finance','stock','reports',
  'users','machines','notifications','notifications_approve','notifications_dispatch',
  'notifications_admin','supplier_prices.view','supplier_prices.import','supplier_prices.apply',
  'supplier_prices.override_block','farm.view','farm.manage','farm.inputs',
  'herd.view','herd.manage','herd.health'],
 muhasebe:['read','sales','purchases','payments','finance','reports','notifications',
  'notifications_approve','notifications_dispatch','supplier_prices.view',
  'supplier_prices.import','supplier_prices.apply','farm.view','herd.view'],
 satis:['read','sales','field_service','payments','notifications','farm.view','herd.view'],
 depo:['read','stock','purchases','supplier_prices.view','farm.view','farm.inputs',
  // Depo aşı GİREMEZ: `herd.health` yok, veterinerlik sorumluluğu.
  'herd.view'],
 rapor:['read','reports','farm.view','herd.view'],
};
// `platform` hiçbir rolden GELMEZ — `*` dahil (AuthContext.can ile aynı kural:
// yalnız `is_platform_operator`). PP3'e kadar bu kopya `*`i `platform`a da
// genişletiyordu; Yedekler Yönetim grubundaydı ve sonuç değişmiyordu. Platform
// kendi grubu olunca bu yanlış admin'e hayali bir grup sayardı.
const can=(role:string,permission:Permission)=>permission==='platform'?false:
 ROLE_PERMISSIONS[role].includes('*')||ROLE_PERMISSIONS[role].includes(permission);
const operatorCan=(permission:Permission)=>permission==='platform'||can('rapor',permission);

describe('navigasyon izin tutarlılığı',()=>{
 it('App.tsx içindeki her korumalı rotanın izin haritasında karşılığı var',()=>{
  const missing=routePathsFromApp().filter(path=>!(path in ROUTE_PERMISSIONS));
  expect(missing).toEqual([]);
 });

 it('izin haritasında App.tsx tarafından tanımlanmayan artık kayıt yok',()=>{
  const declared=new Set(routePathsFromApp());
  const stale=Object.keys(ROUTE_PERMISSIONS).filter(path=>!declared.has(path));
  expect(stale).toEqual([]);
 });

 it('her menü maddesinin izin haritasında karşılığı var',()=>{
  const missing=menuItems.filter(item=>!(item[0] in ROUTE_PERMISSIONS)).map(item=>item[0]);
  expect(missing).toEqual([]);
 });

 it('menüde madde tekrarı yok',()=>{
  const paths=ALL_NAV_ITEMS.map(item=>item.path);
  expect(paths.length).toBe(new Set(paths).size);
 });

 it('tarihsel maddeler + bildirimler + Yedekler + Tedarikçi Fiyatları + Tarla yerleşti',()=>{
  // U1 bir taşıma işidir: hiçbir madde yolda kaybolmamalı.
  // 36 → 43: Tarla grubunun yedi maddesi eklendi, hiçbiri çıkarılmadı.
  // 43 → 47: Hayvancılık grubunun dört maddesi eklendi (mobil-erp#17),
  // yine hiçbiri çıkarılmadı.
  // 47 → 48: Süt & Besi ekranı (FAZ 6).
  // 48 → 49: Maliyet Oranları (mobil-erp#24 FAZ 1) — Finans grubuna eklendi,
  // grup sayısı DEĞİŞMEDİ.
  // 49 → 50: Olay Kuyruğu (FIELD_STOK_OUTBOX açılış koşulu 2) — Tarla
  // grubuna eklendi, grup sayısı yine DEĞİŞMEDİ ve hiçbir madde çıkmadı.
  // 50 → 51: BKÜ Kataloğu (göç 20260901_0063) — PHI gün sayısının firma
  // tarafından doldurulan kaydı. Yine Tarla grubunda; grup sayısı DEĞİŞMEDİ
  // ve hiçbir madde çıkmadı.
  // 51 → 57 (PP3): Platform Yönetimi grubu yedi madde (/yedekler Yönetim'den
  // /platform/yedekler olarak taşındı, altı yeni ekran) — 51 - 1 + 7.
  // Grup sayısı 9 → 10.
  expect(ALL_NAV_ITEMS.length).toBe(57);
  expect(PINNED_ITEMS.length).toBe(2);
  expect(NAV_GROUPS.length).toBe(10);
 });

 it('menü URL sözleşmesi korunur: elle yazılmış adreslerle küme eşitliği',()=>{
  // Bağımsız sözleşme listesiyle karşılaştırma (bkz. BOOKMARKED_MENU_URLS).
  expect(BOOKMARKED_MENU_URLS).toHaveLength(57);
  expect(new Set(BOOKMARKED_MENU_URLS).size).toBe(57);
  const actual=ALL_NAV_ITEMS.map(item=>item.path);
  // Küme eşitliği: sıra önemli değil, içerik birebir olmalı.
  expect([...actual].sort()).toEqual([...BOOKMARKED_MENU_URLS].sort());
 });

 it('her grup en az bir madde içerir ve grup kimlikleri benzersizdir',()=>{
  for(const group of NAV_GROUPS)expect(group.items.length).toBeGreaterThan(0);
  const ids=NAV_GROUPS.map(group=>group.id);
  expect(ids.length).toBe(new Set(ids).size);
 });

 // TEK TIK GEZİNME SÖZLEŞMESİ — kenar çubuğunda grup başlığına tıklamak
 // grubun ana sayfasını açar (bkz. AppShell). Hedef, grubun İLK maddesidir;
 // dolayısıyla bir grubun madde sırasını değiştirmek başlığın nereye gittiğini
 // de değiştirir. Aşağıdaki liste bilinçli olarak elle yazılmıştır: sıra
 // kazara bozulursa test kırılır.
 it('her grubun ilk maddesi o grubun ana sayfasıdır',()=>{
  const LANDING:Record<string,string>={
   sales:'/satislar',customers:'/musteriler',inventory:'/urunler',purchasing:'/alislar',
   service:'/is-emirleri',finance:'/odemeler',farm:'/tarla',herd:'/hayvancilik',
   admin:'/raporlar',platform:'/platform',
  };
  expect(Object.keys(LANDING).sort()).toEqual(NAV_GROUPS.map(group=>group.id).sort());
  for(const group of NAV_GROUPS)expect([group.id,group.items[0].path]).toEqual([group.id,LANDING[group.id]]);
 });

 it('menüde görünen her madde, o rol için route korumasından da geçer',()=>{
  for(const role of Object.keys(ROLE_PERMISSIONS)){
   for(const [path] of menuItems){
    const permission=permissionForPath(path);
    const inMenu=can(role,permission);
    // Menü ve route aynı haritayı okuduğu için bu iki karar ayrışamaz.
    // Test, ileride biri elle geçersiz kılınırsa kırılır.
    const routeAllows=can(role,ROUTE_PERMISSIONS[path as keyof typeof ROUTE_PERMISSIONS]);
    expect({role,path,inMenu}).toEqual({role,path,inMenu:routeAllows});
   }
  }
 });

 it('ölü menü maddesi bırakmaz: /firmalar users iznine bağlıdır',()=>{
  expect(ROUTE_PERMISSIONS['/firmalar']).toBe('users');
  for(const role of ['satis','depo','rapor','muhasebe']){
   expect(can(role,permissionForPath('/firmalar'))).toBe(false);
  }
  for(const role of ['admin','yonetici']){
   expect(can(role,permissionForPath('/firmalar'))).toBe(true);
  }
 });

 /**
  * SEC-3 — `read` DARALTMASININ NAV TARAFI.
  *
  * Backend'de 19 GET ucu `read`ten çıktı; nav bu satırlarla LOCKSTEP hareket
  * ediyor. Nav geride kalsaydı hata SESSİZ olmazdı ama ÇİRKİN olurdu:
  * menüde görünen bir sayfa açılır ve içeride 403 toplardı — `/alacaklar`da
  * bir kez yaşanan ölü-madde kusurunun aynısı.
  *
  * Hangi rolün neyi kaybettiği ELLE YAZILDI, türetimden okunmadı: türetim
  * `ROLE_PERMISSIONS`ı okur ve bu testi kendi kendini doğrulayan bir totoloji
  * yapardı.
  */
 it('SEC-3: /alislar ve /tedarikciler* purchases, satis ile rapor kaybediyor',()=>{
  expect(ROUTE_PERMISSIONS['/alislar']).toBe('purchases');
  expect(ROUTE_PERMISSIONS['/tedarikciler']).toBe('purchases');
  expect(ROUTE_PERMISSIONS['/tedarikciler/:id']).toBe('purchases');
  for(const role of ['satis','rapor']){
   expect(can(role,permissionForPath('/alislar'))).toBe(false);
   expect(can(role,permissionForPath('/tedarikciler'))).toBe(false);
  }
  // `depo` `purchases` TAŞIR: alış tarafı daralmasından HİÇ etkilenmiyor.
  // Bu satır A'nın en güçlü yanının nav'daki karşılığıdır.
  expect(can('depo',permissionForPath('/alislar'))).toBe(true);
  expect(can('depo',permissionForPath('/tedarikciler'))).toBe(true);
 });

 it('SEC-3: /musteriler* read te KALDI — satis ve depo 403 ALMAZ',()=>{
  // Entities.tsx ve EntityDetail.tsx TEK bileşendir ve ucu `type`
  // proposundan seçer. Ayrışma bileşen içinde bir `can()` dalıyla değil, iki
  // AYRI rota kaydıyla ifade edildi. Bu satır o kararın bekçisidir: biri
  // `/musteriler`i de `purchases`a çekerse `satis` müşteri kartını kaybeder.
  expect(ROUTE_PERMISSIONS['/musteriler']).toBe('read');
  expect(ROUTE_PERMISSIONS['/musteriler/:id']).toBe('read');
  for(const role of ['satis','depo','rapor','muhasebe']){
   expect(can(role,permissionForPath('/musteriler'))).toBe(true);
   expect(can(role,permissionForPath('/musteriler/42'))).toBe(true);
  }
 });

 it('SEC-3: /faturalar* sales, /tahsis-defteri payments — depo ile rapor kaybediyor',()=>{
  expect(ROUTE_PERMISSIONS['/faturalar']).toBe('sales');
  expect(ROUTE_PERMISSIONS['/faturalar/:id']).toBe('sales');
  expect(ROUTE_PERMISSIONS['/tahsis-defteri']).toBe('payments');
  for(const role of ['depo','rapor']){
   expect(can(role,permissionForPath('/faturalar'))).toBe(false);
   expect(can(role,permissionForPath('/tahsis-defteri'))).toBe(false);
  }
  // `satis` tahsilat rolüdür: tahsis defterini OKUMAYA devam eder.
  expect(can('satis',permissionForPath('/tahsis-defteri'))).toBe(true);
  expect(can('satis',permissionForPath('/faturalar'))).toBe(true);
 });

 it('SEC-3: /is-emirleri* read te KALDI — depo ve rapor iş emrini AÇAR',()=>{
  // BAYRAK C-1'in nav tarafı: kapatılan şey SAYFA değil, sayfadaki FATURA
  // PANELİ (WorkOrderDetail.tsx, `canSeeInvoice`). Nav `sales`a çekilseydi
  // depo iş emri detayını tamamen kaybederdi ve bu bir stok işidir.
  expect(ROUTE_PERMISSIONS['/is-emirleri']).toBe('read');
  expect(ROUTE_PERMISSIONS['/is-emirleri/:id']).toBe('read');
  for(const role of ['depo','rapor']){
   expect(can(role,permissionForPath('/is-emirleri/7'))).toBe(true);
  }
 });

 it('PP3: /platform* yalnız operatöre açık — hiçbir rol (admin dahil) görmez',()=>{
  const platformYollari=Object.keys(ROUTE_PERMISSIONS).filter(path=>path.startsWith('/platform'));
  expect(platformYollari).toHaveLength(7);
  for(const path of [...platformYollari,'/yedekler']){
   expect(ROUTE_PERMISSIONS[path as keyof typeof ROUTE_PERMISSIONS]).toBe('platform');
   for(const role of Object.keys(ROLE_PERMISSIONS))expect([role,path,can(role,permissionForPath(path))]).toEqual([role,path,false]);
   expect(operatorCan(permissionForPath(path))).toBe(true);
  }
  // Yönetim grubunda artık platform maddesi yok; yedekler kendi grubunda.
  expect(NAV_GROUPS.find(group=>group.id==='admin')!.items.some(i=>i.path==='/yedekler')).toBe(false);
  expect(groupIdForPath('/platform/yedekler')).toBe('platform');
  expect(groupIdForPath('/platform/sirketler')).toBe('platform');
 });

 it('/alacaklar menüde olduğu gibi route tarafında da payments ister',()=>{
  // Menü `payments` ile gizliyordu ama route korumasızdı: URL doğrudan
  // yazılınca açılıyordu. Tek kaynak, daha kısıtlayıcı tarafa çekildi.
  expect(ROUTE_PERMISSIONS['/alacaklar']).toBe('payments');
  expect(can('depo',permissionForPath('/alacaklar'))).toBe(false);
 });
});

describe('permissionForPath',()=>{
 it('sorgu dizesi ve hash ayıklar',()=>{
  expect(permissionForPath('/stok-sayimlari?new=1')).toBe('stock');
  expect(permissionForPath('/satislar?q=SAT-1#top')).toBe('read');
 });

 it('dinamik segmentleri eşler',()=>{
  expect(permissionForPath('/musteriler/42')).toBe('read');
  // SEC-3: /faturalar/:id artık `sales`. `/musteriler/:id` BİLEREK `read`te
  // kaldı — ikisi bir arada, daralmanın nereye vurup nereye vurmadığını
  // tek satırda gösteriyor.
  expect(permissionForPath('/faturalar/17')).toBe('sales');
  expect(permissionForPath('/tedarikciler/42')).toBe('purchases');
  expect(permissionForPath('/depolar/3')).toBe('stock');
  expect(permissionForPath('/stok-sayimlari/9')).toBe('stock');
  // Parsel detayı menüde yok ama rota korumalı olmalı.
  expect(permissionForPath('/tarla/parseller/7')).toBe('farm.view');
 });

 it('sondaki eğik çizgiyi yok sayar',()=>{
  expect(permissionForPath('/kullanicilar/')).toBe('users');
 });

 it('bilinmeyen yol için read varsayar',()=>{
  expect(permissionForPath('/boyle-bir-sayfa-yok')).toBe('read');
 });
});

/**
 * docs/ux-yenileme-f0-tasarim.md §2.3'teki rol tablosunun koda dökülmüş hali.
 * Bir grup, o rolde görünür en az bir maddesi varsa görünür.
 */
describe('rol bazlı üst düzey görünürlük',()=>{
 const topLevelFor=(role:string)=>{
  const pinned=PINNED_ITEMS.filter(item=>can(role,permissionForPath(item.path)));
  const groups=NAV_GROUPS.filter(group=>group.items.some(item=>can(role,permissionForPath(item.path))));
  return {pinned:pinned.length,groups:groups.length,total:pinned.length+groups.length,
   groupLabels:groups.map(group=>group.label)};
 };

 // Dokümandaki tabloyla birebir.
 //
 // DİKKAT — bu sayılar yukarıdaki `ROLE_PERMISSIONS` sabitinden çıkar ve o
 // sabit backend/app/auth.py'nin ELLE KOPYASIDIR; yapısal bir türetme değildir.
 // Backend izin matrisi değişirse (bir role izin eklenir/çıkarılır) hem oradaki
 // kopya hem buradaki beklenen sayılar elle güncellenmelidir; aksi hâlde test
 // gerçeği değil eski varsayımı doğrular.
 //
 // Yapısal türetme backend'den izin matrisinin makine-okunur biçimde export
 // edilmesini (ve kontrat kapısına bağlanmasını) gerektirir — bu PR'ın kapsamı
 // değil, backlog maddesi.
 // Tarla grubu HER rolde görünür: `farm.view` altı rolün hepsinde var (okuma
 // herkese açık, yazma değil). Bu yüzden bütün sayılar +1.
 //
 // `satis` +2: biri Tarla, diğeri ROL KOPYASI DÜZELTİLDİĞİ İÇİN ortaya çıkan
 // Yönetim grubu — aşağıdaki teste bakın, bu bir DAVRANIŞ DEĞİŞİKLİĞİ DEĞİL,
 // zaten üretimde olan bir durumun ilk kez doğru yazılmasıdır.
 // 2026-08-08 (mobil-erp#17): Hayvancılık grubu eklendi ve o da HER rolde
 // görünür — `herd.view` altı rolün hepsinde var (tarlayla aynı gerekçe:
 // okuma herkese açık, yazma değil). Bu yüzden bütün sayılar yine +1.
 // 2026-09-09 (SEC-3, `read` daraltması): İKİ sayı düştü, dördü KIMILDAMADI.
 // Sayılar ÖLÇÜLDÜ, aritmetikle taşınmadı — bir grup, İÇİNDEKİ HERHANGİ bir
 // madde açıksa görünür, yani "iki madde kaybettim" ile "grubu kaybettim"
 // arasında doğrudan bir ilişki YOK.
 //
 //   satis 11 -> 10: ALIŞ grubunu KAYBETTİ. Grubun beş maddesi
 //     (/alislar, /tedarikciler -> `purchases`; iki /raporlar/* -> `reports`;
 //     /tedarikci-fiyatlari -> `supplier_prices.view`) ve `satis` bunların
 //     HİÇBİRİNİ taşımıyor. SEC-3 öncesi grup YALNIZ /alislar ve
 //     /tedarikciler'in `read` olması sayesinde görünüyordu. Bu, kabul edilen
 //     A-2 kararının nav'daki tam karşılığıdır.
 //   depo 9 -> 8: FİNANS grubunu KAYBETTİ. Grubun yedi maddesinden depo'ya
 //     açık olan TEK madde /tahsis-defteri idi (`read`); o da `payments`a
 //     taşındı ve depo `payments` taşımıyor. Aşağıdaki "depo rolü ..." testi
 //     bu kaybı ayrıca ve açıkça yazıyor.
 //   rapor 10'da SABİT ve bu ANLAMLI: rapor da /alislar, /tedarikciler,
 //     /faturalar ve /tahsis-defteri'ni kaybetti, ama İKİ GRUBU DA
 //     kaybetmedi — ALIŞ grubunda iki `reports` maddesi, FİNANS grubunda
 //     /raporlar/alacak-yaslandirma (`reports`) duruyor. Yani rapor rolünün
 //     yüzeyi /raporlar/* altına ÇEKİLDİ, silinmedi; A-2'nin gerekçesi tam
 //     olarak buydu ve burada ölçülüyor.
 //   muhasebe/yonetici/admin 11'de SABİT: `purchases`, `sales` ve `payments`
 //     izinlerinin üçünü de taşıyorlar, yani SEC-3'ten HİÇ etkilenmiyorlar.
 const EXPECTED:Record<string,number>={admin:11,yonetici:11,muhasebe:11,rapor:10,satis:10,depo:8};

 for(const [role,total] of Object.entries(EXPECTED)){
  it(`${role} rolü ${total} üst düzey madde görür`,()=>{
   expect(topLevelFor(role).total).toBe(total);
  });
 }

 /**
 * BULGU (2026-08-07) — bu testin ADI ve iddiası değişti, çünkü eski iddia
 * ÜRETİMDE DOĞRU DEĞİLDİ.
 *
 * Eskiden "satis rolü Yönetim grubunu görmez" deniyordu ve test yeşildi. Ama
 * yeşilliğin sebebi, yukarıdaki elle yazılmış rol kopyasının `satis` rolünde
 * `notifications` iznini ATLAMIŞ olmasıydı. Backend o izni satis'e VERİYOR
 * (bkz. backend/app/auth.py) ve `/bildirimler` Yönetim grubunda duruyor.
 * Yani gerçek uygulamada satış rolü, Bildirimler FAZ-1'den beri Yönetim
 * grubunu görüyor — menü çalışma anında GERÇEK izinleri okuyor, bu kopyayı
 * değil.
 *
 * Burada hiçbir davranış değiştirilmedi; yalnızca test artık doğruyu yazıyor.
 * "Satış rolü Yönetim başlığını görmesin" isteniyorsa bu AYRI bir üründür
 * kararıdır (Bildirimler'i kendi grubuna almak ya da izni daraltmak) —
 * sessizce burada yapılmamalı.
 */
 it('satis rolü Yönetim grubunu YALNIZ Bildirimler yüzünden görür',()=>{
  const {groupLabels,pinned}=topLevelFor('satis');
  expect(pinned).toBe(2); // Ana Sayfa + Hızlı Satış
  expect(groupLabels).toContain(NAV_LABELS.groupFinance);
  expect(groupLabels).toContain(NAV_LABELS.groupSales);
  // Yönetim grubu görünür, ama İÇİNDEN yalnız Bildirimler maddeleri açılır.
  expect(groupLabels).toContain(NAV_LABELS.groupAdmin);
  const yonetim=NAV_GROUPS.find(group=>group.id==='admin')!;
  const gorunen=yonetim.items.filter(i=>can('satis',permissionForPath(i.path))).map(i=>i.path);
  expect(gorunen).toEqual(['/bildirimler','/bildirimler/sablonlar']);
 });

 it('depo rolü POS sabitini, Yönetim ve (SEC-3 sonrası) Finans grubunu görmez',()=>{
  const {groupLabels,pinned}=topLevelFor('depo');
  expect(pinned).toBe(1); // yalnız Ana Sayfa; Hızlı Satış `sales` ister
  expect(groupLabels).not.toContain(NAV_LABELS.groupAdmin);
  // 2026-09-09 (SEC-3): İDDİA TERSİNE DÖNDÜ ve bu BİLİNÇLİ. Eskiden Finans
  // grubu depo'ya YALNIZ Tahsis Defteri'nin `read` olması sayesinde
  // görünüyordu — yani depo, kasa/banka veya alacak maddelerinden HİÇBİRİNİ
  // açamadığı hâlde başlığı görüyordu. /tahsis-defteri `payments`a taşınınca
  // o tek dayanak kalktı ve başlık da gitti. Depo için tahsis defteri bir
  // stok işi DEĞİLDİR; ölü bir menü başlığının kaybolması bir gerileme değil,
  // /alacaklar'da bir kez düzeltilen aynı kusurun bu grupta da kapanmasıdır.
  expect(groupLabels).not.toContain(NAV_LABELS.groupFinance);
  // Kaybın SINIRI: stok yüzeyi ve servis yüzeyi DOKUNULMADAN duruyor.
  expect(groupLabels).toContain(NAV_LABELS.groupInventory);
  expect(groupLabels).toContain(NAV_LABELS.groupService);
  // Alış grubu depo'da KALIYOR: `purchases` taşıyor (A'nın en güçlü yanı).
  expect(groupLabels).toContain(NAV_LABELS.groupPurchasing);
 });

 it('SEC-3: satis Alış grubunu kaybetti, rapor yüzeyi /raporlar/* altına çekildi',()=>{
  // A-2 kararının ölçülmüş hâli. `satis` için grup TAMAMEN kayboluyor;
  // `rapor` için başlık KALIYOR ama içindeki maddeler değişiyor.
  expect(topLevelFor('satis').groupLabels).not.toContain(NAV_LABELS.groupPurchasing);
  expect(topLevelFor('rapor').groupLabels).toContain(NAV_LABELS.groupPurchasing);
  const alis=NAV_GROUPS.find(group=>group.id==='purchasing')!;
  const raporGorur=alis.items.filter(i=>can('rapor',permissionForPath(i.path))).map(i=>i.path);
  // Alış defteri ve tedarikçi kartı GİTTİ; raporlama panoları KALDI.
  expect(raporGorur).toEqual([
   '/raporlar/satin-alma-panosu',
   '/raporlar/tedarikci-karsilastirma',
  ]);
  // FAZ 1'de ölçülmüştü: `/raporlar/tedarikci-karsilastirma` tedarikçi
  // listesini `/api/suppliers`den DEĞİL kendi rapor yanıtından okuyor
  // (`PurchaseComparison.tsx:57`), yani bu madde SEC-3'ten sonra da çalışır.
 });

 it('admin tüm grupları görür',()=>{
  // Beklenen etiketler de üretim kaynağından gelir; kopya literal tutulmaz.
  expect(topLevelFor('admin').groupLabels).toEqual([
   NAV_LABELS.groupSales,
   NAV_LABELS.groupCustomers,
   NAV_LABELS.groupInventory,
   NAV_LABELS.groupPurchasing,
   NAV_LABELS.groupService,
   NAV_LABELS.groupFinance,
   NAV_LABELS.groupFarm,
   NAV_LABELS.groupHerd,
   NAV_LABELS.groupAdmin,
  ]);
 });

 it('tarla grubu her rolde görünür ama YAZMA yetkisi yalnız yöneticide',()=>{
  // Rota izni okuma seviyesindedir; grup bu yüzden herkeste görünür.
  for(const role of Object.keys(ROLE_PERMISSIONS)){
   expect([role,can(role,permissionForPath('/tarla'))]).toEqual([role,true]);
  }
  // Yazma ayrımı burada test edilir ki menü genişlemesi "herkes yazabilir"
  // sanılmasın. `farm.manage` yalnız admin ve yöneticide.
  for(const role of ['satis','depo','rapor','muhasebe'])expect([role,can(role,'farm.manage')]).toEqual([role,false]);
  for(const role of ['admin','yonetici'])expect([role,can(role,'farm.manage')]).toEqual([role,true]);
  // Depo girdi bağlayabilir ama parsel/sezon yazamaz — ayrımın tamamı bu.
  expect(can('depo','farm.inputs')).toBe(true);
  expect(can('satis','farm.inputs')).toBe(false);
 });
});

describe('groupIdForPath',()=>{
 it('madde yolunu kendi grubuna eşler',()=>{
  expect(groupIdForPath('/satislar')).toBe('sales');
  expect(groupIdForPath('/musteriler')).toBe('customers');
  expect(groupIdForPath('/depolar')).toBe('inventory');
  expect(groupIdForPath('/makineler')).toBe('service');
  expect(groupIdForPath('/odemeler')).toBe('finance');
 });

 it('detay rotasını üst maddesinin grubuna eşler',()=>{
  expect(groupIdForPath('/musteriler/42')).toBe('customers');
  expect(groupIdForPath('/makineler/7')).toBe('service');
  expect(groupIdForPath('/depolar/3')).toBe('inventory');
 });

 it('daha uzun eşleşme kazanır: rapor alt yolları kendi grubunda kalır',()=>{
  expect(groupIdForPath('/raporlar')).toBe('admin');
  expect(groupIdForPath('/raporlar/emilim-orani')).toBe('admin');
  // Bu ikisi bilinçli olarak iş gruplarına dağıtıldı (§2.2).
  expect(groupIdForPath('/raporlar/satin-alma-panosu')).toBe('purchasing');
  expect(groupIdForPath('/raporlar/alacak-yaslandirma')).toBe('finance');
 });

 it('sabit maddeler için null döner',()=>{
  expect(groupIdForPath('/')).toBeNull();
  expect(groupIdForPath('/hizli-satis')).toBeNull();
 });
});
