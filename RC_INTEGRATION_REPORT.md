# RC-1 Integration Report — Sungur Tarım ERP
*(codebase: Yerel Hesap Pro v2.9.0)*

**Operation:** controlled Release-Candidate integration of the approved PR chain into a single testable, single-head branch. Feature freeze active — no features, no redesign, no opportunistic refactor.

## Source & branch
- **Source develop SHA:** `aa18e3b44374`
- **Integration branch:** `release/rc-1-integration`
- **Integration branch HEAD:** `3fde17f`
- **Safety recovery tag:** `rc-1-safety-baseline` → `aa18e3b`

## Integrated PRs (in order)
| Order | PR | Title | Merge commit |
|---|---|---|---|
| 1 | #11 | Machine Cards | `306b7b8` |
| 2 | #10 | Work Orders | `8091683` |
| 3 | #12 | Work Order Parts | `92ca6e0` |
| 4 | #13 | Work Order Billing | `9d5b5e4` |
| 5 | #15 | Enterprise Invoice Engine | `bcfbb45` |
| — | (migrations) | Alembic head merge `20260719_0013` | `81fe5b6` |
| 6 | #14 | AI Development System (docs only) | `6a23446` |
| 7 | #16 | Premium Homepage (frontend only) | `3fde17f` |

## Excluded PRs
- **PR #9** (`codex/v3-machine-cards`) — obsolete. **Excluded.** Verified NOT an ancestor of the RC branch.
- **PR #2** (`feature/codex-dashboard-performance`) — verified **NOT** present in develop → **Excluded** per policy.

## Conflict summary
- **Zero product-code conflicts** across all 7 merges.
- **One documentation add/add conflict:** `IMPLEMENTATION_REPORT.md` (PR #15's report vs PR #16's report). **Resolved by preserving both sections** (no information lost). No code affected.

### Files manually resolved
- `IMPLEMENTATION_REPORT.md` — combined the two implementation reports; conflict markers removed; committed with the PR #16 merge.

## Migration head progression
| Stage | Alembic head(s) |
|---|---|
| Before (develop) | `20260715_0007` (single) |
| During (after #11 + work-order chain) | `20260716_0009` **and** `20260718_0012` (two heads — expected) |
| After merge revision `20260719_0013` | `20260719_0013` (single, mergepoint) |

The merge revision `20260719_0013` (`down_revision = (20260716_0009, 20260718_0012)`) contains **no schema mutation** — empty `upgrade()`/`downgrade()`.

## Backend test results
- **SQLite isolated suite (CI `backend-quality` equivalent):** 63 files → **62 pass, 1 fail.**
  - The single failure is `test_v2_9_backup_reconciliation.py` — a **Windows-only `WinError 32` file-lock** during the SQLite backup temp→final rename. The file and its subject (`app/database_backup.py`) are **byte-identical to develop** (untouched by this integration) and this test is **GREEN on the Linux CI** that is the release target. Classified as a local-environment artifact, **not a product regression**.
- **PostgreSQL suite (8 files, fresh DB each):** **8/8 pass** — each boots the app so `AUTO_MIGRATE` reaches the single merged head `20260719_0013`, proving the merge migration works through the runtime path on PG.
- **Focused RC-chain checks:** machine cards (16), work orders, parts, billing, enterprise invoices, and invoice-layering AST tests all pass; `compileall` clean; import-graph SCC shows **no cycles** (54 modules).

## Frontend test results
- `npm ci` (lockfile-preserving) clean; GSAP/Lenis (PR #16) installed.
- **Production build:** passed (2121 modules). Entry chunk **429 kB / 142 kB gzip** — the approved AppShell lazy-load fix is intact and GSAP/Lenis are correctly excluded from the entry (homepage is lazy at `/tanitim`).
- **Lint:** **0 errors** (289 pre-existing `no-explicit-any` style warnings).
- **Unit tests:** **10 pass** (5 files).

## Build results
- Backend `compileall app alembic`: OK.
- Frontend `tsc -b && vite build`: OK.

## PostgreSQL simulation result
- **Scenario A (fresh install):** clean DB → `alembic upgrade head` → single head `20260719_0013`; app boots and `/api/ready` succeeds (via the passing PG app-smoke test).
- **Scenario B (upgrade from develop):** staged at `0007`, inserted representative data, `pg_dump` backup, `alembic upgrade head` → **sentinel data survived (no data loss)**, new tables (`machines`, `work_orders`, `work_order_parts`, `invoices`) created, final revision `20260719_0013`.
- **Scenario C (failure recovery):** `pg_restore` from the custom-format backup restored the DB to its exact pre-migration state (revision `0007`, data intact). PostgreSQL DDL is transactional per revision; no destructive downgrade performed.

## SQLite result
Clean `alembic upgrade head` to `20260719_0013 (mergepoint)`; all SQLite chain tests pass. (Backup-reconciliation Windows artifact noted above.)

## Known limitations
- One backend test fails **only on Windows** (file-lock); green on Linux CI. Not a product defect.
- Lint carries 289 pre-existing style warnings (0 errors).
- Homepage (PR #16) ships a 1.9 MB PNG poster and treats Lighthouse-95 as a target, not a certified result (see `RC_KNOWN_DEBT.md`).

## Final verdict
**READY FOR RC REVIEW.** The chain integrates cleanly into a single-head, testable Release Candidate with no product-code conflicts, no data loss, and no product-code test regressions.

*Read-only integration operation. The RC branch is NOT merged to develop.*
