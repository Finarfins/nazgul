# Yerel Hesap Pro X — V2.9 Test Build

Bu paket V3.0 Müşteri Merkezi Faz 1 tabanı üzerine V2.9 Production Readiness düzeltmelerini içerir.

## Uygulanan düzeltmeler

- Workflow tabloları SQLAlchemy metadata ile tanımlandı.
- Workflow kimlik üretimi PostgreSQL uyumlu hale getirildi.
- Workflow parasal alanları yeni kurulumlarda `Numeric(18,2)`, miktarlar `Numeric(18,4)` olarak oluşturulur.
- Workflow okuma, güncelleme ve silme işlemlerinde tenant sahipliği sıkılaştırıldı.
- Yabancı tenant belge işlemleri 404 döndürür.
- Belge dönüşüm güncellemelerine `company_id` filtresi eklendi.
- `yonetici` rolünün `admin` kullanıcı oluşturması engellendi.
- İşlem listeleme tarih normalizasyonundaki `instr()` kaldırıldı.
- Excel dışa aktarımlarına formula injection koruması eklendi.
- Starlette TestClient için geliştirme bağımlılığı `httpx` olarak düzeltildi.
- Tenant filtresi olmayan ve uygulamaya bağlı olmayan eski `routers/orders.py` kaldırıldı.
- PostgreSQL ve güvenlik regresyon testleri eklendi.

## Bilinen sınırlar

- Gerçek PostgreSQL sunucusunda tam uygulama smoke testi henüz çalıştırılmadı.
- Diğer modüllerde kalan `lastrowid`, SQLite özel SQL ve `Float/REAL` kullanımları tamamen temizlenmiş değildir.
- Mevcut eski veritabanı kolonları otomatik olarak `NUMERIC` tipine dönüştürülmez; Alembic migration gerekir.
- Bu paket test yapısıdır; henüz production-ready etiketi taşımaz.
