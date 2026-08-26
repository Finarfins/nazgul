# Quality Gates — Alembic Schema Ownership

- [x] Clean SQLite database upgrades from no revision to head.
- [x] Required ERP tables are created only through Alembic.
- [x] Startup code contains no `initialize_*` schema calls.
- [x] `AUTO_MIGRATE=false` still rejects stale databases.
- [x] Bootstrap seed is idempotent and DML-only.
- [x] Health/readiness use Alembic revision state.
- [x] Critical regression group: 17/17 passed.
- [x] Real PostgreSQL 16 clean install and upgrade rehearsal. — 2026-08-03, PG 16.4, `develop@cff45b5`: her PG test dosyası **kendi taze veritabanına** karşı koştu; her koşum migration zincirini sıfırdan `20260730_0041`'e kadar uyguladı (63/63 dosya). Kanıt: `RC_EVIDENCE_CHAIN.md`.
