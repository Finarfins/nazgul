# V2.9 Customer/Supplier Authorization Fix

## Fixed

- Added explicit `sales` permission requirement for all state-changing `/api/customers` requests.
- Added explicit `purchases` permission requirement for all state-changing `/api/suppliers` requests.
- Changed the unknown state-changing endpoint fallback from baseline `read` to admin-only deny-by-default behavior.

## Verification

- A `rapor` role can still read customer and supplier lists.
- The same role receives HTTP 403 for customer/supplier create, update, and delete requests.
- Sales panel regression group remains green.

## Tests

- `test_v2_9_customer_supplier_write_permissions.py`
- Six targeted backend tests passed in the final regression run.
