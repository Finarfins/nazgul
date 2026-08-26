# V2.9 Sales Panel Sprint 7

- Split-payment rows are included in transaction detail responses.
- Sales and purchase PDFs now render payment method distribution, paid total, and remaining balance.
- Transaction detail dialog shows payment distribution.
- Saved-document dialog autofocuses the New Sale/New Purchase action; Enter continues the workflow and Escape closes it.
- Added backend regression coverage for split-payment detail/PDF context.

Validation:
- Backend targeted tests: 2 passed.
- Frontend Vitest: 6 passed.
- TypeScript/Vite production build: passed.
- Python compileall: passed.
