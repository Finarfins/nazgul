# V2.9 Claude Audit P0/P1 Checkpoint

Bu checkpoint, bağımsız Claude denetiminde kanıtlanan ilk kritik güvenlik,
finansal doğruluk ve veri bütünlüğü bulgularını kapatır.

## Kapatılan kritik bulgular

- Global belge indirimi artık KDV matrahını ve KDV toplamını oransal olarak düşürür.
  `subtotal + vat_total == final_total` muhasebe invariantı zorunludur.
- `must_change_password` yalnızca frontend yönlendirmesi değildir. Backend middleware,
  şifre değişikliği tamamlanana kadar diğer korumalı API'leri 403 ile engeller.
- `yonetici` rolü artık `admin` veya başka bir `yonetici` oluşturamaz.
- `yonetici` rolü admin/yönetici seviyesindeki kullanıcıları pasife alamaz.
- Firmanın son aktif admin hesabının pasife alınması engellenir.
- Excel sayıları TR ve US biçimlerinde güvenli ayrıştırılır; geçersiz/belirsiz değerler
  sessizce sıfıra çevrilmez.
- Excel yüklemelerine sıkıştırılmış boyut, açılmış arşiv boyutu ve satır sınırı eklendi.
- `products.stock` denormalize özeti, depo stoklarının toplamından tek atomik UPDATE
  cümlesiyle güncellenir; READ COMMITTED altında SELECT+UPDATE drift penceresi kapatıldı.
- Yetkisiz frontend route erişimleri permission guard ile engellendi.
- Kullanıcı/audit/finans/rapor API'lerinde GET istekleri de gerçek permission
  kontrolüne alındı; gizli menüyü URL/API çağrısıyla aşma yolu kapatıldı.
- Kullanıcı rol listesi ve durum aksiyonları aktörün rol seviyesine göre filtrelendi.
- `.db-wal`, `.db-shm` ve `.db-journal` dosyaları Git/Docker ignore kapsamına alındı;
  mevcut WAL/SHM dosyaları dağıtım paketinden çıkarıldı.
- Uygulama başlığındaki sürüm `V2.9` olarak düzeltildi.

## Bilinçli olarak açık bırakılan konular

- Firma bazlı negatif stok politikası ve yönetici onay akışı
- Satış anında kredi/risk limiti zorlaması
- Mutasyon endpointleri için idempotency key altyapısı
- Belge numarası kilit süresinin kısaltılması
- Runtime `initialize_*` DDL ile Alembic'in tek kaynağa indirilmesi
- Karantinadaki legacy testlerin tam izolasyonu
- httpOnly cookie / refresh token geçişi
- Tedarikçi connector job queue, SSRF/XXE ve secret-store altyapısı

Bu maddeler sonraki hardening sprintinde ele alınmalıdır.
