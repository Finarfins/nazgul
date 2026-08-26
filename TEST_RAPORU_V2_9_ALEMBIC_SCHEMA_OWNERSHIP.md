# Test Report — V2.9 Alembic Schema Ownership

## Passed
- Clean SQLite no-schema -> Alembic head rehearsal.
- 41 tables created; admin/company/warehouse/3 finance accounts seeded.
- Critical suite: 17 passed (`clean_install`, `bootstrap_lock`, `runtime_migrations`, `session_security`, `document_sequence`).
- Python compileall passed.

## Environment limitations
- Full combined pytest still exhibits the previously known process-shutdown timeout.
- Several pre-V2.9 legacy tests require a deliberately excluded sample `backend/veriler.db` or bypass the mandatory first-password-change flow.
- Frontend source was not changed. A fresh frontend rebuild could not run because `node_modules` is intentionally absent from the clean checkpoint; the previously built `frontend/dist` remains included.
- PostgreSQL 16 rehearsal remains pending.
