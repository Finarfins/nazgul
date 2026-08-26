# RC-1 Production Checklist

## Source control
- [x] Integration branch `release/rc-1-integration` cut from develop `aa18e3b`
- [x] Approved PRs integrated in dependency order (#11→#10→#12→#13→#15, +#14, +#16)
- [x] PR #9 excluded (obsolete); PR #2 excluded (not in develop)
- [x] No conflict markers; no product-code conflicts (one doc conflict resolved)
- [x] Safety recovery tag `rc-1-safety-baseline` created
- [ ] Draft integration PR reviewed and approved by a human maintainer

## CI
- [x] All approved PR heads were CI-green before integration
- [ ] RC branch CI green (backend-quality, backend-postgresql matrix, frontend, container) after push

## Database
- [x] Single Alembic head `20260719_0013` (mergepoint)
- [x] `alembic upgrade head` clean on PostgreSQL (fresh + from-develop)
- [x] `alembic upgrade head` clean on SQLite
- [x] Upgrade-from-develop simulation: **no data loss**
- [ ] Production pre-migration `pg_dump` backup taken

## Backend
- [x] 62/63 isolated SQLite files pass (1 = Windows-only file-lock artifact, green on Linux CI)
- [x] 8/8 PostgreSQL suite files pass (fresh DB each)
- [x] No import cycles; layering fix intact; `compileall` clean

## Frontend
- [x] `npm ci` clean (lockfile preserved)
- [x] Production build passes; entry bundle 142 kB gzip (AppShell lazy fix intact)
- [x] Lint 0 errors (289 pre-existing style warnings)
- [x] 10 unit tests pass

## Security
- [x] Tenant isolation, RBAC deny-by-default, CSRF, security headers verified (prior RC audit)
- [x] Passwords PBKDF2 210k; opaque digested tokens + refresh rotation; login lockout
- [x] No secrets in tracked files; `.env.example` uses placeholders only

## Backup
- [ ] Pre-deploy backup taken and stored off-host
- [x] Restore procedure verified (`pg_restore` round-trip)

## Migration
- [x] Additive/backward-compatible migrations confirmed
- [x] Irreversible revisions documented (restore-from-backup path)

## Deployment
- [x] Non-root read-only container image built and scanned — 2026-08-03: `uid=100(app)`, `ReadonlyRootfs=true` (kök yazma reddedildi), `CapDrop=[ALL]`, `no-new-privileges:true`; `docker scout cves` → **0C/0H/0M/0L** (200 paket). Kanıt: `RC_EVIDENCE_CHAIN.md`.
- [ ] Required env vars validated in staging (incl. `COOKIE_SECURE=true`)
- [ ] Rolling rollout plan confirmed

## Observability
- [x] `security_audit_logs`, `entity_change_logs`, invoice audit/history present
- [x] `/api/live`, `/api/ready` (migration-current gate), `/api/health`

## Rollback
- [x] Mode A (code-only) and Mode B (restore) documented
- [ ] Rollback owner assigned for the deploy window

## Approval
- [ ] Release engineer sign-off
- [ ] DBA / migration owner sign-off
- [ ] Product owner sign-off
