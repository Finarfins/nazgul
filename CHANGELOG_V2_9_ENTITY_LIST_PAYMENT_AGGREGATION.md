# V2.9 Entity List Payment Aggregation

## Changes
- Customer list balance calculation now joins one company-scoped payment aggregate instead of running a correlated payment sum per customer row.
- Supplier list balance calculation uses the same grouped payment aggregate pattern.
- PostgreSQL-compatible grouping explicitly includes the joined aggregate value.
- `test_v3_customer_center.py` no longer depends on the removed legacy `backend/veriler.db`; it creates and validates a clean isolated database.
- The modernized customer-center test now exercises list balance calculation with a completed sale and partial collection, CRM resources, and tenant isolation.

## Validation
- Customer sales context: passed.
- Supplier purchase context: passed.
- Customer center clean-database test: passed.
- Customer/supplier write permissions: passed.
- Python compileall: passed.
