# RC Frontend Performance Report

## Audit Environment

- Windows 11 workstation, Node 24.18.0, npm 11.16.0
- Vite 7.3.6 production build served by `vite preview`
- Lighthouse 12.8.2; mobile default throttling and desktop preset
- Single lab run per login profile; homepage result is the separately verified post-AppShell run from the same workstation/session
- Authenticated routes: blocked by missing integrated frontend/backend credentials

## Bundle Table

| Asset | Raw | Gzip | Baseline | Result |
| --- | ---: | ---: | ---: | --- |
| Shared entry | 429.13 kB | 142.15 kB | approximately 142.16 kB gzip | PASS |
| Homepage route | 144.71 kB | 54.60 kB | 54.61 kB gzip | PASS |
| Homepage CSS | 9.91 kB | 2.93 kB | 2.93 kB gzip | PASS |
| AppShell | 25.61 kB | 9.23 kB | lazy | PASS |
| Login | approximately 1.67 kB | 0.99 kB | lazy | PASS |
| DataGrid | 403.59 kB | 122.11 kB | split | PASS |
| CartesianChart/Recharts | 334.42 kB | 98.71 kB | split | PASS |

Source maps: disabled. Asset filenames: hashed. No Work Orders chunk exists because the page is absent.

## Chunk Graph Summary

- Public bootstrap loads shared React/MUI/theme/auth infrastructure and the selected route chunk.
- Authenticated AppShell remains a dynamic chunk and is not loaded by the homepage.
- DataGrid and Recharts remain separate large chunks.
- All current route pages are dynamic imports.
- npm resolved one top-level version of each declared runtime package; no duplicate top-level React version was reported.

## Lighthouse Mobile

### `/giris` after verified fixes

| Metric | Result |
| --- | ---: |
| Performance | 97 |
| Accessibility | 100 |
| Best Practices | 96 |
| SEO | 100 |
| FCP | 1.83 s |
| LCP | 2.30 s |
| TBT | 0 ms |
| CLS | 0 |
| Speed Index | 1.83 s |

### `/tanitim` accepted post-AppShell result

| Metric | Result |
| --- | ---: |
| Performance | 94 |
| Accessibility | 100 |
| Best Practices | 96 |
| SEO | 100 |
| FCP | 1.81 s |
| LCP | 2.93 s |
| TBT | 40.5 ms |
| CLS | 0 |

## Lighthouse Desktop

`/giris` before the meta/contrast-only fix: Performance 100, Accessibility 95, Best Practices 96, SEO 91, FCP 0.41 s, LCP 0.54 s, TBT 0, CLS 0, Speed Index 0.49 s. The failed accessibility/SEO audits were the same issues subsequently fixed and verified on mobile at 100/100. A second desktop run was not needed to prove the deterministic DOM fixes.

## Core Web Vitals

- Login lab LCP 2.30 s and CLS 0 meet good thresholds.
- Homepage lab LCP 2.93 s remains above the 2.5 s good threshold; CLS is 0.
- INP cannot be measured reliably without field interaction data. TBT is 0 ms on login and 40.5 ms on homepage as healthy lab proxies.

## Regressions

- Shared entry matches accepted baseline within harmless rounding.
- AppShell lazy loading remains intact.
- No bundle, route-splitting, image, video, or CLS regression found.
- Work Orders route performance is not measurable because the frontend route is absent.

## Accepted Limitations

- Homepage shared provider/framework cost remains; further optimization requires a public entry or SSR/static architecture and is prohibited during freeze.
- Lighthouse console best-practice penalty in standalone preview includes `/api/auth/me` failure because no backend is attached; it is an environment artifact.
- Field p75 LCP/INP/CLS monitoring is not installed.

# BLOCKED BY BACKEND INTEGRATION
