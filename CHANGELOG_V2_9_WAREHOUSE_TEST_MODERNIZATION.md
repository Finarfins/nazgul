# V2.9 Warehouse Test Modernization

- Legacy `test_warehouses.py` no longer depends on a packaged `backend/veriler.db` fixture.
- The test now creates a clean database, completes bootstrap password change, creates a second warehouse and product, performs a transfer, and verifies source/target quantities.
- Critical-stock filtering, transfer movement history, denormalized product stock total, and SQLite integrity are asserted.
- `test_warehouses.py` was removed from `conftest.py` quarantine and is now active pytest coverage.

## Verification

- `test_warehouses.py`: 1 passed
- `test_v2_9_warehouse_stock_operations.py`: 1 passed
- `test_v2_9_tenant_stock_security.py`: 1 passed
- Python compileall: passed
