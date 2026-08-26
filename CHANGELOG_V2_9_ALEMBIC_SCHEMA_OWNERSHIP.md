# V2.9 Alembic Schema Ownership Checkpoint

- Added `20260712_0000_schema_baseline` as the clean-install baseline.
- Alembic is now the only runtime schema owner.
- Removed all `initialize_*` schema calls from application startup.
- Replaced the legacy `schema_migrations` health dependency with Alembic revision status.
- Added DML-only, idempotent bootstrap seeding for the first admin, company, branch, membership, warehouse and finance accounts.
- Existing databases already at `20260714_0004` remain compatible; clean databases traverse the full baseline-to-head chain.
- Critical clean-install, migration-lock, runtime-migration, session-security and document-sequence tests: 17 passed.
