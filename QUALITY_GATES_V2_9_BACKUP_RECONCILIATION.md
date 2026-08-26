# V2.9 Backup + Migration Reconciliation Quality Gates

- [x] SQLite online backup API kullanılıyor.
- [x] Backup sonrası `PRAGMA integrity_check` zorunlu.
- [x] SHA-256 manifest oluşturuluyor ve doğrulanıyor.
- [x] Restore geçici dosyaya yapılıp atomik replace ile tamamlanıyor.
- [x] Üzerine yazmadan önce pre-restore güvenlik kopyası alınıyor.
- [x] PostgreSQL parolası komut satırına yazılmıyor; `PGPASSWORD` ortamı kullanılıyor.
- [x] Numeric kolon manifesti tek kaynağa taşındı.
- [x] Migration öncesi/sonrası satır, NULL, toplam, min ve max mutabakatı var.
- [x] Snapshot checksum ile değişiklik tespit ediliyor.
- [x] Yeni testler CI matrixine eklendi.
- [x] Gerçek PostgreSQL 16 üzerinde pg_dump/pg_restore provası. — 2026-08-03, PG 16.4, `develop@cff45b5`: `test_platform_backups_postgresql.py` **9 passed** (`test_pg_dump_and_pg_restore_smoke` dahil). Kanıt: `RC_EVIDENCE_CHAIN.md`.
- [x] Gerçek legacy REAL veri setinde Alembic + finansal mutabakat provası. — 2026-08-03: legacy float dünyası **gerçek üretim verisi** üzerinde canlandırıldı (90 kolon `REAL`'e düşürülüp `NUMERIC`'e geri alındı), izole ağda, canlıya yazma olmadan. Ayrıca `alembic downgrade` geri alınamaz revizyonda **fail-closed** reddetti (`0041 downgrade refused`, 11 `service_fee` satırı). Kanıt: `RC_EVIDENCE_CHAIN.md` §4.3/§4.4.
