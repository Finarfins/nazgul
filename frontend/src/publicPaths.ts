/**
 * Kimlik doğrulama kabuğunun dışındaki rotalar — İKİ AYRI kavram.
 *
 * Bu ayrım bir inceleme turunda ortaya çıktı: tek bir "public" listesi iki
 * farklı soruyu birden yanıtlayamıyor.
 *
 *   1. "Bu rota izin haritası (ROUTE_PERMISSIONS) gerektirir mi?" -> PUBLIC_PATHS
 *   2. "Bu rota OTURUM gerektirmez mi?"                           -> ANONYMOUS_PATHS
 *
 * `/sifre-degistir` birinciye girer (AppShell dışıdır, izin haritasında yeri
 * yoktur) ama ikinciye GİRMEZ: `must_change_password` bayrağı olan, kimliği
 * doğrulanmış kullanıcı içindir. Oturum gerektirmeyen sayılırsa, oturumu biten
 * kullanıcı o ekranda takılı kalır — yönlendirme tetiklenmez, her istek 401
 * döner, kullanıcı parolasını değiştiremez ve giriş ekranına da atılmaz.
 * Klasik ölü ekran.
 *
 * Token'lı (anonim) parola SIFIRLAMA akışı bu ayrımın canlı örneğidir:
 * `/sifremi-unuttum` ve `/sifre-sifirla` ANONYMOUS_PATHS'e girer — şifresini
 * unutan kullanıcının tanımı gereği oturumu yoktur ve sıfırlama linki e-posta
 * istemcisinden, oturumsuz bir tarayıcıda açılır. `/sifre-degistir` yine
 * girmez: o, kimliği doğrulanmış kullanıcının zorunlu rotasyon ekranıdır.
 *
 * Liste bağımlılıksız bir yaprak modülde durur: `api.ts` (taşıma katmanı) bunu
 * okumak zorunda ve MUI/router yükleyen `navigation.tsx`'e bağımlı olamaz —
 * katman ihlali ve döngüsel içe aktarma riski doğardı.
 */

/** Oturum GEREKTİRMEYEN rotalar: anonim ziyaretçi buralarda kalabilir. */
export const ANONYMOUS_PATHS = [
  '/tanitim',
  '/giris',
  '/kayit',
  '/eposta-dogrula',
  '/sifremi-unuttum',
  '/sifre-sifirla',
] as const;

/**
 * Kimlik doğrulama kabuğunun ve izin haritasının dışındaki rotalar.
 *
 * ANONYMOUS_PATHS'in üst kümesidir; tek fark `/sifre-degistir`'dir: kabuk
 * dışıdır ama oturum ister. Türetilerek yazılır ki iki liste ayrışamasın.
 */
export const PUBLIC_PATHS = [...ANONYMOUS_PATHS, '/sifre-degistir'] as const;

/** Verilen yol oturum gerektirmiyor mu? (yönlendirme kararı bunu kullanır) */
export const isAnonymousPath = (pathname: string): boolean =>
  (ANONYMOUS_PATHS as readonly string[]).includes(pathname);
