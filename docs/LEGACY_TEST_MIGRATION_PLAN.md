# Legacy test migration plan — measured quarantine reasons

The class-level story (import-time smoke scripts, shared `app.main` + `veriler.db`,
order-dependent collection) lives in `backend/LEGACY_TEST_MIGRATION_PLAN.md` and
still names **no** individual files.

This document is the **appendix**: one measured row per `conftest.collect_ignore`
entry. Reasons were not guessed. Each file was run alone from develop `baadecf`
with a clean tree (`backend/veriler.db` absent, as shipped):

```
python -m pytest <file> -x -q
```

`pytest-timeout` is **not** installed (`requirements-dev.txt` has no
`pytest-timeout`); `--timeout 120` was therefore omitted. A GNU `timeout 120`
safety net was used only so a hang could not stall the batch; no file hit it.

A first sequential batch created `veriler.db` as a side effect of files that
import `app.main`. That polluted later `shutil.copy2(..., veriler.db)` runs.
Those fourteen files were **re-measured after deleting `veriler.db`**. The
table below is the clean-tree first failure.

## Files that PASS when run alone

| file | note |
| :--- | :--- |
| `test_v2_4_dashboard.py` | `1 passed` in 3.29s. Already a `tmp_path` + subprocess pytest test with `DEMO_MODE=true`. **Not un-quarantined.** Why it sits in `collect_ignore` is an open finding. |

## Appendix — measured first failure

| file | measured failure class | first error line | suggested disposition |
| :--- | :--- | :--- | :--- |
| `test_detail_workflows.py` | executable smoke script | `AssertionError: {"detail":"Devam etmek için önce şifrenizi değiştirin","code":"PASSWORD_CHANGE_REQUIRED"}` | convert |
| `test_document_engine.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_e2e_browser.py` | ImportError | `ModuleNotFoundError: No module named 'playwright'` | keep-quarantined |
| `test_finance_core.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_imports.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_inventory_reports.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_operations.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_outputs.py` | executable smoke script | `KeyError: 0` | convert |
| `test_performance_filters.py` | executable smoke script | `AssertionError: {"detail":"Devam etmek için önce şifrenizi değiştirin","code":"PASSWORD_CHANGE_REQUIRED"}` | convert |
| `test_search_analytics.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_stabilization.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_stabilization2.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_tenancy_notifications.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_transaction_integrity.py` | executable smoke script | `AssertionError: {"detail":"Oturum açmanız gerekiyor","code":"AUTH_REQUIRED"}` | convert |
| `test_transaction_warehouse.py` | executable smoke script | `AssertionError` (subprocess `<string>` line 12, `change-password` assert) | convert |
| `test_v2_2_validations.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_v2_3_payment_lists.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_v2_4_dashboard.py` | passes! | *(none — `1 passed`)* | keep-quarantined |
| `test_v2_5_cari_crm.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_v2_6_quick_actions.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |
| `test_v2_7_tenant_security.py` | missing fixture | `FileNotFoundError: [Errno 2] No such file or directory: '/workspace/backend/veriler.db'` | convert |

### How the classes were assigned

- **missing fixture** — first exception is `FileNotFoundError` on `shutil.copy2(..., veriler.db)`. `conftest.py` names that file the shared fixture. Measured on a clean tree; `veriler.db` is not in the repo.
- **executable smoke script** — module-level API work (or a pytest wrapper that `subprocess`es the same smoke). Collection or the wrapped script dies on login / password-change / empty seed data.
- **ImportError** — first exception is `ModuleNotFoundError: No module named 'playwright'` at `test_e2e_browser.py:3`, before the `veriler.db` copy.
- **passes!** — file collects and the test function passes when run alone.
- **network** and **order-dependent** — not observed as the first failure of any of the 21 files when run alone.

`test_transaction_integrity.py` and `test_transaction_warehouse.py` already have
`test_*` functions and `tmp_path` databases. They still fail because the body
is an import-time smoke script executed in a subprocess. First inner exception
is recorded above; pytest's outer line is `assert result.returncode == 0`.

No file is marked **delete**. Nothing measured as unused or superseded enough
to drop without a conversion pass.
