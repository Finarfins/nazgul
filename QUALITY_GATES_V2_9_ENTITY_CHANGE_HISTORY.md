# Quality gates

- Entity history + delete/restore test: passed.
- Clean SQLite installation and migration to `20260714_0006`: passed.
- Audit traceability and cookie-session regression: passed.
- Policy, stock, transaction and production deployment regression group: 11 passed.
- Python compileall: passed.
- Legacy `test_finance_core.py` remains quarantined because it requires the intentionally excluded sample `backend/veriler.db`.
- Real PostgreSQL 16 / Docker / HTTPS gate remains external.
