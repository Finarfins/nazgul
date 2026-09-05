# Field stok outbox tüketicisi — AÇILIŞ KOŞULLARI

`FIELD_STOCK_OUTBOX_ENABLED` varsayılan olarak **false**'tur ve aşağıdaki
DÖRT koşulun DÖRDÜ de var olmadan **true yapılmamalıdır**:

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
2. **Başarısızlık sonuçları için bir okuma yüzeyi.** Uygulamada
   `field_integration_events` tablosunu okuyan hiçbir ekran/uç yok; kovalar
   yalnız süreç günlüğünde görünür.
3. **Terminal satırlar için bir yeniden kuyruklama yolu.** Tüketici yalnız
   `PENDING` seçer; `SKIPPED_*`/`DEAD` yazılan satır bir daha ASLA seçilmez
   ve onu `PENDING`e döndüren hiçbir mekanizma yok.
4. **Canlılık/gecikme sinyali.** Zamanlayıcı thread'i ölürse ya da kuyruk
   birikirse bunu söyleyen bir metrik/alarm yok; tek iz süreç günlüğüdür.

## Neden

Bu dördü olmadan anahtar açıksa: her hasat olayı sessizce terminal kovaya
düşer, terminal satır bir daha seçilmez, hiçbir yüzey bunu göstermez ve geri
almanın yolu yoktur — yani her hasat **görünmez ve kurtarılamaz** biçimde
atılmış olur. Anahtar kapalıyken olaylar `PENDING` birikir ve açıldığı gün
işlenir: kayıp yoktur, erteleme vardır.

Okuma yüzeyi, yeniden kuyruklama ve canlılık sinyali AYRI işler olarak
sıradadır; bu tüketicinin kapsamına bilinçli olarak alınmamıştır.

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
