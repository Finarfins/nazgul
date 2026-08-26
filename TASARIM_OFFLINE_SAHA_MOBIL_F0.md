# Offline Saha Mobil — Faz-0 Keşif ve Tasarım

Durum: **Faz-0 revize tasarım / son delta onayı bekliyor**

Tarih: 2026-07-28

Hedef: Teknisyenin zayıf veya olmayan bağlantıda, yalnız kendisine atanmış açık iş emirleri üzerinde güvenli çalışması

Uygulama başlangıç koşulu: Bu dokümanın iki reviewer turu tamamlanıp Berkay tarafından onaylanması

## 1. Yönetici özeti ve karar

V1 için ayrı native uygulama yerine mevcut React + Vite kod tabanına eklenen, dar kapsamlı bir **PWA saha modu** önerilir. Bu karar koşulludur: mevcut frontend bugün kurulabilir/offline çalışan bir PWA değildir. İki manifest dosyası bulunsa da HTML bunlardan hiçbirini bağlamıyor; uygulama service worker kaydetmek yerine eski worker'ları kaldırıyor ve tüm Cache Storage girdilerini temizliyor. Dolayısıyla M1, “mevcut PWA'yı etkinleştirme” değil, geçmişte yaşanan bayat bundle sorununu yeniden üretmeyecek kontrollü bir PWA temeli kurma işidir.

PWA yaklaşımının bu v1 için avantajları tek kod tabanı, mevcut kimlik/rol/API sözleşmelerinin yeniden kullanımı, hızlı dağıtım ve telefon/tablet tarayıcı desteğidir. Native uygulama ancak aşağıdaki gerçekler keşifte ortaya çıkarsa yeniden değerlendirilmelidir:

- uzun süreli ve çok büyük foto kuyruğu, agresif arka plan senkronu veya OS kontrollü güvenilir upload zorunluluğu;
- yönetilen cihaz, uzaktan silme, donanım anahtarı/sertifika gibi MDM gereksinimleri;
- tarayıcının desteklemediği özel kamera, Bluetooth/seri bağlantı veya cihaz entegrasyonu;
- hedef cihaz/tarayıcı matrisinde PWA depolama ve kamera davranışının kabul kriterlerini karşılamaması.

V1 veri sınırı kesindir: cihazda yalnız oturum açmış teknisyene, aktif firmada atanmış ve terminal olmayan iş emirlerinin saha görünümü tutulur. Tüm müşteri/makine/ürün kataloğu veya firmanın tüm iş emirleri cihaza indirilmez. Hedef cihaz matrisi **Android telefon + Android tablet + güncel Chrome/PWA** olarak sabittir; aynı teknisyen iki cihazı da kullanabilir.

## 2. Keşif bulguları

### 2.1 Frontend ve PWA hazırlığı

| Alan | Mevcut durum | Sonuç |
|---|---|---|
| Uygulama yığını | React 19, TypeScript, Vite 7 | PWA için uygun temel |
| PWA eklentisi | `vite-plugin-pwa` veya Workbox yok | Precache/versiyonlama/aktivasyon altyapısı kurulmalı |
| Manifest | `public/manifest.json` ve `public/manifest.webmanifest` var; ad, renk ve ikon kümeleri çelişiyor | Tek kanonik manifest seçilmeli |
| Manifest bağlantısı | `frontend/index.html` içinde `<link rel="manifest">` yok | Bugün installability sözleşmesi tamamlanmıyor |
| Service worker | `public/sw.js` cache yapan worker değil, eski worker'ı kaldıran kill-switch | Offline shell bugün yok |
| Kayıt davranışı | `main.tsx` bütün worker kayıtlarını unregister ediyor, bütün Cache Storage girdilerini siliyor | Yeni worker için kontrollü geçiş şart |
| Geçmiş risk | Eski network-first worker bayat `index.html` ve silinmiş hashed chunk üreterek uygulamayı bozmuş | HTML/chunk sürüm uyumu ve deterministik bridge rollout zorunlu |
| IndexedDB | Uygulama düzeyinde offline veri deposu/kuyruk yok | Yeni, şemalı ve kullanıcı/tenant bölümlü depo gerekli |
| Ağ davranışı | Axios timeout 15 sn; 401'de refresh ve başarısızsa login'e yönlendirme | Offline ile oturum bitimini ayıran hata sınıflaması gerekli |

Karar: M1'de Workbox tabanlı üretim güvenli bir worker (tercihen `vite-plugin-pwa` `injectManifest`) ve uygulamaya özel IndexedDB katmanı kurulmalıdır. Service worker yalnız uygulama kabuğu/statik asset sürümlemesini yönetir; iş emri API yanıtlarının yetkilendirme ve veri kapsamı uygulama kodundaki IndexedDB deposunda açıkça yönetilir. Genel amaçlı API runtime-cache kullanılmaz. Cache stratejisinin adı tek başına karar değildir; zorunlu olan HTML ile content-hash'li chunk'ların aynı sürüm ailesinden gelmesi ve geçmişteki bayat HTML/silinmiş chunk arızasının testlerle engellenmesidir.

### 2.2 Mobil ekranların mevcut durumu

Olumlu taraflar:

- `AppShell` `md` altında drawer'a geçiyor; ana içerik genişliği ve padding breakpoint'lere bağlı.
- İş emri listesi `ResponsiveTable` kullanıyor; `sm` altında DataGrid yerine kart gösteriyor.
- Liste filtreleri `xs` altında dikey diziliyor.
- Detay üst başlığı ve bilgi grid'i breakpoint kullanıyor.

Eksikler/riskler:

- İş emri detayındaki Stepper ve parça, işçilik, ek tabloları mobil kart görünümüne dönüşmüyor; yatay taşma ve küçük dokunma hedefleri saha kullanımında doğrulanmamış.
- Foto/imza aksiyonları masaüstü detay kartına gömülü; offline durum, yerel önizleme, kuyruk durumu ve başarısız upload UX'i yok.
- Sayaç okuma, mevcut makine detay API'sinde bulunmasına rağmen iş emri saha akışında birincil aksiyon değil.
- Detay sayfası ilk açılışta iş emri, parçalar, faturalama, işçilik ve ekler için çoklu canlı istek yapıyor. Offline görünüm için dar, tek amaçlı bir saha snapshot sözleşmesi daha güvenli ve daha küçüktür.
- Mevcut `sales` yazma izni teknisyen persona'sıyla eş anlamlı değildir. Mobil saha yetkileri ayrı ve en az ayrıcalıklı olmalıdır.

M1 kabulünden önce 360×640 ve 390×844 Android telefon ile en az 800×1280 Android tablet üzerinde güncel Chrome/PWA kontrolü gerekir. Normatif kapılar: her viewport'ta yatay sayfa scroll'u **0 px**, bütün birincil dokunma hedefleri en az **44×44 CSS px**, kesilen/üst üste binen birincil aksiyon sayısı **0** ve bağlantı/kuyruk durumu her saha ekranında görünürdür.

### 2.3 İlgili API'ler: replay, CAS ve tenant kapsamı

#### İş emri liste/detay

- Sorgular `company_id` ile tenant-scoped.
- Liste isteğe bağlı teknisyen metin filtresi kabul ediyor fakat oturumdaki kullanıcıyı zorunlu kapsam yapmıyor; varsayılan olarak firmadaki tüm iş emirlerini döndürüyor.
- “Yalnız bana atanmış açık işler” için istemcinin filtre göndermesine güvenilemez. Sunucu, `request.state.user.id` üzerinden kapsamı zorlamalıdır.
- Detay endpoint'i firmadaki herhangi bir iş emrini döndürebilir. Saha endpoint'i teknisyen atamasını da sunucu tarafında doğrulamalıdır; başka teknisyenin ID'sini tahmin ederek veri alınamamalıdır.

#### Durum geçişi

- `PATCH /api/work-orders/{id}/status` geçiş grafiğini doğruluyor.
- PostgreSQL'de satırı kilitliyor ve `UPDATE ... WHERE status=:current_status` ile atomik CAS uyguluyor; yarış kaybedilirse 409 döndürüyor.
- İstemci payload'ı yalnız hedef `status` içeriyor. Offline işlemin dayandığı `expected_status`/snapshot sürümü API sözleşmesinde yok.
- Endpoint `Idempotency-Key` işlemiyor. Aynı başarılı geçiş replay edilirse ikinci çağrı çoğunlukla 409 alır; bu, “ilk çağrı işlendi ama yanıt kayboldu” ile gerçek iş çakışmasını ayırt edemez.

Sonuç: M2 öncesi endpoint, zorunlu `Idempotency-Key`, request fingerprint ve replay edilmiş özgün sonucu döndüren tenant/user-scoped kayıt kazanmalıdır. Payload ayrıca zorunlu `expected_status` taşımalıdır. Sunucu mevcut durum `expected_status` değilse açıklayıcı 409 conflict zarfı dönmelidir.

#### Sayaç okuma

- Servis F1/#168 ile gelen `POST /api/machines/{machine_id}/hour-readings` sayaç için mevcut ve otoriter özel uçtur; genel `PUT /machines` offline sayaç yolu değildir.
- Uç append-only kayıt ekliyor ve `machines.working_hours` projeksiyonunu aynı transaction'da güncelliyor.
- Makine satırı kilidi aynı makinedeki yarışları serileştiriyor.
- İsteğe bağlı `work_order_id`, iş emrinin aynı firmada ve aynı makineye bağlı olduğunu doğruluyor.
- Mevcut sayaçtan düşük normal okumayı reddediyor; `meter_replacement` veya `correction` türünde açıklama/reason zorunluluğuyla düşüşe izin veriyor.
- Endpoint `Idempotency-Key` işlemiyor; aynı payload replay edilirse ikinci sayaç satırı oluşabilir.
- İş emrinin terminal/iptal durumu veya teknisyene atanmışlığı doğrulanmıyor. Örneğin offline iken iptal edilmiş iş emrine bağlı okuma bugün kabul edilebilir.
- Repoda `machine_idempotency` tablosu var, fakat bu yalnız makine oluşturma endpoint'i içindir; sayaç okumalarını korumaz.

Sonuç: M2 sayaç ayağı bu append-only ucu geriye uyumlu genişletir; ikinci bir sayaç yazma ucu veya `PUT /machines` yolu oluşturulmaz. Offline replay nedeniyle `Idempotency-Key`, fingerprint, `device_instance_id`, atama/durum ve açık version precondition'ları zorunludur. İptal/teslim edilmiş ya da artık teknisyene atanmamış iş emri 409 conflict olmalıdır. Daha yüksek güncel sayaç nedeniyle reddedilen normal okuma da kullanıcı çözümü isteyen conflict olarak sınıflanmalıdır.

#### Attachment upload

- `POST /api/work-order-attachments/{work_order_id}` multipart upload kabul ediyor.
- Endpoint terminal ve faturalanmış iş emirlerine yazmayı 409 ile reddediyor; tenant scope var.
- Sunucu stream ederek endpoint düzeyinde varsayılan 10 MiB limit uyguluyor; transport sınırı multipart payı için 1 MiB yüksek.
- İstemcinin bildirdiği MIME değerini metadata olarak saklıyor; içerik imzası/MIME allowlist'i uygulanmıyor. Mevcut UI `image/*,video/*,.pdf` kabul ediyor.
- Endpoint `Idempotency-Key` işlemiyor. Timeout sonrası tekrar yükleme iki dosya ve iki metadata satırı oluşturabilir.
- Dosya yazımı ve DB metadata transaction'ı arasında telafi temizliği var; fakat idempotent replay kaydı yok.

Sonuç: Foto M4'e ertelenmiştir. M4 öncesi foto endpoint'i zorunlu idempotency + içerik hash/fingerprint ile aynı sonucu replay etmelidir. V1 “foto” için sunucu tarafında JPEG/PNG/HEIC desteği ürün/cihaz matrisine göre açıkça belirlenmeli; video/PDF saha foto kapsamına dahil edilmemelidir. Mobil istemci upload öncesi boyutu küçültür, fakat sunucu 10 MiB otoritesini korur.

#### Parça kullanımı

- Parça ekleme stok azaltımıyla aynı transaction'da; tenant, stok ve mutable/unbilled kontrolleri var.
- Endpoint idempotent değil. Offline replay hem yinelenen satır hem yinelenen stok etkisi riski taşır; mevcut unique conflict her ürün/depo senaryosunu güvenli replay'e çevirmiyor.
- Ürün/depo seçimi bugün büyük canlı katalog isteklerine dayanıyor.

Sonuç: F3 iş kuralları gelmeden offline parça başlamaz. F3 hazırsa parça dilimi M3 operasyonel olgunlaşma fazına alınabilir; değilse ertelenir. Parça dilimi idempotency yanında stok kaynağı, teknisyen aracı/deposu, offline indirilecek dar parça kataloğu, fiyat görünürlüğü ve yetersiz stok conflict kurallarını F3 kararıyla alır.

### 2.4 Auth ve offline oturum

Mevcut tarayıcı auth modeli:

- access cookie: HttpOnly, varsayılan 15 dakika;
- rotating refresh cookie: HttpOnly, varsayılan 14 gün, yalnız `/api/auth` yolu;
- CSRF cookie: JavaScript tarafından okunabilir, refresh ömrüyle aynı;
- SPA açılışta `/auth/me` çağırır; hata olursa kullanıcıyı bellekte oturumsuz yapar;
- 401 alan API isteği refresh dener; refresh başarısızsa kullanıcıyı login'e yönlendirir;
- access/refresh token Web Storage'a yazılmaz.

Offline sonuç:

- Kullanıcı uygulamayı çevrimdışı yeniden açarsa `/auth/me` başarısız olur ve mevcut kod cache görünümüne izin vermez.
- HttpOnly cookie'nin varlığı/gerçek geçerliliği çevrimdışıyken JavaScript tarafından doğrulanamaz.
- 14 günlük refresh süresi “14 gün kesin offline çalışma yetkisi” değildir; kullanıcı pasife alınmış, rolü/firması değişmiş veya oturumu sunucuda iptal edilmiş olabilir.

Normatif dar politika:

1. Başarılı online `/auth/me` sonrasında `user_id`, `company_id`, izin özeti ve `last_online_verified_at` hassas token olmadan IndexedDB metadata'sında tutulur.
2. Çevrimdışı açılışta yalnız aynı tarayıcı profili için, son online doğrulamadan itibaren en fazla **12 saat** cache okunur. `last_online_verified_at` ve cihaz saati kullanıcı tarafından değiştirilebilir; bu süre bir güvenlik veya yetkilendirme garantisi değil, veri saklama/UX risk sınırıdır. Saat geriye alınırsa online doğrulama istenir.
3. Offline mod login değildir; “son doğrulanan oturumla sınırlı çevrimdışı görünüm”dür. Kullanıcı adı/şifre/token IndexedDB veya localStorage'a kopyalanmaz.
4. Offline yazılar kuyruklanabilir; sunucuya gönderilmeden başarı sayılmaz. Online dönüşte önce refresh/`me` ve atama yetkisi doğrulanır.
5. Refresh 401/403, kullanıcı/tenant değişimi veya cihazdaki aktif firma değişimi halinde sync durur. Kuyruk otomatik başka kullanıcıya gönderilmez; yerel veri kilitlenir ve yöneticili çözüm/temizleme akışı gerekir.
6. Logout online ise sunucu oturumu iptal edilir. Partition ancak bekleyen işlem yoksa doğrudan temizlenir. Bekleyen sayaç/foto varsa sayı ve veri kaybı etkisi gösteren ayrı, açık ve zorunlu onay gerekir; ürün sahibi export/amir kurtarma yolunu ilgili M2/M4 öncesinde belirler. Sıradan logout sessizce outbox/blob silmez. Offline logout da aynı veri-kaybı kapısına tabidir.

### 2.5 Kayıp cihaz risk kabulü

**Berkay kararı:** teknisyenler kendi kişisel Android telefon/tabletlerini kullanacaktır. MDM, uzaktan silme ve kurumsal cihaz garantisi yoktur. Ekran kilidi/PIN/biometrik kilit güçlü biçimde tavsiye edilir fakat uygulama bunu güvenilir biçimde zorlayamaz. İşveren; kayıp, çalıntı veya paylaşılan kişisel cihazda 12 saatlik pencere içinde dar saha verisinin görüntülenebilmesi kalan riskini **yazılı olarak kabul eder**. Bu kabul kriptografik koruma veya offline revocation varmış gibi yorumlanamaz.

Risk kabulüne rağmen aşağıdaki bedelsiz korumalar normatiftir ve kapsamdan çıkarılamaz:

- offline DTO sunucu taraflı allow-list kullanır; yalnız iş emri no/durum/version/öncelik/plan tarihi, gerekli servis metni, makine kimliği/marka/model/seri-şasi/son sayaç ve müşteri görünen adı bulunabilir;
- **müşteri telefon numarası offline DTO'ya ve IndexedDB'ye girmez**; gerekiyorsa yalnız online detay çağrısında gösterilir;
- finansal veri, bakiye, vergi bilgisi, tüm müşteri/makine/ürün katalogları ve diğer teknisyen verileri cihazda bulunmaz;
- access/refresh token, parola veya bearer credential hiçbir zaman Local Storage, Session Storage veya IndexedDB'ye yazılmaz;
- başarılı çıkışta kullanıcı+firma partition'ı temizlenir; crash recovery temizliği tamamlamadan cache görünür olmaz;
- offline yeniden doğrulama/retention penceresi 12 saattir.

Bu politika cihaz kaybına karşı kriptografik tam disk koruması sağlamaz ve offline iken sunucu taraflı kullanıcı iptalini uygulayamaz. Asıl azaltım veri minimizasyonu, 12 saatlik pencere, token saklamama ve çıkış temizliğidir.

## 3. Hedef mimari

### 3.1 Bileşen sınırları

1. **PWA shell**
   Statik uygulama kabuğunu content-hash'li precache ile saklar. Navigation için yalnız sürümle uyumlu shell kullanır; yeni sürüm geldiğinde kullanıcıya “Güncelleme hazır” gösterir. Aktif kuyruk varken zorla reload yapmaz.
2. **Saha API**
   Mevcut genel ERP endpoint'lerinden ayrı bir `/api/field/...` yüzeyi önerilir. Sunucu kimliğinden teknisyen ve aktif firmayı çıkarır; istemci `technician_id` seçemez. Dar DTO yalnız saha için gereken alanları taşır.
3. **IndexedDB**
   `meta`, `work_order_snapshots`, `outbox`, `photo_blobs`, `conflicts` object store'ları. Bütün anahtarlar en az `[company_id, user_id, ...]` ile bölümlenir.
4. **Sync coordinator**
   Uygulama açıkken online olayı, görünürlük dönüşü ve kullanıcı “Şimdi senkronize et” aksiyonuyla çalışır. Background Sync varsa optimizasyon olarak kullanılabilir; doğruluk buna bağlı olmaz.
5. **Server idempotency store**
   Her mutasyon için `(company_id, actor_user_id, device_instance_id, operation_type, idempotency_key)` kimliği, canonical request fingerprint, `IN_PROGRESS`/`SUCCEEDED` durumu ve response snapshot saklar. Aynı key+aynı fingerprint özgün başarıyı replay eder; aynı key+farklı fingerprint 409 döndürür. Claim kaydı ile iş mutasyonu aynı DB transaction'ında oluşturulup tamamlanır: rollback ikisini de geri alır. Eşzamanlı follower benzersiz anahtar yarışını kaybettiğinde winner transaction'ının sonucunu tekrar okuyup `SUCCEEDED` cevabını replay eder; henüz tamamlanmadıysa kısa kontrollü bekleme/409 `IDEMPOTENCY_IN_PROGRESS` + `Retry-After` sözleşmesi kullanır. Uzun ömürlü `IN_PROGRESS` kaydı yalnız dış kaynak hazırlığı gerektiren foto protokolünde görülür ve lease/created-at tabanlı, audit edilen recovery ile ele alınır; istemci yeni key üretmez.
6. **Client/DB version coordinator**
   `BroadcastChannel` (fallback: `storage` event) yalnız **aynı cihazdaki aynı Chrome profili/origin içindeki sekmeleri** koordine eder; farklı telefon/tablet arasında haberleşmez. Aynı profil içinde sekmeler bir leader seçer. Yalnız leader sync ve IndexedDB upgrade başlatır. Diğer sekmeler `versionchange` olayında bağlantılarını kapatır, yazmayı durdurur ve “güncelleme için bu sekmeyi yenileyin” durumu gösterir.
7. **Device instance identity**
   İlk başarılı kurulumda `crypto.randomUUID()` ile kalıcı, sır olmayan bir `device_instance_id` üretilir. Kurulum/profil başına değişmez; logout'ta silinmez, uygulama verisi kaldırılırsa yeni değer oluşur. Her outbox, idempotency ve audit kaydı bu kimliği taşır. Kimlik auth credential değildir ve tek başına yetki vermez.

### 3.2 Cihaza indirilecek veri

Yalnız aşağıdakiler:

- oturumdaki kullanıcı ve aktif firma için minimal offline metadata;
- `technician_id == authenticated_user.id`;
- durum `DELIVERED` ve `CANCELLED` olmayan açık/planlı iş emirleri;
- allow-list saha DTO'su: iş emri ID/no, durum/sürüm, öncelik, planlanan tarih, şikayet, teşhis/yapılan işlem/teknisyen notu için gerekli metin, makine ID/marka/model/seri/şasi ve son sayaç, müşterinin yalnız görünen adı ve gerekli servis adresi;
- aynı işte diğer cihazlardan gönderilmiş/bekleyen mutasyonları kişisel veri taşımadan özetleyen `other_device_activity` (`has_activity`, işlem türleri, son zaman, server-known durum);
- mevcut eklerin yalnız metadata/thumbnail'ı, o da saha ihtiyacı onaylanırsa;
- F3 hazır olup M3 parça dilimi açılırsa yalnız teknisyenin yetkili deposu/aracı için dar ürün kümesi.

İndirilmeyecekler:

- tüm müşteri listesi, müşteri telefon numarası, bakiyeler, finansal veriler, vergi bilgileri;
- tüm makineler ve servis geçmişinin tamamı;
- diğer teknisyenlerin iş emirleri;
- firma kullanıcı listesi;
- tam ürün kataloğu ve tüm depo stokları;
- faturalar, tahsilat veya maliyet/fiyat verisi (F3 açıkça istemedikçe).

Sunucu snapshot cevabı `snapshot_version`, `generated_at`, `user_id`, `company_id` ve her iş için `version`/`updated_at` taşır. Yeni snapshot uygulanırken artık atanmayan/terminal olan işlerin yerel kaydı kaldırılır; fakat onlara bağlı bekleyen işlem varsa kayıt “erişim/iş akışı çakışması” olarak karantinaya alınır ve kullanıcıya gösterilir.

Snapshot uygulama protokolü:

1. Yanıt önce bellek içinde şema, `user_id`, `company_id`, bütünlük ve monoton `snapshot_version` açısından doğrulanır.
2. Eski veya out-of-order cevap (`incoming_version <= committed_version`) uygulanmaz.
3. `work_order_snapshots` upsert'leri, artık kapsamda olmayan kayıtların silinmesi/karantinaya alınması ve `meta.snapshot_version` güncellemesi **tek IndexedDB transaction** içinde yapılır.
4. Parse/validation/transaction/application crash hatasında transaction abort olur; son sağlam snapshot değişmeden kalır.
5. Pending outbox'a bağlı kayıt silinmez; aynı transaction'da conflict karantinasına taşınır.
6. Sunucu tam snapshot yerine delta sunarsa delta base-version eşleşmesi zorunludur; eşleşmezse tam snapshot alınır.

### 3.3 IndexedDB veri modeli

Önerilen mantıksal şema:

| Store | Temel alanlar | Saklama |
|---|---|---|
| `meta` | partition, schema_version, device_instance_id, last_online_verified_at, last_sync_at, snapshot_version | device ID hariç logout/expiry'ye kadar |
| `work_order_snapshots` | partition, work_order_id, server_version, payload, cached_at | açık/atanmış kaldığı sürece |
| `outbox` | operation_id, idempotency_key, device_instance_id, type, aggregate_id, expected_version/status, payload, created_at, state, attempts, next_attempt_at | başarı/manuel silmeye kadar |
| `photo_blobs` | operation_id, blob, mime, bytes, sha256, local_preview | upload başarısına kadar |
| `conflicts` | operation_id, server_code/message, server_state_summary, conflicted_at | kullanıcı çözene kadar |

`operation_id` ve `idempotency_key` cihazda `crypto.randomUUID()` ile bir kez üretilir ve hiçbir retry'da değiştirilmez. Payload fingerprint sunucuda canonical JSON veya foto için metadata+SHA-256 üzerinden hesaplanır.

IndexedDB sürüm yükseltme protokolü:

- Şema forward-compatible bir geçiş penceresi taşır; yeni alanlar önce additive/optional eklenir. Eski client'ın okuyamayacağı destructive değişiklik aynı release'te yapılmaz.
- Leader upgrade öncesi tüm sekmelere `prepare-upgrade` yayınlar; sekmeler yeni yazı kabul etmeyi bırakır ve DB connection'ını kapatır. Kapanmayan sekme varsa upgrade bloke edilir, zorla veri dönüşümü yapılmaz.
- Upgrade transaction'ı tek sahipli ve atomiktir. Hata/OS kill halinde tarayıcı eski şemayı korur; uygulama `migration_failed` ekranıyla salt-okuma kurtarma/yeniden deneme sunar.
- Dolu outbox upgrade sırasında silinmez veya yeniden key üretmez. Gerekli payload dönüşümü yeni store'a kopyala-doğrula-swap mantığıyla aynı versionchange transaction'ında yapılır.
- Yeni JS, beklenen DB şemasından eski/yeni uyumsuzluk görürse sync başlatmaz. Eski sekme yeni şemaya yazamaz; `versionchange` sonrası bağlantısı kapanır.
- Service worker aktivasyonu, DB migration başarı sinyalinden sonra reload ister. Çok sekmeli durumda bütün client'lar hazır olmadan otomatik aktivasyon yoktur.

### 3.4 Aynı teknisyenin telefon + tablet sözleşmesi

- M2'den itibaren aynı teknisyen hem Android telefonundan hem Android tabletinden yazabilir; tek “ana cihaz” yoktur.
- Her istek `device_instance_id` taşır. Sunucu bu değeri authenticated actor ile birlikte audit ve idempotency kayıtlarına yazar; istemcinin başka kullanıcı adına cihaz kimliği seçmesine izin vermez.
- İki cihaz aynı iş emri için bağımsız operation/idempotency key üretir. Bir cihazın key'i diğer cihazda yeniden kullanılmaz.
- Sunucu aynı `company_id + technician_id + work_order_id + normalized reading_hours` için daha önce başka `device_instance_id` kaynaklı bekleyen veya gönderilmiş okuma bulursa bunu otomatik başarı veya sessiz drop yapmaz. `DUPLICATE_READING_ADVISORY` conflict'i, eşleşen server kaydının kimliksiz özetini ve kaynak cihazın yalnız maskeli etiketini döndürür.
- Field snapshot/sync cevabı iş emri başına `other_device_activity` özeti taşır. UI normatif metni: **“Bu işte başka cihazdan bekleyen/gönderilmiş işlem var.”**
- Aynı okuma gerçekten yeniden girilecekse kullanıcı önce sunucu özetini görür; yeni/değiştirilmiş değer yeni operation+key ile oluşturulur. Teknisyen rolünde duplicate'i zorla kabul ettirme yoktur.
- `BroadcastChannel`, leader seçimi ve IndexedDB lock'ları yalnız aynı Chrome profili içindir; telefon ile tablet arasındaki koordinasyonun tek otoritesi sunucudur.

Kota politikası:

- Kuyruğa alınmadan önce `navigator.storage.estimate()` kontrol edilir.
- Foto için istemci hedefi, ürün kararı sonrası kesinleşmek üzere, JPEG'de uzun kenar yaklaşık 1920 px ve hedef en fazla 2 MiB önerisidir.
- Sunucu tek dosya üst sınırı mevcut 10 MiB olarak kalır.
- Başlangıç önerisi: iş emri başına en fazla 10 bekleyen foto, cihazda toplam en fazla 100 MiB offline foto. Beklenen gerçek adet/boyut bilgisiyle ayarlanmalıdır.
- Kota yetersizse foto “kaydedildi” gösterilmez; net hata ve alan açma önerisi verilir.

### 3.5 Senkron kuyruğu ve sıralama

Outbox durumları: `pending`, `sending`, `retry_wait`, `conflict`, `auth_blocked`, `succeeded`.

Kurallar:

1. Kullanıcı aksiyonu önce IndexedDB transaction'ında outbox'a yazılır; UI ancak bundan sonra “Cihazda kaydedildi, gönderilmeyi bekliyor” gösterir.
2. Aynı iş emrine ait işlemler oluşturulma sırasıyla yürür. Farklı iş emirleri v1'de de basitlik için seri yürütülebilir.
3. Bağlantı geldiğinde önce auth/tenant doğrulaması ve güncel saha snapshot'ı alınır.
4. `pending` işlem aynı idempotency key ile gönderilir.
5. 2xx veya idempotent replay cevabı `succeeded` olur; ilgili snapshot sunucu cevabıyla yenilenir.
6. Ağ hatası, timeout, 408, 429 ve 5xx retryable'dır. Exponential backoff+jitter uygulanır; `Retry-After` varsa uyulur. Öneri: 5 sn, 15 sn, 1 dk, 5 dk, sonra 15 dk tavan; uygulama açıkken kullanıcı manuel retry yapabilir.
7. 400/403/404/409/422 otomatik sonsuz retry edilmez. Sınıflandırılmış conflict veya kalıcı hata olarak kullanıcıya çıkar.
8. 401'de bir refresh denenir. Başarısızsa tüm sync `auth_blocked` olur; payload başka hesaba taşınmaz.
9. Bir iş emrindeki conflict, o iş emrine bağlı sonraki işlemleri bloke eder. Diğer iş emirleri devam edebilir.
10. Başarılı outbox metadata'sı kısa tanı süresi (ör. 7 gün) tutulup payload/blob temizlenir; audit otoritesi sunucudur.
11. Uygulama açılışında tek leader recovery taraması yapar: eski `sending` kayıtları aynı idempotency key korunarak `reconcile` durumuna alınır; önce sunucu replay/status sözleşmesiyle sonuç sorgulanır, körlemesine yeni işlem oluşturulmaz.

Service worker içinden cookie+CSRF ile mutasyon yapmak zorunlu tasarım değildir. V1 foreground sync kullanır. Böylece auth refresh, conflict UX'i ve IndexedDB migration kontrolü uygulama kodunda tek yerde kalır.

### 3.6 Service worker geçiş protokolü

**Seçim: iki dağıtımlı bridge rollout zorunludur.** Tek dağıtım reddedilmiştir; çünkü eski bundle `getRegistrations()` ile bütün registration'ları unregister ediyor ve `caches.keys()` ile origin'deki bütün cache'leri siliyor. Yalnız yeni isim seçmek, henüz açık eski bundle'ın bu global temizliğinden korunmaya yetmez.

İsim uzayı sözleşmesi:

- legacy script/registration: `/sw.js`, legacy cache allow-list'i yalnız `yhp-shell-v1` ve bilinen eski adlar;
- yeni saha başlangıç yolu/manifest `start_url`: `/saha/`;
- yeni script: `/field-pwa/sw-v1.js`;
- yeni registration scope: `/saha/` (sunucu yalnız bu script için `Service-Worker-Allowed: /saha/` verir);
- yeni cache prefix: `sungur-field-pwa-v1:`; her release bunun altında immutable manifest hash'i taşır;
- bridge ve yeni kod hiçbir zaman `getRegistrations()`/`caches.keys()` sonucunu topluca silmez; yalnız exact legacy script URL/scope ve exact legacy cache allow-list'ine dokunur. Böylece **bridge build'den itibaren** yeni registration/cache isim uzayına dokunamama kodla garanti edilir.

Dağıtım 1 — bridge:

1. Yeni PWA worker kaydedilmez.
2. `main.tsx` global unregister/cache-delete davranışı kaldırılır ve yalnız exact legacy allow-list cleanup'ına daraltılır.
3. `/sw.js` yeni kayıt üretmez; mevcut kill-switch'in tamamlanması beklenir.
4. Bridge build kimliği client telemetry'de görünür. B2 açılmadan önce desteklenen cihaz matrisinde bridge'in yüklendiği, legacy registration/cache'in sıfırlandığı ve global cleanup çağrısının kalmadığı doğrulanır. Tarihî bundle global silme yaptığı için salt isim uzayıyla mutlak koruma mümkün değildir; B2'yi bridge kapısından önce açmama bu nedenle normatif dağıtım garantisidir.

Dağıtım 2 — PWA:

1. Uygulama yalnız `/field-pwa/sw-v1.js` script'ini `/saha/` scope ile kaydeder. Bridge sonrası exact legacy cleanup allow-list'i bu script URL'si, scope veya `sungur-field-pwa-v1:` prefix'iyle eşleşemez; yeni isim uzayına dokunmama otomatik test ile pinlenir.
2. Worker install sırasında release manifest'indeki her shell dosyasını geçici `sungur-field-pwa-v1:staging:<manifest-hash>` cache'ine indirir ve hash/öğe sayısını doğrular.
3. **Precache tam değilse install reject edilir; worker active/ready sayılmaz ve staging cache temizlenir.**
4. Tam precache atomik pointer/metadata değişimiyle `ready:<manifest-hash>` olarak ilan edilir. Fetch yalnız ready manifest'i kullanır; HTML ile chunk aynı manifestten gelir.
5. Yeni registrar, legacy cleanup'ın tek-seferlik geç çalışmasına karşı registration ve ready-precache'i yeniden doğrular. Silinmişse aynı script/scope'u yeniden kaydeder ve precache tamlığını yeniden kurar. Ready sinyali alınmadan UI offline-hazır göstermez.
6. Aynı scope'ta waiting/active fazları olabilir ancak settle sonunda tek active registration kalır. Outbox doluyken otomatik `skipWaiting`/reload yoktur.
7. Reload guard release manifest hash'iyle tutulur; aynı hash için en fazla bir reload yapılır.

Zorunlu eşzamanlı fixture: eski sekme (eski bundle'ın global cleanup kodu) + legacy kill-switch + bridge'i geçmiş yeni sekme aynı origin'de eşzamanlı çalıştırılır. Eski cleanup promise'lerinin altı yarış noktası ayrı ayrı geciktirilir:

1. yeni register'dan önce unregister;
2. register ile install arasında unregister;
3. precache yazılırken cache delete;
4. install tamamlandıktan sonra cache delete;
5. activate ile client claim arasında unregister;
6. ready sinyalinden hemen sonra legacy cleanup.

Her yarış noktası için ölçülebilir kabul: settle/reconciliation sonunda **1** aktif `/field-pwa/sw-v1.js` registration, manifestteki beklenen öğe sayısı/hash ile **%100** ready precache, network/log içinde silinmiş hashed chunk isteği **0**, error boundary **0**, aynı manifest için reload sayısı **≤1**. Bu kapılardan biri sağlanmazsa M1 rollout no-go'dur.

### 3.7 Çakışma ve hata stratejisi

Sunucu otoritedir; sessiz last-write-wins yasaktır.

Önerilen standart conflict cevabı:

```json
{
  "detail": {
    "code": "WORK_ORDER_STATUS_CONFLICT",
    "message": "İş emri siz çevrimdışıyken iptal edildi; sayaç okuması gönderilmedi.",
    "operation_id": "uuid",
    "server": {
      "work_order_id": 123,
      "status": "CANCELLED",
      "version": 9,
      "updated_at": "2026-07-28T10:15:00Z"
    }
  }
}
```

Normatif conflict çözüm matrisi:

| Sunucu durumu | Conflict code | Teknisyene izin verilen aksiyonlar | Yasak |
|---|---|---|---|
| İş emri iptal edilmiş | `WORK_ORDER_CANCELLED` | Sunucu özetini görüntüle; yerel conflict'i **yalnız cihazdan sil** | Retry, yeniden gönder, zorla gönder |
| Başka teknisyene atanmış | `WORK_ORDER_REASSIGNED` | Sunucu özetini görüntüle; yerel conflict'i **yalnız cihazdan sil** | Veriyi yeni teknisyen adına gönderme, retry, zorla gönder |
| Durum ilerlemiş/değişmiş | `WORK_ORDER_STATUS_ADVANCED` | Güncel snapshot'ı al; kullanıcı hâlâ yetkiliyse güncel durumdan geçerli yeni aksiyon seçerek **yeni operation + yeni key** oluştur | Stale payload retry, eski key ile değiştirilmiş payload |
| Sunucu sayacı daha yüksek veya aynı okuma başka cihazda var | `METER_READING_HIGHER` / `DUPLICATE_READING_ADVISORY` | Güncel sayacı/diğer cihaz özetini gör; gerçek yeni okuma girerse **yeni operation + yeni key**; yetkili correction/meter replacement akışını online başlat | Eski normal okumayı retry, teknisyen rolünde zorla kabul |
| Yetki/üyelik kaldırılmış | `FIELD_PERMISSION_REVOKED` | Oturumu doğrula; yerel conflict özetini görüntüle ve **yalnız cihazdan sil**; yönetici/destek ile iletişim | Sync, retry, export ile başka hesaba aktarma, zorla gönder |

Matristeki **retry**, stale payload'ı hiçbir koşulda yeniden göndermez. Retry yalnız ağ/408/429/5xx gibi henüz conflict olmayan aynı operation'ın aynı payload+aynı idempotency key ile transport tekrarını ifade eder. Kullanıcı bir değeri değiştirirse bu yeni operation ve yeni idempotency key'dir; eski conflict audit kaydı sunucuda retention süresi boyunca kalır ve yeni operation `supersedes_operation_id` ile ona bağlanır. UI'daki **Sil**, yalnız yerel outbox/conflict/blob kopyasını siler; sunucu audit/kayıtlarını silmez ve bu etki onay metninde açıkça yazılır. Teknisyen rolünde **Zorla gönder** aksiyonu hiçbir conflict için yoktur.

Aynı idempotency key'in ilk isteği işlenip cevabı kaybolmuşsa sunucu aynı başarı cevabını replay eder; bu conflict değildir.

### 3.8 Foto akışı

1. Kamera/dosya seçildikten sonra desteklenen format, boyut ve kota kontrol edilir.
2. Gerekirse istemcide yön/orientation korunarak küçültme yapılır; orijinalin saklanıp saklanmayacağı ürün kararıdır. V1 cihaz riskini azaltmak için yalnız upload edilecek küçültülmüş kopyayı saklamayı önerir.
3. Blob ve outbox metadata aynı mantıksal işlemle kaydedilir; yerel thumbnail gösterilir.
4. Senkron sırasında SHA-256, idempotency key, work order expected status/version ve multipart dosya gönderilir.
5. Sunucu byte ve piksel/decompression limitlerini doğrular; SVG'yi reddeder; allowlist'teki raster formatı güvenli decoder ile açıp yeniden encode eder veya doğrulanmış canonical dosya üretir. Thumbnail üretimi kaynak limitli/izole yürür.
6. Dosya DB transaction'ına katılamadığı için upload önce uygulamaya ait geçici dizine stream edilir. Hash doğrulandıktan sonra DB idempotency claim+attachment metadata aynı transaction'da tamamlanır; commit sonrası temp dosya aynı filesystem içinde atomik rename ile final konuma alınır. Rename/commit arası crash için deterministik recovery/cleanup işi ve `PENDING_FILE` durumu gerekir; metadata final dosya hazır olmadan indirilebilir sayılmaz.
7. Timeout aynı key ile retry edilir. Aynı key/hash mevcut `PENDING_FILE` veya `SUCCEEDED` kaydını tamamlar/replay eder; ikinci attachment üretmez. Başarıdan sonra cihaz blob'u silinir, sunucu attachment metadata'sı snapshot'a eklenir.
8. Download cevabı `Content-Disposition: attachment` ve `X-Content-Type-Options: nosniff` taşır; inline aktif içerik servis edilmez.
9. Kullanıcı bekleyen fotoğrafı göndermeden silebilir; bu yerel ve geri alınamaz bir işlemdir. `sending` durumunda silme önce gönderimi iptal etmeye çalışır; sunucu sonucu belirsizse aynı key ile durum netleştirilmeden yeni upload üretilmez.

Cihaz tarafı foto state machine:

`capturing → processing → ready(pending) → sending → reconcile → succeeded | conflict`

- Kamera sonucu işlenirken uygulama kapanırsa henüz “cihazda kaydedildi” denmez; geçici işlem alanı açılışta temizlenir.
- Sıkıştırılmış blob, hash, outbox payload ve `ready` durumu **tek read-write IndexedDB transaction** içinde commit edilir. Commit öncesi kill hiçbir kayıt bırakmaz; commit sonrası kill tam blob+outbox bırakır.
- Açılış recovery'si orphan blob/outbox tarar. Referanssız blob güvenli süre sonunda silinir; blob'u eksik outbox `local_corrupt` conflict olur ve gönderilmez.
- Eski `sending` kaydı `reconcile` olur; aynı key/hash ile sunucu sonucu sorgulanır/replay edilir. Yeni key üretilmez.
- Blob okunurken byte uzunluğu ve SHA-256 yeniden doğrulanır; bozuk blob upload edilmez.

Sunucu tarafı foto state machine:

`RECEIVING → STAGED → PENDING_FILE → SUCCEEDED | FAILED`

1. Upload, idempotency key+hash ile deterministik uygulama temp yoluna stream edilir; `RECEIVING/STAGED` lease'i aynı-key follower'ı koordine eder.
2. Hash/format doğrulamasından sonra DB transaction'ı idempotency fingerprint'ini ve görünmez attachment metadata'sını `PENDING_FILE` olarak commit eder. Bu aşamada 2xx başarı dönmez.
3. Temp dosya aynı filesystem'de final yola atomik rename edilir ve final hash/varlık doğrulanır.
4. İkinci kısa DB transaction'ı attachment ve idempotency kaydını `SUCCEEDED` yapıp response snapshot'ı yazar. Yalnız bundan sonra 2xx döner.
5. Adım 2 sonrası crash'te recovery worker `PENDING_FILE` kaydını ve deterministik temp/final yollarını inceler: final doğruysa `SUCCEEDED`; temp doğruysa rename+finalize; ikisi de yok/bozuksa `FAILED` ve aynı-key retry'ın yeniden stage etmesine izin veren açık hata sonucu.
6. Kalıcı rename/IO hatası lease sonunda `FAILED` olur, metadata liste/download'da görünmez ve temp retention işiyle temizlenir. Aynı key+aynı hash recovery'yi sürdürür; farklı hash 409 alır.
7. Crash matrisi her adımda DB state, temp/final dosya sayısı, görünür attachment sayısı ve aynı-key retry cevabını sabitler.

### 3.9 UI durumu

Uygulama kabuğunda her zaman görünür tek bir durum bileşeni:

- yeşil: `Çevrimiçi · Tümü senkronize`;
- turuncu: `Çevrimdışı · 3 işlem bekliyor`;
- mavi/dönen: `Senkronize ediliyor · 1/3`;
- kırmızı: `1 çakışma · İncele`;
- gri/kilit: `Oturum doğrulanmalı · Senkron durdu`.

Sayaç yalnız `pending` değil `retry_wait`, `conflict` ve `auth_blocked` sayılarını ayrıştırır. `navigator.onLine` tek başına otorite değildir; API health/auth denemesi gerçek bağlantı durumunu belirler. Her saha kaydında “son sunucu güncellemesi” ve yerel bekleyen değişiklik rozeti gösterilir.

## 4. Fazlama ve kabul kapıları

### M1 — Salt-okuma offline

Kapsam:

- güvenli PWA shell, kanonik manifest ve installability;
- sunucu-zorlamalı “bana atanmış terminal olmayan işler” saha snapshot API'si;
- minimal IndexedDB snapshot ve offline auth doğrulama penceresi;
- mobil liste/detay, offline/last-sync göstergesi;
- kullanıcı/tenant değişimi ve logout'ta partition temizliği;
- iki dağıtımlı bridge ile kill-switch'ten deterministik geçiş;
- atomik snapshot apply ve çok-sekmeli IndexedDB migration/version koordinasyonu.

Kabul:

- ilk online sync sonrası uçak modunda cold reload ile liste/detay açılır;
- API/IndexedDB partition taramasında başka teknisyen/firma kaydı **0**;
- Android telefon/tablet hedef matrisinde yatay sayfa scroll'u **0 px**, 44×44 px altı birincil dokunma hedefi **0**, kesilen birincil aksiyon **0**;
- altı kill-switch yarış noktasının her birinde tek aktif yeni registration **1**, ready precache tamlığı **%100**, silinmiş chunk isteği **0**, reload **≤1**;
- global cache temizliği yoktur; yalnız uygulama prefix'leri yönetilir ve dolu outbox otomatik reload'u engeller;
- kesik/bozuk snapshot, application kill ve out-of-order cevap son sağlam snapshot'ı değiştirmez;
- eski client+yeni client+bekleyen sentetik outbox upgrade fixture'ında tek migration sahibi vardır, eski sekme yazamaz ve outbox korunur;
- 12 saat dolduğunda açık ekran ve cold start cache'i göstermez; cihaz saati geriye giderse online doğrulama ister;
- başarılı online logout ve logout-cleanup crash fixture'ı sonrasında eski cache görünürlüğü **0 kayıt/0 ekran**; `revoked_locally_at` temizleme tamamlanana kadar fail-closed kalır;
- access token/refresh token IndexedDB/localStorage'da bulunmaz;
- M1 hiçbir offline mutasyon sunmaz.

M1 sonunda build otomatik M2'ye geçmez. Android telefon + tablet hedef matrisinde sınırlı pilot çalışır ve en az **5 iş günü** gözlem kapısı uygulanır. Pilot go kapıları: cache/tenant veri sızıntısı **0**, silinmiş chunk/error-boundary olayı **0**, logout sonrası görünür kayıt **0**, başarısız cold-start oranı **0**, kullanıcı başına son başarılı snapshot yaşı çalışma saatlerinde **≤12 saat**. Herhangi bir güvenlik/partition ihlali pilotu durdurur.

### M2 — Yazma kuyruğu: durum + sayaç

Kapsam:

- ortak sunucu idempotency sözleşmesi ve migration;
- `expected_status`/version tabanlı açık CAS;
- sayaç için atama, terminal durum ve güncel sayaç conflict kontrolleri;
- kalıcı `device_instance_id`, cross-device activity özeti ve duplicate reading advisory;
- foreground sync coordinator, retry/backoff, conflict inbox;
- “çevrimdışı — N işlem bekliyor” UI.

Kabul:

- aynı key 10 kez replay edildiğinde tek yan etki ve aynı sonuç;
- eşzamanlı aynı-key yarışı ile timeout/rollback testlerinde claim ve iş mutasyonu ayrışmaz; stale/in-progress sonucu belirsiz kalmaz;
- aynı key farklı payload ile 409;
- cevap kaybı simülasyonunda duplicate durum/sayaç kaydı yok;
- iş iptali/yeniden atama/sayaç ilerlemesi kullanıcıya conflict olur, sessiz ezme yok;
- conflict çözüm matrisindeki her code için izin verilen/yasak aksiyonlar UI ve API testlerinde birebir korunur; stale payload yeniden gönderim sayısı **0**, teknisyen “zorla gönder” aksiyonu **0**;
- iki tenant ve iki kullanıcı arasında outbox karışmaz;
- aynı teknisyenin **iki gerçek Android cihazında** (telefon+tablet; iki sekme kabul edilmez) aynı iş/sayaç senaryosu `DUPLICATE_READING_ADVISORY` üretir, iki audit kaydı doğru `device_instance_id` taşır ve UI diğer cihaz özetini gösterir;
- PostgreSQL concurrency ve SQLite parity testleri geçer.

M2 endpoint transaction sözleşmesi:

| Sonuç | Transaction sırası | İdempotency kaydı | Retry |
|---|---|---|---|
| Geçerli yeni istek | claim/fingerprint → lock aggregate → precondition → mutation+audit → response snapshot → commit | `SUCCEEDED` aynı transaction | Aynı 2xx replay |
| Aynı key+aynı payload, başarılı | mevcut sonucu oku | değişmez | Aynı 2xx replay |
| Aynı key+farklı payload | fingerprint karşılaştır | değişmez | 409 `IDEMPOTENCY_MISMATCH` |
| Validation/403/404, mutasyon öncesi | auth/temel validation claim'den önce | saklanmaz | Güncel doğrulama yeniden çalışır |
| Aggregate precondition conflict | claim → lock → precondition → conflict response snapshot → commit | `CONFLICT` olarak saklanır | Aynı 409 replay |
| 5xx/DB rollback | bütün transaction rollback | kalıcı claim yok | Aynı key yeniden deneyebilir |
| Concurrent aynı key | unique claim winner; follower sonucu tekrar okur | winner otorite | 2xx/409 replay veya kısa `IN_PROGRESS` |

Durum endpoint'i `expected_status` **ve** `expected_version` ister; ikisi de mevcut satırla eşleşmelidir ve mutation `WHERE id/company/status/version` ile version'ı atomik artırır. Sayaç ayağı Servis F1/#168'in `POST /api/machines/{machine_id}/hour-readings` ucudur. Bu uç `Idempotency-Key`, `device_instance_id`, `expected_work_order_status`, `expected_work_order_version`, `expected_machine_version` ve gözlenen `reading_hours` değerini alacak şekilde geriye uyumlu genişletilir; append-only kayıt, projection ve düşüşte reason/correction kuralları korunur. Makine sonra iş emri ortak kilit sırasıyla alınır; atama+terminal+sayaç düşüş+cross-device duplicate advisory kuralları claim ile aynı transaction'da değerlendirilir. Hangi alanın ayrıştığı sınıflı 409 cevabında belirtilir.

### M3 — Operasyonel olgunlaşma + koşullu parça

Kapsam:

- M1/M2 pilot metrikleri, oldest-pending/conflict SLA, destek runbook'u, kuyruk tanılama ve kontrollü rollout/rollback;
- saha cihazı kaybı/logout/conflict destek prosedürlerinin işletilmesi;
- Servis F3 iş kuralları hazır ve ayrıca onaylıysa yalnız teknisyenin yetkili depo/aracı için dar offline parça dilimi;
- F3 hazır değilse M3 parça içermez; operasyonel olgunlaşma bağımsız tamamlanır.

Kabul:

- oldest pending için alarm eşiği **15 dakika**, auth-blocked/conflict için kullanıcıya görünürlük **%100**, tanı ekranından operation/device/work-order korelasyonu **%100**;
- pilot destek runbook'unda kayıp cihaz, logout-cleanup, conflict ve iki-cihaz duplicate senaryoları için sahip ve çözüm adımı bulunur;
- rollback outbox/idempotency key'lerini kaybetmez; rollback sonrası duplicate yan etki **0**;
- parça dilimi açılırsa idempotent atomik stok mutasyonu, tenant/depo scope ve iki-cihaz yarış testleri geçer.

### M4 — Foto (düşük öncelik)

Kapsam ve önkoşullar:

- yerel blob/thumbnail, sıkıştırma ve kota yönetimi;
- upload idempotency+hash, içerik allow-list/sniffing;
- progress, retry, conflict ve cihaz/sunucu crash recovery state machine'leri;
- gerçek Android telefon/tablet kamera testleri.

Kabul:

- timeout/retry duplicate attachment oluşturmaz;
- commit/rename failure-injection recovery'si tek metadata+tek final dosyayla tamamlanır;
- kamera/processing/IndexedDB commit öncesi-sonrası kill testlerinde orphan veya sahte “kaydedildi” durumu oluşmaz;
- `RECEIVING/STAGED/PENDING_FILE/SUCCEEDED` crash matrisinde tek görünür attachment ve deterministik retry oluşur;
- 10 MiB üstü sunucuda 413; SVG/bozuk raster/decompression bomb reddedilir; download `attachment`+`nosniff` taşır;
- quota doluysa kaydedilmiş gibi görünmez; başarıdan sonra yerel blob temizlenir.

F3 gelmeden ürün/depo verisi cihaza indirilmez. Foto düşük önceliklidir ve M1/M2 pilot/operasyonel kapıları geçmeden başlatılmaz.

## 5. API ve güvenlik için uygulama öncesi karar kayıtları

İlgili faz başlamadan aşağıdaki kalan kararlar onaylanmalıdır:

1. Ayrı `/api/field` endpoint ailesi ve dar `field_service` izni kullanılacak mı? Öneri: evet.
2. Teknisyen yeniden atandığında bekleyen yerel işlemin sahibi/inceleme yetkisi kimde?
3. İdempotency kayıtlarının retention süresi nedir? Offline replay penceresinden uzun olmalı; başlangıç önerisi 30 gün.
4. Sunucu resource version alanı monoton integer mı, `updated_at` mı? Öneri: açık monoton integer; timestamp hassasiyet/format farkına dayanmaz.
5. M4 desteklenen foto formatları ve HEIC dönüşüm stratejisi nedir?
6. M2/M4 logout sırasında bekleyen saha kanıtı için export/amir kurtarma yolu gerekli mi?

## 6. Test ve gözlemlenebilirlik tasarımı

Test katmanları:

- unit: IndexedDB migration, partition, sıra, backoff, conflict sınıflama;
- frontend integration: offline cold start, pending count, kullanıcı/tenant değişimi, quota, atomik snapshot, çok sekme/versionchange, 12 saat/clock-tamper ve local photo recovery;
- backend SQLite: field scope, idempotent replay/fingerprint, conflict zarfları;
- PostgreSQL: çift gönderim yarışları, CAS, sayaç ve stok/attachment yan etkisinin tekliği;
- Playwright: installable manifest kontrolü, online→offline→online yolculuğu, altı noktalı kill-switch yarış matrisi, eski/yeni sekme+DB upgrade, mobil viewport;
- gerçek cihaz: Android telefon + Android tablet, güncel Chrome PWA; M2'de aynı teknisyenle iki gerçek cihazlı duplicate/advisory testi, M4'te kamera/depolama/OS kill-reopen.

Üretim metrikleri kişisel veriyi loglamadan:

- outbox başarı/retry/conflict sayısı ve işlem tipi;
- oldest pending age;
- snapshot boyutu/adedi;
- upload byte/süre/hata sınıfı;
- worker versiyonu ve upgrade başarısızlığı;
- auth_blocked sayısı;
- `device_instance_id` bazında işlem sayısı ve cross-device advisory sayısı (ham idempotency key veya müşteri verisi olmadan).

Idempotency key, ham foto, müşteri adı/telefonu ve serbest metin notlar uygulama loglarına yazılmamalıdır. Sunucu audit kaydı mevcut aktör/tenant zincirini korumalıdır.

## 7. Riskler ve sınırlamalar

- iOS/Android tarayıcı depolama politikaları ve OS'nin PWA'yı öldürmesi background sync'i güvenilmez yapabilir; v1 doğruluğu foreground sync'e dayanır.
- Kişisel cihaz kaybı riski işveren tarafından yazılı kabul edilmiştir; Web depolaması güçlü bir kasa değildir. Veri minimizasyonu, 12 saatlik pencere ve logout temizliği ana savunmadır.
- Mevcut detay ekranını olduğu gibi cache'lemek fazla API ve veri taşır; saha DTO'su olmadan M1 yapılmamalıdır.
- Mevcut status CAS içsel olarak atomik olsa da açık client precondition ve idempotency olmadan offline replay güvenli değildir.
- Mevcut 10 MiB upload sınırı mobil veri maliyeti için hedeften büyüktür; istemci küçültme hedefi gerçek foto beklentisiyle doğrulanmalıdır.
- `navigator.onLine`, Background Sync ve tarayıcı kota değerleri yalnız sinyaldir; kullanıcıya sunucuya ulaşıldığı doğrulanmadan “senkronize” denmez.

## 8. Berkay'a açık sorular

Karara bağlananlar: hedef Android telefon + Android tablet/güncel Chrome; cihazlar kişisel; kalan risk yazılı kabul; offline pencere 12 saat; müşteri telefonu offline DTO dışında; foto M4 düşük öncelik.

Kalan ürün/operasyon soruları:

1. İlk pilot ve 12 aylık hedefte kaç teknisyen ve toplam kaç kurulum/device instance var?
2. Pilotun seçilecek telefon/tablet marka-model ve asgari Android/Chrome sürümleri nedir?
3. İdempotency/audit retention süresi ve yeniden atama conflict'inin operasyon sahibi kimdir?
4. M2/M4'te bekleyen sayaç/foto varken logout için veri dışa aktarma veya amir kurtarma gereksinimi var mı?
5. Servis F3 hangi tarihte ve hangi depo/araç stoğu kurallarıyla M3 parça dilimine hazır sayılır?
6. M4 için iş emri başına foto adedi, tipik boyut ve HEIC desteği beklentisi nedir?

## 9. Go / no-go

Faz-0 sonucu: **PWA ile devam et — koşullu GO.**

M1 build için no-go koşulları:

- field API'nin atama bazlı sunucu kapsamı ve dar DTO'su onaylanmadan genel ERP API cache'lenemez;
- iki dağıtımlı bridge ve altı yarış noktalı deterministik worker fixture'ı geçmeden yeni worker üretime alınamaz;
- allow-list DTO, telefon dışlama, token saklamama, 12 saat ve logout-cleanup kapıları eksikse cache açılmaz;
- Android telefon+tablet/güncel Chrome pilot matrisi ve 5 iş günlük gözlem sahibi belirlenmeden M1 pilotu başlamaz.

Bu revizyon ChatGPT son delta onayı tamamlanana kadar uygulama yetkisi vermez. M1 kapsamı salt-okumadır ve bu onay sırasında büyütülemez.
