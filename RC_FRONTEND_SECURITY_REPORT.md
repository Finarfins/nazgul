# RC Frontend Security Report

## Token Handling

- Access and refresh credentials are cookie-based in browser flows; Axios uses `withCredentials:true`.
- Login response contains an access token for non-browser compatibility, but SPA code does not persist or use it.
- Legacy `yhp_token` and `yhp_user` localStorage entries are removed at startup, expiry, and logout.
- localStorage contains only theme and selected-company context, neither of which grants authorization.

Risk: **LOW**, assuming backend cookie flags and server tenant authorization remain correct.

## Cookie Assumptions

- Production must use HTTPS and backend cookies must have appropriate `Secure`, `HttpOnly` for credential cookies, path/domain, and `SameSite` attributes.
- CSRF cookie must remain readable by JavaScript for the double-submit pattern; access/refresh cookies should not be readable.
- Reverse proxy must preserve Cookie and Set-Cookie headers.

Risk: **MEDIUM until deployment configuration is verified**.

## CSRF

- Unsafe methods attach `X-CSRF-Token` from `yhp_csrf_token`.
- Refresh sends the same CSRF header and backend validates it before rotation.
- Same-origin `/api` is the intended production topology.

Risk: **LOW in intended topology**.

## XSS

- No `dangerouslySetInnerHTML`, direct `innerHTML`, `eval`, Function constructor, unsafe Markdown renderer, or `javascript:` URL was found.
- React escapes displayed backend strings by default.
- Raw backend error details are displayed as text, not HTML.
- Authenticated blob downloads use server responses and object URLs; filenames are local constants in current callers.

Risk: **LOW**.

## Environment Variables

- No tracked `.env` file or `VITE_` secret reference was found.
- API base is hard-coded safely to same-origin `/api`; Vite dev proxy targets localhost only in development config.
- No client-side secret is required. Secrets must never be introduced through `VITE_` variables because they are public at build time.

Risk: **LOW**.

## Sensitive Logging

- No application `console.log`, `console.debug`, hard-coded token, sample credential, debug admin account, or sensitive token output was found in frontend source.
- Source maps are disabled for production builds.

Risk: **LOW**.

## Dependency Audit

Command: `npm ci` followed by npm audit output.

- Production vulnerabilities: **0**.
- Development-only vulnerabilities: **0 reported by installation audit**.
- Production dependencies: 154; total installed dependency graph: 495 packages in this run.
- No dependency update was made.

## API and Authorization Risks

- Client permission checks are UX controls only; PR #17 backend middleware remains authoritative.
- Some V2 mutation buttons are visible without action-level guards and will rely on backend 403. This is an information/UX issue, not an authorization bypass.
- V3 UI is absent, so its permission strings, CSRF mutation flow, error redaction, and invoice download behavior cannot be runtime verified.
- CORS, CSP, HSTS, cookie flags, rate limiting, and proxy limits are deployment/backend responsibilities.

## Production Risk Classification

Existing frontend security posture: **LOW TO MEDIUM**, conditional on HTTPS/cookie/proxy verification.

V3 integrated frontend security posture: **UNVERIFIED / BLOCKED**, because the frontend implementation is absent.

# BLOCKED BY BACKEND INTEGRATION
