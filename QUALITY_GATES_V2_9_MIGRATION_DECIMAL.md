# V2.9 Migration + Decimal — Quality Gates

## Geçen kapılar

- [x] Runtime Python kaynakları derleniyor
- [x] Deterministik backend test paketi yeşil: 24 passed
- [x] Frontend testleri yeşil: 6 passed
- [x] TypeScript ve Vite production build yeşil
- [x] SQLite foreign key / WAL / busy timeout etkin
- [x] Alembic runtime bootstrap ve idempotency SQLite üzerinde doğrulandı
- [x] Runtime app kodunda binary float finans sözleşmesi AST testiyle engelleniyor
- [x] Tenant ve rol regresyon testleri mevcut
- [x] Docker non-root ve PostgreSQL 16 CI tanımlı
- [x] Tenant bazlı atomik belge numarası sayacı ve SQLite eşzamanlılık testi
- [x] Alembic head `20260713_0002` ve temiz SQLite migration doğrulaması

## Açık kapılar

- [x] Gerçek PostgreSQL 16 testleri: 4 test koşulmalı ve geçmeli — 2026-08-03, PG 16.4, `develop@cff45b5`: kapsam 4 değil **63 dosya, 63/63 geçti** (`test_numeric_migration_postgresql.py` dahil). Kanıt: `RC_EVIDENCE_CHAIN.md`.
- [x] Docker Compose temiz başlangıç smoke testi — 2026-08-03: sıfırdan build + up, `db`/`app` healthy, `GET /api/ready` **200**, temiz kurulumda Alembic `20260730_0041 (head)`. Bu koşum `.dockerignore`'ın `.venv-sandbox`'ı kaçırdığı build-kıran kusuru ortaya çıkardı (düzeltildi). Kanıt: `RC_EVIDENCE_CHAIN.md`.
- [x] Mevcut üretim kopyasında REAL/Float -> NUMERIC migration provası — 2026-08-03, canlı `pg_dump` kopyası üzerinde **izole ağda** koşuldu (canlıya tek yazma yok). 90 manifest kolonu `REAL`'e düşürülüp `NUMERIC`'e geri alındı. Üretimde REAL kolon **yoktur**; ölçüm "bu veri float'ta saklansaydı" senaryosunun canlandırmasıdır. **Bulgu: kayıp `REAL`'e yazıldığı anda oluşur ve geri getirilemez** (dönüşümün kendisi sadıktır). Tam turda 33 kolonda parasal sapma; ör. `1056830.71 → 1056830.00`. Operasyonel sonuç: finansal kolonlarda `NUMERIC → REAL` downgrade'i **yasak**, geri dönüş yolu yedekten geri yüklemedir. Kanıt: `RC_EVIDENCE_CHAIN.md` §4.4.
- [x] Migration öncesi/sonrası finansal toplam mutabakatı — 2026-08-03, üretim kopyası: **90 numeric kolon / 27 917 satır** snapshot'landı (`capture_numeric_snapshot`, SHA-256 mühürlü). `upgrade head` **no-op**; şema head'de ve mutabakat bozulmadı. Float turunda oluşan sapmalar §4.4'te kolon bazında listelendi. Kanıt: `RC_EVIDENCE_CHAIN.md` §4.2/§4.4.
- [x] Yedek alma ve geri yükleme provası — 2026-08-03, PG 16.4: `test_platform_backups_postgresql.py` 9 passed, `pg_dump`/`pg_restore` smoke dahil. Kanıt: `RC_EVIDENCE_CHAIN.md`.
- [ ] Legacy test karantinasındaki kritik senaryoların modern pytest'e taşınması
- [x] CI'ın gerçek GitHub çalıştırmasında yeşil doğrulanması — 2026-08-03, PR #228: **6/6 yeşil** (backend-postgresql, backend-quality, container, contract-drift, e2e, frontend).

Bu açık kapılar kapanmadan production-ready etiketi verilmez.
