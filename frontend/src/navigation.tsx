/**
 * Navigasyonun TEK kaynağı: izinler, etiketler, gruplar ve ikonlar.
 *
 * Bir rotanın gerektirdiği izin yalnız burada yazılıdır. Route koruması
 * (`App.tsx`), kenar çubuğu menüsü (`AppShell.tsx`) ve komut paleti
 * (`CommandPalette.tsx`) aynı haritayı okur; böylece "menüde görünüyor ama
 * açılmıyor" (veya tersi) durumu yapısal olarak oluşamaz.
 *
 * Kullanıcıya görünen tüm metinler `NAV_LABELS` bloğundadır: bir grubun ya da
 * maddenin adını değiştirmek tek satırlık bir düzenlemedir.
 *
 * `navigation.consistency.test.ts` bu haritanın `App.tsx`'teki rota listesiyle
 * birebir örtüştüğünü, `AppShell.test.tsx` ise rol bazlı görünürlüğü doğrular.
 */
import React from 'react';
import AccountBalanceIcon from '@mui/icons-material/AccountBalance';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import AgricultureIcon from '@mui/icons-material/Agriculture';
import AnalyticsIcon from '@mui/icons-material/Analytics';
import AssessmentIcon from '@mui/icons-material/Assessment';
import BackupIcon from '@mui/icons-material/Backup';
import BuildIcon from '@mui/icons-material/Build';
import BoltIcon from '@mui/icons-material/Bolt';
import BusinessIcon from '@mui/icons-material/Business';
import CalendarMonthIcon from '@mui/icons-material/CalendarMonth';
import ChildCareIcon from '@mui/icons-material/ChildCare';
import DashboardIcon from '@mui/icons-material/Dashboard';
import EventRepeatIcon from '@mui/icons-material/EventRepeat';
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined';
import HistoryIcon from '@mui/icons-material/History';
import Inventory2Icon from '@mui/icons-material/Inventory2';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive';
import ManageAccountsIcon from '@mui/icons-material/ManageAccounts';
import PaymentsIcon from '@mui/icons-material/Payments';
import PeopleIcon from '@mui/icons-material/People';
import PetsIcon from '@mui/icons-material/Pets';
import PointOfSaleIcon from '@mui/icons-material/PointOfSale';
import PriceCheckIcon from '@mui/icons-material/PriceCheck';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import ShoppingCartIcon from '@mui/icons-material/ShoppingCart';
import SpaIcon from '@mui/icons-material/Spa';
import SpeedIcon from '@mui/icons-material/Speed';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import TimelineIcon from '@mui/icons-material/Timeline';
import VaccinesIcon from '@mui/icons-material/Vaccines';
import WarehouseIcon from '@mui/icons-material/Warehouse';
import WaterDropIcon from '@mui/icons-material/WaterDrop';

/** Backend `ROLE_PERMISSIONS` ile aynı izin kümesi (bkz. backend/app/auth.py). */
export type Permission =
  | 'read'
  | 'sales'
  | 'purchases'
  | 'payments'
  | 'finance'
  | 'stock'
  | 'reports'
  | 'users'
  | 'machines'
  | 'field_service'
  // Bildirim yetkileri (FAZ-1). Rota seviyesinde yalnız görüntüleme izni
  // gerekir; onay/tetikleme/yönetim ayrımı sayfa içi eylemlerde ve backend
  // uçlarında uygulanır.
  | 'notifications'
  | 'supplier_prices.view'
  // Tarla yetkileri (mobil-erp#2). Rota seviyesinde yalnız `farm.view`
  // gerekir; yazma (`farm.manage`) ve girdi bağlama (`farm.inputs`) ayrımı
  // sayfa içi eylemlerde ve backend uçlarında uygulanır.
  //
  // DİKKAT — bu üçlü backend'de de üç AYRI yetkidir ve öyle kalmalı: tek bir
  // `farm` yetkisi olsaydı depo rolü, yalnız girdi girebilmek için parsel ve
  // sezon YAZMA hakkı da alırdı.
  | 'farm.view'
  | 'farm.manage'
  | 'farm.inputs'
  // Hayvancılık yetkileri (mobil-erp#17). Rota seviyesinde yalnız `herd.view`
  // gerekir. `herd.health` AYRI bir yetki ve öyle kalmalı: veteriner aşı
  // girebilmeli ama hayvan alım/satımı yapamamalı; tek bir `herd` yetkisi
  // bunu ayıramazdı.
  | 'herd.view'
  | 'herd.manage'
  | 'herd.health'
  | 'platform';

/**
 * Rota → gerekli izin.
 *
 * `:id` içeren anahtarlar desen olarak eşleşir (bkz. `permissionForPath`).
 * Her rol `read` iznine sahiptir; `read` "oturum açmış olmak yeterli" demektir.
 *
 * Not — bu harita kurulurken menü ve route izinleri iki yerde ayrı yazılıydı ve
 * iki noktada ayrışmıştı. Ayrışmanın her ikisi de **daha kısıtlayıcı** olan
 * tarafa çekildi, hiçbir rotanın erişimi genişletilmedi:
 *   - `/firmalar`: menü `read` idi, route `users` istiyordu → `users`.
 *     (Menüde görünüp tıklayınca sessizce Ana Sayfa'ya atan ölü madde buydu.)
 *   - `/alacaklar`: menü `payments` idi, route hiç korunmuyordu → `payments`.
 *     (Menüde gizliydi ama URL doğrudan yazılınca açılıyordu.)
 */
export const ROUTE_PERMISSIONS = {
  '/': 'read',
  '/hizli-satis': 'sales',
  '/satislar': 'read',
  '/alislar': 'read',
  '/belge-akislari': 'read',
  '/musteriler': 'read',
  '/musteriler/:id': 'read',
  '/tedarikciler': 'read',
  '/tedarikciler/:id': 'read',
  '/urunler': 'read',
  '/urunler/:id': 'read',
  '/parca-supersession': 'read',
  '/makineler': 'read',
  '/makineler/:id': 'read',
  '/is-emirleri': 'read',
  '/is-emirleri/:id': 'read',
  '/saha': 'field_service',
  '/saha/:id': 'field_service',
  // Tarla ekranları OKUMA iznine bağlı; yazma sayfa içinde `farm.manage` /
  // `farm.inputs` ile ayrıca sınırlanır ve asıl karar backend'dedir.
  '/tarla': 'farm.view',
  '/tarla/ciftlikler': 'farm.view',
  '/tarla/sezonlar': 'farm.view',
  '/tarla/faaliyetler': 'farm.view',
  '/tarla/gorevler': 'farm.view',
  '/tarla/hasat': 'farm.view',
  // Olay kuyrugu OKUMA iznine bagli. Kuyruk TARLA verisidir: hangi
  // faaliyet/hasat stoga islenmedi ve NEDEN. Backend'de de `farm.view`
  // (bkz. `_FARM_PATH_PREFIXES`); ekran ile uc ayni role bagli.
  '/tarla/olay-kuyrugu': 'farm.view',
  // Hızlı giriş OKUMA iznine bağlı; kaydetme sayfa içinde `farm.manage`
  // ile sınırlanıyor ve asıl karar backend'de.
  '/tarla/hizli-giris': 'farm.view',
  // Parsel detayı menüde DEĞİL: parsel listesinden açılıyor. Menüye koymak
  // "hangi parsel" sorusunu cevapsız bırakırdı.
  '/tarla/parseller/:id': 'farm.view',
  // Hayvancılık ekranları OKUMA iznine bağlı; yazma sayfa içinde
  // `herd.manage` / `herd.health` ile ayrıca sınırlanır ve asıl karar
  // backend'dedir.
  '/hayvancilik': 'herd.view',
  '/hayvancilik/hayvanlar': 'herd.view',
  '/hayvancilik/saglik': 'herd.view',
  '/hayvancilik/doller': 'herd.view',
  '/hayvancilik/verim': 'herd.view',
  // Hayvan detayı menüde DEĞİL: listeden açılıyor. Menüye koymak "hangi
  // hayvan" sorusunu cevapsız bırakırdı (parsel detayıyla aynı gerekçe).
  '/hayvancilik/hayvanlar/:id': 'herd.view',
  // Maliyet oranı bir PARA TANIMI (mobil-erp#24): tarla/hayvancılık okuma
  // izinlerine değil `finance`a bağlı — girdi giren depo rolünün oranları
  // görmesi gerekmiyor. Backend de aynı izni istiyor.
  '/tanimlar/maliyet-oranlari': 'finance',
  '/faturalar': 'read',
  '/faturalar/:id': 'read',
  '/odemeler': 'payments',
  '/tahsis-defteri': 'read',
  '/alacaklar': 'payments',
  '/nakit-yonetimi': 'finance',
  // Harman sezon tanımları finans yönetimidir; backend de tüm
  // /api/harvest-scheduling ucunu ``finance`` iznine bağlar.
  '/tanimlar/harman-sezon': 'finance',
  '/stok-hareketleri': 'read',
  '/depo-transferleri/:id': 'stock',
  // Şubeler arası stok görünürlüğü bir okumadır; içindeki transfer eylemi
  // ayrıca ``stock`` iznine bağlıdır.
  '/sube-transfer': 'read',
  '/stok-sayimlari': 'stock',
  '/stok-sayimlari/:id': 'stock',
  '/depolar': 'stock',
  '/depolar/:id': 'stock',
  '/sezonsal-stok-plani': 'read',
  '/raporlar': 'reports',
  '/raporlar/alacak-yaslandirma': 'reports',
  '/raporlar/tedarikci-karsilastirma': 'reports',
  // Rota raporlama izniyle açılır; panonun gösterdiği maliyet/tedarikçi verisi
  // ayrıca backend'de ``purchases`` iznine bağlıdır, sayfa 403'ü açıkça anlatır.
  '/raporlar/satin-alma-panosu': 'reports',
  '/raporlar/emilim-orani': 'reports',
  '/analizler': 'reports',
  '/firmalar': 'users',
  '/kullanicilar': 'users',
  '/islem-gecmisi': 'users',
  '/aktivite': 'users',
  '/bildirimler': 'notifications',
  '/bildirimler/sablonlar': 'notifications',
  '/tedarikci-fiyatlari': 'supplier_prices.view',
  '/yedekler': 'platform',
} as const satisfies Record<string, Permission>;

export type RoutePath = keyof typeof ROUTE_PERMISSIONS;

/**
 * Kimlik doğrulama kabuğunun dışındaki, izin gerektirmeyen rotalar.
 *
 * Tanım `./publicPaths` modülüne taşındı: `api.ts` de aynı listeyi okumak
 * zorunda ve bu dosya MUI ikonlarını içeri aldığı için ona bağımlı olamaz.
 * Buradan yeniden dışa aktarılır ki mevcut içe aktarımlar kırılmasın.
 */
export {PUBLIC_PATHS} from './publicPaths';

/**
 * Haritada karşılığı olmayan bir yol için varsayılan izin.
 *
 * `read` her rolde bulunduğu için bu, korumasız bir rotanın davranışını
 * değiştirmez; buradaki amaç yeni eklenen bir rotanın sessizce izinsiz
 * kalmamasıdır. Tutarlılık testi zaten haritada eksik rota bırakılmasını
 * engeller.
 */
const DEFAULT_PERMISSION: Permission = 'read';

const PATTERNS = Object.keys(ROUTE_PERMISSIONS)
  .map(pattern => ({pattern, segments: pattern.split('/').filter(Boolean)}))
  // Az joker içeren (daha spesifik) desen önce denensin.
  .sort((a, b) => {
    const wild = (s: string[]) => s.filter(x => x.startsWith(':')).length;
    return wild(a.segments) - wild(b.segments);
  });

/** Sorgu dizesi / hash ayıklanmış, sondaki eğik çizgisi atılmış yol. */
export function normalizePath(path: string): string {
  const clean = path.split('#')[0].split('?')[0];
  if (clean.length > 1 && clean.endsWith('/')) return clean.slice(0, -1);
  return clean || '/';
}

/**
 * Bir yolun (sorgu dizesi dahil olabilir) gerektirdiği izni döndürür.
 * `:id` gibi dinamik segmentler joker olarak eşleşir.
 */
export function permissionForPath(path: string): Permission {
  const clean = normalizePath(path);
  const exact = (ROUTE_PERMISSIONS as Record<string, Permission>)[clean];
  if (exact) return exact;

  const parts = clean.split('/').filter(Boolean);
  for (const {pattern, segments} of PATTERNS) {
    if (segments.length !== parts.length) continue;
    const hit = segments.every((segment, index) =>
      segment.startsWith(':') ? parts[index].length > 0 : segment === parts[index],
    );
    if (hit) return (ROUTE_PERMISSIONS as Record<string, Permission>)[pattern];
  }
  return DEFAULT_PERMISSION;
}

// ============================================================================
// ETİKETLER — kullanıcıya görünen tüm navigasyon metinleri tek blokta.
// Bir grubun/maddenin adını değiştirmek tek satırlık düzenlemedir.
// ============================================================================
export const NAV_LABELS = {
  // Sabit (grup dışı) maddeler
  home: 'Ana Sayfa',
  pos: 'Hızlı Satış',
  /** Üst çubuktaki birincil POS düğmesi — menüdeki maddeden daha kısa. */
  posAction: 'Satış',

  // Grup başlıkları
  groupSales: 'Satış',
  groupCustomers: 'Müşteriler',
  groupInventory: 'Stok & Ürünler',
  groupPurchasing: 'Satın Alma',
  groupService: 'Servis',
  groupFinance: 'Finans',
  groupFarm: 'Tarla',
  groupHerd: 'Hayvancılık',
  groupAdmin: 'Yönetim',

  // Grup içi maddeler
  sales: 'Satışlar',
  invoices: 'Faturalar',
  workflowDocuments: 'Teklif / Sipariş / İade',
  customers: 'Müşteri Listesi',
  products: 'Ürünler',
  stockMovements: 'Stok Hareketleri',
  warehouses: 'Depolar',
  inventoryCounts: 'Stok Sayımları',
  branchTransfer: 'Şubeler Arası Transfer',
  seasonalPlan: 'Sezonsal Stok Planı',
  partSupersession: 'Parça Supersession',
  purchases: 'Alışlar',
  suppliers: 'Tedarikçiler',
  purchaseDashboard: 'Satın Alma Panosu',
  supplierComparison: 'Tedarikçi Karşılaştırma',
  supplierPrices: 'Tedarikçi Fiyatları',
  workOrders: 'İş Emirleri',
  machines: 'Makineler',
  payments: 'Tahsilat / Ödeme',
  receivables: 'Harman Vadesi / Alacaklar',
  allocations: 'Tahsis Defteri',
  cashManagement: 'Nakit Yönetimi',
  receivablesAging: 'Alacak Yaşlandırma',
  harvestSeason: 'Harman Sezon Takvimi',
  costRates: 'Maliyet Oranları',
  farmDashboard: 'Tarla Panosu',
  farms: 'Çiftlikler & Parseller',
  cropSeasons: 'Ekim Sezonları',
  fieldActivities: 'Faaliyetler & Girdiler',
  fieldTasks: 'Tarla Görevleri',
  fieldHarvests: 'Hasat Kayıtları',
  integrationEvents: 'Olay Kuyruğu',
  quickActivity: 'Hızlı Faaliyet Girişi',
  herdDashboard: 'Sürü Panosu',
  animals: 'Hayvanlar & Sürüler',
  herdHealth: 'Sürü Sağlığı',
  herdBreeding: 'Döl Verimi',
  herdYields: 'Süt & Besi',
  reports: 'Raporlar',
  insights: 'Akıllı Analizler',
  absorptionRate: 'Emilim Oranı',
  users: 'Kullanıcılar',
  companies: 'Firma / Şubeler',
  notificationsQueue: 'Bildirimler',
  notificationTemplates: 'Bildirim Şablonları',
  activity: 'Aktivite',
  auditLog: 'İşlem Geçmişi',
  backups: 'Yedekler',

  // Kenar çubuğu bölüm başlığı
  workspace: 'ÇALIŞMA ALANI',
} as const;

// ============================================================================
// NAVİGASYON AĞACI
//
// 2 sabit madde + 8 grup = 10 üst düzey. Bir grup, o rolde görünür EN AZ BİR
// maddesi varsa görünür; izinler yukarıdaki ROUTE_PERMISSIONS'tan okunur, bu
// ağaçta izin YAZILMAZ.
//
// Raporlar bilinçli olarak tek bir "Raporlar" grubunda toplanmadı; her rapor
// kullanıldığı iş grubunda duruyor (satın alma panosu → Satın Alma, alacak
// yaşlandırma → Finans). Bkz. docs/ux-yenileme-f0-tasarim.md §2.2.
// ============================================================================
export type NavItem = {path: RoutePath; label: string; icon: React.ReactElement};
export type NavGroup = {id: string; label: string; icon: React.ReactElement; items: readonly NavItem[]};

const item = (path: RoutePath, label: string, icon: React.ReactElement): NavItem => ({path, label, icon});

/** Grup dışında, kenar çubuğunun en üstünde sabit duran maddeler. */
export const PINNED_ITEMS: readonly NavItem[] = [
  item('/', NAV_LABELS.home, <DashboardIcon />),
  // POS gün içinde en çok açılan ekran ve dokunmatik kullanılıyor; bir grubun
  // arkasına gömmek her satışa fazladan bir dokunuş ekler.
  item('/hizli-satis', NAV_LABELS.pos, <PointOfSaleIcon />),
];

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    id: 'sales',
    label: NAV_LABELS.groupSales,
    icon: <ReceiptLongIcon />,
    items: [
      item('/satislar', NAV_LABELS.sales, <ReceiptLongIcon />),
      item('/faturalar', NAV_LABELS.invoices, <ReceiptLongIcon />),
      item('/belge-akislari', NAV_LABELS.workflowDocuments, <AccountTreeIcon />),
    ],
  },
  {
    id: 'customers',
    label: NAV_LABELS.groupCustomers,
    icon: <PeopleIcon />,
    items: [item('/musteriler', NAV_LABELS.customers, <PeopleIcon />)],
  },
  {
    id: 'inventory',
    label: NAV_LABELS.groupInventory,
    icon: <Inventory2Icon />,
    items: [
      item('/urunler', NAV_LABELS.products, <Inventory2Icon />),
      item('/stok-hareketleri', NAV_LABELS.stockMovements, <TimelineIcon />),
      item('/depolar', NAV_LABELS.warehouses, <WarehouseIcon />),
      item('/stok-sayimlari', NAV_LABELS.inventoryCounts, <FactCheckOutlinedIcon />),
      item('/sube-transfer', NAV_LABELS.branchTransfer, <LocalShippingIcon />),
      item('/sezonsal-stok-plani', NAV_LABELS.seasonalPlan, <EventRepeatIcon />),
      item('/parca-supersession', NAV_LABELS.partSupersession, <SwapHorizIcon />),
    ],
  },
  {
    id: 'purchasing',
    label: NAV_LABELS.groupPurchasing,
    icon: <ShoppingCartIcon />,
    items: [
      item('/alislar', NAV_LABELS.purchases, <ShoppingCartIcon />),
      item('/tedarikciler', NAV_LABELS.suppliers, <BusinessIcon />),
      item('/raporlar/satin-alma-panosu', NAV_LABELS.purchaseDashboard, <ShoppingCartIcon />),
      item('/raporlar/tedarikci-karsilastirma', NAV_LABELS.supplierComparison, <PriceCheckIcon />),
      item('/tedarikci-fiyatlari', NAV_LABELS.supplierPrices, <PriceCheckIcon />),
    ],
  },
  {
    id: 'service',
    label: NAV_LABELS.groupService,
    icon: <BuildIcon />,
    items: [
      item('/is-emirleri', NAV_LABELS.workOrders, <BuildIcon />),
      item('/makineler', NAV_LABELS.machines, <AgricultureIcon />),
    ],
  },
  {
    id: 'finance',
    label: NAV_LABELS.groupFinance,
    icon: <PaymentsIcon />,
    items: [
      item('/odemeler', NAV_LABELS.payments, <PaymentsIcon />),
      item('/alacaklar', NAV_LABELS.receivables, <AgricultureIcon />),
      item('/tahsis-defteri', NAV_LABELS.allocations, <AccountBalanceWalletIcon />),
      item('/nakit-yonetimi', NAV_LABELS.cashManagement, <AccountBalanceIcon />),
      item('/raporlar/alacak-yaslandirma', NAV_LABELS.receivablesAging, <AssessmentIcon />),
      item('/tanimlar/harman-sezon', NAV_LABELS.harvestSeason, <CalendarMonthIcon />),
      item('/tanimlar/maliyet-oranlari', NAV_LABELS.costRates, <PaymentsIcon />),
    ],
  },
  {
    // Tarla, Servis'in altına DEĞİL ayrı bir grup olarak konuldu: servis
    // grubu bayiye ait makine/iş emri işidir, tarla ise çiftçinin kendi
    // üretimi. İkisini birleştirmek "iş emri" ile "faaliyet"i aynı listede
    // gösterirdi ve ikisi farklı şey.
    id: 'farm',
    label: NAV_LABELS.groupFarm,
    icon: <AgricultureIcon />,
    items: [
      item('/tarla', NAV_LABELS.farmDashboard, <AgricultureIcon />),
      item('/tarla/ciftlikler', NAV_LABELS.farms, <AgricultureIcon />),
      item('/tarla/sezonlar', NAV_LABELS.cropSeasons, <EventRepeatIcon />),
      item('/tarla/hizli-giris', NAV_LABELS.quickActivity, <BoltIcon />),
      item('/tarla/faaliyetler', NAV_LABELS.fieldActivities, <SpaIcon />),
      item('/tarla/gorevler', NAV_LABELS.fieldTasks, <FactCheckOutlinedIcon />),
      item('/tarla/hasat', NAV_LABELS.fieldHarvests, <AgricultureIcon />),
      item('/tarla/olay-kuyrugu', NAV_LABELS.integrationEvents, <SwapHorizIcon />),
    ],
  },
  {
    // Hayvancılık, Tarla'nın ALTINA konmadı: tarla bitkisel üretimdir
    // (parsel, sezon, hasat), hayvancılık ayrı bir varlık ve ayrı bir
    // takvimdir (küpe, aşı, doğum). Aynı grupta olsaydı "faaliyet" ile
    // "aşı" tek listede görünürdü ve ikisi farklı iş.
    id: 'herd',
    label: NAV_LABELS.groupHerd,
    icon: <PetsIcon />,
    items: [
      item('/hayvancilik', NAV_LABELS.herdDashboard, <PetsIcon />),
      item('/hayvancilik/hayvanlar', NAV_LABELS.animals, <PetsIcon />),
      item('/hayvancilik/saglik', NAV_LABELS.herdHealth, <VaccinesIcon />),
      item('/hayvancilik/doller', NAV_LABELS.herdBreeding, <ChildCareIcon />),
      item('/hayvancilik/verim', NAV_LABELS.herdYields, <WaterDropIcon />),
    ],
  },
  {
    id: 'admin',
    label: NAV_LABELS.groupAdmin,
    icon: <ManageAccountsIcon />,
    items: [
      item('/raporlar', NAV_LABELS.reports, <AssessmentIcon />),
      item('/analizler', NAV_LABELS.insights, <AnalyticsIcon />),
      item('/raporlar/emilim-orani', NAV_LABELS.absorptionRate, <SpeedIcon />),
      item('/kullanicilar', NAV_LABELS.users, <ManageAccountsIcon />),
      item('/firmalar', NAV_LABELS.companies, <BusinessIcon />),
      item('/bildirimler', NAV_LABELS.notificationsQueue, <NotificationsActiveIcon />),
      item('/bildirimler/sablonlar', NAV_LABELS.notificationTemplates, <NotificationsActiveIcon />),
      item('/aktivite', NAV_LABELS.activity, <FactCheckOutlinedIcon />),
      item('/islem-gecmisi', NAV_LABELS.auditLog, <HistoryIcon />),
      item('/yedekler', NAV_LABELS.backups, <BackupIcon />),
    ],
  },
];

/** Menüde yer alan tüm maddeler (sabitler + grup içerikleri). */
export const ALL_NAV_ITEMS: readonly NavItem[] = [
  ...PINNED_ITEMS,
  ...NAV_GROUPS.flatMap(group => group.items),
];

/**
 * Verilen yolu içeren grubun kimliği; sabit maddeler için `null`.
 * Aktif sayfanın grubunu otomatik açmak için kullanılır.
 */
export function groupIdForPath(pathname: string): string | null {
  const clean = normalizePath(pathname);
  let best: {id: string; length: number} | null = null;
  for (const group of NAV_GROUPS) {
    for (const navItem of group.items) {
      const matches = clean === navItem.path || clean.startsWith(`${navItem.path}/`);
      // En uzun eşleşme kazanır: `/raporlar/emilim-orani` "Yönetim" grubunun
      // kendi maddesidir, `/raporlar` önekiyle karışmamalı.
      if (matches && (!best || navItem.path.length > best.length)) {
        best = {id: group.id, length: navItem.path.length};
      }
    }
  }
  return best?.id ?? null;
}
