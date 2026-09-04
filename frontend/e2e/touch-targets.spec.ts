import type {APIResponse, Locator, Page} from '@playwright/test';
import {readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';

import {
  adminApi, createCustomer, createProduct, expect, login, money, test,
  TOHUM_DIZINI, tohumHatirla,
} from './helpers';

const VIEWPORTS = [
  {name: 'mobile-390', width: 390, height: 844, enforceTouchTargets: true},
  {name: 'desktop-1280', width: 1280, height: 900, enforceTouchTargets: false},
] as const;

// This manifest is deliberately narrow. Adding a route/state to this list means
// the test below must seed it, prove that it rendered, and inspect its controls.
export const TOUCH_TARGET_COVERAGE = {
  covered: [
    'admin happy path',
    'farm dashboard with one active season',
    'parcel detail with one activity',
    'field activity list with one activity',
    'field activity input detail with one input',
    'open work order with one reserved part, one draft labor line, and one attachment',
    'receivables aging with one open completed-work-order receivable and its document detail',
    'issued invoice detail with one invoice snapshot line and e-invoice status NONE',
    'admin activity list with one unique unarchived POS sale event',
    'herd dashboard with one active cow and one manually scheduled overdue vaccination',
    'allocation reconciliation with one unique credit POS document and the allocation engine disabled',
    'notification outbox with one consent-backed event awaiting approval',
    'notification templates with one active service reminder template',
    'POS cart with one unique in-stock product',
    'stock transfer with one unique product stocked in one of two warehouses and empty transfer history',
    'mobile 390px and desktop 1280px action presence',
    'a declared data surface on every screen above, measured for horizontal overflow at 390px',
    'horizontal overflow that CLIPS (overflow-x: hidden/clip), not only overflow that scrolls',
    'deleting a screen declared surface fails the gate instead of measuring nothing',
    'every declared data surface must render at least one seeded row or card before it is measured',
  ],
  notCovered: [
    'new activity dialog validation states',
    'offline/outbox and rejected queue states',
    'API error and loading states',
    'permission-dependent and read-only roles',
    'terminal work orders and returned/void line states',
    'billing ready/not-ready/error variants beyond the seeded open work order',
    'receivables aging empty/error states, alternate as-of dates, other aging buckets, and multiple customers',
    'invoice loading/error/cancelled states and configured provider submission outcomes',
    'activity filters, later pages, detail drawer, archive dialog, archived rows, and non-admin roles',
    'vertical overflow and clipping — this gate measures the horizontal axis only',
    'overflow at viewport widths between 390px and 1280px',
    'herd dashboard empty/error/loading states, group-only herds, other species, and no-overdue states',
    'allocation history, engine-enabled manual/reversal/reallocation flows, and reconciliation error/filter variants',
    'notification pending/disarmed/sent/simulated/problem tabs, preview/approval/dispatch outcomes, and non-admin roles',
    'notification template create/edit/reapproval, commercial classification, error, and non-admin states',
    'POS empty cart, completed sale, discounts, customer/payment variants, keyboard, policy, and out-of-stock states',
    'stock transfer dialog/completion/history/detail, error, no-stock, and permission-dependent states',
  ],
} as const;

type Seed = {
  workOrderId: number;
  parcelId: number;
  parcelName: string;
  cropName: string;
  seasonYear: number;
  activityAreaLabel: string;
  productName: string;
  technicianName: string;
  attachmentName: string;
};

type SliceTwoSeed = {
  activitySummary: string;
  agingDocumentNo: string;
  agingRemaining: string;
  agingTotal: string;
  customerId: number;
  customerName: string;
  invoiceId: number;
  invoiceItemDescription: string;
  invoiceNumber: string;
};

type BatchThreeSeed = {
  herdEarTag: string;
  transferNote: string;
  saleOrderId: number;
  notificationId: number;
  notificationTemplateId: number;
  notificationTemplateName: string;
  paymentDocumentNo: string;
  productBarcode: string;
  productName: string;
};

type Metrics = {
  controlCount: number;
  disabledControlCount: number;
  tooSmallControlCount: number;
  allControls: Array<{
    tag: string;
    role: string | null;
    ariaLabel: string | null;
    text: string;
    width: number;
    height: number;
    minHeight: string;
    minWidth: string;
    heightSource: string;
    fontFamily: string;
    fontSize: string;
    lineHeight: string;
    paddingBlock: string;
    borderBlock: string;
    boxSizing: string;
  }>;
  environment: {
    devicePixelRatio: number;
    fontsStatus: string;
    platform: string;
    userAgent: string;
    bodyFontFamily: string;
    robotoAvailable: boolean | null;
    innerWidth: number;
    clientWidth: number;
    scrollbarWidth: number;
    runningAnimationCount: number;
    totalRunningAnimationCount: number;
    transformedAncestorCount: number;
    measuredGridStep: number;
  };
  tooSmallControls: Array<{
    tag: string;
    role: string | null;
    ariaLabel: string | null;
    text: string;
    width: number;
    height: number;
  }>;
};

type HorizontalOverflowMetrics = {
  checkedSurfaceCount: number;
  overflowingSurfaceCount: number;
  contentRowCount: number;
  emptySurfaceCount: number;
  worstOffender: {overflowX: string; scrollWidth: number; clientWidth: number} | null;
  // En geniş aday, TAŞMASA BİLE. Yeşil bir koşunun ne kadar pay bıraktığını
  // ölçmeden, kapının kararlı mı yoksa yazı-tura mı olduğu bilinemez.
  widest: {overflowX: string; scrollWidth: number; clientWidth: number} | null;
  // KAPININ KENDİ ÖRNEK DİZİSİ. Her örnekte kaç yüzey taşıyordu. Dışa
  // açılmasının sebebi tek: testlerin, kapının GÖRDÜĞÜNÜ ikinci kez ve AYRI bir
  // zamanlamayla gözlemek zorunda kalmaması. İki bağımsız gözlem, iki ayrı
  // zamanlama riski demekti ve ölçülen kararsızlığın kaynağı tam olarak buydu.
  ornekler: number[];
};

// What counts as a rendered row. At 390px ResponsiveTable draws one marked Card
// per row; on desktop the DataGrid draws .MuiDataGrid-row. The empty-state and
// loading shells match NEITHER, which is the whole point: a surface showing
// "Henüz kayıt yok" must not be mistaken for a surface with data.
const CONTENT_ROW_SELECTOR = '[data-responsive-row], .MuiDataGrid-row';

// How many data surfaces each screen is EXPECTED to declare. Asserting the
// exact number, not ">= 1", is deliberate: WorkOrderDetail carries three
// tables (attachments, labor lines, parts) and under a ">= 1" rule deleting
// two of them would still pass. The count is the contract — change this map
// only when a screen genuinely gains or loses a data surface.
// YAPILANDIRMA. Tahsis motoru üretimde KAPALI gelir; kapı, kullanıcıların
// sahip olduğu yapılandırmayı ölçmek zorundadır. Bayrak açıkken tahsis geçmişi
// bir veri yüzeyi BEYAN EDER ve içerik gösterir; kapalıyken yüzey hiç beyan
// edilmez ve ekran açık bir "devre dışı" durumu çizer.
export const ALLOCATION_ENGINE_ENABLED =
  process.env.PAYMENT_ALLOCATION_ENGINE_ENABLED === 'true';

export const DECLARED_DATA_SURFACES = {
  'work-order-detail-data-surface': 3,
  'parcel-detail-data-surface': 1,
  'field-activities-data-surface': 1,
  'field-activity-detail-data-surface': 1,
  'farm-dashboard-data-surface': 1,
  'receivables-aging-data-surface': 1,
  'invoice-detail-data-surface': 1,
  'activity-log-data-surface': 1,
  // Ölçülen mobil parti (#57). Her ekran bir yüzey beyan eder; iki ekran
  // İKİŞER yüzey taşır (PaymentAllocations: mutabakat + geçmiş,
  // StockTransfer: şube stoğu + transfer geçmişi) ve ikisi de ayrı ayrı
  // ölçülür — dolu bir tablo boş kardeşini örtemez.
  'herd-dashboard-data-surface': 1,
  'payment-allocations-reconciliation-data-surface': 1,
  'notifications-data-surface': 1,
  'notification-templates-data-surface': 1,
  'pos-cart-data-surface': 1,
  'stock-transfer-stock-data-surface': 1,
  'stock-transfer-history-data-surface': 1,
  ...(ALLOCATION_ENGINE_ENABLED
    ? {'payment-allocations-history-data-surface': 1 as const}
    : {}),
} as const;

const CONTROL_SELECTOR = [
  'button',
  '[role="button"]',
  '[role="link"]',
  'a[href]',
  'input',
  'select',
  'textarea',
  '[type="checkbox"]',
  '[type="radio"]',
].join(',');

// ÖLÇÜM BİRİMİ: SABİT 1/64 CSS pikseli (yerleşim ızgarası birimi).
//
// NE YAPIYOR: Karşılaştırma, ölçülen kutuyu AŞAĞIDAKİ SABİTE göre yuvarlar.
// `LAYOUT_GRID_DENOMINATOR` derleme zamanı bir sabittir; koşu anında ölçülen
// değer bu yuvarlamayı SÜRMEZ. Ortamın ızgara adımı ayrıca ölçülür ama YALNIZ
// BEKÇİ olarak: 1/64 değilse koşu kırmızı yanar ve buradaki gerekçe yeniden
// açılır. Bu ayrım bilinçlidir — yuvarlamayı ölçülen değere bağlamak toleransı
// ÜST SINIRSIZ yapardı; ortam bir gün daha kaba bir ızgara bildirse kapı sessizce
// gevşerdi.
//
// 1/64 NEREDEN GELİYOR: Blink'in `LayoutUnit` tipi sabit noktalı bir sayıdır ve
// paydası 64'tür (`kFixedPointDenominator`, layout_unit.h). Motor kutu
// boyutlarını bu ızgarada temsil eder, daha incesini İFADE EDEMEZ. Bu depoda
// kaynağı okuyarak değil ÖLÇEREK doğrulandı: `min-height:43.999px` istendiğinde
// geri gelen değer 43.984375 (= 43 + 63/64), `44.015625px` (= 44 + 1/64) ise
// aynen korunuyor. Aynı ölçüm CI'ın Linux koşucusunda da 1/64 verdi.
//
// NE OLDUĞU — VE NE OLMADIĞI: bu snap bir ÖLÇÜM NİCELEME KALKANIDIR. Sapmayı
// AÇIKLAMAZ ve sebebini DÜZELTMEZ; yalnız niceleme gürültüsünü eşiğin altına
// düşmekten alıkoyar. Sebep ayrıca ölçüldü ve ayrıca kapatıldı (bkz.
// `settleAnimations`): `getBoundingClientRect()` kenarları float32'dir ve
// animasyon sürerken kutu kesirli bir konuma oturduğunda `top` ile `bottom`
// FARKLI ikili aralıklara düşüp ayrı ayrı yuvarlanır; farkları yerleşim
// yüksekliğini bir ulp ıskalayabilir. Ölçülen sapma ±1/16384'tür — 1/64'ün
// katı DEĞİLDİR, yani kaynağı LayoutUnit niceleme değildir.
//
// Bu ayrım önemlidir: #66'nın gerekçesi sapmayı LayoutUnit sabit noktasına
// bağlıyordu ve bu aritmetik olarak tutmuyor (1/16384, 1/64'ün katı değil).
// Bu PR o cümleyi düzeltir. Snap KALIR, çünkü ölçüm nicelemesine karşı hâlâ
// doğru kalkandır; ama açıklama olarak sunulmaz.
//   43.99993896484375 -> 44         (artefakt, geçer — doğru)
//   43.984375         -> 43.984375  (bir ızgara birimi eksik, KIRMIZI kalır)
//   43.5              -> 43.5       (KIRMIZI kalır)
//
// BUNUN ETKİN TOLERANSI NEDİR, DÜRÜSTÇE: `Math.round` kullanıldığı için 44'ün
// YARIM IZGARA BİRİMİ altına kadar (1/128 = 0.0078125) olan değerler 44'e
// oturur. Yani bu bir tolerans DEĞİL demek yanlış olur; doğru ifade şudur:
// tolerans VARDIR, üst sınırı yarım ızgara birimidir ve bu sınır ayarlanabilir
// bir eşikten değil motorun temsil granülaritesinden gelir. Gerçekten kısa
// tasarlanmış bir kontrolün ölçülebilecek en küçük eksikliği bir TAM ızgara
// birimidir (1/64), yani her gerçek ihlal bu sınırın dışında kalır.
//
// KURAL: EŞİK 44 CSS PİKSELİDİR, YERLEŞİM MOTORUNUN TEMSİL ETTİĞİ HÂLİYLE.
const LAYOUT_GRID_DENOMINATOR = 64;

function snapToLayoutGrid(value: number): number {
  return Math.round(value * LAYOUT_GRID_DENOMINATOR) / LAYOUT_GRID_DENOMINATOR;
}

// ÖLÇÜM ANI YERLEŞMİŞ OLMALI — ama YALNIZ ÖLÇÜLEN GEOMETRİYİ OYNATABİLEN
// animasyonlar için.
//
// MEKANİZMA (ölçüldü 2026-08-17, NotificationTemplates/"Düzenle"):
//   * `getBoundingClientRect()` kenarları float32 olarak döner. Ölçülen 13
//     karenin HEPSİNDE `top` ve `bottom` tam olarak float32'de temsil
//     edilebilir değerlerdi (`Math.fround(x) === x`).
//   * `height` bağımsız saklanmaz: her karede tam olarak `bottom - top`.
//   * Animasyon sürerken kutu KESİRLİ bir konuma oturur. Ölçülen örnekte
//     `top ≈ 1014.6` → [512,1024) ikili aralığı, ulp = 2^-14 = 1/16384;
//     `bottom ≈ 1058.6` → [1024,2048), ulp = 2^-13 = 1/8192. İki kenar AYRI
//     AYRI ve FARKLI ızgaralarda yuvarlanır; farkları bu yüzden yerleşim
//     yüksekliğini bir ulp ıskalayabilir. Ölçülen sapma ±1/16384'tür ve
//     kareler arasında İŞARET DEĞİŞTİRİR (-, 0, +) — bağımsız yuvarlamanın
//     imzası budur. Sınır de tutar: |sapma| <= ulp(top)/2 + ulp(bottom)/2
//     = 3/32768 ≈ 9.15e-5; ölçülen en büyük sapma 6.10e-5.
//   * `offsetHeight` (tamsayı yerleşim) 13 karenin hepsinde tam 44'tü.
//
// Yani ana öğedeki `translateY` yüksekliği DEĞİŞTİRMEZ; kutuyu kesirli bir
// konuma taşır, bağımsız float32 yuvarlaması da farkı oradan kaydırır.
// Kontrol hiçbir an küçük değildi.
//
// KAPSAM — NİYE HER ANİMASYON DEĞİL: kırmızı, "bir dokunma hedefi küçük"
// demektir. Ölçülen geometriyi oynatamayan (ilgisiz alt ağaçta, ya da yalnız
// renk/opaklık oynatan) kalıcı bir animasyonu kırmızıya çevirmek, kimsenin
// üzerine gidemeyeceği bir kırmızı üretir; böyle bir kapı ilk engellediği
// kişide devre dışı bırakılır. Bu yüzden ilgi, NİYETE göre değil
// `getAnimations()`'tan OKUNABİLEN iki ölçüte göre tanımlanır:
//   1. animasyonun hedefi ölçülen bir kontroldür ya da onu İÇEREN bir atadır,
//   2. oynattığı özelliklerden en az biri boyut/konum değiştirebilir.
// SINIFLANDIRMA TERSİNE ÇEVRİLDİ — VARSAYILAN: İLGİLİ.
//
// ÖNCEKİ HÂLİ BİR İZİN LİSTESİYDİ: listede olmayan her şey ZARARSIZ sayılıyordu.
// Bu, J6'nın yapısal-önek muafiyetiyle aynı şekildir — eksik olan sessizce muaf
// olur. Ölçülerek kaçtı (runtime Şekil A): `--shift` animasyonu + üst öğede
// `transform: translateY(var(--shift))`; geometri gerçekten oynuyor,
// `runningAnimationCount` 0 kalıyordu. Aynı boşluk `transform-origin`,
// `grid-template-*` ve mantıksal `margin-inline-*` için de okunarak gösterildi.
//
// ARTIK: animasyonun oynattığı HER özellik ölçülen kutuyu oynatabilir SAYILIR;
// yalnız aşağıdaki KÜÇÜK ve TEK TEK GEREKÇELENDİRİLMİŞ küme muaftır. Yük artık
// muafiyet tarafındadır: bir özellik ancak kutuyu KANITLANABİLİR biçimde
// oynatamıyorsa girer.
//
// MUAFİYET GEREKÇELERİ — hepsi yalnız BOYAMA, kenar kutusunu değiştiremez:
//   opacity                       → yalnız boya
//   color / *-color / fill/stroke → yalnız boya
//   background, background-*      → yalnız boya (background-size dahil)
//   box-shadow, text-shadow       → kenar kutusunun dışına çizilir
//   outline-color                 → outline yerleşime girmez
// KASITLI OLARAK MUAF DEĞİL: `filter` (içeren blok yaratır), `visibility`
// (ölçülen POPÜLASYONU değiştirir), `pointer-events` (metrikteki `disabled`
// sınıflandırmasını değiştirir).
//
// ÖZEL DEĞİŞKENLER (`--x`) HİÇBİR ZAMAN MUAF DEĞİLDİR — SEÇİM VE BEDELİ:
//   * ÖLÇÜLDÜ: özel değişken animasyonunda `getKeyframes()` özelliği HİÇ
//     bildirmez; yalnız üst veri döner (`offset/easing/composite/
//     computedOffset`). Yani "adı `--` ile başlıyorsa" denetimi TEK BAŞINA
//     çalışamaz — ad zaten görünmez. Bu yüzden kural şudur: OKUNABİLİR HİÇBİR
//     ÖZELLİK YOKSA atalet kanıtlanamaz, animasyon İLGİLİ sayılır.
//   * Adından ne beslediği OKUNAMAZ; `var()` dolaylılığı her özelliğe ulaşır.
//   * BEDEL: yalnız renk besleyen bir özel değişken animasyonu kapıyı KIRMIZI
//     yapar. Bu bir YANLIŞ KIRMIZIDIR ve bilerek kabul edilmiştir.
//   * BEDEL ÖLÇÜLDÜ: bugün bu depoda animasyonlu özel değişken YOK — ne
//     `@keyframes` içinde `--x` bildirimi, ne `transition`/`animation` hedefi.
//     Bugünkü maliyet SIFIR; biri eklenirse kapı sessiz kalmaz, kırmızı verir.
//   * REDDEDİLEN ALTERNATİFLER: (a) muaf saymak Şekil A'yı geri getirir;
//     (b) değişkenin neyi beslediğini CSSOM'dan çözmek `var()` zinciri, satır
//     içi stil ve çapraz-köken sayfalar yüzünden güvenilmezdir ve çözemediğinde
//     zaten kapalıya düşmek zorundadır.
const LAYOUT_INERT_PROPERTIES = new Set([
  'opacity',
  'color', 'backgroundcolor', 'bordercolor', 'bordertopcolor', 'borderbottomcolor',
  'borderleftcolor', 'borderrightcolor', 'outlinecolor', 'textdecorationcolor',
  'caretcolor', 'accentcolor', 'fill', 'stroke',
  'background', 'backgroundimage', 'backgroundposition', 'backgroundsize',
  'backgroundrepeat', 'backgroundblendmode',
  'boxshadow', 'textshadow',
]);

// Keyframe ÜST VERİSİ özellik değildir; tersine kuralda bunları özellik saymak
// HER animasyonu ilgili yapardı.
const KEYFRAME_METADATA_KEYS = new Set(['offset', 'computedoffset', 'easing', 'composite']);

// TEK KAYNAK: bekleme ile ölçüm aynı yüklemi kullanır; ayrı yazılsalar biri
// daralıp diğeri kalabilir ve kapı ölçmediği bir şeyi beklerdi.
const RELEVANT_ANIMATIONS_SOURCE = `(node, selector, inertProperties, metadataKeys) => {
  const controls = Array.from(node.querySelectorAll(selector));
  const touchesMeasuredGeometry = (animation) => {
    const effect = animation.effect;
    if (!effect || typeof effect.getKeyframes !== 'function') return false;
    const target = effect.target;
    if (!target) return false;
    const coversAControl = controls.some(
      (control) => control === target || target.contains(control));
    if (!coversAControl) return false;
    let sawAnyProperty = false;
    for (const frame of effect.getKeyframes()) {
      for (const key of Object.keys(frame)) {
        const lowered = key.toLowerCase();
        if (metadataKeys.has(lowered)) continue;
        sawAnyProperty = true;
        if (key.startsWith('--')) return true;
        if (!inertProperties.has(lowered.replace(/-/g, ''))) return true;
      }
    }
    // OKUNABILIR OZELLIK YOKSA: atalet KANITLANAMAZ -> ILGILI.
    // Olculdu: ozel degisken animasyonlarinda getKeyframes() YALNIZ ust veri
    // dondurur (offset/easing/composite/computedOffset); --x adi hic gorunmez.
    // Sekil A tam olarak buradan kaciyordu.
    return !sawAnyProperty;
  };
  return node.getAnimations({subtree: true})
    .filter((animation) => animation.playState === 'running')
    .filter(touchesMeasuredGeometry);
}`;

async function settleAnimations(root: Locator): Promise<void> {
  await root.evaluate(async (node, {selector, properties, metadataKeys, source}) => {
    const relevant = new Function(`return ${source}`)() as (
      n: Element, s: string, inert: Set<string>, meta: Set<string>) => Animation[];
    const inert = new Set(properties);
    const meta = new Set(metadataKeys);
    const running = () => relevant(node, selector, inert, meta);
    const deadline = performance.now() + 5000;
    while (running().length > 0 && performance.now() < deadline) {
      await Promise.race([
        Promise.allSettled(running().map(animation => animation.finished)),
        new Promise(resolve => setTimeout(resolve, 100)),
      ]);
    }
    await new Promise<void>(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  }, {selector: CONTROL_SELECTOR, properties: [...LAYOUT_INERT_PROPERTIES],
      metadataKeys: [...KEYFRAME_METADATA_KEYS], source: RELEVANT_ANIMATIONS_SOURCE});
}

async function collectMetrics(root: Locator): Promise<Metrics> {
  await expect(root, 'screen root must render before controls are measured').toBeVisible();
  await settleAnimations(root);
  const metrics = await root.evaluate((node, {selector, gridDenominator, inertProperties, metadataKeys, relevantSource}) => {
    const snap = (value: number) => Math.round(value * gridDenominator) / gridDenominator;
    const controls = Array.from(node.querySelectorAll<HTMLElement>(selector))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        if (
          rect.width <= 0 ||
          rect.height <= 0 ||
          style.display === 'none' ||
          style.visibility === 'hidden' ||
          element.getAttribute('aria-hidden') === 'true' ||
          (element instanceof HTMLInputElement && element.type === 'hidden')
        ) return null;
        return {
          tag: element.tagName,
          role: element.getAttribute('role'),
          ariaLabel: element.getAttribute('aria-label'),
          text: element.textContent?.trim() ?? '',
          width: rect.width,
          height: rect.height,
          // TEŞHİS: bildirilen kutu ile BEYAN EDİLEN asgarî ölçüyü ayrı tut.
          // Kırmızı geldiğinde "kutu neden küçük" sorusunun iki farklı cevabı
          // var: kontrol gerçekten küçük tanımlanmış, ya da ölçüm anında henüz
          // son boyutuna ulaşmamış. Bu ikisi ancak yan yana görülünce ayrılır.
          minHeight: style.minHeight,
          minWidth: style.minWidth,
          // YÜKSEKLİĞİN KAYNAĞI: açık bir `min-height` mi, yoksa içerik +
          // satır yüksekliğinden mi türüyor? İkisi farklı kusur sınıfıdır ve
          // ölçülen kutuya bakarak ayırt EDİLEMEZ.
          heightSource:
            style.minHeight !== 'auto' && style.minHeight !== '0px'
              ? 'explicit-min-height'
              : 'derived-from-content',
          // ÇÖZÜLEN yazı tipi — istenen değil. Yazı tipi metrikleri platforma
          // göre değişir; Linux koşucusu ile Windows geliştirici makinesi aynı
          // aileyi bulamayabilir ve içerikten türeyen yükseklik o noktada kayar.
          fontFamily: style.fontFamily,
          fontSize: style.fontSize,
          lineHeight: style.lineHeight,
          paddingBlock: `${style.paddingTop}/${style.paddingBottom}`,
          borderBlock: `${style.borderTopWidth}/${style.borderBottomWidth}`,
          boxSizing: style.boxSizing,
          disabled: element.hasAttribute('disabled') || style.pointerEvents === 'none',
        };
      })
      .filter((control): control is NonNullable<typeof control> => control !== null);

    return {
      controlCount: controls.length,
      disabledControlCount: controls.filter(control => control.disabled).length,
      tooSmallControlCount: controls.filter(
        control => snap(control.width) < 44 || snap(control.height) < 44,
      ).length,
      tooSmallControls: controls
        .filter(control => snap(control.width) < 44 || snap(control.height) < 44)
        .map(({disabled: _disabled, ...control}) => control),
      // TÜM kontroller — yalnız ihlal edeni değil. Kırmızı bir koşuyu yeşil bir
      // koşuyla karşılaştırmanın tek yolu POPÜLASYONLARI karşılaştırmaktır:
      // aynı kontroller mi ölçüldü, aynı sayıda mı, aynı boyutlarda mı?
      allControls: controls.map(({disabled: _disabled, ...control}) => control),
      // Ölçümün taşıdığı ORTAM KOŞULLARI. Bunlar yazılmazsa her yeşil,
      // kimsenin kaydetmediği bir koşula bağlı kalır (bkz. saat dilimi dersi).
      environment: {
        devicePixelRatio: window.devicePixelRatio,
        fontsStatus: (document as {fonts?: {status?: string}}).fonts?.status ?? 'unavailable',
        // PLATFORM ve yazı tipi KULLANILABİLİRLİĞİ. Kapı bugüne kadar hangi
        // platformda ve hangi yazı tipi yığınıyla ölçtüğünü hiç bildirmiyordu.
        platform: navigator.platform,
        userAgent: navigator.userAgent,
        bodyFontFamily: window.getComputedStyle(document.body).fontFamily,
        robotoAvailable: (document as {fonts?: {check?: (f: string) => boolean}}).fonts?.check
          ? (document as unknown as {fonts: {check: (f: string) => boolean}}).fonts.check('16px Roboto')
          : null,
        innerWidth: window.innerWidth,
        clientWidth: document.documentElement.clientWidth,
        scrollbarWidth: window.innerWidth - document.documentElement.clientWidth,
        // ÖLÇÜM ANININ YERLEŞMİŞLİĞİ. Bunlar sıfır değilse ölçülen kutular
        // dönüşüm uzayında alınmıştır ve ±1/16384 sapma buradan gelir.
        // YALNIZ ÖLÇÜLEN GEOMETRİYİ OYNATABİLEN animasyonlar sayılır;
        // ilgisiz alt ağaçtaki kalıcı animasyon kapıyı kırmızıya ÇEVİRMEZ.
        runningAnimationCount: (
          new Function(`return ${relevantSource}`)() as (
            n: Element, s: string, inert: Set<string>, meta: Set<string>) => Animation[]
        )(node, selector, new Set(inertProperties), new Set(metadataKeys)).length,
        totalRunningAnimationCount: node.getAnimations({subtree: true})
          .filter(animation => animation.playState === 'running').length,
        transformedAncestorCount: (() => {
          let count = 0;
          for (const element of Array.from(node.querySelectorAll<HTMLElement>('*'))) {
            if (window.getComputedStyle(element).transform !== 'none') count += 1;
          }
          return count;
        })(),
        // Motorun temsil edebildiği EN KÜÇÜK boyut farkı, koşu anında ölçülür.
        measuredGridStep: (() => {
          const probe = document.createElement('div');
          probe.style.cssText = 'position:absolute;visibility:hidden;width:10px;height:43.999px';
          document.body.appendChild(probe);
          const snapped = probe.getBoundingClientRect().height;
          probe.remove();
          return Math.round((44 - snapped) * 1e6) / 1e6;
        })(),
      },
    };
  }, {selector: CONTROL_SELECTOR, gridDenominator: LAYOUT_GRID_DENOMINATOR,
      inertProperties: [...LAYOUT_INERT_PROPERTIES], metadataKeys: [...KEYFRAME_METADATA_KEYS],
      relevantSource: RELEVANT_ANIMATIONS_SOURCE});

  expect(metrics.controlCount, 'rendered screen must contain at least one visible control').toBeGreaterThan(0);
  // IZGARA ADIMI KOŞU ANINDA DOĞRULANIR. 1/64 Windows'ta ölçüldü; kusurun
  // göründüğü ortam başsız Linux. Granülarite orada farklıysa yukarıdaki
  // gerekçe çöker, bu yüzden varsayılmaz — her koşuda ölçülür ve bildirilir.
  expect(
    metrics.environment.measuredGridStep,
    `layout grid step guard: snapping uses the FIXED 1/${LAYOUT_GRID_DENOMINATOR} constant, ` +
    `and this measurement only verifies the environment still agrees; a mismatch reopens the rationale`,
  ).toBeCloseTo(1 / LAYOUT_GRID_DENOMINATOR, 10);
  // ÖLÇÜM ANI YERLEŞMİŞ Mİ? Animasyon sürerken alınan kutu dönüşüm uzayındadır
  // ve ±1/16384 sapar (ölçüldü: NotificationTemplates/Düzenle). Snap bunu
  // yutuyor olsa bile, ölçümün yerleşmemiş bir anda yapılması KAPININ KENDİ
  // KUSURUDUR: aynı zamanlama popülasyonu da (kaç kontrol mount olmuş)
  // kaydırır. Bu yüzden burada sessizce geçilmez.
  expect(
    metrics.environment.runningAnimationCount,
    'controls must be measured after geometry-affecting animations settle; while such an ' +
    'animation runs, getBoundingClientRect edges are float32-rounded independently and their ' +
    'difference can miss the layout height by one ulp (measured: ±1/16384)',
  ).toBe(0);
  return metrics;
}

function actionByAccessibleName(page: Page, name: string): Locator {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const prefixedName = new RegExp(` — ${escapedName}$`);
  return page.getByRole('button', {name, exact: true})
    .or(page.getByRole('link', {name, exact: true}))
    .or(page.getByRole('button', {name: prefixedName}))
    .or(page.getByRole('link', {name: prefixedName}));
}

async function expectActions(page: Page, accessibleNames: string[]): Promise<void> {
  expect(accessibleNames.length, 'action selector contract must not be empty').toBeGreaterThan(0);
  for (const name of accessibleNames) {
    const action = actionByAccessibleName(page, name);
    await expect(action, `required action must render: ${name}`).toHaveCount(1);
    await expect(action, `required action must be visible: ${name}`).toBeVisible();
  }
}

async function expectApiOk(response: APIResponse, label: string): Promise<void> {
  expect(response.ok(), `${label}: ${response.status()} ${await response.text()}`).toBeTruthy();
}

function relativeIsoDay(offsetDays: number): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() + offsetDays);
  return value.toISOString().slice(0, 10);
}

// A screen may declare MORE than one surface (WorkOrderDetail carries three
// tables). Every match is measured; zero matches is a hard failure, because a
// deleted surface would otherwise measure nothing and pass silently.
// --- BEYAN EDİLEN SINIRLAR ------------------------------------------------
//
// SINIFLANDIRMA ÖLÇÜTÜ, ETİKETTEN ÖNCE. Bir sınır COST'tur: kapı çalışmaya
// devam eder, yanılırsa SIKI yönde yanılır ve yanıldığını SÖYLER. Bir sınır
// HOLE'dür: kapı YEŞİL kalırken güvence ettiği özellik YANLIŞTIR — yani gerçek
// bir taşma kapıya GÖRÜNMEZ. HOLE bir sınır notu değil, kapının blocker'ıdır.
// Aşağıda her satır bu ölçüte göre ayrıca gerekçelendirilmiştir.
//
// S1 — COST. Sağa KALICI taşan SÜSLEYİCİ bir animasyon kırmızı verir.
//   GEREKÇE: yanılma yönü sıkı. Kapı bir kusuru gizlemiyor, olmayan bir kusuru
//   bildiriyor ve sebebini `OVERFLOW_UNSETTLED ... KALICI` satırında yazıyor.
//   Güvence ettiği özellik ("ulaşılamaz içerik yok") yeşilken hâlâ doğru.
//   ÖLÇÜM: bugünkü üç kalıcı süsleme sınıfı — odak halkası, kaydırma degradesi,
//   sabit konumlu perde — taşma üretmiyor ve üçü de kapıda yeşil.
//
// S2 — COST. Pencerenin başında başlayıp TEK SEFERDE biten taşma 4000ms'e kadar
//   yeşil okunur.
//   GEREKÇE: bu, yeşilken özelliğin yanlış olabildiği tek nokta, ama sınır
//   ÖLÇÜLMÜŞ ve İKİ YAKASI DA TESTLİ — dolayısıyla kapıya görünmez değil,
//   BEYAN EDİLMİŞ ve sabitlenmiş. Sınır kayarsa `SINIR:` testlerinden biri
//   düşer. Kapının ADI da bunu söylüyor: "yerleşir", "hiç taşmaz" değil.
//   ÖLÇÜM: 500/1500/3000/3800ms yeşil, 4200/6000ms kırmızı → sınır tam pencere
//   boyu; ölçülen gerçek geçici (tıklama dalgası, 550ms) ile pay ~7.1x.
//
// S3 — COST. Onay penceresi (1000ms) KAPANDIKTAN sonra ulaşılamaz hale gelen
//   içerik görülmez.
//   GEREKÇE: her nokta-ölçümünde bulunan sınır. Pencere İÇİNDE gelen taşma
//   artık KIRMIZI (bu S5'in kapatılmasıyla değişti), pencere DIŞI ise ayrı bir
//   testle sabitlenmiş sınır. İki test birlikte sınırın YERİNİ pinliyor: sınır
//   kayarsa ikisinden biri düşer.
//
// S4 — COST. Yalnız BEYAN EDİLEN yüzeyler ölçülür, sayıları TAM eşleşir.
//   GEREKÇE: beyan edilmemiş yüzey ölçülmüyor, ama beyan edilmiş bir yüzeyin
//   KAYBI ya da SATIRSIZ çizilmesi ayrı testlerle kırmızıya düşüyor; yani
//   kapsam daralması sessiz kalamaz.
//
// S5 — KAPANDI. ARTIK SINIR DEĞİL.
//   ÖNCE COST DİYE İŞARETLEMİŞTİM VE BU YANLIŞTI. Kural "arka arkaya iki sıfır
//   görürsen yeşil" idi; salınan GERÇEK bir taşma iki sıfır örneğine denk gelip
//   kapıyı yeşile düşürebiliyordu. Yeşilken özellik YANLIŞ oluyordu, yani
//   tanım gereği HOLE.
//   SINIF TASARIMDAN ÖNCE ÖLÇÜLDÜ (4000ms, 50ms örnekleme, 64-65 örnek):
//     karusel      TT..TT..TT..    taşan 34/64  ≥2 sıfır koşusu 14  eski kural YEŞİL
//     akordiyon    TTT..TTT..TT... taşan 34/65  ≥2 sıfır koşusu 13  eski kural YEŞİL
//     sanal satır  TTT..TT..TTT..  taşan 37/65  ≥2 sıfır koşusu 14  eski kural YEŞİL
//     tembel görsel ........TTTT... taşan 56/64 ≥2 sıfır koşusu  1  eski kural YEŞİL
//   Dördünde de taşma 156px'ti: T anlarında içerik gerçekten ulaşılamazdı.
//   KAPATMA: kural örnek SAYISINI artırmakla değil, GİRDİ UZAYINI daraltmakla
//   kapatıldı — kabul edilen tek desen "TEK KESİNTİSİZ KOŞU + sıfır kuyruğu".
//   Salınan her şey birden çok koşu üretir ve sorulacak soru kalmaz. Dördü de
//   şimdi kırmızı ve her biri kendi testine sahip.
//
// S6 — COST (tohum belleği). Üretici DÜŞERSE tohum o koşu için zehirlenir.
//   GEREKÇE: bu bir sessiz yeşil değil, GÜRÜLTÜLÜ kırmızı — altı işçinin altısı
//   da düşüyor ve sebebi yazılıyor. Alternatifi ölçüldü ve reddedildi: kilit
//   bırakıldığında üretici 5 kez koştu, yani "tam bir kez" güvencesi hata
//   yolunda çöküyordu.
//
// S7 — COST (tohum belleği). `wx` atomikliği YEREL dosya sistemi varsayar.
//   GEREKÇE: varsayım bir not değil, HER KOŞUDA SINANAN bir kapı.
//   `tohum-yaris.spec.ts` CI'da da koşar; atomiklik yitirilirse "üretici TAM
//   BİR KEZ" iddiası orada kırmızı olur. Ölçüm ortamı `TOHUM_YARIS_ORTAM`
//   satırında kayıtlı. Yani sınır kapıya görünmez değil, kapının kendisi.
//
// S8 — COST. Kapının KENDİ testlerinden birinin (tıklama dalgası) kararsızlığı.
//   ÖNCE "86 ardışık geçiş" demiştim; bu kanıt DEĞİLDİ. 1/140 oranında 86 temiz
//   koşunun beklenen hata sayısı ~0.6, yani sıfır görmek olağan sonuçtur.
//   Sessizlik kanıt değil.
//
//   KAYNAK ÖLÇÜLDÜ, SONRA DARALTILDI. Kararsızlık kuralda değil KURULUMDAydı:
//   test dalgayı İKİ KEZ, birbirinden BAĞIMSIZ gözlüyordu — önce kendi anketi
//   (120 x 25ms bütçe), sonra kapının penceresi. Testin geçmesi için İKİSİNİN
//   DE yakalaması gerekiyordu, yani iki ayrı zamanlama riski. Ayrı anket ve
//   yardımcısı SİLİNDİ; çapa artık kapının KENDİ örnek dizisini okuyor
//   (`metrics.ornekler`). "Anketim kaçırdı ama kapı gördü" sınıfı artık
//   YAPISAL OLARAK kurulamaz — bu bir oran iddiası değil, bir ELEME.
//
//   KALAN TEK RİSK ÖLÇÜLDÜ. Aparat: GERÇEK TARAYICI, 500 sayfa yüklemesi +
//   gerçek tık + gerçek MUI dalgası, kapının protokolünün aynısı (4000ms
//   pencere, 50ms örnekleme). Eşik ÖLÇÜMDEN ÖNCE ilan edildi: sıfır hatada %95
//   üst sınır ~3/N olduğundan, orijinal 1/140 (%0.71) gözleminin altına inmek
//   için N=500 (%0.6) seçildi.
//     çapa kaçırması (hiç taşma görülmedi)      0/500
//     kapı kırmızısı (tek koşu + kuyruk yok)    0/500
//     ilk taşmaya kadar EN GEÇ                  97ms (pencere 4000ms)
//     tepe taşma en küçük / ortalama            402px / 406px
//   Yani pay 41x (97ms/4000ms) ve 402x (402px/1px eşik).
//
//   NEDEN HÂLÂ SINIR OLARAK DURUYOR: 0/500, oranın SIFIR olduğunu kanıtlamaz;
//   yalnız %95 üst sınırı %0.6'ya indirir. Sınıfın kendisi elenmiş olsa da
//   sayı hâlâ bir üst sınırdır, o yüzden "çözüldü" demiyorum.
//
//   KARŞILAŞTIRMA KOLU, ölçümün totoloji olmadığının kanıtı. Aynı protokol,
//   tek fark tıklama konumu (eski MERKEZ tıklaması), 200 deneme:
//     merkez: ilk taşma EN GEÇ 329ms, tepe 73px  (eşik 400ms'ye %18 pay)
//     sağ kenar: ilk taşma EN GEÇ  77ms, tepe 402px (%92 pay)
//   İki kol 4.3x ve 5.5x ayrışıyor. DÜRÜST SINIR: hiçbir kol hata ÜRETMEDİ,
//   yani bu ölçüm kolları ORAN üzerinden ayırmıyor — PAY üzerinden ayırıyor ve
//   140'ta 1 kaçırmayı yeniden ÜRETEMEDİ.
const TASMA_PENCERESI_MS = 4_000;
// ONAY PENCERESİ. Taşma İLK örnekte görülmese de kapı bu kadar süre izler;
// yoksa pencere içinde SONRADAN gelen bir taşma (geç yüklenen görsel,
// sanallaştırılmış satır) hiç görülmezdi. Süre bir tercihtir ve BEYAN EDİLİR:
// bu süreden SONRA ulaşılamaz hale gelen içerik bu kapının değil, ölçüm ANI
// sınırının (S3) konusudur. Sınırın iki yakası da test edilir; bkz. `SINIR:`.
const TASMA_ONAY_PENCERESI_MS = 1_000;
const TASMA_ORNEKLEME_MS = 50;

async function collectHorizontalOverflow(
  surface: Locator,
  label: string,
  expectedSurfaces: number,
): Promise<HorizontalOverflowMetrics> {
  const baslangic = Date.now();
  const dizi: number[] = [];
  let olcum = await collectHorizontalOverflowOnce(surface, label, expectedSurfaces);
  let enKotu = olcum;
  dizi.push(olcum.overflowingSurfaceCount);
  let gorulen = olcum.overflowingSurfaceCount > 0;

  for (;;) {
    const pencere = gorulen ? TASMA_PENCERESI_MS : TASMA_ONAY_PENCERESI_MS;
    if (Date.now() - baslangic >= pencere) break;
    await surface.page().waitForTimeout(TASMA_ORNEKLEME_MS);
    olcum = await collectHorizontalOverflowOnce(surface, label, expectedSurfaces);
    dizi.push(olcum.overflowingSurfaceCount);
    if (olcum.overflowingSurfaceCount > 0) {
      gorulen = true;
      if (enKotu.overflowingSurfaceCount === 0) enKotu = olcum;
    }
  }

  const gecen = Date.now() - baslangic;
  const ozet = `örnek=${dizi.length} dizi=${dizi.map(n => (n ? 'T' : '.')).join('')}`;
  if (!gorulen) return olcum;

  // TEK BİR KESİNTİSİZ KOŞU ve SIFIR KUYRUĞU.
  //
  // Kural önce "taşma İLK örnekte olmalı" diye yazılmıştı ve GERÇEK EKRANDA
  // DÜŞTÜ: kapı tıklamadan hemen sonra ölçmeye başlıyor, dalga ise birkaç örnek
  // sonra sağ kenarı aşıyor. Dizi `...TTTT......` çıkıyor — tek bir yükselip
  // inme, ama ilk örnekte değil. Doğru ölçüt "başta olması" değil, TEK SEFER
  // olması: bir kez yükselir, bir kez iner, BİR DAHA DÖNMEZ.
  const kosuSayisi = dizi.reduce(
    (toplam, deger, i) => toplam + (deger > 0 && (i === 0 || dizi[i - 1] === 0) ? 1 : 0),
    0,
  );
  const kuyruk = dizi.length - 1 - dizi.map(n => n > 0).lastIndexOf(true);
  const dinmis = kosuSayisi <= 1 && kuyruk >= 2;
  if (dinmis) {
    console.log(
      `OVERFLOW_SETTLED ${label} ${gecen}ms — TEK koşu, ${kuyruk} örnek sıfır kuyruğu (${ozet})`,
    );
    return {...olcum, ornekler: dizi};
  }

  const tur = dizi.every(n => n > 0) ? 'KALICI'
    : kosuSayisi > 1 ? `ARALIKLI (${kosuSayisi} ayrı koşu — bitip GERİ DÖNDÜ)`
      : 'BİTMEDİ (sıfır kuyruğu yok)';
  console.log(
    `OVERFLOW_UNSETTLED ${label} ${gecen}ms — ${tur} (${ozet}) ` +
    `worst=${JSON.stringify(enKotu.worstOffender)}`,
  );
  return {...enKotu, ornekler: dizi};
}

async function collectHorizontalOverflowOnce(
  surface: Locator,
  label: string,
  expectedSurfaces: number,
): Promise<HorizontalOverflowMetrics> {
  // Exact count. A screen that silently loses one of its declared surfaces
  // fails here rather than passing by measuring the survivors.
  await expect(
    surface,
    `${label} must declare exactly ${expectedSurfaces} data surface(s)`,
  ).toHaveCount(expectedSurfaces);
  const declaredCount = expectedSurfaces;
  const totals: HorizontalOverflowMetrics = {
    checkedSurfaceCount: 0,
    overflowingSurfaceCount: 0,
    contentRowCount: 0,
    emptySurfaceCount: 0,
    worstOffender: null,
    widest: null,
    ornekler: [],
  };

  for (let index = 0; index < declaredCount; index += 1) {
    const node = surface.nth(index);
    await expect(node, `${label} data surface must render`).toBeVisible();
    const metrics = await node.evaluate((root, rowSelector) => {
      const contentRowCount = root.querySelectorAll(rowSelector).length;
      // EXTENDED RULE. The original filter kept only `auto` and `scroll`, so a
      // container with `overflow-x: hidden` whose content is wider than its box
      // was invisible to the gate: the content is unreachable AND there is no
      // scrollbar to hint at it, which is strictly worse than scrolling.
      // `clip` behaves the same way and is included for the same reason.
      const SCROLLS = new Set(['auto', 'scroll']);
      const CLIPS = new Set(['hidden', 'clip']);
      const candidates = [root, ...Array.from(root.querySelectorAll<HTMLElement>('*'))]
        .filter((element): element is HTMLElement => element instanceof HTMLElement)
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          const style = window.getComputedStyle(element);
          return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
            style.visibility !== 'hidden';
        })
        .filter((element) => {
          const overflowX = window.getComputedStyle(element).overflowX;
          return element === root || SCROLLS.has(overflowX) || CLIPS.has(overflowX);
        });

      // HAM ÖLÇÜM. Hiçbir öğe sınıflandırılmaz; "bu içerik mi" sorusu
      // sorulmaz. Geçici olanı ayıran şey sınıfı değil DİNMESİDİR — gerekçe ve
      // ölçüm için bkz. `collectHorizontalOverflow`.
      const described = candidates.map(element => ({
        overflowX: window.getComputedStyle(element).overflowX,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      }));
      const overflowing = described
        .filter(entry => entry.scrollWidth > entry.clientWidth + 1)
        .sort((a, b) => (b.scrollWidth - b.clientWidth) - (a.scrollWidth - a.clientWidth));
      // WIDEST MARGIN IS REPORTED EVEN WHEN NOTHING OVERFLOWS.
      //
      // The gate used to surface widths only on failure, so a green run said
      // nothing about how much headroom was left. A screen sitting at +0px and
      // one sitting at -1px both printed "overflowing=0", and the difference
      // between them is the difference between a stable gate and a coin flip.
      // Reporting the margin every run makes the input measurable, which is a
      // precondition for trusting either colour.
      const widest = [...described]
        .sort((a, b) => (b.scrollWidth - b.clientWidth) - (a.scrollWidth - a.clientWidth))[0] ?? null;

      return {
        checkedSurfaceCount: candidates.length,
        overflowingSurfaceCount: overflowing.length,
        contentRowCount,
        worstOffender: overflowing[0] ?? null,
        widest,
      };
    }, CONTENT_ROW_SELECTOR);

    totals.checkedSurfaceCount += metrics.checkedSurfaceCount;
    totals.overflowingSurfaceCount += metrics.overflowingSurfaceCount;
    totals.contentRowCount += metrics.contentRowCount;
    if (metrics.contentRowCount === 0) totals.emptySurfaceCount += 1;
    if (metrics.widest && (
      !totals.widest ||
      metrics.widest.scrollWidth - metrics.widest.clientWidth >
        totals.widest.scrollWidth - totals.widest.clientWidth
    )) {
      totals.widest = metrics.widest;
    }
    if (metrics.worstOffender && (
      !totals.worstOffender ||
      metrics.worstOffender.scrollWidth - metrics.worstOffender.clientWidth >
        totals.worstOffender.scrollWidth - totals.worstOffender.clientWidth
    )) {
      totals.worstOffender = metrics.worstOffender;
    }
  }

  expect(totals.checkedSurfaceCount, `${label} must expose a measurable data surface`).toBeGreaterThan(0);
  return totals;
}

async function expectSurfaceSettlesWithNoOverflow(
  surface: Locator,
  label: string,
  expectedSurfaces: number,
): Promise<HorizontalOverflowMetrics> {
  const metrics = await collectHorizontalOverflow(surface, label, expectedSurfaces);
  const offender = metrics.worstOffender;
  const detail = offender
    ? ` worst: overflow-x:${offender.overflowX} scrollWidth=${offender.scrollWidth} clientWidth=${offender.clientWidth} (+${offender.scrollWidth - offender.clientWidth}px ${CLIPPING_VALUES.has(offender.overflowX) ? 'CLIPPED — unreachable' : 'scrolled'})`
    : '';
  // A surface with no rows cannot overflow, so an overflow-only gate reports it
  // clean. That is exactly how a screen gets called "measured" while its real
  // data was never on screen — and these screens measure almost clean when
  // empty (one went from +2px empty to +260px with a single row). Content is
  // therefore a precondition of the measurement, asserted PER SURFACE so one
  // populated table cannot cover for an empty sibling.
  expect(
    metrics.emptySurfaceCount,
    `${label}: every declared data surface must render at least one row or card (contentRows=${metrics.contentRowCount} across ${expectedSurfaces} surface(s)); an empty surface measures clean and proves nothing`,
  ).toBe(0);
  expect(
    metrics.contentRowCount,
    `${label} must measure real seeded content, not an empty-state shell`,
  ).toBeGreaterThan(0);
  expect(
    metrics.overflowingSurfaceCount,
    `${label} yüzeyi 390px'te YERLEŞTİKTEN SONRA yatayda ne kaydırmalı ne kırpmalı. "
    + "Taşma BİR KEZ olup bitebilir; GERİ DÖNERSE ya da SÜRERSE bu kırmızıdır.${detail}`,
  ).toBe(0);
  const widest = metrics.widest;
  const margin = widest ? widest.scrollWidth - widest.clientWidth : 0;
  console.log(
    `OVERFLOW_METRIC ${label} declaredSurfaces=${expectedSurfaces} elementsChecked=${metrics.checkedSurfaceCount} contentRows=${metrics.contentRowCount} emptySurfaces=${metrics.emptySurfaceCount} overflowing=${metrics.overflowingSurfaceCount} widestMargin=${margin}px (scrollWidth=${widest ? widest.scrollWidth : 0} clientWidth=${widest ? widest.clientWidth : 0} overflow-x:${widest ? widest.overflowX : 'n/a'})`,
  );
  return metrics;
}

const CLIPPING_VALUES = new Set(['hidden', 'clip']);

async function expectSeededMarker(
  marker: Locator,
  label: string,
  timeout = 15_000,
): Promise<void> {
  await expect(marker, `${label} must match exactly one seeded record`).toHaveCount(1, {timeout});
  await expect(marker, `${label} must be visible`).toBeVisible({timeout});
}

async function assertScreen(
  page: Page,
  screenName: string,
  marker: Locator,
  actions: string[],
  enforceTouchTargets: boolean,
): Promise<Metrics> {
  await expect(marker, `${screenName} marker must render`).toBeVisible({timeout: 15_000});
  await expectActions(page, actions);
  const root = page.locator('main');
  const metrics = await collectMetrics(root);
  console.log(
    `TOUCH_TARGET_METRIC ${screenName} controls=${metrics.controlCount} tooSmall=${metrics.tooSmallControlCount}` +
    ` dpr=${metrics.environment.devicePixelRatio} fonts=${metrics.environment.fontsStatus}` +
    ` scrollbar=${metrics.environment.scrollbarWidth}`,
  );
  if (enforceTouchTargets) {
    // DÖKÜM HER ZAMAN YAZILIR — yalnız kırmızıda değil.
    // Kırmızı bir koşuyu yeşil bir koşuyla karşılaştırmanın tek yolu iki
    // popülasyonun da elde olmasıdır; yalnız kırmızıyı yazmak, farkın nerede
    // olduğunu değil yalnızca "bir şey küçüktü"yü kaydeder.
    const populationArtifact = join(
      'test-results',
      `${screenName.replace(/[^a-z0-9_-]+/gi, '-')}-population.json`,
    );
    await writeFile(populationArtifact, JSON.stringify({
      screen: screenName,
      environment: metrics.environment,
      controlCount: metrics.controlCount,
      tooSmallControlCount: metrics.tooSmallControlCount,
      allControls: metrics.allControls,
    }, null, 2), 'utf8');
    console.log(
      `TOUCH_TARGET_POPULATION ${screenName} -> ${populationArtifact}` +
      ` platform=${metrics.environment.platform} bodyFont=${metrics.environment.bodyFontFamily}`,
    );
    if (metrics.tooSmallControlCount > 0) {
      const artifact = join(
        'test-results',
        `${screenName.replace(/[^a-z0-9_-]+/gi, '-')}-too-small-controls.json`,
      );
      // TAM POPÜLASYON yazılır, yalnız ihlal eden değil: kırmızı ile yeşil
      // koşuyu karşılaştırmak ancak iki listenin tamamı elde varken mümkün.
      await writeFile(artifact, JSON.stringify({
        screen: screenName,
        environment: metrics.environment,
        controlCount: metrics.controlCount,
        tooSmallControlCount: metrics.tooSmallControlCount,
        tooSmallControls: metrics.tooSmallControls,
        allControls: metrics.allControls,
      }, null, 2), 'utf8');
      console.log(`TOUCH_TARGET_DIAGNOSTIC ${artifact}`);
    }
    expect(metrics.tooSmallControlCount, `${screenName} must have no visible control below 44x44`).toBe(0);
  }
  return metrics;
}

// TOHUM GİRDİSİ SABİTTİR — `Date.now()` DEĞİL.
//
// Ölçülen kusur: bu dosyadaki üç tohum da adlarını `Date.now()` ile kuruyordu,
// yani genişliği belirleyen metin her koşuda BAŞKAydı. Taşma kapısının girdisi
// koşudan koşuya değişince kapı hiçbir yönde bir şey kanıtlamaz: bugünün
// +17px'i yarın +3px olur, kapı yeşile döner ve kusur yerinde kalır. Aynı
// koşuda ölçülen +7px ve +17px bunun doğrudan kanıtıydı.
//
// Sabit değer GÜVENLİ, çünkü e2e her koşuda TAZE bir SQLite alıyor
// (`e2e/serve.py` -> `tempfile.mkdtemp`), yani koşular arası çakışma yok.
// Değerler birbirinden FARKLI: barkod/seri numarası alanları tohumlar arasında
// çakışmasın diye.
const SEED_STAMP_TOUCH = '17550000000001';
const SEED_STAMP_SLICE_TWO = '17550000000002';
const SEED_STAMP_BATCH_THREE = '17550000000003';

// TEKRAR DENEMEDE YENİDEN TOHUMLAMA YOK — VE BELLEK SÜREÇTE DEĞİL, DOSYADA.
//
// Eski hâli modül düzeyinde bir değişkendi, yani YALNIZ aynı işçi sürecinde
// tutuyordu. Playwright tekrar denemeyi YENİ bir işçide koşar; orada değişken
// boştur ve tohum aynı veritabanına ikinci kez yazmaya kalkar. Bunun tek bir
// noktada değil ARDIŞIK benzersizlik kısıtlarında düştüğü ölçüldü; gerekçe ve
// ölçüm için bkz. `helpers.tohumHatirla`.
async function createSeed(): Promise<Seed> {
  return tohumHatirla('touch', createSeedUret);
}

async function createSliceTwoSeed(): Promise<SliceTwoSeed> {
  return tohumHatirla('slice-two', createSliceTwoSeedUret);
}

async function createBatchThreeSeed(): Promise<BatchThreeSeed> {
  return tohumHatirla('batch-three', createBatchThreeSeedUret);
}

async function createSeedUret(): Promise<Seed> {
  const admin = await adminApi();
  const stamp = SEED_STAMP_TOUCH;
  const productName = `Dokunma Parçası ${stamp}`;
  const attachmentName = `dokunma-${stamp}.txt`;
  const parcelName = `Dokunma Parseli ${stamp}`;
  const cropName = `Dokunma Buğdayı ${stamp}`;
  const seasonYear = new Date().getFullYear();
  const activityAreaLabel = '8,37 dekar';
  try {
    const headers = await admin.headers();
    const customerId = await createCustomer(admin, `Dokunma Müşterisi ${stamp}`);
    const productId = await createProduct(admin, {
      name: productName,
      productCode: `DT-${stamp}`,
      barcode: `868${String(stamp).slice(-10)}`,
    });

    const meResponse = await admin.api.get('/api/auth/me', {headers});
    expect(meResponse.ok(), await meResponse.text()).toBeTruthy();
    const me = await meResponse.json();
    const technicianId = me.user.id as number;
    const technicianName = String(me.user.display_name || me.user.username);

    const machineResponse = await admin.api.post('/api/machines', {
      headers,
      data: {
        customer_id: customerId,
        brand: 'Sungur',
        model: 'Dokunma Kapısı',
        serial_number: `DT-${stamp}`,
      },
    });
    expect(machineResponse.ok(), await machineResponse.text()).toBeTruthy();
    const machineId = (await machineResponse.json()).id as number;

    const workOrderResponse = await admin.api.post('/api/work-orders', {
      headers,
      data: {
        machine_id: machineId,
        customer_id: customerId,
        technician_id: technicianId,
        actual_hours: '1.75',
        estimated_hours: '2.00',
        labor_rate: '250.00',
        priority: 'NORMAL',
        status: 'OPEN',
        warranty_type: 'NONE',
      },
    });
    expect(workOrderResponse.ok(), await workOrderResponse.text()).toBeTruthy();
    const workOrderId = (await workOrderResponse.json()).id as number;

    const warehousesResponse = await admin.api.get('/api/warehouses', {headers});
    expect(warehousesResponse.ok(), await warehousesResponse.text()).toBeTruthy();
    const warehouseId = (await warehousesResponse.json())[0]?.id as number | undefined;
    expect(warehouseId, 'seed warehouse must exist').toBeTruthy();

    const partResponse = await admin.api.post(`/api/work-orders/${workOrderId}/parts`, {
      headers,
      data: {
        product_id: productId,
        warehouse_id: warehouseId,
        quantity: '2.00',
        unit_price: '17.50',
        discount: '2.00',
        tax_rate: '20.00',
      },
    });
    expect(partResponse.ok(), await partResponse.text()).toBeTruthy();

    const laborResponse = await admin.api.post(`/api/work-orders/${workOrderId}/labor-lines`, {
      headers,
      data: {
        technician_user_id: technicianId,
        hours: '1.25',
        hourly_rate: '220.00',
        note: 'Dokunma kapısı işçilik satırı',
      },
    });
    expect(laborResponse.ok(), await laborResponse.text()).toBeTruthy();

    const attachmentResponse = await admin.api.post(`/api/work-order-attachments/${workOrderId}`, {
      headers,
      multipart: {
        kind: 'photo',
        file: {
          name: attachmentName,
          mimeType: 'text/plain',
          buffer: Buffer.from('touch target gate attachment'),
        },
      },
    });
    expect(attachmentResponse.ok(), await attachmentResponse.text()).toBeTruthy();

    const farmResponse = await admin.api.post('/api/farms', {
      headers,
      data: {name: `Dokunma Çiftliği ${stamp}`, code: `DCF-${stamp}`},
    });
    expect(farmResponse.ok(), await farmResponse.text()).toBeTruthy();
    const farmId = (await farmResponse.json()).id as number;

    const parcelResponse = await admin.api.post('/api/farm-parcels', {
      headers,
      data: {
        farm_id: farmId,
        name: parcelName,
        code: `DCP-${stamp}`,
        area_decare: '12.50',
        city: 'Bursa',
        district: 'Nilüfer',
      },
    });
    expect(parcelResponse.ok(), await parcelResponse.text()).toBeTruthy();
    const parcelId = (await parcelResponse.json()).id as number;

    const seasonResponse = await admin.api.post('/api/crop-seasons', {
      headers,
      data: {
        parcel_id: parcelId,
        crop: cropName,
        season_year: seasonYear,
        planted_area_decare: '12.50',
        started_on: new Date().toISOString().slice(0, 10),
      },
    });
    expect(seasonResponse.ok(), await seasonResponse.text()).toBeTruthy();
    const season = await seasonResponse.json();
    const seasonId = season.id as number;
    const activateSeasonResponse = await admin.api.put(`/api/crop-seasons/${seasonId}`, {
      headers,
      data: {
        parcel_id: parcelId,
        crop: cropName,
        season_year: seasonYear,
        variety: null,
        planted_area_decare: '12.50',
        started_on: new Date().toISOString().slice(0, 10),
        ended_on: null,
        status: 'ACTIVE',
        notes: null,
        expected_updated_at: season.updated_at,
      },
    });
    expect(activateSeasonResponse.ok(), await activateSeasonResponse.text()).toBeTruthy();

    const activityResponse = await admin.api.post('/api/field-activities', {
      headers,
      data: {
        season_id: seasonId,
        activity_type: 'FERTILIZING',
        performed_at: new Date().toISOString(),
        applied_area_decare: '8.37',
      },
    });
    expect(activityResponse.ok(), await activityResponse.text()).toBeTruthy();
    const activityId = (await activityResponse.json()).id as number;

    const inputResponse = await admin.api.post(`/api/field-activities/${activityId}/inputs`, {
      headers,
      data: {
        input_name: 'Azot',
        quantity: '5.00',
        unit: 'KG',
        unit_cost: '40.00',
        dose: '1.50',
        dose_unit: 'KG/DEKAR',
      },
    });
    expect(inputResponse.ok(), await inputResponse.text()).toBeTruthy();

    const seeded = {
      workOrderId,
      parcelId,
      parcelName,
      cropName,
      seasonYear,
      activityAreaLabel,
      productName,
      technicianName,
      attachmentName,
    };
    return seeded;
  } finally {
    await admin.dispose();
  }
}

async function createSliceTwoSeedUret(): Promise<SliceTwoSeed> {
  const admin = await adminApi();
  const stamp = SEED_STAMP_SLICE_TWO;
  // EN KÖTÜ GERÇEKÇİ GİRDİ, ÇİVİLENDİ. Kartın genişliğini belirleyen şey müşteri
  // adıdır ve KIRPILMAYI belirleyen şey adın BOŞLUKSUZ olmasıdır: boşluklu ad
  // kendiliğinden alt satıra geçer, boşluksuz tek sözcük geçemez. Kullanıcılar
  // unvanı bitişik yazabiliyor, dolayısıyla bu girdi uydurma değil gerçekçi.
  // Ölçüm: düzeltmeden ÖNCE 390px'te +61px KIRPILMIŞ (ulaşılamaz).
  const customerName = `MobilDilimMüşterisiUnvanıSanayiVeTicaretLimitedŞirketi${stamp}`;
  try {
    const customerId = await createCustomer(admin, customerName);
    const productId = await createProduct(admin, {
      name: `Mobil Dilim Ürünü ${stamp}`,
      productCode: `MD-${stamp}`,
      barcode: `867${String(stamp).slice(-10)}`,
    });

    const saleResponse = await admin.api.post('/api/pos/sale', {
      headers: {
        ...(await admin.headers()),
        'Idempotency-Key': `mobile-slice-2-${stamp}`,
      },
      data: {
        customer_id: customerId,
        payment_type: 'credit',
        note: `Mobil dilim veresiye ${stamp}`,
        items: [{product_id: productId, quantity: '3.00', unit_price: '10.00'}],
      },
    });
    expect(saleResponse.ok(), await saleResponse.text()).toBeTruthy();
    const sale = await saleResponse.json();

    const machineResponse = await admin.api.post('/api/machines', {
      headers: await admin.headers(),
      data: {
        customer_id: customerId,
        brand: 'Sungur',
        model: 'Mobil Dilim',
        serial_number: `MD-${stamp}`,
      },
    });
    expect(machineResponse.ok(), await machineResponse.text()).toBeTruthy();
    const machineId = (await machineResponse.json()).id as number;

    const meResponse = await admin.api.get('/api/auth/me', {headers: await admin.headers()});
    expect(meResponse.ok(), await meResponse.text()).toBeTruthy();
    const technicianId = (await meResponse.json()).user.id as number;

    const workOrderResponse = await admin.api.post('/api/work-orders', {
      headers: await admin.headers(),
      data: {
        machine_id: machineId,
        customer_id: customerId,
        technician_id: technicianId,
        actual_hours: '2.00',
        estimated_hours: '2.00',
        labor_rate: '137.50',
        priority: 'NORMAL',
        status: 'OPEN',
        warranty_type: 'NONE',
      },
    });
    expect(workOrderResponse.ok(), await workOrderResponse.text()).toBeTruthy();
    const workOrder = await workOrderResponse.json();
    const workOrderId = workOrder.id as number;
    const workOrderNo = workOrder.work_order_no as string;
    expect(workOrderNo, 'seeded work order must expose its exact document number').toBeTruthy();

    for (const status of ['IN_PROGRESS', 'COMPLETED']) {
      const statusResponse = await admin.api.patch(`/api/work-orders/${workOrderId}/status`, {
        headers: await admin.headers(),
        data: {status},
      });
      expect(statusResponse.ok(), await statusResponse.text()).toBeTruthy();
    }

    const invoiceResponse = await admin.api.post('/api/invoices/generate', {
      headers: await admin.headers(),
      data: {work_order_id: workOrderId, notes: `Mobil dilim faturası ${stamp}`},
    });
    expect(invoiceResponse.ok(), await invoiceResponse.text()).toBeTruthy();
    const invoice = await invoiceResponse.json();
    expect(invoice.id, 'generated invoice must expose its id').toBeTruthy();

    // The aging response does not expose invoice_id. Fetching the generated
    // invoice by its returned id makes the join fail closed:
    // invoice.work_order_id -> exact work-order number -> service-fee row,
    // while the invoice grand total must also equal the receivable total.
    const issuedInvoiceResponse = await admin.api.get(`/api/invoices/${invoice.id}`, {
      headers: await admin.headers(),
    });
    expect(issuedInvoiceResponse.ok(), await issuedInvoiceResponse.text()).toBeTruthy();
    const issuedInvoice = await issuedInvoiceResponse.json();
    expect(issuedInvoice.id, 'fetched invoice id must match the generated invoice').toBe(invoice.id);
    expect(issuedInvoice.invoice_number, 'fetched invoice number must match the generated invoice')
      .toBe(invoice.invoice_number);
    expect(issuedInvoice.work_order_id, 'generated invoice must be bound to the seeded work order')
      .toBe(workOrderId);
    expect(issuedInvoice.status, 'generated invoice must be issued').toBe('ISSUED');

    const activityResponse = await admin.api.get('/api/activity-logs', {
      headers: await admin.headers(),
      params: {limit: 100, offset: 0},
    });
    expect(activityResponse.ok(), await activityResponse.text()).toBeTruthy();
    const activityPage = await activityResponse.json();
    const saleActivity = activityPage.items.find(
      (item: {action_type: string; resource_id: number}) =>
        item.action_type === 'pos.sale_created' && item.resource_id === sale.sale_id,
    );
    expect(saleActivity, 'seeded POS activity must exist').toBeTruthy();

    const agingResponse = await admin.api.get('/api/reports/receivables-aging', {
      headers: await admin.headers(),
    });
    expect(agingResponse.ok(), await agingResponse.text()).toBeTruthy();
    const agingReport = await agingResponse.json();
    const agingCustomer = agingReport.customers.find(
      (customer: {customer_id: number}) => customer.customer_id === customerId,
    );
    expect(agingCustomer, 'seeded receivable customer must exist').toBeTruthy();
    expect(agingCustomer.documents.length, 'seeded receivable must expose a document').toBeGreaterThan(0);
    const expectedInvoiceReceivableNo = `${workOrderNo}-R3`;
    const agingDocument = agingCustomer.documents.find((document: {
      document_no: string | null;
      document_type: string;
      remaining: number | string;
    }) =>
      document.document_type === 'service_fee' &&
      document.document_no === expectedInvoiceReceivableNo &&
      money(document.remaining) === money(issuedInvoice.totals.grand_total),
    );
    expect(
      agingDocument,
      `aging service-fee row must belong to generated invoice ${issuedInvoice.invoice_number}`,
    ).toBeTruthy();
    if (!agingDocument) {
      throw new Error('generated invoice receivable row was not found');
    }

    const seeded = {
      activitySummary: saleActivity.summary as string,
      agingDocumentNo: String(agingDocument.document_no || `#${agingDocument.id}`),
      agingRemaining: String(agingDocument.remaining),
      agingTotal: String(agingCustomer.total),
      customerId,
      customerName,
      invoiceId: invoice.id as number,
      invoiceItemDescription: String(invoice.items[0].description),
      invoiceNumber: invoice.invoice_number as string,
    };
    return seeded;
  } finally {
    await admin.dispose();
  }
}

async function createBatchThreeSeedUret(): Promise<BatchThreeSeed> {
  const admin = await adminApi();
  const stamp = SEED_STAMP_BATCH_THREE;
  const suffix = String(stamp).slice(-10);
  const productName = `Mobil Parti Ürünü ${stamp}`;
  const productBarcode = `866${suffix}`;
  const herdEarTag = `TR${suffix}`;
  const notificationTemplateName = `Mobil Parti Şablonu ${stamp}`;
  try {
    const customerResponse = await admin.api.post('/api/customers', {
      headers: await admin.headers(),
      data: {name: `Mobil Parti Müşterisi ${stamp}`, phone: '0532 111 22 33'},
    });
    await expectApiOk(customerResponse, 'batch-three customer seed');
    const customerId = (await customerResponse.json()).id as number;

    const productId = await createProduct(admin, {
      name: productName,
      productCode: `MP-${suffix}`,
      barcode: productBarcode,
    });
    const saleResponse = await admin.api.post('/api/pos/sale', {
      headers: {
        ...(await admin.headers()),
        'Idempotency-Key': `mobile-batch-sale-${stamp}`,
      },
      data: {
        customer_id: customerId,
        payment_type: 'credit',
        note: `Mobil parti veresiye ${stamp}`,
        items: [{product_id: productId, quantity: '1.00', unit_price: '10.00'}],
      },
    });
    await expectApiOk(saleResponse, 'batch-three credit sale seed');
    const saleId = (await saleResponse.json()).sale_id as number;
    const saleDetailResponse = await admin.api.get(`/api/orders/${saleId}`, {
      headers: await admin.headers(),
    });
    await expectApiOk(saleDetailResponse, 'batch-three credit sale detail seed');
    const paymentDocumentNo = (await saleDetailResponse.json()).document.document_no as string;
    expect(paymentDocumentNo, 'credit sale must expose a document number').toBeTruthy();

    // Tahsis defterinin GEÇMİŞ yüzeyi ancak gerçek bir tahsis kaydıyla dolar.
    // Veresiye satışa referanslı bir tahsilat, tahsis motoru açıkken
    // (e2e/serve.py) allocation satırı üretir.
    // Motor kapalıyken bu tahsilat allocation üretmez; tohum yalnız açık
    // yapılandırmada anlamlıdır.
    if (ALLOCATION_ENGINE_ENABLED) {
    const allocationPaymentResponse = await admin.api.post('/api/payments', {
      headers: {
        ...(await admin.headers()),
        'Idempotency-Key': `mobile-batch-allocation-${stamp}`,
      },
      data: {
        entity_type: 'customer',
        entity_id: customerId,
        amount: '4.00',
        payment_date: relativeIsoDay(0),
        payment_method: 'cash',
        note: `Mobil parti tahsilatı ${stamp}`,
        reference_type: 'order',
        reference_id: saleId,
      },
    });
    await expectApiOk(allocationPaymentResponse, 'batch-three allocation payment seed');
    }

    const animalResponse = await admin.api.post('/api/animals', {
      headers: await admin.headers(),
      data: {
        ear_tag: herdEarTag,
        name: `Mobil Parti İneği ${stamp}`,
        species: 'CATTLE',
        sex: 'FEMALE',
        birth_date: relativeIsoDay(-900),
      },
    });
    await expectApiOk(animalResponse, 'batch-three animal seed');
    const animalId = (await animalResponse.json()).id as number;
    const vaccinationResponse = await admin.api.post('/api/animal-vaccinations', {
      headers: await admin.headers(),
      data: {
        animal_id: animalId,
        vaccine: `Mobil Parti Aşısı ${stamp}`,
        applied_on: relativeIsoDay(-60),
        next_due_on: relativeIsoDay(-1),
      },
    });
    await expectApiOk(vaccinationResponse, 'batch-three vaccination seed');

    const templateBody = `Mobil parti ${stamp}: Sayın {musteri_adi}, {makine_adi} servis randevunuz {randevu_tarihi}. {firma_adi}`;
    const templateListResponse = await admin.api.get('/api/notifications/templates', {
      headers: await admin.headers(),
    });
    await expectApiOk(templateListResponse, 'batch-three notification template lookup');
    const existingTemplate = (await templateListResponse.json() as Array<{
      id: number; code: string; channel: string;
    }>).find(row => row.code === 'service.reminder' && row.channel === 'SMS');
    const templateResponse = existingTemplate
      ? await admin.api.patch(`/api/notifications/templates/${existingTemplate.id}`, {
        headers: await admin.headers(),
        data: {
          name: notificationTemplateName,
          body: templateBody,
          message_class: 'SERVICE_TRANSACTIONAL',
        },
      })
      : await admin.api.post('/api/notifications/templates', {
        headers: await admin.headers(),
        data: {
          code: 'service.reminder',
          channel: 'SMS',
          name: notificationTemplateName,
          body: templateBody,
          message_class: 'SERVICE_TRANSACTIONAL',
        },
      });
    await expectApiOk(templateResponse, 'batch-three notification template seed');
    const notificationTemplateId = (await templateResponse.json()).id as number;
    const approveTemplateResponse = await admin.api.post(
      `/api/notifications/templates/${notificationTemplateId}/approve`,
      {headers: await admin.headers()},
    );
    await expectApiOk(approveTemplateResponse, 'batch-three notification template approval seed');

    const machineResponse = await admin.api.post('/api/machines', {
      headers: await admin.headers(),
      data: {
        customer_id: customerId,
        brand: 'Sungur',
        model: `Mobil Parti ${stamp}`,
        serial_number: `MP-${stamp}`,
      },
    });
    await expectApiOk(machineResponse, 'batch-three notification machine seed');
    const machineId = (await machineResponse.json()).id as number;
    const meResponse = await admin.api.get('/api/auth/me', {headers: await admin.headers()});
    await expectApiOk(meResponse, 'batch-three current user seed');
    const technicianId = (await meResponse.json()).user.id as number;
    const workOrderResponse = await admin.api.post('/api/work-orders', {
      headers: await admin.headers(),
      data: {
        machine_id: machineId,
        customer_id: customerId,
        technician_id: technicianId,
        status: 'SCHEDULED',
        scheduled_date: `${relativeIsoDay(7)}T09:30:00+00:00`,
        complaint: `Mobil parti bildirim ${stamp}`,
      },
    });
    await expectApiOk(workOrderResponse, 'batch-three notification work order seed');
    const workOrderId = (await workOrderResponse.json()).id as number;
    const consentResponse = await admin.api.put('/api/notifications/consents', {
      headers: await admin.headers(),
      data: {
        party_type: 'CUSTOMER',
        party_id: customerId,
        channel: 'SMS',
        granted: true,
        source: 'FORM',
        recipient: '0532 111 22 33',
      },
    });
    await expectApiOk(consentResponse, 'batch-three notification consent seed');
    // Şube transferi GEÇMİŞİ yüzeyi gerçek bir transfer olmadan boş kalır ve
    // BOŞ bir yüzey taşmadan geçer — yani hiçbir şey kanıtlamaz. Bu yüzden iki
    // depo arasında gerçek bir transfer tohumlanır.
    const transferWarehousesResponse = await admin.api.get('/api/warehouses', {
      headers: await admin.headers(),
    });
    await expectApiOk(transferWarehousesResponse, 'batch-three transfer warehouse lookup');
    const transferWarehouses = (await transferWarehousesResponse.json()) as Array<{id: number}>;
    expect(
      transferWarehouses.length,
      'stock transfer seed needs two warehouses in the tenant',
    ).toBeGreaterThan(1);
    const transferNote = `Mobil parti transferi ${stamp}`;
    // AYRI bir ürün: parti ürününü taşımak ikinci depoya da stok koyar ve
    // "tam olarak bir geçerli kaynak depo" iddiasını bozar. Geçmiş tablosu
    // ürüne göre filtrelenmediği için ayrı ürün de yüzeyi doldurur.
    const transferProductId = await createProduct(admin, {
      name: `Mobil Parti Transfer Ürünü ${stamp}`,
      productCode: `MPT-${suffix}`,
      barcode: `867${suffix}`,
    });
    const transferResponse = await admin.api.post('/api/warehouses/transfers', {
      headers: await admin.headers(),
      data: {
        source_warehouse_id: transferWarehouses[0].id,
        target_warehouse_id: transferWarehouses[1].id,
        transfer_date: relativeIsoDay(0),
        note: transferNote,
        items: [{product_id: transferProductId, quantity: '1.00'}],
      },
    });
    await expectApiOk(transferResponse, 'batch-three stock transfer seed');

    const notificationResponse = await admin.api.post(
      `/api/notifications/work-orders/${workOrderId}/reminder`,
      {headers: await admin.headers(), data: {channel: 'SMS'}},
    );
    await expectApiOk(notificationResponse, 'batch-three notification outbox seed');
    const notificationId = (await notificationResponse.json()).id as number;

    const seeded = {
      herdEarTag,
      transferNote,
      saleOrderId: saleId,
      notificationId,
      notificationTemplateId,
      notificationTemplateName,
      paymentDocumentNo,
      productBarcode,
      productName,
    };
    return seeded;
  } finally {
    await admin.dispose();
  }
}

test.describe.serial('touch target and responsive action gate', () => {
  let seed: Seed;

  test.beforeAll(async () => {
    seed = await createSeed();
  });

  for (const viewport of VIEWPORTS) {
    test(`${viewport.name}: four screens render and preserve their actions`, async ({page}) => {
      test.setTimeout(120_000);
      await page.setViewportSize({width: viewport.width, height: viewport.height});
      await login(page);

      await page.goto(`/is-emirleri/${seed.workOrderId}`);
      const workOrderActions = [
        `${seed.attachmentName} — İndir`,
        `${seed.attachmentName} — Sil`,
        `${seed.technicianName} — Düzenle`,
        `${seed.technicianName} — Onayla`,
        `${seed.technicianName} — İptal`,
        `${seed.productName} — Çıkış`,
        `${seed.productName} — İade`,
        `${seed.productName} — Düzenle`,
        `${seed.productName} — Sil`,
      ];
      await assertScreen(
        page,
        `${viewport.name}/WorkOrderDetail`,
        page.getByRole('heading', {name: 'Kullanılan Parçalar'}),
        workOrderActions,
        viewport.enforceTouchTargets,
      );
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('work-order-detail-data-surface'),
          'WorkOrderDetail',
          DECLARED_DATA_SURFACES['work-order-detail-data-surface'],
        );
      }

      await page.goto(`/tarla/parseller/${seed.parcelId}`);
      await assertScreen(
        page,
        `${viewport.name}/ParcelDetail`,
        page.getByRole('heading', {name: seed.parcelName}),
        ['Parsel listesi'],
        viewport.enforceTouchTargets,
      );
      const seededActivity = page.getByText('Gübreleme', {exact: true});
      await expect(seededActivity, 'seeded ParcelDetail activity must render').toHaveCount(1);
      await expect(seededActivity.locator('..')).toContainText(seed.cropName);
      await expect(seededActivity.locator('..')).toContainText(seed.activityAreaLabel);
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('parcel-detail-data-surface'),
          'ParcelDetail',
          DECLARED_DATA_SURFACES['parcel-detail-data-surface'],
        );
      }

      await page.goto('/tarla/faaliyetler');
      await assertScreen(
        page,
        `${viewport.name}/FieldActivitiesList`,
        page.getByRole('heading', {name: 'Faaliyetler & Girdiler'}),
        ['Gübreleme — Girdiler'],
        viewport.enforceTouchTargets,
      );
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('field-activities-data-surface'),
          'FieldActivitiesList',
          DECLARED_DATA_SURFACES['field-activities-data-surface'],
        );
      }

      await actionByAccessibleName(page, 'Gübreleme — Girdiler').click();
      const detailDialog = page.getByRole('dialog');
      await expect(detailDialog.getByText('Faaliyet toplamı')).toBeVisible({timeout: 15_000});
      await expect(detailDialog.getByTestId('activity-total-row')).toContainText('200,00');
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          detailDialog.getByTestId('field-activity-detail-data-surface'),
          'FieldActivitiesDetailDialog',
          DECLARED_DATA_SURFACES['field-activity-detail-data-surface'],
        );
      }
      const dialogMetrics = await collectMetrics(detailDialog);
      console.log(
        `TOUCH_TARGET_METRIC ${viewport.name}/FieldActivitiesDetailDialog controls=${dialogMetrics.controlCount} tooSmall=${dialogMetrics.tooSmallControlCount}`,
      );
      if (viewport.enforceTouchTargets) {
        expect(dialogMetrics.tooSmallControlCount, 'detail dialog controls must be at least 44x44').toBe(0);
      }
      await detailDialog.getByRole('button', {name: 'Kapat'}).click();
      await expect(detailDialog).toBeHidden();

      await page.goto('/tarla');
      await assertScreen(
        page,
        `${viewport.name}/FarmDashboard`,
        page.getByRole('heading', {name: 'Tarla Panosu'}),
        ['Tüm görevler'],
        viewport.enforceTouchTargets,
      );
      const seededSeason = page.getByText(`${seed.cropName} · ${seed.seasonYear}`, {exact: true});
      await expect(seededSeason, 'seeded FarmDashboard active season must render').toHaveCount(1);
      await expect(seededSeason).toBeVisible();
      await expect(seededSeason.locator('..')).toContainText(seed.parcelName);
      await expect(seededSeason.locator('..')).toContainText('Sürüyor');
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('farm-dashboard-data-surface'),
          'FarmDashboard',
          DECLARED_DATA_SURFACES['farm-dashboard-data-surface'],
        );
      }
    });
  }

  test('metric fails closed and the mutation is caught in CI', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/tarla');
    await expect(page.getByRole('heading', {name: 'Tarla Panosu'})).toBeVisible({timeout: 15_000});
    const root = page.locator('main');
    const baseline = await collectMetrics(root);
    expect(baseline.tooSmallControlCount).toBe(0);

    const hiddenMetric = await root.evaluate((node) => {
      const hidden = document.createElement('button');
      hidden.dataset.touchMetricProbe = 'hidden';
      hidden.style.display = 'none';
      hidden.style.width = '10px';
      hidden.style.height = '10px';
      node.appendChild(hidden);
      return true;
    });
    expect(hiddenMetric).toBe(true);
    const afterHidden = await collectMetrics(root);
    expect(afterHidden.controlCount, 'hidden controls are excluded').toBe(baseline.controlCount);
    expect(afterHidden.tooSmallControlCount, 'hidden controls cannot make the gate red').toBe(0);

    const target = page.getByRole('link', {name: 'Tüm görevler'});
    await expect(target, 'mutation target selector must match exactly one visible control').toHaveCount(1);
    await expect(target).toBeVisible();
    await target.evaluate((element) => {
      element.setAttribute('disabled', '');
      element.style.setProperty('width', '20px', 'important');
      element.style.setProperty('min-width', '20px', 'important');
      element.style.setProperty('height', '20px', 'important');
      element.style.setProperty('min-height', '20px', 'important');
      element.style.setProperty('padding', '0', 'important');
    });
    const mutated = await collectMetrics(root);
    expect(mutated.disabledControlCount, 'visible disabled controls stay in the metric').toBeGreaterThan(
      baseline.disabledControlCount,
    );
    expect(mutated.tooSmallControlCount, 'shrinking a visible control must turn the metric red').toBe(1);
    console.log(
      `MUTATION_RED FarmDashboard tooSmallControlCount: ${baseline.tooSmallControlCount} -> ${mutated.tooSmallControlCount}`,
    );
  });
});

test.describe.serial('receivables, invoice, and activity mobile slice gate', () => {
  let seed: SliceTwoSeed;

  test.beforeAll(async () => {
    seed = await createSliceTwoSeed();
  });

  for (const viewport of VIEWPORTS) {
    test(`${viewport.name}: seeded data and required actions survive responsive rendering`, async ({page}) => {
      test.setTimeout(120_000);
      await page.setViewportSize({width: viewport.width, height: viewport.height});
      await login(page);

      await page.goto('/raporlar/alacak-yaslandirma');
      const agingCustomer = page.getByText(seed.customerName, {exact: true});
      await expectSeededMarker(agingCustomer, 'seeded ReceivablesAging customer');
      await expect(agingCustomer.locator('..')).toContainText(money(seed.agingTotal));
      await agingCustomer.click();
      await assertScreen(
        page,
        `${viewport.name}/ReceivablesAging`,
        page.getByRole('heading', {name: 'Alacak Yaşlandırma'}),
        ['Müşteri kartını aç'],
        viewport.enforceTouchTargets,
      );
      const agingDocument = page.getByText(seed.agingDocumentNo, {exact: true});
      await expectSeededMarker(agingDocument, 'seeded ReceivablesAging document');
      await expect(agingDocument.locator('..')).toContainText(`Kalan: ${money(seed.agingRemaining)}`);
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('receivables-aging-data-surface'),
          'ReceivablesAging',
          DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
        );
      }

      await page.goto(`/faturalar/${seed.invoiceId}`);
      await assertScreen(
        page,
        `${viewport.name}/InvoiceDetail`,
        page.getByRole('heading', {name: seed.invoiceNumber}),
        ['Geri', 'e-Fatura Gönder'],
        viewport.enforceTouchTargets,
      );
      await expectSeededMarker(
        page.getByText(seed.customerName, {exact: true}),
        'seeded InvoiceDetail customer',
      );
      await expectSeededMarker(
        page.getByText(seed.invoiceItemDescription, {exact: true}),
        'seeded InvoiceDetail line item',
      );
      await expectSeededMarker(
        page.getByText('ISSUED', {exact: true}),
        'seeded InvoiceDetail ISSUED status',
      );
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('invoice-detail-data-surface'),
          'InvoiceDetail',
          DECLARED_DATA_SURFACES['invoice-detail-data-surface'],
        );
      }

      await page.goto('/aktivite');
      await expectSeededMarker(
        page.getByText(seed.activitySummary, {exact: true}),
        'seeded ActivityLog event',
      );
      await assertScreen(
        page,
        `${viewport.name}/ActivityLog`,
        page.getByRole('heading', {name: 'Aktivite'}),
        [`${seed.activitySummary} — Arşivle`],
        viewport.enforceTouchTargets,
      );
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('activity-log-data-surface'),
          'ActivityLog',
          DECLARED_DATA_SURFACES['activity-log-data-surface'],
        );
      }
    });
  }

  test('removing one declared screen seed makes the shared gate red', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await page.route('**/api/reports/receivables-aging**', async route => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({
        response,
        json: {
          ...body,
          customers: body.customers.filter(
            (customer: {customer_id: number}) => customer.customer_id !== seed.customerId,
          ),
        },
      });
    });
    await login(page);
    await page.goto('/raporlar/alacak-yaslandirma');
    await expect(page.getByRole('heading', {name: 'Alacak Yaşlandırma'})).toBeVisible({timeout: 15_000});

    const removedMarker = page.getByText(seed.customerName, {exact: true});
    let gateRejectedMissingSeed = false;
    try {
      await expectSeededMarker(removedMarker, 'seeded ReceivablesAging customer', 250);
    } catch {
      gateRejectedMissingSeed = true;
    }
    expect(gateRejectedMissingSeed, 'missing seeded data must turn the shared screen gate red').toBe(true);
    expect(await removedMarker.count()).toBe(0);
    console.log('MUTATION_RED ReceivablesAging seededCustomerCount: 1 -> 0');
  });

  test('restoring the old wide table branch makes the overflow gate red', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/raporlar/alacak-yaslandirma');
    await expectSeededMarker(
      page.getByText(seed.customerName, {exact: true}),
      'seeded ReceivablesAging customer',
    );

    const surface = page.getByTestId('receivables-aging-data-surface');
    const baseline = await expectSurfaceSettlesWithNoOverflow(
      surface,
      'ReceivablesAging mutation baseline',
      1,
    );

    await surface.evaluate((node) => {
      const oldTableContainer = document.createElement('div');
      oldTableContainer.dataset.overflowMutation = 'old-wide-table-branch';
      oldTableContainer.style.width = '100%';
      oldTableContainer.style.overflowX = 'auto';
      const oldTable = document.createElement('table');
      oldTable.style.width = '900px';
      oldTable.style.minWidth = '900px';
      oldTable.innerHTML = '<tbody><tr><td>old wide table branch</td></tr></tbody>';
      oldTableContainer.appendChild(oldTable);
      node.replaceChildren(oldTableContainer);
    });

    let gateRejectedOldTable = false;
    try {
      await expectSurfaceSettlesWithNoOverflow(surface, 'ReceivablesAging old-table mutation', 1);
    } catch {
      gateRejectedOldTable = true;
    }
    expect(
      gateRejectedOldTable,
      'restoring a 900px table in the mobile data surface must turn the shared gate red',
    ).toBe(true);
    const mutated = await collectHorizontalOverflow(surface, 'ReceivablesAging mutation evidence', 1);
    expect(mutated.overflowingSurfaceCount).toBeGreaterThan(0);
    console.log(
      `MUTATION_RED ReceivablesAging horizontalOverflowCount: ${baseline.overflowingSurfaceCount} -> ${mutated.overflowingSurfaceCount}`,
    );
  });
});

// ---------------------------------------------------------------------------
// Mutations for the EXTENDED rule and for the declared-surface contract.
//
// FarmDashboard is used because it declares exactly one surface, so removing
// that surface really does take the screen's coverage to zero — which is the
// failure this block exists to prove is caught.
// ---------------------------------------------------------------------------
test.describe.serial('extended overflow rule mutations', () => {
  let seed: Seed;

  test.beforeAll(async () => {
    seed = await createSeed();
  });

  async function gateIsRed(surface: Locator, label: string, expectedSurfaces: number): Promise<boolean> {
    try {
      await expectSurfaceSettlesWithNoOverflow(surface, label, expectedSurfaces);
      return false;
    } catch {
      return true;
    }
  }

  test('a restored wide table turns the gate red, whether it scrolls or clips', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/tarla');
    await expect(page.getByText(`${seed.cropName} · ${seed.seasonYear}`, {exact: true})).toBeVisible({timeout: 15_000});

    const surface = page.getByTestId('farm-dashboard-data-surface');
    const baseline = await expectSurfaceSettlesWithNoOverflow(surface, 'FarmDashboard mutation baseline', 1);
    expect(baseline.overflowingSurfaceCount, 'baseline must be clean before mutating').toBe(0);

    // (a) The pre-fix shape: the 654px season table inside a scrolling container.
    await surface.evaluate((node) => {
      const container = document.createElement('div');
      container.dataset.overflowMutation = 'restored-wide-season-table';
      container.style.width = '100%';
      container.style.overflowX = 'auto';
      const table = document.createElement('table');
      table.style.width = '654px';
      table.style.minWidth = '654px';
      table.innerHTML = '<tbody><tr><td>restored wide season table</td></tr></tbody>';
      container.appendChild(table);
      node.replaceChildren(container);
    });
    expect(
      await gateIsRed(surface, 'FarmDashboard scrolling mutation', 1),
      'restoring the wide season table must turn the gate red',
    ).toBe(true);
    const scrolled = await collectHorizontalOverflow(surface, 'FarmDashboard scrolling evidence', 1);
    console.log(
      `MUTATION_RED FarmDashboard/scroll ${baseline.overflowingSurfaceCount} -> ${scrolled.overflowingSurfaceCount} worst=${JSON.stringify(scrolled.worstOffender)}`,
    );

    // (b) The shape the ORIGINAL rule could not see: same content, clipped.
    // 397px inside a 364px `overflow-x: hidden` box — 33px unreachable, no
    // scrollbar. Under the old auto/scroll-only filter this measured clean.
    await surface.evaluate((node) => {
      const clip = document.createElement('div');
      clip.dataset.overflowMutation = 'clipped-wide-table';
      clip.style.width = '364px';
      clip.style.overflowX = 'hidden';
      const wide = document.createElement('div');
      wide.style.width = '397px';
      wide.style.height = '20px';
      clip.appendChild(wide);
      node.replaceChildren(clip);
    });
    expect(
      await gateIsRed(surface, 'FarmDashboard clipping mutation', 1),
      'a 397px table clipped inside a 364px container must turn the gate red',
    ).toBe(true);
    const clipped = await collectHorizontalOverflow(surface, 'FarmDashboard clipping evidence', 1);
    expect(clipped.worstOffender?.overflowX, 'the caught offender must be the clipped one').toBe('hidden');
    expect(clipped.worstOffender?.scrollWidth).toBe(397);
    expect(clipped.worstOffender?.clientWidth).toBe(364);
    console.log(
      `MUTATION_RED FarmDashboard/clip ${baseline.overflowingSurfaceCount} -> ${clipped.overflowingSurfaceCount} worst=${JSON.stringify(clipped.worstOffender)}`,
    );
  });

  test('deleting ONE of WorkOrderDetail three surfaces turns the gate red', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto(`/is-emirleri/${seed.workOrderId}`);
    await expect(page.getByRole('heading', {name: 'Kullanılan Parçalar'})).toBeVisible({timeout: 15_000});

    const surfaces = page.getByTestId('work-order-detail-data-surface');
    const expected = DECLARED_DATA_SURFACES['work-order-detail-data-surface'];
    await expectSurfaceSettlesWithNoOverflow(surfaces, 'WorkOrderDetail multi-surface baseline', expected);

    // Drop exactly ONE of the three. The other two still render and still
    // measure clean, so a ">= 1" rule would pass here — that is precisely the
    // half-open trap this exact-count assertion closes.
    await surfaces.first().evaluate((node) => {
      node.removeAttribute('data-testid');
    });
    await expect(surfaces, 'one declared surface must really be gone').toHaveCount(expected - 1);

    expect(
      await gateIsRed(surfaces, 'WorkOrderDetail one-surface-deleted mutation', expected),
      'losing one of three declared surfaces must fail the gate, not pass on the survivors',
    ).toBe(true);
    console.log(
      `MUTATION_RED WorkOrderDetail/one-surface-deleted declaredSurfaces: ${expected} -> ${expected - 1}`,
    );
  });

  test('a declared surface that renders no rows turns the gate red', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/tarla');
    await expect(page.getByText(`${seed.cropName} · ${seed.seasonYear}`, {exact: true})).toBeVisible({timeout: 15_000});

    const surface = page.getByTestId('farm-dashboard-data-surface');
    const baseline = await expectSurfaceSettlesWithNoOverflow(surface, 'FarmDashboard empty-surface baseline', 1);
    expect(baseline.contentRowCount, 'baseline surface must carry seeded rows').toBeGreaterThan(0);

    // Strip the ROWS but leave the declared surface and an empty-state shell
    // exactly where they were. Nothing overflows a surface with no content, so
    // an overflow-only gate reports this as clean — which is how a screen can
    // be called measured while its real data was never on screen.
    await surface.evaluate((node) => {
      const empty = document.createElement('div');
      empty.className = 'MuiCard-root';
      empty.dataset.overflowMutation = 'emptied-surface';
      empty.textContent = 'Henüz kayıt yok';
      node.replaceChildren(empty);
    });
    await expect(surface, 'the declared surface itself must still be present').toHaveCount(1);

    const emptied = await collectHorizontalOverflow(surface, 'FarmDashboard emptied evidence', 1);
    expect(emptied.overflowingSurfaceCount, 'an empty surface cannot overflow — that is the trap').toBe(0);

    expect(
      await gateIsRed(surface, 'FarmDashboard empty-surface mutation', 1),
      'a declared surface rendering zero rows must fail the gate, not pass for lack of content',
    ).toBe(true);
    console.log(
      `MUTATION_RED FarmDashboard/emptied-surface contentRows: ${baseline.contentRowCount} -> ${emptied.contentRowCount}`,
    );
  });

  test('deleting a screen declared surface turns the gate red instead of measuring nothing', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/tarla');
    await expect(page.getByText(`${seed.cropName} · ${seed.seasonYear}`, {exact: true})).toBeVisible({timeout: 15_000});

    const surface = page.getByTestId('farm-dashboard-data-surface');
    await expectSurfaceSettlesWithNoOverflow(surface, 'FarmDashboard surface-removal baseline', 1);

    // Delete the declaration exactly as a careless refactor would: the data is
    // still on screen, only the gate's handle on it is gone.
    await surface.evaluate((node) => {
      node.removeAttribute('data-testid');
    });
    await expect(surface, 'the declared surface must really be gone').toHaveCount(0);

    expect(
      await gateIsRed(surface, 'FarmDashboard surface-removal mutation', 1),
      'a screen whose declared surface was deleted must fail the gate, not pass by measuring nothing',
    ).toBe(true);
    console.log('MUTATION_RED FarmDashboard/surface-deleted declaredSurfaces: 1 -> 0');
  });
});

test.describe('yerleşme kapsamı', () => {
  // DARALTMA İKİ YÖNDE de kanıtlanır. Tek yön yetmez: yalnız "ilgisiz animasyon
  // kırmızı yapmıyor"u kanıtlamak, kapının ARTIK HİÇ ateşlemediği bir daraltmayı
  // da yeşil gösterir. İkinci test, ateşleyebildiğini kanıtlar.
  //
  // İkisi de KAPININ KENDİ yolundan geçer (`collectMetrics`), yüklemi ayrıca
  // taklit etmez — taklit, kapı daraldığında sessizce ayrışırdı.
  const injectAnimation = async (
    page: Page,
    mode: 'unrelated' | 'ancestor' | 'custom-property' | 'inert-paint',
  ) => {
    await page.evaluate((kind) => {
      const style = document.createElement('style');
      style.textContent =
        '@keyframes gateProbeSpin {from{transform:rotate(0deg)}to{transform:rotate(360deg)}}' +
        '@keyframes gateProbeSlide {from{transform:translateY(0)}to{transform:translateY(6px)}}' +
        '@keyframes gateProbeFade {from{opacity:1}to{opacity:.4}}';
      document.head.appendChild(style);
      const main = document.querySelector('main')!;
      if (kind === 'unrelated') {
        // İÇİNDE HİÇ KONTROL YOK ve kalıcı (infinite): eski sürüm bunu 5 sn
        // bekleyip kırmızıya çevirirdi.
        const box = document.createElement('div');
        box.textContent = 'ilgisiz alt ağaç';
        box.style.animation = 'gateProbeSpin 600s infinite linear';
        main.appendChild(box);
      } else if (kind === 'custom-property') {
        // ŞEKİL A (runtime ölçtü, ESKİ sürümde KAÇIYORDU): animasyon bir ÖZEL
        // DEĞİŞKENİ oynatıyor, üst öğe onu `transform` içinde kullanıyor.
        // Geometri gerçekten oynar; izin listesinde `--shift` olmadığı için
        // eski yüklem bunu ilgisiz sayıyordu.
        const control = main.querySelector<HTMLElement>(
          'button,[role="button"],[role="link"],a[href],input,select,textarea');
        if (!control || !control.parentElement) {
          throw new Error('probe precondition: no measured control with a parent inside <main>');
        }
        const host = control.parentElement as HTMLElement;
        const varStyle = document.createElement('style');
        varStyle.textContent =
          '@property --gateProbeShift {syntax:"<length>";inherits:false;initial-value:0px}' +
          '@keyframes gateProbeVar {from{--gateProbeShift:0px}to{--gateProbeShift:10px}}';
        document.head.appendChild(varStyle);
        host.style.transform = 'translateY(var(--gateProbeShift))';
        host.style.animation = 'gateProbeVar 600s infinite alternate';
      } else if (kind === 'inert-paint') {
        // MUAFİYET TARAFI: yalnız boya oynatan kalıcı animasyon, üstelik
        // ölçülen kontrolün ATASINDA. Tersine çevirme bunu KIRMIZI yapsaydı,
        // bir önceki turda kaldırılan yanlış kırmızı geri gelmiş olurdu.
        const control = main.querySelector<HTMLElement>(
          'button,[role="button"],[role="link"],a[href],input,select,textarea');
        if (!control || !control.parentElement) {
          throw new Error('probe precondition: no measured control with a parent inside <main>');
        }
        (control.parentElement as HTMLElement).style.animation =
          'gateProbeFade 600s infinite alternate';
      } else {
        // Ölçülen bir kontrolün ATASI, üstelik geometri oynatıyor ve kalıcı.
        const control = main.querySelector<HTMLElement>(
          'button,[role="button"],[role="link"],a[href],input,select,textarea');
        if (!control || !control.parentElement) {
          throw new Error('probe precondition: no measured control with a parent inside <main>');
        }
        (control.parentElement as HTMLElement).style.animation =
          'gateProbeSlide 600s infinite alternate';
      }
    }, mode);
    await page.waitForTimeout(200);
  };

  test('ilgisiz alt ağaçtaki KALICI animasyon kapıyı KIRMIZI YAPMAZ', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/hayvancilik');
    await expect(page.getByRole('link', {name: 'Hayvan listesi', exact: true}))
      .toHaveCount(1, {timeout: 15_000});
    await injectAnimation(page, 'unrelated');

    const metrics = await collectMetrics(page.locator('main'));
    expect(
      metrics.environment.totalRunningAnimationCount,
      'probe must actually leave a persistent animation running, else this proves nothing',
    ).toBeGreaterThan(0);
    expect(
      metrics.environment.runningAnimationCount,
      'an animation that cannot move measured geometry must not be counted',
    ).toBe(0);
    console.log(
      'MUTATION_GREEN unrelated-persistent-animation: ' +
      `total=${metrics.environment.totalRunningAnimationCount} relevant=0 (kapı KIRMIZI OLMADI)`,
    );
  });

  test('kontrolün ATASINDAKİ geometri animasyonu kapıyı KIRMIZI YAPAR', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/hayvancilik');
    await expect(page.getByRole('link', {name: 'Hayvan listesi', exact: true}))
      .toHaveCount(1, {timeout: 15_000});
    await injectAnimation(page, 'ancestor');

    // KAPININ KENDİ iddiası kırmızı vermeli — kaza değil, o cümle.
    let message = '';
    try {
      await collectMetrics(page.locator('main'));
    } catch (error) {
      message = String((error as Error).message);
    }
    expect(message, 'gate must go red when a geometry animation runs on a control ancestor').toContain(
      'controls must be measured after geometry-affecting animations settle',
    );
    console.log(`MUTATION_RED ancestor-geometry-animation :: ${message.split(String.fromCharCode(10))[0]}`);
  });

  test('ŞEKİL A: ÖZEL DEĞİŞKEN transform besliyorsa kapı KIRMIZI YAPAR', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/hayvancilik');
    await expect(page.getByRole('link', {name: 'Hayvan listesi', exact: true}))
      .toHaveCount(1, {timeout: 15_000});
    await injectAnimation(page, 'custom-property');

    let message = '';
    try {
      await collectMetrics(page.locator('main'));
    } catch (error) {
      message = String((error as Error).message);
    }
    expect(
      message,
      'an animated custom property feeding transform must be treated as relevant',
    ).toContain('controls must be measured after geometry-affecting animations settle');
    console.log(`MUTATION_RED custom-property-shape-A :: ${message.split(String.fromCharCode(10))[0]}`);
  });

  test('MUAFİYET: yalnız BOYA oynatan atadaki animasyon KIRMIZI YAPMAZ', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/hayvancilik');
    await expect(page.getByRole('link', {name: 'Hayvan listesi', exact: true}))
      .toHaveCount(1, {timeout: 15_000});
    await injectAnimation(page, 'inert-paint');

    const metrics = await collectMetrics(page.locator('main'));
    expect(
      metrics.environment.totalRunningAnimationCount,
      'probe must actually leave a persistent paint animation running',
    ).toBeGreaterThan(0);
    expect(
      metrics.environment.runningAnimationCount,
      'a paint-only animation cannot move the measured box and must stay exempt',
    ).toBe(0);
    console.log(
      'MUTATION_GREEN inert-paint-on-ancestor: ' +
      `total=${metrics.environment.totalRunningAnimationCount} relevant=0 (kapı KIRMIZI OLMADI)`,
    );
  });
});

test.describe('layout-grid snapping', () => {
  // Snap kuralının İKİ YÖNÜ de kanıtlanır. Tek yön kanıtlamak yeterli değildir:
  // "artefakt geçiyor" tek başına, kuralın gevşetilmediğini göstermez.
  test('bir ızgara birimi eksik kontrol KIRMIZI kalır, artefakt YEŞİL geçer', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/hayvancilik');

    const cases = await page.evaluate(() => {
      const gridDenominator = 64;
      const snap = (v: number) => Math.round(v * gridDenominator) / gridDenominator;
      const host = document.querySelector('main') ?? document.body;
      const probe = document.createElement('button');
      probe.textContent = 'x';
      probe.style.cssText = 'width:200px;box-sizing:border-box';
      host.appendChild(probe);
      const measure = (css: string) => {
        probe.style.setProperty('height', css, 'important');
        probe.style.setProperty('min-height', css, 'important');
        const raw = probe.getBoundingClientRect().height;
        return {requested: css, raw, snapped: snap(raw), tooSmall: snap(raw) < 44};
      };
      const out = {
        // GERÇEK ihlal: tam bir ızgara birimi eksik. Motor bunu temsil EDEBİLİR.
        oneGridUnitShort: measure('43.984375px'),
        // GÖZLENEN ARTEFAKT: ızgara adımının 1/256'sı kadar eksik.
        observedArtifact: (() => {
          probe.style.removeProperty('height');
          probe.style.setProperty('min-height', '44px', 'important');
          const raw = 43.99993896484375;
          return {requested: 'observed 43.99993896484375', raw, snapped: snap(raw), tooSmall: snap(raw) < 44};
        })(),
        exactly44: measure('44px'),
        // ETKİN TOLERANS SINIRI ölçülür, iddia edilmez: yarım ızgara biriminden
        // uzaktaki her değer KIRMIZI kalmalı. 43.5 bariz bir ihlaldir.
        halfPixelShort: (() => {
          const raw = 43.5;
          return {requested: 'bound 43.5', raw, snapped: snap(raw), tooSmall: snap(raw) < 44};
        })(),
      };
      probe.remove();
      return out;
    });

    // Ölçülen ızgara birimi eksikliği KIRMIZI kalmalı — snap bunu maskelemez.
    expect(cases.oneGridUnitShort.snapped, 'bir ızgara birimi eksik kutu ızgarada da eksik kalmalı').toBeLessThan(44);
    expect(cases.oneGridUnitShort.tooSmall, 'gerçek ihlal KIRMIZI kalmalı').toBe(true);
    // CI'da gözlenen artefakt YEŞİL geçmeli — temsil edilemez bir fark.
    expect(cases.observedArtifact.snapped, 'artefakt ızgarada tam 44 olmalı').toBe(44);
    expect(cases.observedArtifact.tooSmall, 'temsil edilemez sapma ihlal SAYILMAMALI').toBe(false);
    expect(cases.exactly44.tooSmall, 'tam 44 geçmeli').toBe(false);
    // Beyan edilen sınırın kendisi de kontrol edilir: 43.5 ızgarada 43.5 kalır.
    expect(cases.halfPixelShort.snapped, '43.5 ızgarada 43.5 kalmalı').toBe(43.5);
    expect(cases.halfPixelShort.tooSmall, '43.5 KIRMIZI kalmalı').toBe(true);
    console.log(
      `GRID_SNAP oneGridUnitShort raw=${cases.oneGridUnitShort.raw} snapped=${cases.oneGridUnitShort.snapped} tooSmall=${cases.oneGridUnitShort.tooSmall}` +
      ` | artifact raw=${cases.observedArtifact.raw} snapped=${cases.observedArtifact.snapped} tooSmall=${cases.observedArtifact.tooSmall}` +
      ` | bound raw=${cases.halfPixelShort.raw} snapped=${cases.halfPixelShort.snapped} tooSmall=${cases.halfPixelShort.tooSmall}`,
    );
  });
});

test.describe.serial('six-screen measured mobile batch gate', () => {
  let seed: BatchThreeSeed;

  test.beforeAll(async () => {
    seed = await createBatchThreeSeed();
  });

  for (const viewport of VIEWPORTS) {
    test(`${viewport.name}: seeded data and required actions survive responsive rendering @tahsis-motoru`, async ({page}) => {
      test.setTimeout(180_000);
      await page.setViewportSize({width: viewport.width, height: viewport.height});
      await login(page);

      await page.goto('/hayvancilik');
      await expectSeededMarker(
        page.getByText(seed.herdEarTag, {exact: true}),
        'seeded HerdDashboard overdue vaccination',
      );
      await assertScreen(
        page,
        `${viewport.name}/HerdDashboard`,
        page.getByRole('heading', {name: 'Sürü Panosu'}),
        ['Hayvan listesi', 'Zorunlu takvim', 'Tümü'],
        viewport.enforceTouchTargets,
      );
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('herd-dashboard-data-surface'),
          'HerdDashboard',
          DECLARED_DATA_SURFACES['herd-dashboard-data-surface'],
        );
      }

      await page.goto('/tahsis-defteri');
      await expect(page.getByRole('heading', {name: 'Tahsis Defteri'})).toBeVisible({timeout: 15_000});
      await page.getByRole('tab', {name: /Mutabakat/}).click();
      await expectSeededMarker(
        page.getByText(seed.paymentDocumentNo, {exact: true}),
        'seeded PaymentAllocations reconciliation document',
      );
      await assertScreen(
        page,
        `${viewport.name}/PaymentAllocations`,
        page.getByRole('heading', {name: 'Tahsis Defteri'}),
        ['Manuel Tahsis'],
        viewport.enforceTouchTargets,
      );
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('payment-allocations-reconciliation-data-surface'),
          'PaymentAllocations reconciliation',
          DECLARED_DATA_SURFACES['payment-allocations-reconciliation-data-surface'],
        );
      }

      // İKİ YAPILANDIRMA DA ÖLÇÜLÜR.
      await page.getByRole('tab', {name: 'Tahsis Görünümü'}).click();
      if (!ALLOCATION_ENGINE_ENABLED) {
        // ÜRETİM VARSAYILANI. Ekran açık bir devre dışı durumu çizmeli ve veri
        // yüzeyi BEYAN ETMEMELİ: kapalı bir özellik ile boş bir tablo ne
        // kullanıcıya ne de kapıya aynı görünmeli.
        await expect(
          page.getByTestId('payment-allocations-history-disabled'),
          'motor kapalıyken açık bir devre dışı durumu çizilmeli',
        ).toBeVisible({timeout: 15_000});
        await expect(
          page.getByTestId('payment-allocations-history-data-surface'),
          'motor kapalıyken tahsis geçmişi veri yüzeyi BEYAN EDİLMEMELİ',
        ).toHaveCount(0);
        console.log('CONFIG_STATE PaymentAllocations history: engine=off -> disabled state, surfaces=0');
      } else {

      await page.getByLabel('Kaynak').click();
      await page.getByRole('option', {name: 'Satış Belgesi'}).click();
      await page.getByLabel('Sipariş No').fill(String(seed.saleOrderId));
      await page.getByRole('button', {name: 'Geçmişi Getir'}).click();
      const historySurface = page.getByTestId('payment-allocations-history-data-surface');
      // Yalnız bu tohumun ürettiği iki değer: aranan satış belgesinin id'si ve
      // tahsis edilen tutar. Ekran başlığı ya da sütun adı DEĞİL — onlar veri
      // olmadan da render edilir.
      await expect(historySurface, 'seeded allocation must name the searched order')
        .toContainText(`#${seed.saleOrderId}`, {timeout: 15_000});
      await expect(historySurface, 'seeded allocation must show its allocated amount')
        .toContainText(money('4.00'));
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('payment-allocations-history-data-surface'),
          'PaymentAllocations history',
          DECLARED_DATA_SURFACES['payment-allocations-history-data-surface']!,
        );
      }
      console.log('CONFIG_STATE PaymentAllocations history: engine=on -> declared surface with content');
      }

      await page.goto('/bildirimler');
      await expect(page.getByRole('heading', {name: 'Bildirimler'})).toBeVisible({timeout: 15_000});
      await page.getByRole('tab', {name: 'Onay Bekleyenler'}).click();
      await expectSeededMarker(
        page.getByTestId('notifications-data-surface').getByText(String(seed.notificationId), {exact: true}),
        'seeded Notifications awaiting-approval row',
      );
      await assertScreen(
        page,
        `${viewport.name}/Notifications`,
        page.getByRole('heading', {name: 'Bildirimler'}),
        [
          `${seed.notificationId} — İncele / Onayla`,
          `${seed.notificationId} — İptal`,
        ],
        viewport.enforceTouchTargets,
      );
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('notifications-data-surface'),
          'Notifications',
          DECLARED_DATA_SURFACES['notifications-data-surface'],
        );
      }

      await page.goto('/bildirimler/sablonlar');
      await expectSeededMarker(
        page.getByText(seed.notificationTemplateName, {exact: true}),
        'seeded active NotificationTemplates row',
      );
      await assertScreen(
        page,
        `${viewport.name}/NotificationTemplates`,
        page.getByRole('heading', {name: 'Bildirim Şablonları'}),
        ['Düzenle'],
        viewport.enforceTouchTargets,
      );
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('notification-templates-data-surface'),
          'NotificationTemplates',
          DECLARED_DATA_SURFACES['notification-templates-data-surface'],
        );
      }

      await page.goto('/hizli-satis');
      await expect(page.getByRole('heading', {name: 'Hızlı Satış'})).toBeVisible({timeout: 15_000});
      await page.getByLabel('Barkod').fill(seed.productBarcode);
      await page.getByLabel('Barkod').press('Enter');
      await expectSeededMarker(
        page.getByText(seed.productName, {exact: true}),
        'seeded POS cart line',
      );
      await assertScreen(
        page,
        `${viewport.name}/Pos`,
        page.getByRole('heading', {name: 'Hızlı Satış'}),
        ['Satışı Tamamla'],
        viewport.enforceTouchTargets,
      );
      const deleteAction = page.getByRole('button', {
        name: new RegExp(`${seed.productName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?: —)? [Ss]il$`),
      });
      await expect(deleteAction, 'seeded POS delete action must render').toHaveCount(1);
      await expect(deleteAction).toBeVisible();
      if (viewport.enforceTouchTargets) {
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('pos-cart-data-surface'),
          'Pos',
          DECLARED_DATA_SURFACES['pos-cart-data-surface'],
        );
      }

      await page.goto('/sube-transfer');
      await expect(page.getByRole('heading', {name: 'Şubeler Arası Transfer'})).toBeVisible({timeout: 15_000});
      const productInput = page.getByLabel('Parça ara (ad / kod)');
      await productInput.fill(seed.productName);
      await page.getByRole('option', {name: new RegExp(seed.productName)}).click();
      await expect(productInput, 'seeded StockTransfer product must stay selected')
        .toHaveValue(new RegExp(seed.productName));
      const transferActions = page.getByRole('button', {name: /Bu depodan gönder$/});
      await expect(transferActions, 'both seeded warehouse actions must render').toHaveCount(2);
      await expect(
        transferActions.and(page.locator('button:not([disabled])')),
        'exactly one seeded warehouse must be a valid transfer source',
      ).toHaveCount(1);
      const stockMetrics = await collectMetrics(page.locator('main'));
      console.log(
        `TOUCH_TARGET_METRIC ${viewport.name}/StockTransfer controls=${stockMetrics.controlCount} tooSmall=${stockMetrics.tooSmallControlCount}`,
      );
      if (viewport.enforceTouchTargets) {
        expect(stockMetrics.tooSmallControlCount, 'StockTransfer must have no visible control below 44x44').toBe(0);
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('stock-transfer-stock-data-surface'),
          'StockTransfer stock',
          DECLARED_DATA_SURFACES['stock-transfer-stock-data-surface'],
        );
        await expectSurfaceSettlesWithNoOverflow(
          page.getByTestId('stock-transfer-history-data-surface'),
          'StockTransfer history',
          DECLARED_DATA_SURFACES['stock-transfer-history-data-surface'],
        );
      }
    });
  }

  test('shrinking one batch control makes the touch-target gate red', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/hayvancilik');
    await expectSeededMarker(
      page.getByText(seed.herdEarTag, {exact: true}),
      'seeded HerdDashboard overdue vaccination',
    );
    const root = page.locator('main');
    const baseline = await collectMetrics(root);
    expect(baseline.tooSmallControlCount).toBe(0);
    const target = page.getByRole('link', {name: 'Hayvan listesi', exact: true});
    await expect(target).toHaveCount(1);
    await target.evaluate(element => {
      element.style.setProperty('width', '20px', 'important');
      element.style.setProperty('height', '20px', 'important');
      element.style.setProperty('min-width', '20px', 'important');
      element.style.setProperty('min-height', '20px', 'important');
      element.style.setProperty('padding', '0', 'important');
    });
    const mutated = await collectMetrics(root);
    expect(mutated.tooSmallControlCount, 'shrinking a batch control must turn the gate red').toBe(1);
    console.log(
      `MUTATION_RED HerdDashboard tooSmallControlCount: ${baseline.tooSmallControlCount} -> ${mutated.tooSmallControlCount}`,
    );
  });

  test('restoring a wide table makes the batch overflow gate red', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/bildirimler');
    await page.getByRole('tab', {name: 'Onay Bekleyenler'}).click();
    await expectSeededMarker(
      page.getByTestId('notifications-data-surface').getByText(String(seed.notificationId), {exact: true}),
      'seeded Notifications awaiting-approval row',
    );
    const surface = page.getByTestId('notifications-data-surface');
    const baseline = await expectSurfaceSettlesWithNoOverflow(
      surface,
      'Notifications mutation baseline',
      DECLARED_DATA_SURFACES['notifications-data-surface'],
    );
    await surface.evaluate(node => {
      const oldTableContainer = document.createElement('div');
      oldTableContainer.style.width = '100%';
      oldTableContainer.style.overflowX = 'auto';
      const oldTable = document.createElement('table');
      oldTable.style.width = '900px';
      oldTable.style.minWidth = '900px';
      oldTable.innerHTML = '<tbody><tr><td>old wide table branch</td></tr></tbody>';
      oldTableContainer.appendChild(oldTable);
      node.replaceChildren(oldTableContainer);
    });
    let gateRejectedOldTable = false;
    try {
      await expectSurfaceSettlesWithNoOverflow(
        surface,
        'Notifications old-table mutation',
        DECLARED_DATA_SURFACES['notifications-data-surface'],
      );
    } catch {
      gateRejectedOldTable = true;
    }
    expect(gateRejectedOldTable, 'restoring a wide table must turn the batch gate red').toBe(true);
    const mutated = await collectHorizontalOverflow(
        surface,
        'Notifications mutation evidence',
        DECLARED_DATA_SURFACES['notifications-data-surface'],
      );
    expect(mutated.overflowingSurfaceCount).toBeGreaterThan(0);
    console.log(
      `MUTATION_RED Notifications horizontalOverflowCount: ${baseline.overflowingSurfaceCount} -> ${mutated.overflowingSurfaceCount}`,
    );
  });

  // --- Devralınan üç kuralın BU PR'ın ekranlarında kanıtı -------------------
  // #58'in mutasyonları kendi ekranlarında (FarmDashboard/WorkOrderDetail)
  // koşuyor. Aynı üç kural bu partinin ekranlarında da kırmızıya dönmeli;
  // kırmızıya dönemeyen bir iddia kapı değildir.

  async function batchGateIsRed(
    surface: Locator,
    label: string,
    expectedSurfaces: number,
  ): Promise<boolean> {
    try {
      await expectSurfaceSettlesWithNoOverflow(surface, label, expectedSurfaces);
      return false;
    } catch {
      return true;
    }
  }

  async function openStockTransfer(page: Page): Promise<void> {
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/sube-transfer');
    await expect(page.getByRole('heading', {name: 'Şubeler Arası Transfer'}))
      .toBeVisible({timeout: 15_000});
    const productInput = page.getByLabel('Parça ara (ad / kod)');
    await productInput.fill(seed.productName);
    await page.getByRole('option', {name: new RegExp(seed.productName)}).click();
  }

  test('KIRPMA: StockTransfer stok yüzeyi kırpılırsa kapı kırmızı olur', async ({page}) => {
    test.setTimeout(120_000);
    await openStockTransfer(page);
    const surface = page.getByTestId('stock-transfer-stock-data-surface');
    const expected = DECLARED_DATA_SURFACES['stock-transfer-stock-data-surface'];
    const baseline = await expectSurfaceSettlesWithNoOverflow(surface, 'StockTransfer clip baseline', expected);
    expect(baseline.overflowingSurfaceCount).toBe(0);

    // Bu ekranın ÖLÇÜLEN kusuru tam olarak buydu: 364px'lik overflow:hidden bir
    // kabın içinde 397px'lik tablo. Eski kural bunu TEMİZ raporluyordu.
    await surface.evaluate((node) => {
      const clip = document.createElement('div');
      clip.dataset.overflowMutation = 'stock-transfer-clip';
      clip.style.width = '364px';
      clip.style.overflowX = 'hidden';
      const wide = document.createElement('div');
      wide.style.width = '397px';
      wide.style.height = '20px';
      clip.appendChild(wide);
      node.replaceChildren(clip);
    });
    expect(
      await batchGateIsRed(surface, 'StockTransfer clipping mutation', expected),
      'kırpılmış 397px içerik 364px kapta kapıyı kırmızıya çevirmeli',
    ).toBe(true);
    const clipped = await collectHorizontalOverflow(surface, 'StockTransfer clipping evidence', expected);
    expect(clipped.worstOffender?.overflowX).toBe('hidden');
    console.log(
      `MUTATION_RED StockTransfer/clip 0 -> ${clipped.overflowingSurfaceCount} worst=${JSON.stringify(clipped.worstOffender)}`,
    );
  });

  test('İÇERİK: StockTransfer geçmiş yüzeyi boşalırsa kapı kırmızı olur', async ({page}) => {
    test.setTimeout(120_000);
    await openStockTransfer(page);
    const surface = page.getByTestId('stock-transfer-history-data-surface');
    const expected = DECLARED_DATA_SURFACES['stock-transfer-history-data-surface'];
    const baseline = await expectSurfaceSettlesWithNoOverflow(surface, 'StockTransfer history content baseline', expected);
    expect(baseline.contentRowCount, 'temel ölçümde tohumlanmış transfer satırı olmalı').toBeGreaterThan(0);

    // Satırları çıkar, yüzeyi ve boş-durum kabuğunu yerinde bırak: taşma
    // ölçen bir kapı bunu TEMİZ görür.
    await surface.evaluate((node) => {
      const empty = document.createElement('div');
      empty.className = 'MuiCard-root';
      empty.dataset.overflowMutation = 'stock-transfer-history-emptied';
      empty.textContent = 'Kayıt yok';
      node.replaceChildren(empty);
    });
    await expect(surface, 'beyan edilen yüzey yerinde kalmalı').toHaveCount(expected);
    expect(
      await batchGateIsRed(surface, 'StockTransfer history empty mutation', expected),
      'satırsız bir yüzey kapıyı kırmızıya çevirmeli',
    ).toBe(true);
    const emptied = await collectHorizontalOverflow(surface, 'StockTransfer history empty evidence', expected);
    console.log(
      `MUTATION_RED StockTransfer/history-emptied contentRows: ${baseline.contentRowCount} -> ${emptied.contentRowCount}`,
    );
  });

  test('YÜZEY SAYISI: HerdDashboard yüzeyi silinirse kapı kırmızı olur', async ({page}) => {
    test.setTimeout(120_000);
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/hayvancilik');
    await expectSeededMarker(
      page.getByText(seed.herdEarTag, {exact: true}),
      'seeded HerdDashboard overdue vaccination',
    );
    const surface = page.getByTestId('herd-dashboard-data-surface');
    const expected = DECLARED_DATA_SURFACES['herd-dashboard-data-surface'];
    await expectSurfaceSettlesWithNoOverflow(surface, 'HerdDashboard surface-count baseline', expected);

    // Yüzeyin testid'sini kaldır: ekran hâlâ çiziliyor ama beyan edilen yüzey
    // kayboluyor. Sayı sözleşmesi olmasaydı kapı HİÇBİR ŞEY ölçmeden geçerdi.
    await surface.evaluate((node) => {
      node.removeAttribute('data-testid');
    });
    await expect(surface, 'beyan edilen yüzey artık bulunmamalı').toHaveCount(0);
    expect(
      await batchGateIsRed(surface, 'HerdDashboard surface-removal mutation', expected),
      'beyan edilen yüzeyi silinen ekran, hiçbir şey ölçmeden geçmemeli',
    ).toBe(true);
    console.log(`MUTATION_RED HerdDashboard/surface-deleted declaredSurfaces: ${expected} -> 0`);
  });

  // --- YAPILANDIRMA DÜRÜSTLÜĞÜ: üretim varsayılanında (motor KAPALI) ---------
  // Bu iki mutasyon yalnız kapalı yapılandırmada anlamlıdır; açık yapılandırma
  // zaten yukarıdaki içerik/taşma kapılarıyla ölçülüyor.

  test('KAPALI MOTOR: geçmiş yüzeyi yine de beyan edilirse kapı kırmızı olur', async ({page}) => {
    test.setTimeout(120_000);
    test.skip(ALLOCATION_ENGINE_ENABLED, 'yalnız üretim varsayılanında (motor kapalı) geçerli');
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/tahsis-defteri');
    await page.getByRole('tab', {name: 'Tahsis Görünümü'}).click();
    const surface = page.getByTestId('payment-allocations-history-data-surface');
    await expect(surface, 'temel durumda yüzey beyan edilmemeli').toHaveCount(0);

    // Kapalı motorda KALICI OLARAK boş kalacak bir yüzey beyan et.
    await page.getByTestId('payment-allocations-history-disabled').evaluate((node) => {
      const injected = document.createElement('div');
      injected.dataset.testid = 'payment-allocations-history-data-surface';
      injected.dataset.overflowMutation = 'declared-while-engine-off';
      node.replaceChildren(injected);
    });
    let red = false;
    try {
      await expect(surface, 'mutasyon sonrası yüzey görünür olmamalıydı').toHaveCount(0, {timeout: 5_000});
    } catch {
      red = true;
    }
    expect(red, 'motor kapalıyken beyan edilen bir geçmiş yüzeyi kapıyı kırmızıya çevirmeli').toBe(true);
    console.log('MUTATION_RED PaymentAllocations/declared-while-engine-off surfaces: 0 -> 1');
  });

  test('KAPALI MOTOR: devre dışı durumu yerine boş tablo çizilirse kapı kırmızı olur', async ({page}) => {
    test.setTimeout(120_000);
    test.skip(ALLOCATION_ENGINE_ENABLED, 'yalnız üretim varsayılanında (motor kapalı) geçerli');
    await page.setViewportSize({width: 390, height: 844});
    await login(page);
    await page.goto('/tahsis-defteri');
    await page.getByRole('tab', {name: 'Tahsis Görünümü'}).click();
    const disabled = page.getByTestId('payment-allocations-history-disabled');
    await expect(disabled, 'temel durumda açık devre dışı durumu çizilmeli').toBeVisible({timeout: 15_000});

    // Devre dışı durumunu KALDIR, yerine sıradan bir boş tablo bırak: kullanıcı
    // "kayıt yok" sanır, kapı da bunu ayırt edemezse defekti gizler.
    await disabled.evaluate((node) => {
      const emptyTable = document.createElement('div');
      emptyTable.dataset.overflowMutation = 'empty-table-instead-of-disabled';
      emptyTable.textContent = 'Kayıt yok.';
      node.replaceWith(emptyTable);
    });
    let red = false;
    try {
      await expect(disabled, 'mutasyon sonrası devre dışı durumu kaybolmalıydı').toBeVisible({timeout: 5_000});
    } catch {
      red = true;
    }
    expect(red, 'devre dışı durumu yerine boş tablo kapıyı kırmızıya çevirmeli').toBe(true);
    console.log('MUTATION_RED PaymentAllocations/empty-table-instead-of-disabled disabledState: 1 -> 0');
  });

  test('removing one batch seed makes the shared gate red', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await page.route('**/api/notifications/templates', async route => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({
        response,
        json: body.filter((row: {id: number}) => row.id !== seed.notificationTemplateId),
      });
    });
    await login(page);
    await page.goto('/bildirimler/sablonlar');
    await expect(page.getByRole('heading', {name: 'Bildirim Şablonları'})).toBeVisible({timeout: 15_000});
    const removedMarker = page.getByText(seed.notificationTemplateName, {exact: true});
    let gateRejectedMissingSeed = false;
    try {
      await expectSeededMarker(removedMarker, 'seeded active NotificationTemplates row', 250);
    } catch {
      gateRejectedMissingSeed = true;
    }
    expect(gateRejectedMissingSeed, 'missing batch seed must turn the shared gate red').toBe(true);
    expect(await removedMarker.count()).toBe(0);
    console.log('MUTATION_RED NotificationTemplates seededTemplateCount: 1 -> 0');
  });
});

// ÖZELLİK: 390px'te alacak yaşlandırma yüzeyi, SABİTLENMİŞ en kötü girdide
// hiçbir içeriği yatayda ulaşılamaz bırakmaz — ve yüzeyin üstünde koşan geçici
// bir tıklama süslemesi bu ölçümü değiştiremez.
//
// NEDEN AYRI BİR BÖLÜM. Yukarıdaki ekran kapısı CANLI veriyle koşar: yüzeye
// veritabanındaki HER açık alacak müşterisi girer, yani genişliği o an hangi
// spec'lerin tohumlandığına bağlıdır. O kapı ekranın GERÇEKTEN çizildiğini
// kanıtlar ve öyle kalmalı. Buradaki kapı ise girdiyi ÇİVİLER: rengi artık
// "bugün veri uzun muydu"yu değil, YERLEŞİMİN kendisini ölçer.
const ALACAK_EN_KOTU_AD =
  'MobilDilimMüşterisiUnvanıSanayiVeTicaretLimitedŞirketiAnonimOrtaklığı17550000000002';

// Tutarlar da en kötü hâlde çivilendi: rakam sayısı genişliğe giren tek
// değişkendi ve artık koşudan koşuya oynayamaz.
const ALACAK_EN_KOTU_TUTAR = '9876543210987.65';

function alacakYuku() {
  const kova = {
    not_due: ALACAK_EN_KOTU_TUTAR, days_1_30: ALACAK_EN_KOTU_TUTAR,
    days_31_60: ALACAK_EN_KOTU_TUTAR, days_61_90: ALACAK_EN_KOTU_TUTAR,
    days_90_plus: ALACAK_EN_KOTU_TUTAR, total: ALACAK_EN_KOTU_TUTAR,
  };
  return {
    as_of: '2026-08-20',
    customers: [{
      customer_id: 424242,
      customer_name: ALACAK_EN_KOTU_AD,
      ...kova,
      documents: [{
        id: 424242, document_no: 'ALC-2026-000000424242-R3',
        due_date: '2026-08-01', remaining: ALACAK_EN_KOTU_TUTAR,
      }],
    }],
    totals: kova,
  };
}

// SERİ DEĞİL. Bu bloğun satırları birbirinden bağımsız: her test kendi
// sayfasını açıyor ve girdisini kendi rotasıyla çiviliyor. `describe.serial`
// olsaydı ilk kırmızıdan sonrakiler ATLANIRDI ve mutasyon tablosu beş kaçış
// şeklini tek koşuda gösteremezdi — ölçüldü, tabloyu yazarken bu oldu.
test.describe('alacak yaşlandırma: çivilenmiş girdi ve geçici süsleme', () => {
  async function acSayfayi(page: Page): Promise<Locator> {
    await page.setViewportSize({width: 390, height: 844});
    await page.route('**/api/reports/receivables-aging**', route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(alacakYuku()),
    }));
    await login(page);
    await page.goto('/raporlar/alacak-yaslandirma');
    const surface = page.getByTestId('receivables-aging-data-surface');
    await expect(surface).toBeVisible({timeout: 15_000});
    await expect(page.getByText(ALACAK_EN_KOTU_AD, {exact: true})).toBeVisible({timeout: 15_000});
    return surface;
  }

  // Kökün İÇİNDEKİ ham `scrollWidth` taşması — düzeltme ÖNCESİ kapının gördüğü
  // sayı. Testlerin boşa düşmediğini kanıtlamak için kullanılır.

  test('ÇİVİLENMİŞ EN KÖTÜ GİRDİ: yüzey kırpmıyor', async ({page}) => {
    test.setTimeout(120_000);
    const surface = await acSayfayi(page);
    const metrics = await expectSurfaceSettlesWithNoOverflow(
      surface, 'ReceivablesAging pinned-worst-case',
      DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
    );
    expect(metrics.contentRowCount, 'çivilenmiş girdi bir satır çizmeli').toBe(1);
    // Boşa düşme çapası: en kötü girdi gerçekten ÇİZİLMİŞ olmalı. Metin
    // gelmeseydi kapı da boş bir kutuyu ölçüp yeşil kalırdı.
    expect(
      await surface.getByText(ALACAK_EN_KOTU_AD, {exact: true}).count(),
      'en kötü ad yüzeyin İÇİNDE çizilmeli',
    ).toBe(1);
  });

  test('TIKLAMA DALGASI kapıyı KIRMIZI YAPMAZ — animasyon içerik değildir', async ({page}) => {
    test.setTimeout(120_000);
    const surface = await acSayfayi(page);
    await expectSurfaceSettlesWithNoOverflow(
      surface, 'ReceivablesAging ripple baseline',
      DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
    );

    // TIKLAMA YERİ BELİRLENİMCİ. Dalganın SAĞA taşması tıklamanın kartın
    // neresine düştüğüne bağlıdır: merkeze yakın bir tık, dalga çapı kart
    // genişliğini aşmadıkça taşma üretmeyebilir. Ölçüldü — bloğu 140 kez
    // koştururken bu çapa BİR KEZ boşa düştü ve testi kararsız yaptı. Sağ
    // kenara yakın tıklamak dalganın sağ sınırı aşmasını GARANTİ eder; kapının
    // kendi testi kararsız kalamaz.
    const kart = surface.locator('[data-responsive-row]').first();
    const kutu = await kart.boundingBox();
    expect(kutu, 'kart kutusu ölçülemedi').not.toBeNull();
    await kart.click({position: {x: Math.round(kutu!.width) - 8, y: 12}});

    const metrics = await expectSurfaceSettlesWithNoOverflow(
      surface, 'ReceivablesAging ripple mid-flight',
      DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
    );
    expect(metrics.overflowingSurfaceCount, 'geçici süsleme taşma sayılmamalı').toBe(0);

    // BOŞA DÜŞME ÇAPASI — KAPININ KENDİ DİZİSİNDEN, AYRI BİR GÖZLEMDEN DEĞİL.
    //
    // ÖLÇÜLMÜŞ KUSUR: burada eskiden ikinci, BAĞIMSIZ bir anket vardı — testin
    // kendi döngüsü dalgayı yakalamak zorundaydı, sonra kapı da ayrıca
    // yakalamak zorundaydı. İKİ zamanlama riski demekti ve 140 çalıştırmada bir
    // kez tam da bu yüzden düştü. Kapı zaten örnek örnek bakıyor; çapa artık
    // ONUN gördüğünü okuyor. Tek gözlem, tek risk.
    const tasanOrnek = metrics.ornekler.filter(n => n > 0).length;
    expect(
      tasanOrnek,
      'dalga kapının penceresinde GÖRÜLMELİYDİ; görülmediyse bu yeşil hiçbir şey kanıtlamaz',
    ).toBeGreaterThan(0);
    console.log(
      `MUTATION_GREEN ReceivablesAging ripple taşanÖrnek=${tasanOrnek}/${metrics.ornekler.length}`
      + ' -> kapı 0',
    );
  });

  // KIRMIZI OLMALI — İNCELEMENİN ÖLÇTÜĞÜ BEŞ KAÇIŞ ŞEKLİ ve dört ek şekil.
  //
  // Beşi de ÖNCEKİ tasarımda MUAF çıkıyordu: hepsi akış dışı, etkileşimsiz ve
  // metinsizdi, dolayısıyla dört koşullu sınıflandırıcıyı geçiyorlardı. Artık
  // sınıflandırma yok; taşmaları SÜRDÜĞÜ için kırmızı olurlar. Kapının bu
  // şekilleri TANIMASI gerekmiyor — tanımadığı bir şekil de aynı sebeple
  // kırmızı olur, hatanın yönü budur.
  const KIRMIZI_OLMALI: {ad: string; konum: string; kur: string; etiket?: string}[] = [
    {
      ad: '2A metinsiz kutuda ARKA PLAN GÖRSELİ',
      konum: 'absolute',
      kur: `el.style.pointerEvents = 'none';
            el.style.backgroundImage = 'linear-gradient(90deg,#164a8a,#8ac)';`,
    },
    {
      ad: '2B boş kutuda ::before İKON GLİFİ',
      konum: 'absolute',
      kur: `el.style.pointerEvents = 'none';
            el.classList.add('olcum-glif');
            if (!document.getElementById('olcum-glif-stil')) {
              const stil = document.createElement('style');
              stil.id = 'olcum-glif-stil';
              stil.textContent = '.olcum-glif::before{content:"\\\\2605 durum";font-size:14px}';
              document.head.appendChild(stil);
            }`,
    },
    {
      ad: '2C EN ÜSTTEKİ <svg> (kendisi svg)',
      konum: 'absolute',
      etiket: 'svg',
      kur: `el.style.pointerEvents = 'none';`,
    },
    {
      ad: '2D EN ÜSTTEKİ <canvas> (kendisi canvas)',
      konum: 'absolute',
      etiket: 'canvas',
      kur: `el.style.pointerEvents = 'none';`,
    },
    {
      ad: '2E role="img" etiketli ÇOCUKSUZ kutu',
      konum: 'absolute',
      kur: `el.style.pointerEvents = 'none';
            el.setAttribute('role', 'img');
            el.setAttribute('aria-label', 'Status Chart');`,
    },
    {
      // Önceki turdan gelen dört şekil. Sınıflandırıcı kalktığı için artık
      // bunlar da aynı sebeple kırmızı; kapsamı daraltmamak için duruyorlar.
      ad: 'AKIŞ İÇİ metinsiz kutu',
      konum: 'static',
      kur: `el.style.pointerEvents = 'none';`,
    },
    {
      ad: 'METİN taşıyan akış dışı kutu',
      konum: 'absolute',
      kur: `el.style.pointerEvents = 'none'; el.textContent = 'ulaşılamayan gerçek metin';`,
    },
    {
      ad: 'ETKİLEŞİMLİ akış dışı kutu',
      konum: 'absolute',
      kur: `el.style.pointerEvents = 'auto';`,
    },
    {
      ad: 'GÖRSEL taşıyan akış dışı kutu',
      konum: 'absolute',
      kur: `el.style.pointerEvents = 'none';
            const img = document.createElement('img');
            img.alt = 'ulaşılamayan görsel';
            img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
            img.style.width = '10px'; img.style.height = '10px';
            el.appendChild(img);`,
    },
  ];

  for (const ornek of KIRMIZI_OLMALI) {
    test(`KIRMIZI: ${ornek.ad}`, async ({page}) => {
      test.setTimeout(180_000);
      const surface = await acSayfayi(page);
      await expectSurfaceSettlesWithNoOverflow(
        surface, `ReceivablesAging ${ornek.ad} baseline`,
        DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
      );

      const eklendi = await surface.evaluate((node, ornek) => {
        const kart = node.querySelector('[data-responsive-row]') as HTMLElement | null;
        const hedef = kart ?? (node as HTMLElement);
        hedef.style.position = 'relative';
        // <svg> SVG ad alanında, <canvas> HTML ad alanında yaratılmalı;
        // aksi hâlde "canvas" adlı bir SVG düğümü çıkar ve şekil taklit
        // edilmiş olmaz.
        const el = (ornek.etiket === 'svg'
          ? document.createElementNS('http://www.w3.org/2000/svg', 'svg')
          : ornek.etiket
            ? document.createElement(ornek.etiket)
            : document.createElement('div')) as unknown as HTMLElement;
        el.setAttribute('data-overflow-mutation', 'escape-shape');
        el.style.position = ornek.konum;
        if (ornek.konum !== 'static') {
          el.style.top = '0px';
          el.style.left = '0px';
        }
        el.style.width = '520px';   // 364px kutudan GENİŞ
        el.style.height = '16px';
        new Function('el', ornek.kur)(el);
        hedef.appendChild(el);
        return !!hedef.querySelector('[data-overflow-mutation="escape-shape"]');
      }, {konum: ornek.konum, kur: ornek.kur, etiket: ornek.etiket ?? ''});
      // BOŞA DÜŞME ÇAPASI: şekil gerçekten eklenmiş olmalı.
      expect(eklendi, `${ornek.ad} sayfaya eklenemedi`).toBe(true);

      let kirmizi = false;
      try {
        await expectSurfaceSettlesWithNoOverflow(
          surface, `ReceivablesAging ${ornek.ad}`,
          DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
        );
      } catch {
        kirmizi = true;
      }
      expect(kirmizi, `${ornek.ad} taşması KIRMIZI olmalıydı`).toBe(true);
      console.log(`MUTATION_RED ReceivablesAging kaçış/${ornek.ad}`);
    });
  }


  // ARALIKLI ŞEKİLLER — HEPSİ KIRMIZI OLMALI.
  //
  // İncelemenin adlandırdığı dört şekil. Hiçbiri doğası gereği süsleme değil:
  // dördü de ölçüm penceresi içinde GERÇEK içeriği ulaşılamaz bırakır. Önceki
  // "arka arkaya iki sıfır" kuralında dördü de YEŞİL geçiyordu; genişletilmiş
  // kural (`taşan* sıfır+`) dördünü de kırmızıya çevirir.
  const ARALIKLI_SEKILLER: {ad: string; kur: string}[] = [
    {
      ad: 'KARUSEL/MARKİ — genişlik dönüşümlü olarak taşıyor',
      kur: `let genis = true;
            const cevir = () => {
              el.style.width = genis ? '520px' : '100px';
              genis = !genis;
            };
            cevir();
            el.__zamanlayici = setInterval(cevir, 120);`,
    },
    {
      ad: 'AKORDİYON — açılıp kapanan içerik',
      kur: `let acik = true;
            const cevir = () => {
              el.style.display = acik ? 'block' : 'none';
              acik = !acik;
            };
            cevir();
            el.__zamanlayici = setInterval(cevir, 150);`,
    },
    {
      ad: 'SANALLAŞTIRILMIŞ SATIR — dönüşümlü olarak DOM ağacına girip çıkıyor',
      kur: `const ebeveyn = el.parentElement;
            let icerde = true;
            el.__zamanlayici = setInterval(() => {
              if (icerde) el.remove();
              else ebeveyn.appendChild(el);
              icerde = !icerde;
            }, 140);`,
    },
    {
      ad: 'GEÇ GELEN GÖRSEL — onay penceresi İÇİNDE sonradan taşırıyor',
      kur: `el.style.width = '100px';
            el.__zamanlayici = setTimeout(() => {
              el.style.width = '520px';
            }, 500);`,
    },
  ];

  for (const sekil of ARALIKLI_SEKILLER) {
    test(`ARALIKLI: ${sekil.ad} kapıyı KIRMIZI YAPAR`, async ({page}) => {
      test.setTimeout(180_000);
      const surface = await acSayfayi(page);

      await surface.evaluate((node, kur) => {
        const kart = (node.querySelector('[data-responsive-row]') ?? node) as HTMLElement;
        kart.style.position = 'relative';
        const el = document.createElement('div');
        el.setAttribute('data-overflow-mutation', 'aralikli');
        el.style.cssText =
          'position:absolute;top:0;left:0;width:520px;height:16px;pointer-events:none';
        // ÖNCE EKLE, SONRA KUR: sanal satır şekli `parentElement` istiyor ve
        // eklenmeden önce o null olurdu.
        kart.appendChild(el);
        new Function('el', kur)(el);
      }, sekil.kur);

      let kirmizi = false;
      try {
        await expectSurfaceSettlesWithNoOverflow(
          surface, `ReceivablesAging ${sekil.ad}`,
          DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
        );
      } catch {
        kirmizi = true;
      }
      expect(kirmizi, `${sekil.ad} KIRMIZI olmalıydı — içerik pencere içinde ulaşılamaz`).toBe(true);
      console.log(`MUTATION_RED ReceivablesAging aralıklı/${sekil.ad}`);
    });
  }

  // SINIR: kapının adının söylediği şey — VE SÖYLEMEDİĞİ ŞEY.
  //
  // Genişletmeden sonra yeşil kalabilen tek gerçek-içerik durumu: pencerenin
  // BAŞINDA taşan, TEK SEFERDE biten, bir daha DÖNMEYEN içerik. Bu sınır
  // kaldırılamaz — tıklama dalgasının imzasının aynısıdır. Bu yüzden bir not
  // değil, KAPININ ADI: `expectSurfaceSettlesWithNoOverflow` "yerleşir" der,
  // "hiç taşmaz" demez. Aşağıdaki test o sınırın NEREDE olduğunu sabitler:
  // sınırın içindeki şekil yeşil, sınırın hemen dışındaki (geri dönen) kırmızı.
  test('SINIR: BİR KEZ bitip DÖNMEYEN taşma yeşil, DÖNEN taşma kırmızı', async ({page}) => {
    test.setTimeout(180_000);
    const surface = await acSayfayi(page);

    // (a) SINIRIN İÇİ: 600ms taşıyıp biten, bir daha dönmeyen kutu.
    await surface.evaluate(node => {
      const kart = (node.querySelector('[data-responsive-row]') ?? node) as HTMLElement;
      kart.style.position = 'relative';
      const el = document.createElement('div');
      el.setAttribute('data-overflow-mutation', 'sinir-ici');
      el.style.cssText =
        'position:absolute;top:0;left:0;width:520px;height:16px;pointer-events:none';
      kart.appendChild(el);
      setTimeout(() => el.remove(), 600);
    });
    const icerdeki = await expectSurfaceSettlesWithNoOverflow(
      surface, 'ReceivablesAging sınır-içi tek seferlik',
      DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
    );
    expect(icerdeki.overflowingSurfaceCount, 'tek seferlik taşma yeşil kalmalı').toBe(0);

    // (b) SINIRIN DIŞI: aynı süre taşıyıp biten, ama GERİ DÖNEN kutu.
    await surface.evaluate(node => {
      const kart = (node.querySelector('[data-responsive-row]') ?? node) as HTMLElement;
      const el = document.createElement('div');
      el.setAttribute('data-overflow-mutation', 'sinir-disi');
      el.style.cssText =
        'position:absolute;top:0;left:0;width:520px;height:16px;pointer-events:none';
      kart.appendChild(el);
      setTimeout(() => { el.style.width = '100px'; }, 600);
      setTimeout(() => { el.style.width = '520px'; }, 1400);
    });
    let kirmizi = false;
    try {
      await expectSurfaceSettlesWithNoOverflow(
        surface, 'ReceivablesAging sınır-dışı geri dönen',
        DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
      );
    } catch {
      kirmizi = true;
    }
    expect(kirmizi, 'geri dönen taşma KIRMIZI olmalıydı').toBe(true);
    console.log('MUTATION_BOUNDARY ReceivablesAging tek-seferlik=yeşil geri-dönen=kırmızı');
  });

  // SINIR: onay penceresinden SONRA gelen taşma bu kapının konusu DEĞİLDİR.
  //
  // Bu test bir başarı değil, bir SINIR sabitler ve bilerek yeşildir. Kapı bir
  // GÖZLEM PENCERESİ boyunca ölçer; pencere kapandıktan sonra ulaşılamaz hale
  // gelen içerik (çok geç yüklenen görsel, sonraki kullanıcı etkileşimi) bu
  // ölçümün dışındadır — S3. Sınırın İÇİ zaten `ARALIKLI: GEÇ GELEN GÖRSEL`
  // satırında kırmızı; burada sınırın DIŞI sabitleniyor, ki sınır kayarsa
  // ikisinden biri düşsün.
  test('SINIR: onay penceresinden SONRA gelen taşma yeşil kalır (S3)', async ({page}) => {
    test.setTimeout(180_000);
    const surface = await acSayfayi(page);
    await surface.evaluate(node => {
      const kart = (node.querySelector('[data-responsive-row]') ?? node) as HTMLElement;
      kart.style.position = 'relative';
      const el = document.createElement('div');
      el.setAttribute('data-overflow-mutation', 'cok-gec');
      el.style.cssText =
        'position:absolute;top:0;left:0;width:100px;height:16px;pointer-events:none';
      kart.appendChild(el);
      setTimeout(() => { el.style.width = '520px'; }, 2_500);
    });

    const metrics = await expectSurfaceSettlesWithNoOverflow(
      surface, 'ReceivablesAging pencere SONRASI gelen taşma',
      DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
    );
    expect(metrics.overflowingSurfaceCount, 'pencere kapandıktan sonrası S3 sınırıdır').toBe(0);

    // BOŞA DÜŞME ÇAPASI: taşma GERÇEKTEN gelmiş olmalı, yoksa bu test bir sınır
    // değil, hiçbir şey sabitlemiş olurdu.
    await page.waitForTimeout(2_600);
    const sonra = await surface.evaluate(node => {
      const el = node.querySelector('[data-overflow-mutation="cok-gec"]') as HTMLElement;
      return el ? el.getBoundingClientRect().width : -1;
    });
    expect(sonra, 'geç taşma sonradan GERÇEKTEN oluşmalı').toBeGreaterThan(500);
    console.log('MUTATION_BOUNDARY ReceivablesAging pencere-sonrası=yeşil (S3 sınırı sabit)');
  });

  // YEŞİL KALMALI — DÖRT GERÇEK SÜSLEME.
  //
  // Bunlar bu uygulamada gerçekten bulunan süsleme biçimleri. Kapı onları
  // TANIMIYOR; yeşil kalmalarının sebebi ya taşmayı hiç üretmemeleri (odak
  // halkası, kaydırma degradesi, sabit konumlu perde) ya da ürettikleri
  // taşmanın DİNMESİ (tıklama dalgası). Her satır, süslemenin ölçüm anında
  // GERÇEKTEN sayfada olduğunu ayrıca çapalıyor; olmasaydı yeşil hiçbir şey
  // kanıtlamazdı.
  const YESIL_KALMALI: {ad: string; kur: string; secici: string}[] = [
    {
      ad: 'ODAK HALKASI (outline + box-shadow)',
      secici: '[data-decoration="odak"]',
      kur: `const kart = hedef.querySelector('.MuiCardActionArea-root') || hedef;
            kart.setAttribute('data-decoration', 'odak');
            kart.style.outline = '3px solid #164a8a';
            kart.style.outlineOffset = '4px';
            kart.style.boxShadow = '0 0 0 6px rgba(22,74,138,.3)';`,
    },
    {
      ad: 'KAYDIRMA DEGRADESİ (sağ kenarda mutlak katman)',
      secici: '[data-decoration="degrade"]',
      kur: `hedef.style.position = 'relative';
            const d = document.createElement('div');
            d.setAttribute('data-decoration', 'degrade');
            d.style.cssText = 'position:absolute;top:0;right:0;bottom:0;width:24px;' +
              'pointer-events:none;background:linear-gradient(90deg,transparent,#fff)';
            hedef.appendChild(d);`,
    },
    {
      ad: 'ETKİLEŞİMLİ PERDE (position:fixed, tüm ekran)',
      secici: '[data-decoration="perde"]',
      kur: `const d = document.createElement('div');
            d.setAttribute('data-decoration', 'perde');
            d.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.4);' +
              'pointer-events:auto;z-index:1200';
            hedef.appendChild(d);`,
    },
  ];

  for (const sus of YESIL_KALMALI) {
    test(`YEŞİL: ${sus.ad} kapıyı kırmızı YAPMAZ`, async ({page}) => {
      test.setTimeout(180_000);
      const surface = await acSayfayi(page);
      await surface.evaluate((node, kur) => {
        const hedef = (node.querySelector('[data-responsive-row]') ?? node) as HTMLElement;
        new Function('hedef', kur)(hedef);
      }, sus.kur);

      // BOŞA DÜŞME ÇAPASI: süsleme ölçüm anında sayfada OLMALI.
      expect(
        await page.locator(sus.secici).count(),
        `${sus.ad} sayfaya eklenemedi; yeşil hiçbir şey kanıtlamazdı`,
      ).toBeGreaterThan(0);

      const metrics = await expectSurfaceSettlesWithNoOverflow(
        surface, `ReceivablesAging ${sus.ad}`,
        DECLARED_DATA_SURFACES['receivables-aging-data-surface'],
      );
      expect(metrics.overflowingSurfaceCount, `${sus.ad} taşma sayılmamalı`).toBe(0);
      console.log(`MUTATION_GREEN ReceivablesAging süsleme/${sus.ad}`);
    });
  }
});

test.describe.serial('tohum İKİNCİ kez istendiğinde', () => {
  // NEDEN BU KAPI VAR. Playwright tekrar denemeyi YENİ bir işçi sürecinde
  // koşar; süreç belleği gider, sunucu ve veritabanı yerinde kalır. Bu yol
  // ölçülmeden bırakılmıştı ve dört dalda ASIL başarısızlığın üstüne okunmaz
  // bir hata yazdı. Burada işçi yeniden başlatması BİREBİR taklit edilemez —
  // ama tohumun İKİNCİ çağrısı veritabanına HİÇ dokunmadan aynı değeri
  // döndürmek zorunda, ki kalıcı belleğin ölçülebilir özelliği tam olarak budur.
  test('veritabanına dokunmadan AYNI tohumu döndürür', async () => {
    test.setTimeout(180_000);
    const birinci = await createSliceTwoSeed();

    // ASIL ÖZELLİK: tohum SÜRECİN DIŞINDA kayıtlı olmalı. Süreç içi bir
    // değişken de ikinci çağrıyı veritabanına götürmezdi — ama yeniden
    // başlatılan işçide yok olurdu. Bu çapa olmadan aşağıdaki kontroller eski
    // hâlde de yeşil kalır, yani hiçbir şey ölçmezdi.
    const kayit = JSON.parse(
      await readFile(join(TOHUM_DIZINI, 'slice-two.json'), 'utf8'),
    );
    expect(kayit, 'tohum işçiler arası paylaşılan dosyaya yazılmalı').toEqual(birinci);

    const admin = await adminApi();
    let oncekiSayi = 0;
    try {
      const once = await admin.api.get('/api/customers', {
        headers: await admin.headers(), params: {q: birinci.customerName},
      });
      await expectApiOk(once, 'tekrar tohumlama öncesi müşteri listesi');
      const govde = await once.json();
      oncekiSayi = ((Array.isArray(govde) ? govde : govde.items ?? []) as {name: string}[])
        .filter(musteri => musteri.name === birinci.customerName).length;
      expect(oncekiSayi, 'tohum tek bir müşteri yaratmalı').toBe(1);

      const ikinci = await createSliceTwoSeed();
      expect(ikinci, 'ikinci çağrı AYNI tohumu döndürmeli').toEqual(birinci);

      const sonra = await admin.api.get('/api/customers', {
        headers: await admin.headers(), params: {q: birinci.customerName},
      });
      await expectApiOk(sonra, 'tekrar tohumlama sonrası müşteri listesi');
      const govde2 = await sonra.json();
      const sonrakiSayi = ((Array.isArray(govde2) ? govde2 : govde2.items ?? []) as {name: string}[])
        .filter(musteri => musteri.name === birinci.customerName).length;
      expect(sonrakiSayi, 'ikinci çağrı veritabanına YAZMAMALI').toBe(oncekiSayi);
    } finally {
      await admin.dispose();
    }
    console.log('RESEED_OK sliceTwo ikinci çağrı yazmadı');
  });
});
