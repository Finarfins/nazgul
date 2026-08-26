# Premium Homepage Production Checklist

Audit target: `feature/premium-homepage-experience`, Draft PR #16, `/tanitim` production build.

## Verified

- [x] **GSAP timeline cleanup:** all page timelines and tweens are created inside a root-scoped `gsap.context`; `destroy()` calls `context.revert()` and clears the reference.
- [x] **ScrollTrigger cleanup:** triggers created by context-owned tweens are reverted with the GSAP context. The window `load` listener is also removed during unmount.
- [x] **Lenis lifecycle:** the GSAP ticker callback is removed and `lenis.destroy()` is called on every effect cleanup. No global ticker configuration is changed.
- [x] **React StrictMode:** mount/cleanup/remount is idempotent. Split text is guarded per mounted DOM node, event listeners are paired, and all route effects return cleanup functions.
- [x] **Memory leaks:** GSAP context, ScrollTrigger instances, Lenis, ticker callback, load listener, IntersectionObserver and JSON-LD node all have explicit cleanup paths.
- [x] **Accessibility:** semantic landmarks, heading hierarchy, skip link, keyboard focus rings, meaningful alternative text, decorative empty alternative text and reduced-motion handling are present. Lighthouse accessibility: **100**.
- [x] **SEO metadata:** description, canonical, OpenGraph, Twitter Card, Organization JSON-LD and Breadcrumb JSON-LD are present. Route metadata and document title are restored on unmount. Lighthouse SEO: **100**.
- [x] **CLS:** external runtime fonts were removed and media dimensions/reserved containers are defined. Lighthouse CLS: **0**.
- [x] **Mobile layout:** 390×844 browser validation showed no horizontal overflow, no console errors and correctly stacked CTAs.
- [x] **Image optimization:** hero asset converted from 1,995,220-byte PNG to 108,630-byte WebP, a **94.6% reduction**. Lighthouse reports zero modern-format savings.
- [x] **Video loading:** no production video is shipped. Configured video uses `preload="none"`, poster fallback, IntersectionObserver loading, muted inline playback and observer cleanup.
- [x] **Reduced motion:** Lenis and GSAP setup are skipped when `prefers-reduced-motion: reduce` matches; CSS disables animation and smooth scrolling.
- [x] **Regression gates:** Vitest **10/10**, TypeScript/Vite production build passed, changed-source ESLint passed with zero warnings, and `git diff --check` passed.

## Needs Improvement

- [ ] **Lighthouse performance target:** production-build mobile Lighthouse score is **91–92**, below the requested 95+ target.
- [ ] **LCP:** measured at **3.2 seconds** under Lighthouse mobile throttling. The 108 KB hero is route-preloaded and high priority, but the shared SPA bootstrap remains on the critical path.
- [ ] **INP:** a reliable field INP cannot be certified in a lab audit without real-user interaction data. Lighthouse TBT is **45–80 ms** and Max Potential FID is approximately **130 ms**, which are healthy lab proxies.
- [ ] **Mobile JavaScript cost:** the homepage route chunk is approximately **54.5 KB gzip**, while the shared SPA entry is approximately **191.6 KB gzip** because the public route still boots the existing application shell/providers.
- [ ] **LCP architecture:** reaching a stable 95+ performance score likely requires a separate public entry/HTML shell or SSR/static rendering. That is an architecture change and was intentionally excluded from this production-bug pass.

## Optional

- [ ] Add Lighthouse CI budgets to prevent LCP, CLS, accessibility and bundle-size regressions.
- [ ] Collect Web Vitals through privacy-safe real-user monitoring to validate p75 LCP and INP on production devices and networks.
- [ ] Add AVIF alongside WebP when the deployment pipeline supports responsive `<picture>` generation.
- [ ] Add a brand-approved, codec-optimized video with WebM/MP4 sources and a data-saver/mobile policy.
- [ ] Self-host a carefully subsetted brand font only if visual identity requires it; preserve metric-compatible fallback values to keep CLS at zero.
- [ ] Add automated axe coverage and keyboard journey tests in CI.
