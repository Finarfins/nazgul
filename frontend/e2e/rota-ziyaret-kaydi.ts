// ROTA ZİYARET KAYDI — `spec` tasnifinin ÇALIŞMA ZAMANI kanıtı.
//
// ÖLÇÜLEN BOŞLUK. `spec` tasnifi iki şeyi kanıtlıyordu: G3 dosyanın ve test
// ADININ diskte var olduğunu, R3 o testin koşup GEÇTİĞİNİ. İkisi de doğruydu ve
// ikisi de rotayı hiç ölçmüyordu: rota ile test arasında ÇALIŞMA ZAMANI bağı
// yoktu. `/alislar` girdisini, gerçekten koşan ve gerçekten geçen ama yalnız
// `/` açan bir teste bağlamak iki kapıyı da yeşil bırakıyordu.
//
// KANIT: TARAYICININ KENDİ GEZİNTİ OLAYLARI. Her bağlama (context) bir başlangıç
// betiği kurulur; betik `history.pushState` / `history.replaceState` çağrılarını
// ve her belge yüklenişini Node tarafına bildirir. Test bittiğinde kayıt
// testInfo EKİ olarak iliştirilir; raportör onu okur ve envanterdeki rota
// deseniyle GERÇEK ziyaret arasında birebir eşleşme arar.
//
// NEDEN `framenavigated` DEĞİL. Playwright'ın olayı gezinmenin PUSH mu REPLACE
// mi olduğunu söylemez; aşağıdaki "olumsuz yönlendirme" ayrımı tam olarak buna
// dayanır ve tarayıcı API'sini kaynağında yakalamadan yapılamaz.
//
// OLUMSUZ YÖNLENDİRME KANIT SAYILMAZ — VE AYRIM ZAMANLAMAYA DAYANMAZ.
// `/nakit-yonetimi`ne `satis` rolüyle gitmeyi deneyen bir test o rotayı AÇMAZ:
// `Protected` içindeki `<Navigate to="/" replace/>` onu ana sayfaya düşürür. Bu
// düşüş React Router'da bir `replaceState`tir. Kural bu yüzden bir eşik ya da
// bekleme süresi değil, YAPISALDIR:
//
//   bir kayıt, KENDİSİNDEN SONRAKİ kayıt (aynı sekmede) farklı bir yola giden
//   bir `replace` İSE ve o iki kayıt arasında SAYFAYLA HİÇ ETKİLEŞİLMEMİŞSE
//   kalıcı değildir.
//
// `push` bir öncekini geçersiz kılmaz (kullanıcı ileri gitmiştir, önceki ziyaret
// gerçekten olmuştur); aynı yola yapılan `replace` de kılmaz (sorgu dizesi
// tazelemesi rotayı değiştirmez).
//
// ETKİLEŞİM KOŞULU ÖLÇÜLDÜ, EKLENDİ. İlk kural yalnız "sonraki kayıt bir
// replace mi" diye soruyordu ve GERÇEK bir ziyareti yanlışlıkla eledi:
// `session-lifecycle.spec.ts`teki zorunlu şifre rotasyonu testi
// `/sifre-degistir` ekranını açıyor, başlığını doğruluyor, ÜÇ alanı dolduruyor
// ve düğmeye basıyor; uygulama bunun ARDINDAN `replace` ile panele geçiyor. Bu
// bir "hedefe hiç varılmadı" değil, "işini bitirip çıktı"dır. Ayrımı taşıyan
// şey süre değil ETKİLEŞİMdir: olumsuz yönlendirmede sayfaya tek bir tuş ya da
// tıklama bile gitmez, gerçek ziyarette gider. Betik bu yüzden her kayda "bir
// önceki kayıttan bu yana kullanıcı girdisi oldu mu" bayrağını da iliştirir
// (`pointerdown` / `keydown` / `input` / `submit`, yakalama fazında).

import type {BrowserContext, TestInfo} from '@playwright/test';

/** Sayfaya açılan bildirim fonksiyonunun adı. */
export const ZIYARET_BINDING = '__rotaZiyaretiBildir';

/** testInfo ekinin adı — raportör kaydı bu adla arar. */
export const ZIYARET_EKI = 'rota-ziyaretleri';

export type ZiyaretKaynagi = 'yukleme' | 'push' | 'replace' | 'geri';

export interface ZiyaretKaydi {
  /** Kayıt hangi sekmede oluştu — sekmeler AYRI geçmişlerdir, ayrı çözümlenir. */
  readonly sekme: number;
  readonly yol: string;
  readonly kaynak: ZiyaretKaynagi;
  /** BİR ÖNCEKİ kayıttan bu yana sayfaya gerçek kullanıcı girdisi gitti mi. */
  readonly etkilesim: boolean;
}

/**
 * Sayfaya kurulan başlangıç betiği.
 *
 * ANA ÇERÇEVEYE SINIRLI: `addInitScript` her çerçevede koşar, ama rota kavramı
 * yalnız üst belgede vardır. Çapraz-köken bir çerçevede `window.top` okuması
 * fırlatabilir; o durumda da susulur.
 */
export const ZIYARET_INIT_BETIGI = `(() => {
  try { if (window.top !== window) return; } catch (_) { return; }
  // Bir önceki kayıttan bu yana sayfaya gerçek kullanıcı girdisi gitti mi.
  // Playwright'ın \`fill\`/\`click\` çağrıları gerçek olay üretir; \`evaluate\` üretmez
  // ve üretmemelidir — ölçülen şey KULLANICININ sayfayla temasıdır.
  let etkilesim = false;
  for (const olay of ['pointerdown', 'keydown', 'input', 'submit']) {
    window.addEventListener(olay, () => { etkilesim = true; }, true);
  }
  const bildir = (kaynak) => {
    const fn = window[${JSON.stringify(ZIYARET_BINDING)}];
    const vardi = etkilesim;
    etkilesim = false;
    if (typeof fn !== 'function') return;
    try { fn({yol: location.pathname, kaynak: kaynak, etkilesim: vardi}); } catch (_) { /* kapanan sayfa */ }
  };
  bildir('yukleme');
  const gercekPush = history.pushState;
  const gercekReplace = history.replaceState;
  history.pushState = function (...args) {
    const sonuc = gercekPush.apply(this, args);
    bildir('push');
    return sonuc;
  };
  history.replaceState = function (...args) {
    const sonuc = gercekReplace.apply(this, args);
    bildir('replace');
    return sonuc;
  };
  window.addEventListener('popstate', () => bildir('geri'));
})();`;

/**
 * Bir bağlamı kayıt tutar hâle getirir ve testin sonunda kaydı EKLER.
 *
 * `context` fixture'ında çağrılır: bağlamda AÇILAN HER sayfa (ikincil sekmeler
 * dahil) kayda girer, çünkü hem bildirim hem başlangıç betiği bağlam
 * seviyesindedir ve sayfalar yaratılmadan ÖNCE kurulur.
 */
export async function ziyaretKaydiniKur(
  context: BrowserContext,
  testInfo: TestInfo,
): Promise<() => Promise<void>> {
  const kayitlar: ZiyaretKaydi[] = [];
  const sekmeler = new Map<unknown, number>();

  await context.exposeBinding(
    ZIYARET_BINDING,
    (kaynak, veri: {yol: string; kaynak: ZiyaretKaynagi; etkilesim: boolean}) => {
      const sayfa = kaynak.page;
      if (!sekmeler.has(sayfa)) sekmeler.set(sayfa, sekmeler.size);
      kayitlar.push({
        sekme: sekmeler.get(sayfa) as number,
        yol: veri.yol,
        kaynak: veri.kaynak,
        etkilesim: Boolean(veri.etkilesim),
      });
    },
  );
  await context.addInitScript(ZIYARET_INIT_BETIGI);

  return async () => {
    await testInfo.attach(ZIYARET_EKI, {
      body: JSON.stringify(kayitlar),
      contentType: 'application/json',
    });
  };
}

/**
 * Bir kayıt dizisinden KALICI ziyaretleri süzer.
 *
 * Kural (bkz. dosya başlığı): bir kayıt, aynı sekmedeki BİR SONRAKİ kayıt farklı
 * bir yola giden bir `replace` İSE ve o kayıt arada hiçbir kullanıcı girdisi
 * görmediyse kalıcı değildir — yani rotaya varılmış ama sayfayla hiç
 * etkileşilmeden düşürülmüştür.
 */
export function kaliciZiyaretler(kayitlar: readonly ZiyaretKaydi[]): readonly ZiyaretKaydi[] {
  const kalici: ZiyaretKaydi[] = [];
  for (let i = 0; i < kayitlar.length; i += 1) {
    const kayit = kayitlar[i];
    const sonraki = kayitlar.slice(i + 1).find(aday => aday.sekme === kayit.sekme);
    const olumsuzYonlendirme =
      sonraki !== undefined &&
      sonraki.kaynak === 'replace' &&
      sonraki.yol !== kayit.yol &&
      !sonraki.etkilesim;
    if (olumsuzYonlendirme) continue;
    kalici.push(kayit);
  }
  return kalici;
}

/**
 * Gerçek bir URL yolunu envanterdeki rota DESENİNE indirger.
 *
 * `:id` rotaları için gereklidir: tarayıcı `/musteriler/7` gezer, envanter
 * `/musteriler/:id` bildirir. Eşleşme React Router'ın seçimini taklit eder —
 * segment sayısı tutmalı, sabit segmentler HARFİ HARFİNE tutmalı ve birden çok
 * aday varsa en AZ parametreli olan kazanır (`/depolar/:id` ile `/depolar/yeni`
 * ikisi de tanımlıysa sabit olan seçilir).
 *
 * Hiçbir desen tutmuyorsa `null` döner: uygulamanın bilmediği bir yoldur ve
 * kapsam kanıtı sayılmaz.
 */
export function yoluRotayaCoz(yol: string, desenler: readonly string[]): string | null {
  const parcala = (deger: string): string[] => deger.split('/').filter(parca => parca.length > 0);
  const hedef = parcala(yol);
  let enIyi: {desen: string; parametre: number} | null = null;
  for (const desen of desenler) {
    const parcalar = parcala(desen);
    if (parcalar.length !== hedef.length) continue;
    let parametre = 0;
    let tutuyor = true;
    for (let i = 0; i < parcalar.length; i += 1) {
      if (parcalar[i].startsWith(':')) {
        parametre += 1;
        // Boş bir segment parametreyi DOLDURMAZ.
        if (hedef[i].length === 0) tutuyor = false;
      } else if (parcalar[i] !== hedef[i]) {
        tutuyor = false;
      }
      if (!tutuyor) break;
    }
    if (!tutuyor) continue;
    if (enIyi === null || parametre < enIyi.parametre) enIyi = {desen, parametre};
  }
  return enIyi?.desen ?? null;
}
