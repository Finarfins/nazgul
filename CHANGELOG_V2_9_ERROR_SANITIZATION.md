# V2.9 Unexpected Error Sanitization

- Unexpected sale/purchase save failures no longer return raw exception text to API clients.
- Unexpected sale/purchase delete failures now return a generic server error while retaining the full traceback in server logs.
- Unexpected manual payment create/update failures no longer expose database, constraint, table, or column details.
- Known business-rule `ValueError` and `HTTPException` responses remain unchanged.
- Added `test_v2_9_error_sanitization.py` to inject secret-bearing internal failures and prove that API responses do not leak them.

## Verification

- New isolated sanitization test: 1 passed.
- Combined targeted regression emitted 12 passing test markers; the process then hit the repository's known single-process pytest shutdown hang.
- Python compileall: passed.
