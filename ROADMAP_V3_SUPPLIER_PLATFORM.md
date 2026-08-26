# V3+ Tedarikçi Platformu Yol Haritası

Bu belge ürün sahibi tarafından talep edilen üç yeteneği kalıcı yol haritasına alır:

1. Tedarikçi PDF fiyat listesi/katalog aktarımı
2. Ürün ve katalog görsel yönetimi
3. Tedarikçi B2B sistemlerine bağlanıp birleşik ürün/fiyat/stok sorgulama

## Ürün ilkesi

Tedarikçi kaynaklarından gelen veriler ana ürün kartına doğrudan yazılmaz. Önce staging/katalog alanına alınır, güven skoru ve kullanıcı onayıyla eşleştirilir. Böylece yanlış OCR, değişen fiyat veya farklı tedarikçi kodu ana stok verisini bozmaz.

## Aşama 1 — Tedarikçi Merkezi

- Tedarikçi firma listesi
- Tedarikçi detay ekranı
- İletişim, vade, risk, sipariş geçmişi
- `Ürün Katalogları` sekmesi
- `B2B Bağlantıları` sekmesi
- `Fiyat Geçmişi` sekmesi

## Aşama 2 — PDF/Katalog Aktarımı

Önerilen tablolar:

- `supplier_catalogs`
- `supplier_catalog_imports`
- `supplier_catalog_items`
- `supplier_catalog_item_images`
- `supplier_product_links`

İçe aktarım akışı:

```text
PDF yükle
→ tedarikçi ve katalog yılı seç
→ tablo/sayfa analizi
→ ürün kodu, OEM, ad, model, fiyat ve görsel çıkar
→ güven skoru
→ kullanıcı ön izlemesi
→ ana ürünle eşleştir veya yeni ürün öner
→ onayla
```

Her kayıt kaynak PDF, sayfa numarası ve ham metinle izlenebilir olmalıdır.

## Aşama 3 — Ürün Görselleri

Önerilen tablolar:

- `product_images`
- `supplier_catalog_item_images`

Temel alanlar:

- `company_id`
- `product_id`
- `storage_key`
- `content_hash`
- `mime_type`
- `width`, `height`, `size_bytes`
- `image_role` (`primary`, `gallery`, `technical`, `catalog`)
- `source_type`, `source_id`, `source_page`
- `created_by`, `created_at`

Local modda dosya sistemi; bulut modunda S3 uyumlu object storage kullanılmalıdır. Görseller otomatik thumbnail/WebP üretimi, hash ile tekrar önleme ve tenant kontrollü indirme URL'si kullanmalıdır.

## Aşama 4 — B2B Connector Platformu

Her tedarikçiye özel kod ana ERP'ye gömülmez. Standart connector sözleşmesi kullanılır:

```python
class SupplierConnector:
    def test_connection(self): ...
    def search_products(self, query): ...
    def get_product(self, external_code): ...
    def get_price_and_stock(self, external_code): ...
    def create_order(self, payload): ...
    def get_order_status(self, external_order_id): ...
    def download_invoice(self, external_order_id): ...
```

Destek sırası:

1. Resmî REST/SOAP API
2. OAuth2/API key entegrasyonu
3. cXML/PunchOut veya SAP OCI
4. XML/CSV/Excel/SFTP
5. Firma tarafından onaylanmış tarayıcı otomasyonu

## Birleşik ürün sorgulama

Kullanıcı OEM/parça no, barkod veya ad ile arar. Sistem bağlı tedarikçilerden paralel sonuç toplar:

- Tedarikçi
- Ürün kodu/OEM
- Marka ve uyumlu model
- Liste fiyatı, iskonto, net fiyat
- KDV ve para birimi
- Depo bazlı stok
- Tahmini sevkiyat
- Minimum sipariş
- Son güncellenme zamanı

Sonuçlar en düşük net maliyet, en hızlı teslimat, tercih edilen tedarikçi veya en yüksek stok ölçütüne göre sıralanabilir.

## Güvenlik

- B2B şifreleri ve API anahtarları düz metin tutulmaz.
- Local modda OS korumalı anahtar deposu; bulutta KMS/secrets manager kullanılır.
- Bağlantı testleri ve sorgular audit log'a yazılır.
- İlk bağlantı salt okunur açılır.
- Sipariş gönderme ayrı izin ve kullanıcı onayı gerektirir.
- Her bağlantı ve sorgu `company_id` ile tenant izole edilir.
- Connector cevapları güvenilmeyen dış veri kabul edilip doğrulanır.
- Rate limit, timeout, retry, circuit breaker ve cache zorunludur.

## Teslim sırası

```text
V3.1 Tedarikçi Merkezi
V3.2 PDF/Katalog Aktarım Merkezi
V3.3 Ürün Görsel Yönetimi
V3.4 B2B Connector Altyapısı
V3.5 İlk Gerçek Tedarikçi Bağlantısı
V3.6 Birleşik Fiyat/Stok Arama
V3.7 Satın Alma Sepeti ve Sipariş Gönderme
V3.8 Sevkiyat/Fatura Senkronizasyonu
```

V2.9 production readiness tamamlanmadan bu modüller production koduna açılmayacaktır.
