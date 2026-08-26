# V2.9 Hardening Test Raporu

Tarih: 2026-07-13

## Bu ortamda gerçekten çalıştırılan kontroller

```text
Python compileall: geçti
Kritik backend testleri: 14 geçti
PostgreSQL entegrasyon testleri: 4 atlandı (PostgreSQL sunucusu yok)
Frontend TypeScript + Vite production build: geçti
Frontend Vitest: 3 dosya / 6 test geçti
Alembic SQLite bootstrap: geçti
```

## Backend kapsamı

- 6 karakter parola politikası
- Yönetici rolünün admin oluşturamaması
- Temiz SQLite kurulum ve çoklu firma yeniden başlatma
- Excel formula injection koruması
- Cross-dialect tarih normalizasyonu
- Workflow oluşturma/dönüştürme/silme
- Workflow tenant read/update/delete IDOR koruması
- Atomik stok azaltma ve negatif stok guard'ı
- `Decimal` miktar hassasiyeti
- Yeni firma için anında varsayılan depo
- Stok uçlarında yabancı firma ürün/depo kimliklerinin reddedilmesi
- `/api/live`, `/api/ready`, auth sınırları ve cache headerları
- Alembic revision bootstrap
- Migration manifestinin tüm SQLAlchemy Numeric kolonlarını kapsaması
- CI ve non-root Docker statik kalite kapıları

## Doğrulanmayan bölüm

Bu çalışma ortamında Docker ve PostgreSQL sunucusu bulunmadığı için aşağıdaki testler yazılmış fakat burada çalıştırılmamıştır:

- Temiz PostgreSQL 16 uygulama başlangıcı
- PostgreSQL restart/idempotency
- PostgreSQL workflow DDL ve generated PK
- PostgreSQL transaction tarih filtreleri
- PostgreSQL legacy `REAL/DOUBLE -> NUMERIC` migration'ı

Bu testler GitHub Actions PostgreSQL 16 servisinde çalışacak şekilde CI'a eklenmiştir. CI sonucu görülmeden production-ready etiketi verilmemelidir.
