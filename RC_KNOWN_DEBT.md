# RC-1 Known Debt (non-blocking)

These are **verified, non-blocking** items carried into the Release Candidate. **None is fixed in
this integration** (feature freeze). They do not affect production safety, data integrity, tenant
isolation, or security.

## Backend

- **Repeated tax/discount logic (×3).** Implemented in `billing_service`, `invoice_engines`, and
  `routers/work_order_parts`. The earlier "formula-identical" note was **imprecise**: the verified
  invariant is only *within-module* aggregate reconciliation (`Σ items == header parts_total`). At
  the **per-line** level the sites are **not** result-identical — `billing_service` rounds each
  component (`money(raw − money(raw·disc))`, then `money(taxable·tax)`) while
  `routers/work_order_parts._total` rounds once per stage (`money(raw·(100−disc)/100)`, then
  `money(·(100+tax)/100)`). These diverge by one cent on ≈9% of a (qty, price, discount, tax) grid
  (e.g. price `1.00`, discount `7.5%`, tax `0` → `0.92` vs `0.93`); `invoice_engines` additionally
  supports `FIXED` discounts and tax exemptions the other two do not. Note `work_order_parts._total`
  produces the **stored, actually-billed** `total_price`, whereas `billing_service` recomputes a
  summary/preview. Maintenance risk only; no reconciliation or data-integrity impact today.
  *Future:* unifying is a **deliberate money-behavior decision** (choose one canonical rounding —
  preferably the billed `work_order_parts` formula — and accept the resulting summary/preview
  change), **not** a mechanical refactor. Any consolidation must ship with cross-site parity tests.

- **Multiple audit/history stores (×3).** `entity_change_logs` (`record_change`),
  `security_audit_logs` (middleware), and `invoice_audit`/`invoice_history` (invoice service).
  Works; fragmented. *Future:* consolidate.

- **No repository abstraction.** Raw `text()` SQL with **manually repeated** `company_id`
  tenant-scoping in every query. No omission found (tenant isolation verified), but safety rests on
  discipline. *Future:* a thin tenant-scoped query helper.

- **Known N+1 in the work-order list.** `routers/work_orders.py` embeds a correlated
  `SELECT SUM(work_order_parts.total_price)` **twice per row**. Bounded (indexed on
  `(company_id, work_order_id)`; list benchmark < 10 s / 50 pages). *Future:* de-correlate to a
  `LEFT JOIN … GROUP BY`.

- **`can_assign_role` / `can_manage_role`** are byte-identical (both used; pre-existing on develop).

- **Windows-only test artifact.** `test_v2_9_backup_reconciliation.py` fails locally on Windows
  (`WinError 32` file-lock in the SQLite backup rename); **green on the Linux CI**. Not a product
  defect and untouched by this integration.

## Frontend

- **LCP vs the 2.5 s ideal.** The authenticated app measured Perf 100 / LCP 0.5 s (desktop) and
  97 / 2.3 s (mobile) — under 2.5 s. The **public homepage** (`/tanitim`, PR #16) ships a **1.9 MB
  PNG poster** and treats **Lighthouse-95 as a target, not a certified result** (no Lighthouse CI
  configured). Under real mobile throttling the homepage LCP may exceed 2.5 s until responsive
  AVIF/WebP media variants land. *Future:* responsive media pipeline + Lighthouse CI budgets.

- **Lint style warnings.** 289 pre-existing `@typescript-eslint/no-explicit-any` warnings (0 errors).

- **Work Orders / invoice frontend surface.** The V3 work-order/invoice **UI is not part of this
  release** (backend + homepage only per the enterprise-invoice report's "Deferred work"); those
  screens remain a future frontend deliverable.

---

**Do not fix these in the RC integration.** They are recorded for post-RC planning only.
