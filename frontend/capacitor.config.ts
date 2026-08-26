import type {CapacitorConfig} from '@capacitor/cli';

/**
 * Capacitor: aynı web uygulamasını iOS/Android kabuğunda çalıştırır.
 *
 * `appId` MAĞAZA KİMLİĞİDİR ve bir kez yayınlandıktan sonra ASLA değişmez —
 * değiştirmek mağazada bambaşka bir uygulama demektir (indirmeler, yorumlar
 * sıfırlanır). Ters alan adı kuralıyla harmanzamani.com'dan türetildi.
 * Marka kararı sonrası SABİTLENDİ; yayından sonra dokunulmayacak.
 */
const config: CapacitorConfig = {
  appId: 'com.harmanzamani.app',
  appName: 'Harman Zamanı',
  webDir: 'dist',
  android: {
    // Kabuk https origin'inde çalışsın: karışık içerik (mixed content) uyarıları
    // çıkmasın ve güvenli bağlam gerektiren tarayıcı API'leri (kamera, konum,
    // servis çalışanı) kullanılabilir olsun.
    allowMixedContent: false,
  },
  server: {
    androidScheme: 'https',
  },
  plugins: {
    /**
     * OTURUM MOBİLDE NASIL ÇALIŞACAK — bu iki satır işin özü.
     *
     * Web tarafı oturumu ÜÇ çerezle tutuyor: erişim (15 dk), yenileme (14 gün)
     * ve JavaScript'in okuyabildiği CSRF çerezi (çift gönderim doğrulaması,
     * `api.ts` onu `X-CSRF-Token` başlığına koyuyor). Yenileme ucu da tokenı
     * YALNIZ çerezden okuyor.
     *
     * Kabuk içinde uygulamanın adresi `https://localhost`; sunucunun çerezleri
     * tarayıcı gözüyle "başka siteye ait" olur ve `SameSite=lax` yüzünden hiç
     * gönderilmez. Sonuç: giriş yapılır ama sonraki istek 401 döner.
     *
     * Çözüm: istekleri tarayıcı yerine CİHAZIN ağ katmanından geçirmek.
     * Native istemcinin kendi çerez kavanozu var; SameSite ve CORS tarayıcı
     * kuralları olduğu için devreye girmez. `CapacitorCookies` de
     * `document.cookie`'yi o kavanoza bağlar, böylece CSRF okuması çalışır.
     *
     * Bu yaklaşımın bedeli: sunucuda HİÇBİR değişiklik gerekmiyor, oturum
     * ömrü webdekiyle aynı kalıyor. Alternatifi (mobil için Bearer token yolu)
     * backend'de yenileme ucunu değiştirmeyi ve 14 günlük tokenı uygulama
     * deposunda tutmayı gerektirirdi.
     *
     * DOĞRULANDI (2026-08-07, Pixel 8 emülatörü, API 36). Ölçülen:
     *
     *   giriş                                200
     *   /auth/me (giriş sonrası)             200   → oturum taşınıyor
     *   document.cookie CSRF tokenını okur   evet  → çift gönderim çalışıyor
     *   POST, X-CSRF-Token ile               201
     *   POST, başlık olmadan                 403   → koruma gerçekten işliyor
     *   /auth/refresh                        200
     *   yenileme sonrası /auth/me            200
     *
     * Son satırdan önceki 403 kasıtlı bir kontroldü: yazma isteklerinin
     * kazara korumasız geçmediğini gösteriyor.
     *
     * HttpOnly çerezler `document.cookie`'de GÖRÜNMÜYOR (doğrusu bu), ama
     * /auth/me'nin 200 dönmesi native katmanın onları gönderdiğini kanıtlıyor.
     *
     * Sınamanın kapsamadığı tek fark: ölçüm emülatörden yerel backend'e
     * (http://10.0.2.2:5050) yapıldı, yani çerezlerde `Secure` bayrağı yoktu.
     * Asıl risk olan `SameSite=lax` ölçümde vardı ve sorun çıkarmadı; `Secure`
     * ise HTTPS altında zaten sağlanır. Plan B'ye (yenileme tokenını gövdede
     * vermek) gerek kalmadı, backend'de değişiklik YAPILMADI.
     */
    CapacitorHttp: {enabled: true},
    CapacitorCookies: {enabled: true},
  },
};

export default config;
