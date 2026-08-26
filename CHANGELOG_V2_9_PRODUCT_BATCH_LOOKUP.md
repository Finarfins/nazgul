# V2.9 Product Batch Lookup

- Transaction totals now load all referenced products in one expanding `IN` query.
- Workflow document totals use the same batch lookup pattern.
- Missing products still return the same business error.
- Added an integration test that asserts one product SELECT for multi-item sales and quotes.

Validation:
- Batch lookup integration: 1/1
- Transaction integrity: 1/1
- Workflow documents: 1/1
- Split payment: 1/1
- Purchase supplier context: 1/1
- Python compileall: passed
