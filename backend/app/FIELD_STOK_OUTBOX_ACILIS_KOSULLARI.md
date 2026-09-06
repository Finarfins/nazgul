# Field stok outbox tüketicisi — AÇILIŞ KOŞULLARI

`FIELD_STOCK_OUTBOX_ENABLED` varsayılan olarak **false**'tur ve aşağıdaki
DÖRT koşulun DÖRDÜ de var olmadan **true yapılmamalıdır**.

**DÖRT KOŞULUN DÖRDÜ DE KARŞILANDI; anahtar berkay karar verene kadar KAPALI
kalır.** Bu satır bir açma emri DEĞİLDİR ve bu dilim anahtara DOKUNMADI:
`field_stock_outbox_enabled` varsayılanı hâlâ `False`. Koşullar "açılabilir"
der, "açıldı" demez — ve aşağıdaki "Açarken bilinmesi gereken iki şey daha"
başlığı koşullar sağlandıktan SONRA da AYNEN geçerlidir (birikmiş kuyruğu
ÖNCE ölç; `RECOVERY_FAILED` hâlâ veritabanında iz bırakmaz).

1. ~~**Hasat → ürün yolu.**~~ **KARŞILANDI** (göç `20260827_0062`).
   Eskiden: `field_harvests` içinde `product_id` yoktu, `crop_seasons.crop`
   serbest metindi ve ikisinden de `products`a bağ yoktu (ölçüldü, c9d3eb1);
   bu yol olmadan HER hasat olayı terminal `SKIPPED_NO_PRODUCT` kovasına
   düşüyordu.

   Bugün: **ÜRÜNÜ SEZON BİLDİRİR, HASAT DEVRALIR.** `crop_seasons.product_id`
   var, `products`a bileşik (kiracı kapsamlı) yabancı anahtarla bağlı, ve
   `field_stok_tuketici._hasat_kalemleri` hasadı sezonu üzerinden ürüne
   çözüyor.

   **KOVA KALDIRILMADI, KAÇINILABİLİR YAPILDI.** Sütun NULL kabul eder:
   ürünü bildirilmemiş bir sezonun hasadı hâlâ `SKIPPED_NO_PRODUCT`a düşer —
   artık gerekçesinde hangi kaydın düzeltileceğini söyleyerek. Yani bu koşul
   "hiçbir hasat kovaya düşmez" anlamına GELMEZ; "düşmesi artık bir VERİ
   EKSİĞİDİR, bir ŞEMA EKSİĞİ değil" anlamına gelir.

   **ANAHTARI AÇMADAN ÖNCE ÖLÇÜLECEK ŞEY.** Mevcut sezonların ürünü
   bildirilmemiş olarak gelir (göç değer UYDURMAZ). Açmadan önce:

       SELECT count(*) FROM crop_seasons WHERE product_id IS NULL;

   Sıfır değilse o sezonların hasatları kovaya düşmeye devam eder. Bu
   ölçümün ANLAMLI olması, aşağıdaki 2. koşula (okuma yüzeyi) bağlıdır.
2. ~~**Başarısızlık sonuçları için bir okuma yüzeyi.**~~ **KARŞILANDI**
   (**GÖÇ YOK**). Eskiden: uygulamada `field_integration_events` tablosunu
   okuyan hiçbir ekran/uç yoktu; kovalar yalnız süreç günlüğünde görünüyordu.

   Bugün: `GET /api/field-integration-events` (liste, `last_error` metniyle,
   `failed_only` süzgeci) ve `GET /api/field-integration-events/summary`
   (kaynak tipi × durum kırılımı, `oldest_created_at` ile). İkisi de
   `farm.view` iznine ve `company_id=:cid` yüklemine bağlı
   (`app/routers/entegrasyon_olaylari.py`).

   NOT — BU MADDE BUGÜNE KADAR ÜSTÜ ÇİZİLMEMİŞTİ: başlık 2'yi karşılanmış
   sayıyor ve 3. koşul metni "koşul 2'de eklenen `_FARM_PATH_PREFIXES`"
   diyerek ona dayanıyordu, ama madde eski hâliyle duruyordu. Koşul 4'ün
   indiği bu turda ölçülerek düzeltildi.
3. ~~**Terminal satırlar için bir yeniden kuyruklama yolu.**~~ **KARŞILANDI**
   (PR #55; **GÖÇ YOK**).
   Eskiden: tüketici yalnız `PENDING` seçer; `SKIPPED_*`/`DEAD` yazılan satır
   bir daha ASLA seçilmez ve onu `PENDING`e döndüren hiçbir mekanizma yok.

   Bugün: **`POST /api/field-integration-events/{id}/requeue`**. İzni
   `farm.manage` (ÖLÇÜLDÜ, varsayılmadı: yol koşul 2'de eklenen
   `_FARM_PATH_PREFIXES` önekinin altında ve güvenli olmayan yöntem oraya
   çözülüyor) — yani okuma yüzeyinin `farm.view`inden DAHA DAR: kuyruğu
   GÖRMEK ile onu OYNATMAK ayrı izinlerdir.

   **NE YAPAR.** İzin verilen küme terminal durumlardan `SENT` çıkarılmış
   hâlidir — `SKIPPED_SOURCE_NOT_VISIBLE`, `SKIPPED_NO_PRODUCT`,
   `SKIPPED_TABAN_BILDIRILMEMIS`, `DEAD` — ve okuma yüzeyinin `failed_only`
   süzgecini kuran demetin AYNISINDAN gelir; iki ayrı yerde yazılsaydı yeni
   bir kova ekleyen dilim (C2 ekledi) ekranda görünür ama geri alınamaz bir
   olay yaratırdı. Satırda değişen ÜÇ sütun vardır: `status` -> `PENDING`,
   `attempts` -> `0`, `updated_at` -> şimdi.

   **`SENT` GERİ ALINAMAZ (409 `EVENT_ALREADY_SENT`).** Gönderilmiş olayın
   stok hareketi YAZILMIŞTIR ve tüketici `stock_movements` satırlarını
   hiçbir yolda UPDATE/DELETE etmez; yeniden gönderim "tekrar denemek"
   değil İKİNCİ BİR HAREKET yazmayı denemek olurdu. Veritabanı ikinci hattı
   zaten tutuyor (göç 0060, kısmi benzersiz indeks) ama uç okunur bir cevap
   borçlu olduğu için kapı uygulamada da duruyor.

   **`attempts` SIFIRLANIR — BEDELİ AÇIKÇA YAZILIYOR.** İlk niyet onu
   korumaktı; ÖLÇÜLDÜ ve o niyet mekanizmayı ateşlenemez kılıyor: tavanı
   doldurarak ölen satırın `attempts`i 4'tür (`AZAMI_DENEME` 3, tavan kolu
   `attempts = deneme` MUTLAK yazar), yani korunsaydı geri alınan olay bir
   sonraki döngüde 5 > 3 ile YENİDEN `DEAD` olurdu — koşulun VAR OLMA
   SEBEBİ olan sınıfta uç hiçbir şey yapmazdı. Kaybolan bilgi YER
   DEĞİŞTİRİR: `last_error` KORUNUR ("deneme tavanı aşıldı (3)") ve
   `activity_logs` satırının `details`i ÖNCEKİ durumu ve ÖNCEKİ `attempts`
   değerini saklar.

   **`processed_at` KORUNUR** ve bu, sıfırlanan `attempts`in İSTENEN
   karşılığıdır: geri alınmış bir satırı HİÇ denenmemiş bir satırdan ayıran
   TEK sütun odur. Aşağıdaki `RECOVERY_FAILED` notunun şikâyeti tam buydu;
   `processed_at`i de temizlemek onu büyütürdü.

   **DENETİM İZİ GÖÇ İSTEMEDİ.** `requeued_by`/`requeued_at` sütunu YOK ve
   bu dilim göç EKLEMEDİ; kimin hangi olayı hangi durumdan geri aldığı
   `activity_logs`ta `field_event.requeued` satırı olarak durur (katalog
   60 -> 61). Satır ucun kendi işleminde yazılır ve TEK commit ile biter:
   denetimsiz bir yeniden kuyruklama OLUŞAMAZ.

   **YARIŞ.** Kararı veren şey `status IN (<terminal>)` yüklemli KOŞULLU
   UPDATE'in rowcount'udur — tüketicinin `_talep_et`indeki desenin aynısı.
   Sınıflandırma SELECT'i yalnız hangi 4xx'in döneceğini seçer. Uçuştaki
   bir tüketicinin talep ettiği (`CLAIMED`) ya da başka bir istekle zaten
   geri alınmış (`PENDING`) satır DOKUNULMADAN 409 alır. Bu SQLite'ta
   ölçülemez (yazmalar seri); davranış kanıtı PG ikizindedir.

   **NE YAPMAZ.** `RECOVERY_FAILED` sınıfını bu uç da KAPATMAZ (aşağıdaki
   nota bakın): o olay veritabanında iz bırakmaz, `PENDING` kalır ve bu
   ucun seçeceği bir işaret yoktur.
4. ~~**Canlılık/gecikme sinyali.**~~ **KARŞILANDI** (**GÖÇ YOK**).
   Eskiden: zamanlayıcı thread'i ölürse ya da kuyruk birikirse bunu söyleyen
   bir metrik/alarm yoktu; tek iz süreç günlüğüydü. Okuma yüzeyi (koşul 2) bu
   boşluğu KAPATMIYORDU ve gerekçesi bu belgede yazılıydı: `summary` kuyruğun
   BOYUNU ve YAŞINI gösteriyor ama tüketicinin KOŞUP KOŞMADIĞINI
   göstermiyordu — **ölü bir thread ile boş bir kuyruk o ekranda AYNI
   görünüyordu**.

   Bugün: **ZAMANLAYICI HER DÖNGÜDE KALP ATIŞI YAZIYOR** ve `summary` yanıtı
   bir `scheduler` bloğu taşıyor:

       enabled, alive, last_cycle_started_at, last_cycle_finished_at,
       seconds_since_last_cycle, interval_seconds, stale,
       pending_oldest_age_seconds

   **YENİ UÇ AÇILMADI.** Blok VAR OLAN `summary` ucuna eklendi. İkinci bir uç,
   "kuyruk" ile "onu boşaltan şey"i iki ekrana bölerdi: 40 gündür bekleyen bir
   olayı gören kişi, tüketicinin ölü olduğunu ancak BAŞKA bir yere bakarsa
   öğrenirdi.

   **GÖÇ YOK, ÖLÇÜLDÜ.** Depoda bir işçi kalp atışı tablosu ARANDI ve YOK:
   `heartbeat` geçen tek şema yeri `platform_maintenance.heartbeat_at`tır
   (göç `20260728_0034`) ve o sütun BAKIM İŞLEMİNİN kalp atışıdır —
   zamanlayıcı oraya yazsaydı bakım kilidinin sahipliği hakkında YALAN
   söylerdi. Kullanılan şey ZATEN VAR OLAN `settings` anahtar/değer
   tablosudur (`app/core_schema.py`; şemaya taban göçü `20260712_0000` ile
   giriyor, yani hem SQLite hem PostgreSQL kurulumlarında VAR) ve tek satır
   `field_stok_zamanlayici.heartbeat` anahtarında durur. Satırın gövdesi
   JSON'dur: `started_at`, `finished_at`, `companies_processed`,
   `companies_total`, `events_processed`, `last_error`.

   **KALP ATIŞI PLATFORM DÜZEYİNDEDİR, `company_id` TAŞIMAZ.** Zamanlayıcı
   süreç-içi TEK bir thread'dir ve tüm firmaları TEK döngüde gezer; kiracısı
   olmayan bir olguya kiracı uydurmak olurdu. Bunun bedeli açıkça yazılıyor:
   `scheduler` bloğunun `pending_oldest_age_seconds` DIŞINDAKİ her alanı HER
   KİRACI İÇİN AYNIDIR. O tek alan ise KİRACIYA özeldir ve ucun ZATEN
   `company_id=:cid` ile koşan özet sorgusundan türer — **ikinci bir sorgu
   açılmadı**, çünkü özet kova başına `MIN(created_at)` zaten seçiyor.

   **`alive` İLE `stale` AYRI SORULARDIR.** `alive` `Thread.is_alive()`tir:
   yalnız thread'i TAŞIYAN süreçte anlamlıdır, hiçbir şeyi hayatta kalmaz ama
   SAHTELENEMEZ. `stale` kalıcı kalp atışından türer, yani SÜRECİ AŞAR: bir
   süreç ölüp yerine yenisi gelmediyse sorulacak bir thread yoktur ve kalan
   tek delil o satırdır. Taze başlamış bir süreçte ilk döngü bitene kadar
   `alive=True, stale=True` görülür — thread ORADADIR ama tamamlanmış bir
   döngü KANITI henüz yoktur.

   **BAYATLIK EŞİĞİ `3×interval_seconds` ve KATI BÜYÜKTÜR.** Bir döngüyü
   kaçırmak jitterdir, üçünü birden kaçırmak arızadır. Kalp atışı HİÇ yoksa
   `stale` TRUE'dur: kanıt yokluğu tazelik değildir.

   **`last_error` KAYITTA VAR, YÜZEYDE YOK.** Kalp atışı satırı düşen bir
   döngünün istisna metnini taşır ama uç onu DÖNDÜRMEZ. Gerekçe deponun kendi
   kararıdır (`_gerekceyi_arindir`): ham istisna metni SQL, kısıt adı ve satır
   değeri taşıyabilir ve bu uç `farm.view` taşıyan salt-okur rollere açıktır —
   koşul 2'de kapatılan sızıntı sınıfını koşul 4'te yeniden açmıyoruz.

   **NE YAPMAZ.** Kalp atışı bir TARİH SERİSİ değildir: tek satır, yalnız SON
   döngü. "Son bir saatte kaç döngü koştu" burada CEVAPLANMAZ ve cevaplanması
   bir tablo (yani göç) isterdi. Bir ALARM da değildir: bu sinyali kim okuyup
   kime haber vereceği bu dilimin kapsamı DIŞINDADIR — uç `stale`i söyler,
   birinin bakması gerekir.

## Neden

Bu dördü olmadan anahtar açıksa: her hasat olayı sessizce terminal kovaya
düşer, terminal satır bir daha seçilmez, hiçbir yüzey bunu göstermez ve geri
almanın yolu yoktur — yani her hasat **görünmez ve kurtarılamaz** biçimde
atılmış olur. Anahtar kapalıyken olaylar `PENDING` birikir ve açıldığı gün
işlenir: kayıp yoktur, erteleme vardır.

Okuma yüzeyi, yeniden kuyruklama ve canlılık sinyali AYRI işler olarak
sıradaydı; bu tüketicinin kapsamına bilinçli olarak alınmamışlardı. ÜÇÜ DE
İNDİ (koşul 2, 3 ve 4) ve **dört koşulun dördü de karşılandı; anahtar berkay
karar verene kadar KAPALI kalır.**

Yukarıdaki paragrafın "hiçbir yüzey bunu göstermez" cümlesi ARTIK GEÇERLİ
DEĞİLDİR ve bu bir düzeltmedir, bir çıkarma değil: `summary`/liste kuyruğu
gösterir, `requeue` terminal satırı geri alır, `scheduler` bloğu tüketicinin
koşup koşmadığını söyler. GEÇERLİ KALAN tek cümle şudur: anahtar kapalıyken
olaylar `PENDING` birikir ve açıldığı gün işlenir — kayıp yoktur, erteleme
vardır.

## Açarken bilinmesi gereken iki şey daha

Bunlar yukarıdaki dört koşula EK değildir — koşullar sağlandığında bile
geçerli kalan iki ölçülmüş gerçektir.

- **BİRİKMİŞ KUYRUĞU ÖNCE ÖLÇ.** Anahtar kapalıyken olaylar `PENDING`
  birikir ve seçici (`olaylari_isle`) bir firmanın PENDING satırlarının
  TAMAMINI okur, `AZAMI_PARTI` (200) kırpması bundan SONRA Python tarafında
  yapılır — yani ilk döngüler birikmiş kuyruğun tamamını okur, 200'ünü işler
  ve geri kalanı bir sonraki döngüde yeniden okur. Açmadan önce
  `SELECT company_id, count(*) FROM field_integration_events WHERE
  status = 'PENDING' GROUP BY company_id` ile firma başına birikimi ölç:
  büyük bir kuyrukta ilk boşalma, 30 sn'lik döngü başına firma başına ~200
  olayla ilerler ve o süre boyunca her döngü kuyruğun tamamını okumaya devam
  eder. Kuyruk beklediğinden büyükse anahtarı bakım penceresinde aç.
- **`RECOVERY_FAILED` VERİTABANINDA İZ BIRAKMAZ.** Bu kovaya düşen olayda
  hiçbir sütun değişmez: `status` `PENDING` kalır, `attempts` ESKİ değerinde
  kalır, `last_error`, `updated_at` ve `processed_at` da öyle. Sonuç: hiç
  denenmemiş bir olayla, denenip kurtarma yazımı da başarısız olmuş bir olay
  veritabanında AYIRT EDİLEMEZ; deneme yakılmadığı için `AZAMI_DENEME`
  tavanı bu sınıfta HİÇ dolmaz ve olay süresiz yeniden denenir. Sıradaki
  okuma yüzeyi (koşul 2) ile yeniden kuyruklama yolu (koşul 3) bunu TEK
  BAŞLARINA KAPATMAZ: okuma yüzeyi bu satırları TAZE İŞ olarak gösterir,
  yeniden kuyruklama yolunun ise seçeceği bir işaret yoktur. Bu sınıfın tek
  izi süreç günlüğündeki `logger.exception` satırları ve döngü kova
  sayacıdır.
- **KUYRUKTA ARTIK KANTAR FİŞİ OLAYLARI DA VAR (C2).** `POST
  /api/field-harvest-tickets` her fiş için TAM BİR olay yazıyor (kaynak tipi
  `field_harvest_ticket`, anahtar `field_harvest_ticket:<fiş id>:stock`).
  Anahtar kapalıyken bunlar da `PENDING` birikiyor, yani yukarıdaki birikim
  ölçümü artık iki değil ÜÇ kaynak tipini kapsıyor. Fiş olayının tüketimi bir
  MİKTAR değil bir FARK yazar (`Σ fiş netleri − hasadın taban miktarı −
  o hasat için daha önce yazılmış fiş düzeltmeleri`); farkı sıfır çıkan olay
  `SENT` biter ve HİÇBİR SATIR yazmaz.
- **YENİ TERMİNAL KOVA: `SKIPPED_TABAN_BILDIRILMEMIS` (C2).** Ürünü belli ama
  `products.base_unit`i bildirilmemiş olay bu kovaya düşer ve SAYILIR; çaresi
  ürün kartıdır (`PUT /api/products/{id}`). C2 aynı çeviriyi `field_harvest`
  yoluna da uyguladı — o yol miktarı daha önce HAM yazıyordu ve `unit="ton"`
  giren bir hasat için bu 1000× bir sapma demekti. Anahtar üretimde hiç
  açılmadığı için CANLI VERİ YOKTUR ve geriye dönük düzeltilecek bir hareket
  de yoktur; açmadan önce `SELECT count(*) FROM products WHERE company_id = ?
  AND base_unit IS NULL` ile kaç ürünün bu kovaya düşeceğini ölç.
