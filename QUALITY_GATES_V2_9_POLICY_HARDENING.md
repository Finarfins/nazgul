# Quality Gates — V2.9 Policy Hardening

Tarih: 13 Temmuz 2026

## Statik ve build kontrolleri

- `python -m compileall -q backend/app` — geçti
- Uygulama kodunda `allow_negative=True` taraması — eşleşme yok
- Alembic migration zinciri — yeni policy migration'ı eklendi
- `npm test -- --run` — 3 test dosyası, 6/6 test geçti
- `npm run build` — TypeScript ve Vite production build geçti

Frontend testlerinde yalnızca mevcut Recharts/JSDOM SVG casing uyarıları görüldü;
test veya build başarısızlığı oluşmadı.

## Aktif backend test matrisi

`pytest --collect-only` ile 51 aktif test toplandı.

- 46 test geçti.
- 5 PostgreSQL entegrasyon testi, bu ortamda gerçek PostgreSQL bağlantısı
  sağlanmadığı için `skipped` oldu.
- Başarısız test yok.

Geçen başlıca kapılar:

- Firma negatif stok ve kredi limiti politikaları
- Yönetici istisna yetkisi ve audit kaydı
- Ürün, manuel stok, toplu stok, Excel ve depo transferi politika kapsamı
- Satış stok/ödeme/finans tutarlılığı
- Workflow dönüşüm idempotency ve hedef çakışması
- Tenant IDOR korumaları
- Decimal/muhasebe doğruluğu
- Temiz SQLite kurulum ve yeniden başlatma
- Runtime migration ve stale-schema fail-fast
- Belge numarası eşzamanlılığı
- Backup checksum ve finansal mutabakat kontrolleri
- Rol hiyerarşisi ve parola politikası

## PostgreSQL nedeniyle atlanan testler

- `test_numeric_migration_postgresql.py` — 1
- `test_postgresql_app_smoke.py` — 1
- `test_transactions_postgresql.py` — 1
- `test_workflow_postgresql.py` — 2

Bu testler gerçek PostgreSQL 16 final gate'inde zorunlu olarak çalıştırılmalıdır.

## Test çalıştırma notu

Projenin bilinen tek-process pytest kapanış beklemesi birleşik koşularda zaman zaman
tekrar görülebiliyor. Test dosyaları CI matrix mantığıyla izole süreçlerde çalıştırıldı;
aynı dosyalar tek başına çalıştırıldığında geçmiştir. Bu durum test başarısızlığı olarak
gizlenmedi ve legacy test izolasyonu tamamlanana kadar kalite riski olarak korunmaktadır.

## Karar

Yerel SQLite, API, migration, güvenlik ve frontend kapıları geçti.
Gerçek PostgreSQL 16 kapısı tamamlanmadığı için `production-ready: HAYIR`.
