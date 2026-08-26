# RC-1 Merge Manifest

**Integration branch:** `release/rc-1-integration` @ `3fde17f`
**Base:** `develop` @ `aa18e3b44374`
**Method:** `git merge --no-ff` per PR, in dependency order (real three-way merges; no cherry-pick, no manual file copying, no squash).

## Integrated PRs

### PR #11 — Machine Cards
- Source branch: `feature/v3-machine-cards-foundation`
- Source SHA: `e42abfd`
- Integration order: 1
- Merge commit: `306b7b8`
- Dependencies: none (base develop)
- Conflict status: **clean**
- Verification: machine model/schema/router present; migrations `0008_machine_cards` + `0009_machine_idempotency`; 16 machine-card tests pass; tenant isolation & RBAC (`/api/machines` permission) present; PR #9 confirmed excluded.

### PR #10 — Work Orders
- Source branch: `feature/v3-work-orders-foundation`
- Source SHA: `350cbfe`
- Integration order: 2
- Merge commit: `8091683`
- Dependencies: machine cards (0008 baseline)
- Conflict status: **clean** (auth.py permissions unioned automatically: machines + work-orders)
- Verification: `routers/work_orders.py`, schemas, migration `0009_work_orders`; alembic multi-head as expected.

### PR #12 — Work Order Parts
- Source branch: `feature/v3-work-order-parts`
- Source SHA: `e032ebf`
- Integration order: 3
- Merge commit: `92ca6e0`
- Dependencies: PR #10
- Conflict status: **clean**
- Verification: `routers/work_order_parts.py` (imports `work_orders`, `inventory` — stacked dependency intact); migration `0010`; parts tests pass.

### PR #13 — Work Order Billing
- Source branch: `feature/v3-3-work-order-billing`
- Source SHA: `6c22fd8`
- Integration order: 4
- Merge commit: `9d5b5e4`
- Dependencies: PR #12
- Conflict status: **clean**
- Verification: `routers/work_order_billing.py`, `work_order_billing_schemas.py`, migration `0011` (warranty columns); billing tests pass.

### PR #15 — Enterprise Invoice Engine
- Source branch: `feature/v3-4-enterprise-invoice-engine`
- Source SHA: `056c89f`
- Integration order: 5
- Merge commit: `bcfbb45`
- Dependencies: PR #13
- Conflict status: **clean**
- Verification: `invoice_service.py`, `billing_service.py`, `invoice_engines.py`, `routers/invoices.py`, migration `0012`; layering fix intact (`invoice_service → billing_service`, no router import); all 5 routers registered in `main.py`; invoice tests + AST layering tests pass.

### Alembic head merge (required integration fix)
- Revision: `20260719_0013_merge_machine_and_work_order_heads`
- Commit: `81fe5b6`
- `down_revision = (20260716_0009, 20260718_0012)`; empty `upgrade()`/`downgrade()`; no schema mutation.
- Verification: `alembic heads` → single `20260719_0013`; `upgrade head` clean on PostgreSQL and SQLite.

### PR #14 — AI Development System
- Source branch: `chore/ai-development-system`
- Source SHA: `85f7d9c`
- Integration order: 6
- Merge commit: `6a23446`
- Dependencies: none (independent)
- Conflict status: **clean**
- Verification: adds `.ai/`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` only; **zero** change under `backend/app` or `backend/alembic` (no runtime impact).

### PR #16 — Premium Homepage
- Source branch: `feature/premium-homepage-experience`
- Source SHA: `53ed599`
- Integration order: 7
- Merge commit: `3fde17f`
- Dependencies: none (independent, frontend only)
- Conflict status: **one doc conflict** — `IMPLEMENTATION_REPORT.md` (add/add), resolved by preserving both reports. **Zero** backend change.
- Verification: approved perf fix present (`const AppShell=lazy(...)`); `npm ci` + build + lint (0 errors) + 10 tests pass; entry bundle unchanged at 142 kB gzip; GSAP/Lenis lazy at `/tanitim`.

## Explicit exclusions
- **PR #9 — EXCLUDED** (`codex/v3-machine-cards`, obsolete). Verified NOT an ancestor of `release/rc-1-integration`.
- **PR #2 — EXCLUDED** (`feature/codex-dashboard-performance`). Verified **NOT already part of develop**; therefore not included per policy.
