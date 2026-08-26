# RC Frontend Integration Report

## Executive Summary

The current frontend cannot be approved as the integrated V3 RC client. PR #17 contains the reviewed Machine Cards, Work Orders, Parts, Billing, and Enterprise Invoice backend stack, but neither PR #16 nor PR #17 contains the reported `feature/v3-1-work-orders-frontend` implementation. No V3 frontend routes or API calls exist in the inspected tree. Static backend contracts were verified directly; runtime V3 integration is blocked.

Existing V2/frontend behavior remains buildable and test-green. Three objectively verified login quality defects were fixed: missing favicon, missing default meta description, and a WCAG contrast failure. No backend or API contract was changed.

## Branch and Commit Information

- Active branch: `feature/premium-homepage-experience`
- Starting SHA: `53ed5994e63456135e9c9f02806bad2430ce12db`
- Target/base: `develop`
- Compared integrated backend: PR #17, `release/rc-1-integration`, SHA `50ae3e4ae09d6dec75cd60a083a329b7ac7ff070`
- Node: 24.18.0
- Package manager: npm 11.16.0 with `frontend/package-lock.json`
- Bundler: Vite 7.3.6
- Environment strategy: no tracked `.env`; frontend uses same-origin `/api` and Vite dev proxy only.

## Scope

Complete current frontend inventory, current API usage, auth/RBAC/session behavior, PR #17 V3 backend router/schema contracts, build/chunks, security, public Lighthouse, release hygiene, deployment assumptions, and operator smoke planning.

## Route Inventory

All page components are lazy. `AppShell` is also lazy. Protected routes inherit `Protected`; explicit feature permission is noted below.

| Route | Component | Access/permission | Primary backend dependency | States | Verification |
| --- | --- | --- | --- | --- | --- |
| `/tanitim` | PremiumHomepage | public | none | static | verified build/browser/Lighthouse |
| `/giris` | Login | public | auth login/me | loading/error | verified build/Lighthouse; no live backend login |
| `/sifre-degistir` | ChangePassword | session self-service | auth change-password | loading/error | static verified |
| `/` | Dashboard | protected/read | dashboard endpoints | loading/error/empty | unit test; runtime blocked by credentials |
| `/satislar` | Transactions(sale) | protected; backend sales | orders/customers/products | loading/error/empty | static/unit coverage |
| `/alislar` | Transactions(purchase) | protected; backend purchases | purchases/suppliers/products | loading/error/empty | static/unit coverage |
| `/belge-akislari` | WorkflowDocuments | protected; backend sales/purchases | workflow | loading/partial errors/empty | static |
| `/musteriler` | Entities(customer) | protected; backend writes sales | customers | loading/error/empty | static |
| `/tedarikciler` | Entities(supplier) | protected; backend writes purchases | suppliers | loading/error/empty | static |
| `/musteriler/:id` | EntityDetail(customer) | protected | customer CRM | loading/error | static |
| `/tedarikciler/:id` | EntityDetail(supplier) | protected | supplier CRM | loading/error | static |
| `/urunler` | Products | protected; backend writes stock | products | loading/error/empty | static/component test |
| `/urunler/:id` | ProductDetail | protected | products | loading/error/not-found via error | static |
| `/odemeler` | Payments | protected; backend finance | payments | loading/error/empty | static |
| `/nakit-yonetimi` | Finance | protected + `finance` | finance | error/data tables | static |
| `/stok-hareketleri` | StockMovements | protected | products/warehouses | table/empty; weak error | static |
| `/depolar` | Warehouses | protected + `stock` | warehouses | error/table | static |
| `/raporlar` | Reports | protected + `reports` | reports summary | loading returns null; weak error | static |
| `/analizler` | Insights | protected + `reports` | analytics | loading/error | static |
| `/firmalar` | Companies | protected + `users` | companies | table/error | static |
| `/kullanicilar` | Users | protected + `users` | users | error/table | static |
| `/islem-gecmisi` | Audit | protected + `users` | audit | table; weak error | static |
| `*` | Navigate `/` | route fallback | auth/protected resolution | n/a | verified definition |

Missing routes: Machines, Work Orders, Work Order detail, Parts, Billing, Invoice list/detail/generation.

## Authentication

- Browser session uses access/refresh cookies with `withCredentials:true`; SPA ignores the legacy-compatible access token returned in login JSON.
- CSRF double-submit header is attached to POST/PUT/PATCH/DELETE. Refresh also sends the CSRF header.
- Concurrent 401 refreshes are serialized through one promise; one retry per request prevents loops.
- Failed refresh clears legacy storage and redirects to `/giris`.
- Reload calls `/auth/me`; selected company persists in localStorage and is sent as `X-Company-ID`.
- Logout revokes server sessions when reachable and always clears local UI state.
- Gaps: no return-to-original-route behavior; authenticated access to `/giris` is not redirected; server permission changes require `/auth/me` reload; structured password-change-required refresh response is not surfaced specifically; multi-tab auth expiry relies on reload/event behavior rather than BroadcastChannel.

## Authorization

Backend middleware enforces read/feature permissions. Frontend explicitly guards finance, stock warehouses, reports/analytics, companies/users/audit. Several other pages rely on menu visibility or backend rejection and do not guard mutation controls individually.

| Feature | Frontend | Backend | Result | Risk |
| --- | --- | --- | --- | --- |
| Machine reads/writes | missing | read / `machines` | BLOCKED | high |
| Work-order/parts writes | missing | `sales` | BLOCKED | critical |
| Invoice writes | missing | `sales` | BLOCKED | critical |
| Finance | `finance` route | `finance` | MATCH | low |
| Reports | `reports` route | `reports` | MATCH | low |
| Users/audit | `users` route | `users` | MATCH | low |
| Customer/supplier/product actions | no explicit button guard | sales/purchases/stock | PARTIAL | unauthorized actions may be visible and fail 403 |

## Work Orders

Backend PR #17 provides list/detail/create/update/status endpoints from PR #10. Machine/customer/technician tenant validation, status transitions, terminal protection, timestamps, CAS status change, audit, Decimal labor calculation, and pagination are present. Frontend status: **MISSING**. No page, types, filters, pagination adapter, mutation handling, or route exists.

## Parts

Backend PR #17 provides transactional list/create/update/delete from PR #12 with active references, stock reservation/restore, duplicate protection, terminal protection, audit, and backend totals. Frontend status: **MISSING**. No formula mismatch exists because no consumer exists; integration is unverified.

## Billing

Backend PR #17 exposes `GET /work-orders/{id}/invoice` from PR #13. It returns Decimal labor, parts, tax, discount, grand total, and warranty allocation. Frontend status: **MISSING**.

## Invoices

Backend PR #17 exposes generate/list/detail/history/cancel/PDF from PR #15. Frontend status: **MISSING**. Idempotency, duplicate conflict UX, reconciliation, PDF download, and Decimal display cannot be verified.

## Machine Cards

Backend PR #17 exposes list/detail/create/update/active from PR #11, including tenant isolation, uniqueness, soft deactivation, customer association, and optional POST idempotency. Frontend status: **MISSING**. No obsolete PR #9 reference was found in the current frontend.

## Error Handling

- Global: one render ErrorBoundary; Axios 401 refresh/retry; 15-second timeout.
- Local: most forms show backend `detail`; some list effects swallow failures or render blank/null.
- 400/403/404/409/422 are not normalized globally. 429 login detail is displayed. 500/network errors can expose Axios message text in some screens, but no server stack rendering was found.
- Several mutations lack duplicate-click/idempotency guards outside button busy states. No V3 behavior can be tested.

## Race-Condition Findings

- Command palette correctly debounces and aborts stale searches.
- Entities uses a request sequence to prevent stale list overwrites.
- Several legacy searches use timers without AbortController; no reproducible corruption was demonstrated.
- Refresh requests are deduplicated.
- No theoretical race was changed.

## Build and Test Results

| Command | Result |
| --- | --- |
| `npm ci` | first attempt tooling FAIL (preview-held esbuild lock); retry PASS, 494 packages, 0 vulnerabilities |
| `npx tsc -b` | PASS |
| `npm run lint` | PASS with 0 errors, 289 pre-existing warnings |
| `npm test -- --run` | PASS: 5 files, 10 tests executed |
| `npm run build` | PASS: 2,121 modules transformed |
| authenticated integration tests | BLOCKED BY ENVIRONMENT/frontend absence |

## Backend Dependencies

- PR #17 contains the complete reviewed backend chain and single-head migration integration.
- The frontend feature branch named in the brief is unavailable locally/remotely and is not included in PR #17.
- Current frontend and PR #17 are therefore not a complete functional V3 pair.

## Applied Fixes

1. Added a real favicon reference to the existing icon asset; before: 404; after: request succeeds.
2. Added a safe default document description; before: login SEO audit failed; after: SEO 100.
3. Changed login brand accent from `secondary.main` to `secondary.dark`; before: 2.45:1 contrast; after: Accessibility 100.

Files: `frontend/index.html`, `frontend/src/pages/Login.tsx`. Risk: low. Verification: mobile Lighthouse and build/tests.

## Unresolved Findings

- Entire V3 frontend surface is missing.
- No live authenticated dashboard/Work Orders Lighthouse or integration journey was possible.
- V2 action-level permission guards are incomplete.
- Some legacy screens have weak error/empty-state presentation.
- Lint has 289 warnings; no warning cleanup was attempted in freeze.

## Known Debt

No E2E suite, no coverage threshold, no axe CI, no performance budget, no global error DTO normalization, no real-user Web Vitals, and no automated route/permission matrix.

## Final Verdict

# BLOCKED BY BACKEND INTEGRATION
