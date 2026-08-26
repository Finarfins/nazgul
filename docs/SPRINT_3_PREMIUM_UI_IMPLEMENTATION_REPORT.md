# Sprint 3 — Premium UI/UX Redesign: First Milestone

## Scope

This milestone establishes the frontend design-system foundation and applies the first page-level redesign only to the Sales List and New Sale experiences. Backend logic, API contracts, database code, authentication, permissions, transaction behavior, and keyboard shortcuts are unchanged.

## Design system

- Consolidated light and dark modes into a single MUI 7 theme factory.
- Added Inter as the primary UI font with native system fallbacks.
- Defined a professional typography hierarchy, 8px spacing rhythm, semantic enterprise palette, refined borders, shadows, and radii.
- Standardized buttons, icon buttons, text fields, selects, menus, dialogs, cards, alerts, chips, and Data Grid visuals.
- Added consistent hover, active, focus-visible, selected, disabled, and dark-mode behavior.
- Redesigned the shared sidebar and top navigation with clearer hierarchy, denser navigation, command search, and responsive mobile drawer behavior.
- Improved the shared responsive table for desktop density and mobile card readability.

## First redesigned screens

### Sales List

- Clear page title, supporting description, and primary action hierarchy.
- Unified filter surface with search affordance and consistent field sizing.
- Compact enterprise table styling and semantic status badges.
- Preserved filtering, sorting, pagination, document actions, exports, double-click detail behavior, and responsive cards.

### New Sale

- Improved dialog hierarchy, context label, section heading, keyboard-shortcut presentation, line-item cards, and totals emphasis.
- Preserved F2, F4, F6, F8, F9, Enter, and Escape workflows.
- Preserved quick customer/product creation, barcode entry, stock checks, split payments, manager override, totals, and save behavior.

## Validation

- `npm run lint`: exit 0; no errors. The repository still reports 291 pre-existing warnings.
- `npm run test`: exit 0; 5 files and 10 tests passed.
- `npm run build`: exit 0; TypeScript and Vite production build passed.
- Browser smoke test: Sales List loaded with demo data; New Sale opened successfully; desktop screenshots captured before and after.

## Database, security, and tenancy

- No migrations or database application code changed.
- No API payloads, routes, or response handling changed.
- Tenant/company selection, RBAC visibility, authentication, CSRF behavior, and manager override flows remain intact.
- Screenshot data came from a temporary local demo SQLite database and is not part of the product changes.

## Screenshots

- `docs/screenshots/sprint-3/before-sales-list.png`
- `docs/screenshots/sprint-3/after-sales-list.png`
- `docs/screenshots/sprint-3/before-new-sale.png`
- `docs/screenshots/sprint-3/after-new-sale.png`

## Remaining scope

The rest of the ERP intentionally retains its current page composition for this milestone. Shared theme and shell improvements affect visual primitives globally, while page-level redesign work remains limited to Sales List and New Sale as requested.
