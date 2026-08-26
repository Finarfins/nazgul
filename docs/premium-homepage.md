# Premium Homepage Experience

## Architecture

The public experience is isolated at `/tanitim`; the authenticated ERP route and authorization flow are unchanged. Content, presentation, motion, scrolling and SEO concerns live under `frontend/src/premium-homepage`. Components receive plain configuration data and can be moved to a CMS later without changing their markup.

## Animation system

`HomepageTimelineManager` owns GSAP lifecycle and ScrollTrigger registration. It exposes mount, refresh and destroy operations so animation state never leaks across routes. `splitText` provides dependency-free word splitting compatible with the timeline. Every animation is disabled when `prefers-reduced-motion` is active.

Lenis is integrated through one hook and one GSAP ticker. It supports wheels, touch devices, anchors and dynamic layout refreshes while native scrolling remains available for reduced-motion users.

## Components and design system

The page consists of Hero, Services, Statistics, Machine Showcase, Testimonials, Partners, Gallery, CTA and Footer components. Reusable Container, SectionHeader, button and card primitives share CSS tokens for color, spacing, radii, elevation, blur and motion durations.

## Performance

- The route is code-split through React lazy loading.
- The hero poster is eager and high priority; below-fold images are lazy and async decoded.
- Optional video uses metadata preload and a poster fallback.
- GSAP and Lenis load only with the public route bundle.
- Section animation is ScrollTrigger-based rather than continuous DOM polling.

The generated hero image should be converted to responsive AVIF/WebP variants in the asset pipeline before a high-traffic public launch. No video is shipped in v1 to avoid a large critical-path transfer.

## Accessibility

Semantic sections, landmarks, keyboard-visible focus rings, a skip link, AA-oriented contrast and reduced-motion behavior are included. Decorative gallery imagery has empty alternative text; meaningful hero imagery is labelled.

## SEO

The route sets canonical, description, OpenGraph, Twitter Card and Organization JSON-LD metadata. The helper is intentionally small and can later be replaced by a shared route metadata provider with breadcrumb schema support.

## Future expansion

- CMS-backed content and breadcrumb schema;
- localized metadata and copy;
- responsive AVIF/WebP source sets;
- consent-aware, optimized brand video;
- verified customer logos and testimonials;
- automated Lighthouse budgets in CI.
