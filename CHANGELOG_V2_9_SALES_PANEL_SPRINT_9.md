# V2.9 Sales Panel Sprint 9

## Scope
Sale deletion reversal and destructive-action UX hardening.

## Changes
- Added a clean-install end-to-end regression test for deleting a completed sale with split payments.
- Verified stock is restored to the exact pre-sale quantity.
- Verified customer balance returns to the exact pre-sale value.
- Verified all linked payment rows are removed.
- Verified every linked finance transaction is removed.
- Verified order items, order header and stock movement rows are removed.
- Improved the delete confirmation message to explicitly explain stock, balance, payment and finance effects.
- Disabled document delete actions while a delete request is running to prevent duplicate destructive requests.

## Quality gates
- Backend targeted regressions: 3 passed.
- Frontend Vitest: 6 passed.
- TypeScript/Vite production build: passed.
- Python compile check: passed.
