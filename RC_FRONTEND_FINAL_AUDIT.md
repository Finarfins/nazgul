# Sungur Tarim ERP Frontend RC Final Audit

Audit target: Draft PR #16, `feature/premium-homepage-experience`, production build of `/tanitim` and static review of the complete frontend.

Audit date: 2026-07-18

## Executive Summary

The frontend is suitable for release-candidate deployment, but not yet certified for unrestricted production rollout. Build, tests, TypeScript, CI, dependency security, accessibility, SEO, layout stability, cleanup lifecycles, and public-route behavior pass. One reproducible production performance defect was corrected: the authenticated `AppShell` was statically included in the public homepage bootstrap. Making that existing boundary lazy reduced the shared entry from 191.59 kB to 142.16 kB gzip and improved mobile Lighthouse Performance from 91 to 94.

The remaining primary risk is mobile LCP at 2.93 seconds in the lab, above the 2.5-second Core Web Vitals good threshold. This should be validated under release-candidate hosting with CDN/cache behavior and real-user monitoring before production promotion.

## Architecture

- The application uses a single React 19/Vite entry, React Router route boundaries, application-level theme/auth providers, an error boundary, a protected authenticated layout, and route-local pages.
- Page routes are dynamically imported. The authenticated `AppShell` is now dynamically imported and no longer enters the public homepage request path.
- Public homepage code is isolated under `src/premium-homepage`; shared ERP components and pages remain under `src/components` and `src/pages`.
- State ownership is mostly local, with authentication/company selection and theme as the two global contexts. API data is fetched by owning pages/components.
- Dependency direction is conventional: pages consume shared components/API helpers; shared utilities do not import pages.
- Production build completed with 2,121 transformed modules and no unresolved or circular-import build failures. There is no dedicated circular-dependency CI rule, so this is a build/static-review conclusion rather than a formal graph proof.
- `frontend/vite.config.ts.bak` is a tracked obsolete-looking backup file. It is not part of the build and was not removed during Feature Freeze.

## React Quality

- Route pages, premium homepage, and authenticated shell use `React.lazy` with an application-level `Suspense` fallback.
- The application-level `ErrorBoundary` contains render failures. There is no route-specific recovery UI; this is acceptable for RC but can reduce fault isolation.
- Keys inspected in repeated homepage content are stable content identifiers or stable configured values.
- `StrictMode` is enabled. Premium homepage effects are mount/cleanup/remount safe.
- GSAP timelines and ScrollTriggers are scoped to `gsap.context`; teardown calls `context.revert()`.
- Lenis ticker integration removes the ticker callback and calls `destroy()` on cleanup.
- Window listeners, IntersectionObserver, JSON-LD nodes, and temporary object URLs have cleanup paths.
- The application is client-rendered and does not perform SSR hydration; hydration mismatch is not an active runtime path.
- Existing hook dependency lint warnings remain in legacy/authenticated components. They were not changed because this audit did not establish a reproducible production defect for those warnings.

## Performance

### Lighthouse 12.8.2

Production Vite preview, mobile defaults:

| Metric | Result |
| --- | ---: |
| Performance | 94 |
| Accessibility | 100 |
| Best Practices | 96 |
| SEO | 100 |
| FCP | 1.81 s |
| LCP | 2.93 s |
| CLS | 0 |
| TBT | 40.5 ms |
| Speed Index | 1.91 s |
| Total transfer | approximately 311 kB |
| Requests | 7 |

INP requires field interaction data and cannot be certified by this lab run. TBT is a healthy lab responsiveness proxy.

### Bundle analysis

| Asset | Before gzip | After gzip | Assessment |
| --- | ---: | ---: | --- |
| Shared entry | 191.59 kB | 142.16 kB | Improved by 49.43 kB (25.8%) |
| Homepage route | 54.60 kB | 54.61 kB | Stable |
| Homepage CSS | 2.93 kB | 2.93 kB | Stable |
| Authenticated AppShell | bundled in entry | 9.24 kB lazy chunk | Correctly deferred |

- Lighthouse still estimates approximately 88 kB of unused JavaScript on the public route, primarily from shared providers/framework dependencies.
- The homepage request loads the shared entry; the AppShell chunk is not requested on `/tanitim`.
- Below-the-fold images use native lazy loading. The LCP hero is eager, high-priority, dimensioned, asynchronously decoded, and route-preloaded.
- The hero WebP is 108,630 bytes, a 94.6% reduction from the previous PNG.
- No production video payload is shipped. Optional video uses poster fallback, `preload="none"`, IntersectionObserver activation, muted inline playback, and cleanup.
- No runtime web font is fetched, avoiding font-induced CLS.
- Recharts and DataGrid are separated into large lazy chunks. DataGrid provides virtualization for tabular desktop workloads.
- No bundle-analyzer package is installed; Vite output and Lighthouse network/unused-code audits were used.

## Accessibility

- Homepage Lighthouse Accessibility is 100.
- Rendered audit found one `h1`, one `main`, no missing image `alt` attributes, no unlabeled buttons, and no horizontal overflow at 1280 x 720.
- Prior 390 x 844 validation found no overflow and correctly stacked CTAs.
- A keyboard skip link, native links/buttons, visible focus styles, semantic headings/landmarks, reduced-motion handling, and decorative empty alt text are present.
- MUI dialogs/menus provide baseline focus management and keyboard behavior. Authenticated workflows were statically reviewed but not fully screen-reader-tested against a live backend session.
- Loading fallbacks are visible but do not consistently expose explicit live-region status text across the legacy application.
- Lighthouse contrast passed for the public page. Dark-mode and every authenticated error/empty state were not exhaustively measured in this RC audit.

## Security

- `npm audit --omit=dev` reports 0 known production dependency vulnerabilities across 154 production dependencies.
- No `dangerouslySetInnerHTML`, `eval`, `new Function`, embedded secret, or application console logging was found in frontend source.
- Authentication uses credentialed HTTP cookies; legacy access-token/user values are actively removed from Web Storage.
- Unsafe API methods attach the CSRF cookie value as `X-CSRF-Token`; refresh requests are serialized to avoid refresh storms.
- Company selection is stored locally only as routing/request context; server-side tenant enforcement remains mandatory.
- API base is same-origin `/api`, reducing production CORS exposure. Correct cookie `Secure`/`SameSite`, CSRF-cookie readability, CSP, CORS, and proxy headers remain deployment/backend responsibilities.
- Source maps are disabled in the production build.

## Code Quality

- TypeScript strict mode and production compilation pass.
- ESLint completes with 0 errors and 289 warnings. Most warnings are legacy `any`, import ordering, and hook-dependency findings. Bulk cleanup was prohibited by Feature Freeze.
- No TODO, FIXME, XXX, temporary feature flag, or debug console statement was found in application source.
- The tracked Vite backup configuration is technical debt but not a runtime defect.
- Some large legacy components remain densely implemented and weakly typed. Refactoring them during RC would introduce disproportionate regression risk.

## Testing

- Vitest: 5 test files, 10 tests, all passing.
- Production TypeScript/Vite build: passing.
- GitHub Actions before this audit change: frontend, backend quality, PostgreSQL matrix, and container checks passing.
- Existing tests cover selected homepage lifecycle/SEO behavior and a small number of ERP components/pages.
- Material gaps: no coverage threshold, no browser E2E suite, no automated axe suite, no authenticated keyboard journey, no visual regression tests, and no performance budget in CI.
- Current tests are predominantly behavior assertions with mocks; no snapshot overuse was found.

## Known Risks

1. Mobile lab LCP is 2.93 seconds, above the 2.5-second good threshold.
2. Public bootstrap still includes shared React/MUI/theme/auth infrastructure; Lighthouse estimates about 88 kB unused JavaScript.
3. The frontend test surface is small relative to the number of pages and transactional workflows.
4. Legacy ESLint warnings include hook-dependency findings that require targeted behavioral investigation after release freeze.
5. Accessibility certification is strongest for the public homepage; authenticated workflows lack automated end-to-end accessibility coverage.

## Operational Risks

- BrowserRouter requires server fallback of unknown routes to `index.html`.
- `/api` must remain same-origin or be correctly proxied with credential and CSRF headers preserved.
- The application unregisters old service workers and clears old Cache Storage to recover from legacy PWA caches; deployment should confirm this remains intentional.
- Field Core Web Vitals are not currently collected, so production INP and p75 LCP are unknown.
- Cache-control must distinguish immutable hashed assets from `index.html`.

## Deployment Checklist

- [ ] Deploy first to an RC/staging environment matching production CDN, TLS, compression, caching, and proxy behavior.
- [ ] Confirm SPA fallback for `/tanitim`, `/giris`, and authenticated deep links.
- [ ] Confirm Brotli/gzip for JavaScript, CSS, JSON, SVG, and manifest assets.
- [ ] Cache hashed assets immutably; keep `index.html` revalidatable.
- [ ] Verify cookie `Secure`, `HttpOnly` where applicable, and `SameSite` policy over HTTPS.
- [ ] Run smoke journeys: public homepage, login, forced password change, dashboard, transaction create, reports, logout, and expired-session refresh.
- [ ] Re-run Lighthouse against the deployed `/tanitim` URL.
- [ ] Verify robots, manifest, canonical, OpenGraph image, Twitter card, and JSON-LD on the deployed origin.
- [ ] Enable frontend error reporting and Web Vitals collection before expanding traffic.
- [ ] Promote using a canary or low-percentage rollout after RC checks pass.

## Rollback Checklist

- [ ] Preserve the previously known-good frontend artifact and deployment manifest.
- [ ] Roll back atomically to the prior hashed-asset set and `index.html`.
- [ ] Purge or revalidate CDN `index.html`; do not purge immutable assets unnecessarily.
- [ ] Confirm API compatibility before rollback; this frontend change does not alter API contracts.
- [ ] Verify login, homepage, dashboard, and one critical transaction after rollback.
- [ ] Record error-rate, LCP, and failed-navigation evidence that triggered rollback.

## Deployment Strategy

1. Deploy the immutable frontend artifact to the RC environment.
2. Run the deployment checklist and production-origin Lighthouse audit.
3. Enable error, navigation, LCP, INP, and CLS monitoring.
4. Canary to a limited audience; compare error rate and p75 Web Vitals with the prior build.
5. Promote only if no functional regression appears and p75 LCP/INP are acceptable for the agreed device/network population.

## Monitoring Recommendations

- Collect p75 LCP, INP, and CLS segmented by route, device class, browser, and network type.
- Alert on uncaught exceptions, chunk-load failures, authentication refresh loops, and API timeout rate.
- Track homepage asset transfer size and shared-entry gzip size in CI.
- Add synthetic homepage and authenticated smoke probes from the production region.

## Future Improvements (not for this release)

- Add Lighthouse CI budgets for LCP, CLS, accessibility, and JavaScript size.
- Add browser E2E and automated accessibility tests for authenticated critical journeys.
- Establish test coverage thresholds and prioritize finance/transaction workflows.
- Evaluate a separate public entry or static/SSR shell to remove shared provider cost from `/tanitim`.
- Add an explicit circular-dependency check and remove obsolete tracked backup configuration.
- Investigate hook-dependency warnings individually with regression tests before changing behavior.

# READY FOR RELEASE CANDIDATE
