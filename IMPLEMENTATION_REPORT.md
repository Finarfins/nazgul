# Enterprise Invoice Engine Implementation Report

## Scope

Introduces a tenant-isolated, immutable invoice aggregate generated from completed or delivered Work Orders, with deterministic Decimal calculations, atomic numbering, audit/history, cancellation and printable A4 PDF output.

## Verification

- Python bytecode compilation: passed.
- Enterprise invoice SQLite tests: 2 passed.
- Enterprise invoice PostgreSQL concurrency test: collected; skipped locally because no PostgreSQL test URL was configured. It is included in the CI PostgreSQL matrix.
- Full isolated backend suite: 61 of 62 test files passed. The sole failure is the pre-existing Windows/OneDrive file-lock failure in `test_v2_9_backup_reconciliation.py`; all invoice and Work Order tests passed.
- PDF output: rendered to PNG and visually inspected for A4 layout, Turkish glyphs, table alignment, QR code, barcode, totals and pagination.
- Encoding regression scan: passed for all new invoice source, test and documentation files.

The repository has no configured Python formatter, linter or static type checker. `compileall` and `git diff --check` were used as the available source-quality gates; CI runs the same compilation gate.

## Architectural issue observed

The repository currently mixes SQLAlchemy Core tables and text queries directly in routers. Introducing a new repository/CQRS framework only for invoices would create inconsistent architecture and unnecessary maintenance cost. This implementation therefore keeps orchestration in a focused service and leaves a broader persistence-layer standardization as a future, separately approved decision.

Risk: domain rules can spread across routers as modules grow.

Recommendation: after the service workflow stabilizes, evaluate a small shared transaction/service convention across financial modules.

Estimated effort: 3-5 engineering days for a design proposal and one pilot module; larger migration should be planned separately.

---

# Follow-up: A-2 / DEP-1 — Layering inversion fix (architecture_review_v3.md)

## Exact problem

`invoice_service.py` (the invoice service/domain layer) imported and called a
router handler:

```python
from .routers.work_order_billing import invoice_summary          # service -> router import
...
summary = invoice_summary(payload.work_order_id, request, db)     # HTTP Request pushed into the domain layer
```

This inverted the dependency direction (a service depended on a router) and pushed
the HTTP `Request` object into the domain calculation. It also coupled invoice
generation to FastAPI and blocked unit-testing the summary calculation without a
`Request`. Confirmed High finding, PR #15.

## Smallest safe fix

The reusable financial calculation was extracted verbatim (same SQL, same Decimal
math, same PostgreSQL `FOR SHARE OF wo` lock) into a neutral module that never
touches the HTTP layer. The router and the service both call it.

## Exact files changed

| File | Change |
|---|---|
| `backend/app/billing_service.py` | **New** neutral module. `build_invoice_summary(db, cid, work_order_id) -> dict` — the extracted calculation, no `Request`, no router import. |
| `backend/app/routers/work_order_billing.py` | `invoice_summary` route reduced to the HTTP boundary: resolve tenant via `company_id(request)`, then `return build_invoice_summary(db, company_id(request), work_order_id)`. Response and `response_model` unchanged. |
| `backend/app/invoice_service.py` | Removed `from .routers.work_order_billing import invoice_summary`; added `from .billing_service import build_invoice_summary`; call site now `build_invoice_summary(db, cid, payload.work_order_id)`. |
| `backend/test_v3_invoice_layering.py` | **New** tests (structural + behavioural equivalence). |

## Before / after dependency direction

- **Before:** `routers/invoices` → `invoice_service` → **`routers/work_order_billing`** (service depends on router; `Request` enters the domain calc).
- **After:** `routers/invoices` → `invoice_service` → `billing_service`; and `routers/work_order_billing` → `billing_service`. Both routers and the service depend on the neutral `billing_service`; the service no longer imports any router, and the calculation receives a plain `cid` instead of a `Request`.

`cid` passed to `generate_invoice` is `company_id(request)` (invoices router), i.e.
identical to the value the old code re-derived inside `invoice_summary`, so behaviour
is preserved exactly.

## Test evidence

- `test_v3_invoice_layering.py` (SQLite): 4 passed — structural guarantees (`invoice_service` imports no router; `billing_service` contains no `Request`; `build_invoice_summary` signature is `(db, cid, work_order_id)`) plus behavioural equivalence (router endpoint totals == direct `build_invoice_summary` totals == generated-invoice totals; tenant isolation returns 404 across the delegation).
- `test_enterprise_invoices.py`, `test_v3_work_order_billing.py` (SQLite): unchanged, all passed (7 passed together with the new file).
- `test_v3_work_orders.py`, `test_v3_work_order_parts.py` (SQLite): passed (no collateral impact).
- `python -m compileall app alembic`: passed.
- **PostgreSQL 16 (local, port 5433, clean DB per file as in CI):**
  - `test_enterprise_invoices_postgresql.py` — 1 passed (exercises `generate_invoice` → `build_invoice_summary`, concurrent numbering, `FOR SHARE OF wo`).
  - `test_work_order_billing_postgresql.py` — passed.
  - (Note: running multiple PG test files against one shared local DB reproduces the pre-existing hardcoded-`admin123` login fragility because an earlier file rotates the admin password; CI gives each matrix entry a fresh `postgres:16` service, so each passes independently. This is unrelated to this change.)

## Migration impact

None. No schema, migration, table, column, index or constraint was touched. Alembic
chain unchanged.

## API compatibility

None broken. `GET /work-orders/{id}/invoice` returns the identical body and
`response_model` (`InvoiceSummaryResponse`). `POST /invoices/generate` and all other
invoice endpoints are unchanged. Money serialization, warranty coverage and totals are
byte-identical.

## Scope discipline

- Only the confirmed High finding (A-2 / DEP-1) was implemented.
- **A-1** (two architectural styles across the chain) was treated as documented technical
  debt only — no broad refactor, no repository/CQRS/event-bus introduced.
- No unrelated Work Orders code was refactored. Tenant isolation and transaction
  boundaries (including the PG row lock and the single `db.commit()` in `generate_invoice`)
  are preserved.

## Remaining Medium/Low findings NOT implemented (from architecture_review_v3.md)

D-1 (tax/discount math duplicated 3×), D-4 (three audit mechanisms), API-1 (money
serialized as number vs string), API-2 (GET invoice detail/pdf write + commit), S-1
(invoices gated by `sales` rather than `finance`), P-1 (correlated subquery per row in
the work-order list), D-2 (`can_assign_role`/`can_manage_role` duplicate), EX-1 (no
domain exception hierarchy). These remain open and unmodified.

---

## Deferred work

- fiscal/e-invoice provider integration;
- jurisdiction-specific withholding tax;
- accounting-ledger postings for cancellation, refunds and credit notes;
- configurable logo asset management;
- UI.

---

# Premium Homepage Implementation Report

## Scope

Introduces an isolated public enterprise homepage at `/tanitim` without changing the authenticated ERP dashboard, backend APIs, authorization or data model.

## Files and architecture

The page is split into configurable content, reusable components, SEO metadata, GSAP timeline management, Lenis scrolling and scoped design-token CSS. The route is lazy-loaded so GSAP and Lenis are excluded from the authenticated application's initial route unless the homepage is visited.

## Validation

- Frontend tests: 5 files, 9 tests passed.
- Production TypeScript/Vite build: passed.
- Strict lint for changed source: 0 warnings and 0 errors.
- Repository lint: 0 errors; existing unrelated warnings remain.
- Browser QA: desktop and 390×844 mobile passed with no horizontal overflow or console errors.
- SEO checks: title, canonical, OpenGraph, Twitter metadata, Organization and Breadcrumb JSON-LD present.
- `git diff --check`: passed.

The requested `vendor/bin/pint` and `php artisan test` commands are not applicable because this repository is React/Vite with a Python backend, not Laravel.

## Risks and remaining work

- The generated 1.9 MB PNG poster should receive AVIF/WebP responsive variants before a high-traffic launch.
- A production video is not bundled. The component supports progressive IntersectionObserver loading and poster fallback once a brand-approved video URL is configured.
- Customer testimonials and partner labels require business verification before public publication.
- Lighthouse 95+ is a target, not a certified result; automated Lighthouse CI is not configured in this repository.

## Suggested next task

Create the approved responsive media pipeline and add Lighthouse CI budgets without changing homepage content or the ERP application.
