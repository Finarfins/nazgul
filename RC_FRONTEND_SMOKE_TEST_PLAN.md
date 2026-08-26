# RC Frontend Smoke Test Plan

For every failure capture: timestamp, environment/commit, user/role/company, route, request method/URL/status, response body with secrets redacted, browser console, screenshot, and reproduction steps.

## 1. Valid Login

- Precondition: active RC user assigned to one company; clean browser session.
- Action: open `/giris`, enter valid username/password, submit once.
- Expected: session cookies set; dashboard opens; user/company menu is populated; no token is written to localStorage.
- Failure evidence: login and `/auth/me` network entries, cookie names/flags without values, resulting route.

## 2. Invalid Login

- Precondition: logged out.
- Action: submit a known invalid password once.
- Expected: remain on `/giris`; localized error shown; no session cookie; password is not logged.
- Failure evidence: POST status/body, screenshot, storage state without secret values.

## 3. Logout

- Precondition: authenticated dashboard session.
- Action: activate Logout once, then refresh `/`.
- Expected: POST logout returns 204; cookies cleared; refresh redirects to `/giris`.
- Failure evidence: logout response, subsequent `/auth/me`, route.

## 4. Expired Session

- Precondition: authenticated session with expired access cookie and valid refresh cookie.
- Action: load dashboard or trigger one GET.
- Expected: one refresh request, original request retries once, session continues; invalid refresh redirects to `/giris` without loop.
- Failure evidence: ordered network waterfall and redirect count.

## 5. Dashboard

- Precondition: authenticated user with `read` and selected company.
- Action: open `/`, wait for all cards, change any available date/filter once.
- Expected: loading indicator resolves; tenant-scoped values render; API failure produces visible error.
- Failure evidence: screenshot and dashboard response/status.

## 6. Customers

- Precondition: user with sales permission; company has zero or more customers.
- Action: open `/musteriler`, search rapidly for two terms, clear search.
- Expected: latest query wins; rows/empty state match final query; no stale overwrite.
- Failure evidence: request order, query params, final table.

## 7. Products

- Precondition: stock-permitted user and active warehouse.
- Action: open `/urunler`, create/edit a non-production test product, then reload.
- Expected: validation preserved; server result displayed; list/detail agree.
- Failure evidence: payload with sensitive values redacted, response, final row.

## 8. Machines

- Precondition: integrated V3 frontend present, RC backend PR #17 deployed, user has `machines`.
- Action: open machine list, create a machine with customer/serial, open detail, edit model, deactivate.
- Expected: POST 201, detail matches, PUT persists, PATCH toggles active; duplicate serial produces clean 409.
- Failure evidence: each request/response and machine id.
- Current status: **BLOCKED — frontend route absent**.

## 9. Create Machine Idempotency

- Precondition: same as test 8.
- Action: send the same create action twice with the same Idempotency-Key through the UI/retry mechanism.
- Expected: one machine; both successful responses reference same id.
- Failure evidence: request headers with key partially redacted, ids, list count.
- Current status: **BLOCKED**.

## 10. Work-Order List

- Precondition: integrated V3 frontend; at least two work orders with different status/customer.
- Action: open list; filter status/customer; paginate; clear filters.
- Expected: query uses `page/page_size`; totals/pages agree; empty result is explicit.
- Failure evidence: query params, response envelope, screenshot.
- Current status: **BLOCKED**.

## 11. Create Work Order

- Precondition: active machine/customer/technician in same company; sales permission.
- Action: select machine, matching owner, technician, priority and complaint; submit once, then double-click test in isolated data.
- Expected: one 201 result, generated number, backend-calculated labor; invalid owner/technician produces localized 400/409 without losing form.
- Failure evidence: request payload, status/body, created id/count.
- Current status: **BLOCKED**.

## 12. Update and Status Workflow

- Precondition: OPEN work order.
- Action: edit allowed fields; transition OPEN→IN_PROGRESS→COMPLETED→DELIVERED; attempt normal PUT after delivery.
- Expected: timestamps are server-returned; valid transitions succeed; terminal PUT returns 409 and UI preserves data.
- Failure evidence: ordered requests/responses and timestamps.
- Current status: **BLOCKED**.

## 13. Add Part

- Precondition: mutable work order, active product/warehouse, sufficient stock.
- Action: add quantity, unit price, discount and tax.
- Expected: UI displays server `total_price`; stock reservation occurs once; refreshed parts_total matches server.
- Failure evidence: before/after stock, payload, returned Decimal strings.
- Current status: **BLOCKED**.

## 14. Remove Part

- Precondition: existing reserved part.
- Action: delete once, then retry stale delete.
- Expected: first returns 204 and restores stock; second gives 404 without extra stock restoration.
- Failure evidence: stock values and responses.
- Current status: **BLOCKED**.

## 15. Negative/Duplicate Part

- Precondition: insufficient stock and an existing product/warehouse line.
- Action: submit excessive quantity; then duplicate combination.
- Expected: clean 409; no partial line, negative stock, or authoritative client calculation.
- Failure evidence: stock before/after and transaction responses.
- Current status: **BLOCKED**.

## 16. Billing Summary

- Precondition: COMPLETED or DELIVERED work order with labor/parts/warranty.
- Action: open billing view and refresh.
- Expected: all displayed labor/parts/tax/discount/grand/warranty values come from response; null/zero render correctly.
- Failure evidence: response JSON and annotated screenshot totals.
- Current status: **BLOCKED**.

## 17. Invoice Generation and Reconciliation

- Precondition: billable work order with no invoice.
- Action: generate once, immediately retry, open detail/history/PDF.
- Expected: one invoice; duplicate prevention handled; displayed line sum equals displayed header totals; PDF downloads; history records actions.
- Failure evidence: invoice id/number, response snapshots, manual sum, PDF headers.
- Current status: **BLOCKED**.

## 18. Permission Denied

- Precondition: read-only role.
- Action: deep-link to guarded admin route and attempt a write action visible elsewhere.
- Expected: guarded page redirects; backend returns 403 for direct write; no data mutation or sensitive payload.
- Failure evidence: permission payload, route, 403 response.

## 19. Empty Lists

- Precondition: test company with no records for target module.
- Action: open customers/products/machines/work orders/invoices.
- Expected: explicit empty state, no infinite spinner, controls remain usable.
- Failure evidence: empty API response and screenshot.

## 20. Network Failure

- Precondition: authenticated page loaded; block `/api` or stop backend.
- Action: refresh list and submit one safe test form.
- Expected: request ends near 15-second timeout or immediate refusal; visible error; form values remain; no retry loop.
- Failure evidence: timing, network error, screenshot, request count.

## 21. Mobile Layout

- Precondition: 390 x 844 viewport.
- Action: keyboard/touch through login, homepage, dashboard navigation, one table/card list and one dialog.
- Expected: no horizontal page overflow, controls remain visible, dialog scrolls, touch targets work.
- Failure evidence: full-page screenshots and viewport size.

## 22. Keyboard Navigation

- Precondition: mouse unused.
- Action: Tab/Shift+Tab through login and shell; open/close dialogs with Enter/Escape; use skip link on homepage.
- Expected: logical order, visible focus, focus trapped/restored in dialogs, all actions operable.
- Failure evidence: screen recording or focused-element screenshots.

## 23. Nested Route Refresh

- Precondition: authenticated access and reverse proxy configured.
- Action: directly request `/urunler/1`, an entity detail, and future `/work-orders/1`; refresh each.
- Expected: server returns SPA index, router renders page, unauthorized state redirects correctly; no server 404.
- Failure evidence: document response status/headers and rendered route.

# BLOCKED BY BACKEND INTEGRATION
