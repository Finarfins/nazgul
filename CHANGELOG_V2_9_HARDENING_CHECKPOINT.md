# V2.9 Hardening Checkpoint — 2026-07-13

## Güvenlik ve tenant izolasyonu

- Ürün stok düzeltme, toplu stok ve kritik stok uçlarında ürün/depo sahipliği doğrulanıyor.
- Başka firmaya ait depo veya ürün kimliğiyle stok satırı oluşturulması engellendi.
- Depo, ürün ve stok JOIN sorgularına savunma derinliği amacıyla `company_id` eşleşmeleri eklendi.
- Yeni firma oluşturulduğu anda merkez şube ve varsayılan depo hazır hale geliyor; uygulama yeniden başlatması gerekmiyor.
- Audit log yazım hataları artık sessizce yutulmuyor, yapılandırılmış uygulama loguna düşüyor.
- SPA statik dosya sunumunda dizin dışına çıkma denemelerine karşı `resolve()` tabanlı sınır kontrolü eklendi.

## Veri bütünlüğü ve eşzamanlılık

- Depo stok güncellemesi `SELECT + UPDATE` yerine atomik `UPDATE ... quantity = quantity + delta` desenine geçirildi.
- Negatif stok engeli aynı SQL ifadesinin `WHERE` koşulunda uygulanıyor.
- Eşzamanlı ilk stok satırı oluşturma yarışında savepoint + tekrar deneme kullanılıyor.
- Miktar matematiği `Decimal` ve `NUMERIC(18,4)` hassasiyetiyle yapılıyor.
- Ürün toplam stok senkronizasyonu SQLAlchemy tipli UPDATE kullanıyor.

## Operasyon ve dağıtım

- `/api/live` liveness ve `/api/ready` veritabanı readiness uçları eklendi.
- Windows başlatıcı sunucu hazır olma kontrolünü `/api/ready` üzerinden yapıyor.
- API yanıtlarına `Cache-Control: no-store`, SPA kabuğuna no-cache, hash'li Vite assetlerine immutable cache politikası eklendi.
- Docker image non-root `app` kullanıcısıyla çalışıyor.
- Docker healthcheck `/api/live` kullanıyor.
- Docker Compose'a `init`, PostgreSQL health dependency ve `/tmp` tmpfs eklendi.

## Migration ve CI

- Alembic altyapısı ve ilk production hardening migration'ı eklendi.
- PostgreSQL'deki legacy finans alanları `NUMERIC(18,2)`, miktarlar `NUMERIC(18,4)` tipine dönüştürülüyor.
- SQLite migration sürümü kaydediliyor; riskli toplu tablo rebuild'i yapılmıyor.
- GitHub Actions: Python 3.12, PostgreSQL 16, kritik SQLite testleri, PostgreSQL entegrasyon testleri, frontend build/test ve Docker build işleri eklendi.

## Test altyapısı

- Rol güvenliği ve workflow testleri hazır `veriler.db` bağımlılığından kurtarıldı.
- Testler kendi geçici ve temiz veritabanlarını oluşturuyor.
- Atomik stok, hassasiyet, readiness/liveness, cache politikaları, Alembic revision, non-root Docker ve tenant stok IDOR senaryoları eklendi.
