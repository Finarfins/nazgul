# RC Full-Stack Contract Matrix

Compared frontend: PR #16 at `53ed5994e63456135e9c9f02806bad2430ce12db` plus the fixes recorded by this audit.

Compared backend: PR #17 `release/rc-1-integration` at `50ae3e4ae09d6dec75cd60a083a329b7ac7ff070`, which integrates PRs #11, #10, #12, #13, and #15.

All API paths are relative to the frontend `/api` Axios base. Backend references use PR #17 line numbers.

| Feature | Frontend file | Frontend call | Backend file | Backend endpoint | Request match | Response match | Error match | Permission match | Status | Risk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Login | `frontend/src/AuthContext.tsx:34` | `POST /auth/login` `{username,password}` | `backend/app/routers/auth.py:198` | `POST /api/auth/login` | MATCH | PARTIAL MATCH: SPA consumes session payload and intentionally ignores returned access token | MATCH for 401/429 detail | Public before session middleware | MATCH | Low |
| Current session | `frontend/src/AuthContext.tsx:31` | `GET /auth/me` | `backend/app/routers/auth.py:315` | `GET /api/auth/me` | MATCH | MATCH: user, permissions, companies | MATCH: 401 triggers refresh interceptor | authenticated/read | MATCH | Low |
| Refresh | `frontend/src/api.ts:43-58` | `POST /auth/refresh`, CSRF header, cookies | `backend/app/routers/auth.py:247` | `POST /api/auth/refresh` | MATCH | MATCH; rotated cookies are authoritative | PARTIAL MATCH: structured 403 password-change detail becomes generic session expiry | read/self-service | PARTIAL MATCH | Medium: forced-change refresh UX is not specialized |
| Logout | `frontend/src/AuthContext.tsx:36` | `POST /auth/logout` | `backend/app/routers/auth.py:300` | `POST /api/auth/logout`, 204 | MATCH | MATCH | PARTIAL: frontend intentionally swallows logout network errors | read/self-service | MATCH | Low |
| Change password | `frontend/src/AuthContext.tsx:35` | `POST /auth/change-password` | `backend/app/routers/auth.py:323` | same | MATCH | MATCH | MATCH detail handling | read/self-service | MATCH | Low |
| Users | `frontend/src/pages/Users.tsx:18-22` | GET, POST, PATCH `/users` | `backend/app/routers/auth.py:388,409,450` | same | MATCH | MATCH by current usage | MATCH for 400/403/404/409 details | frontend route `users`; backend `users` | MATCH | Low |
| Audit | `frontend/src/pages/Audit.tsx:3` | `GET /audit` | `backend/app/routers/auth.py:491` | same | MATCH | MATCH | PARTIAL: no visible request error state | frontend route `users`; backend `users` | PARTIAL MATCH | Medium UX risk |
| Customers | `frontend/src/pages/Entities.tsx`, `EntityDetail.tsx` | list/detail/create/update/active/delete and CRM children | PR #17 customer/CRM routers | `/api/customers...` | MATCH for existing V2 calls | MATCH in current tests/build | PARTIAL: several mutation paths show raw `detail` | frontend actions not individually guarded; backend writes require `sales` | PARTIAL MATCH | Medium: unauthorized buttons can remain visible |
| Suppliers | same shared files | `/suppliers...` | PR #17 supplier/CRM routers | `/api/suppliers...` | MATCH | MATCH | PARTIAL | frontend actions not individually guarded; backend writes require `purchases` | PARTIAL MATCH | Medium |
| Products | `Products.tsx`, `ProductDetail.tsx`, `ProductDialog.tsx` | `/products...` | `backend/app/routers/products.py:67+` | `/api/products...` | MATCH | MATCH for current use | PARTIAL: raw detail displayed | no page guard; backend writes require `stock` | PARTIAL MATCH | Medium |
| Warehouses | `Warehouses.tsx` | `/warehouses...` | PR #17 warehouse router | `/api/warehouses...` | MATCH | MATCH | PARTIAL | frontend `stock`; backend writes `stock` | MATCH | Low |
| Sales/purchases | `Transactions.tsx`, `TransactionDialog.tsx` | `/orders`, `/purchases` | PR #17 transaction router | same | MATCH | MATCH for current use | PARTIAL: 409 policy override handled, other details passed through | sales/purchases backend; route relies on shell/menu rather than explicit guard | PARTIAL MATCH | Medium |
| Payments/finance | `Payments.tsx`, `Finance.tsx` | `/payments`, `/finance...` | PR #17 finance routers | same | MATCH | MATCH | PARTIAL | frontend finance guard only on finance page; backend finance on all calls | PARTIAL MATCH | Medium |
| Reports/analytics | `Reports.tsx`, `Insights.tsx` | `/reports/summary`, `/analytics/insights` | PR #17 report routers | same | MATCH | MATCH | PARTIAL: Reports has no visible error state | frontend/backend `reports` | MATCH | Low |
| Machine list | no frontend file or route | NOT USED | `backend/app/routers/machines.py:141` | `GET /api/machines?q&customer_id&active&limit&offset` | BLOCKED | BLOCKED | BLOCKED | backend read | BLOCKED | High: no integrated UI |
| Machine detail | no frontend file or route | NOT USED | `backend/app/routers/machines.py:191` | `GET /api/machines/{machine_id}` | BLOCKED | BLOCKED | BLOCKED | backend read | BLOCKED | High |
| Machine create | no frontend file or route | NOT USED | `backend/app/routers/machines.py:199` | `POST /api/machines`; optional `Idempotency-Key` | BLOCKED | BLOCKED | BLOCKED for 400/409 | backend `machines` | BLOCKED | High |
| Machine update/status | no frontend file or route | NOT USED | `backend/app/routers/machines.py:263,310` | `PUT /machines/{id}`, `PATCH /machines/{id}/active` | BLOCKED | BLOCKED | BLOCKED for 404/409 | backend `machines` | BLOCKED | High |
| Work-order list/search | no frontend file or route | NOT USED | `backend/app/routers/work_orders.py:170` | `GET /api/work-orders`; status, technician, customer, machine, date range, q, page, page_size | BLOCKED | BLOCKED: backend returns `{items,page,page_size,total,pages}` | BLOCKED | backend read | BLOCKED | Critical integration gap |
| Work-order detail | no frontend file or route | NOT USED | `backend/app/routers/work_orders.py:256` | `GET /api/work-orders/{id}` | BLOCKED | BLOCKED | BLOCKED for 404 | backend read | BLOCKED | Critical |
| Work-order create | no frontend file or route | NOT USED | `backend/app/routers/work_orders.py:265` | `POST /api/work-orders`, 201 | BLOCKED | BLOCKED | BLOCKED for 400/404/409/422 | backend `sales` | BLOCKED | Critical |
| Work-order update | no frontend file or route | NOT USED | `backend/app/routers/work_orders.py:326` | `PUT /api/work-orders/{id}` | BLOCKED | BLOCKED | BLOCKED; terminal orders return 409 | backend `sales` | BLOCKED | Critical |
| Work-order status | no frontend file or route | NOT USED | `backend/app/routers/work_orders.py:374` | `PATCH /api/work-orders/{id}/status` | BLOCKED | BLOCKED | BLOCKED for invalid transition/CAS conflict | backend `sales` | BLOCKED | Critical |
| Work-order parts list | no frontend file or route | NOT USED | `backend/app/routers/work_order_parts.py:75` | `GET /api/work-orders/{id}/parts` | BLOCKED | BLOCKED: `{items,parts_total}` | BLOCKED | backend read | BLOCKED | Critical |
| Work-order parts mutation | no frontend file or route | NOT USED | `backend/app/routers/work_order_parts.py:89,117,149` | POST/PUT/DELETE parts | BLOCKED | BLOCKED | BLOCKED for inactive refs, stock 409, duplicate 409, terminal 409, 422 | backend `sales` | BLOCKED | Critical |
| Billing summary | no frontend file or route | NOT USED | `backend/app/routers/work_order_billing.py:14` | `GET /api/work-orders/{id}/invoice` | BLOCKED | BLOCKED: Decimal-rich `InvoiceSummaryResponse` | BLOCKED for 404/409 | backend read | BLOCKED | Critical |
| Invoice generate | no frontend file or route | NOT USED | `backend/app/routers/invoices.py:28` | `POST /api/invoices/generate`, 201 | BLOCKED | BLOCKED | BLOCKED for validation/duplicate/conflict | backend `sales` | BLOCKED | Critical |
| Invoice list/detail/history | no frontend file or route | NOT USED | `backend/app/routers/invoices.py:32,47,51` | GET collection/detail/history | BLOCKED | BLOCKED: paginated list and snapshot detail | BLOCKED | backend read | BLOCKED | Critical |
| Invoice cancel/PDF | no frontend file or route | NOT USED | `backend/app/routers/invoices.py:55,62` | POST cancel; GET PDF | BLOCKED | BLOCKED | BLOCKED for 404/409 | cancel `sales`, PDF read | BLOCKED | Critical |

## V3 schema facts that the missing frontend must honor

- Work-order states: `OPEN`, `IN_PROGRESS`, `WAITING_PARTS`, `WAITING_CUSTOMER`, `COMPLETED`, `DELIVERED`, `CANCELLED`.
- Priorities: `LOW`, `NORMAL`, `HIGH`, `URGENT`.
- Work-order money/hours are Decimal strings in JSON; timestamps are ISO-8601 datetimes.
- Parts body: positive `product_id`, `warehouse_id`, `quantity`; non-negative `unit_price`; discount/tax 0..100. Backend totals are authoritative.
- Billing warranty types: `FULL`, `PARTIAL`, `NONE`; all totals are backend-calculated Decimal values.
- List pagination for work orders/invoices is `{items,page,page_size,total,pages}`. Machines use `limit/offset` and return an array.
- Unsafe requests require credentials plus the CSRF cookie echoed as `X-CSRF-Token`; company context is sent as `X-Company-ID`.

## Matrix conclusion

The existing V2 frontend contracts are broadly compatible with the RC backend. The V3 backend contracts are available in PR #17 but have no integrated frontend consumer, route, permission UI, response type, or error behavior to verify.

# BLOCKED BY BACKEND INTEGRATION
