# Quality Gates — V2.9 Session Security

Tarih: 14 Temmuz 2026

## Statik ve build kontrolleri

- `python -m compileall -q backend/app backend/alembic/versions` — geçti
- Frontend kaynaklarında access token `localStorage.setItem` taraması — eşleşme yok
- `npm test -- --run` — 3 test dosyası, 6/6 test geçti
- `npm run build` — TypeScript ve Vite production build geçti
- Alembic zinciri — tek head `20260714_0004`

Frontend testlerinde yalnızca mevcut Recharts/JSDOM SVG casing uyarıları görüldü;
test veya build başarısızlığı oluşmadı.

## Aktif backend test matrisi

`pytest --collect-only -q` ile 54 aktif test toplandı.

- 49 test geçti.
- 5 PostgreSQL entegrasyon testi, bu ortamda gerçek PostgreSQL bağlantısı
  sağlanmadığı için `skipped` oldu.
- Başarısız test yok.

Yeni doğrulanan oturum kapıları:

- Login yanıtında HttpOnly access ve refresh cookie'leri
- JavaScript tarafından okunabilir fakat kimlik bilgisi olmayan CSRF cookie'si
- Cookie-auth unsafe isteklerde CSRF zorunluluğu
- Cookie ile `/auth/me` doğrulaması
- Refresh token rotasyonu
- Eski refresh token replay'inde tüm ailenin iptali
- Bearer istemci geriye uyumluluğu
- Frontend'in access token'ı Web Storage'a yazmaması

## PostgreSQL nedeniyle atlanan testler

- `test_numeric_migration_postgresql.py` — 1
- `test_postgresql_app_smoke.py` — 1
- `test_transactions_postgresql.py` — 1
- `test_workflow_postgresql.py` — 2

## Test çalıştırma notu

Birleşik tek-process pytest çalıştırması projenin bilinen kapanış beklemesine yeniden
girdi. Test dosyaları ayrı süreçlerde çalıştırıldı ve tamamı yukarıdaki sonucu verdi.
Bu durum assertion başarısızlığı olarak gizlenmedi; legacy test/fixture izolasyonu
bitene kadar operasyonel kalite riski olarak korunmaktadır.

## Karar

Cookie session hardening, frontend build ve yerel regresyon kapıları geçti.
Gerçek PostgreSQL 16 ve HTTPS reverse proxy provası tamamlanmadığı için
`production-ready: HAYIR`.
