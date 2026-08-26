# V2.9 Forced Password Refresh Hardening

## Scope

- Verified that protected API routes already reject users with `must_change_password=true`.
- Closed the remaining refresh-session persistence gap.

## Changes

- `rotate_refresh_token` now detects users that still require a password change.
- The whole active refresh-token family is revoked instead of rotated.
- `/api/auth/refresh` returns HTTP 403 with `PASSWORD_CHANGE_REQUIRED`.
- Access, refresh and CSRF cookies are expired in the rejection response.
- Added an isolated regression test covering protected-route rejection, refresh rejection and database family revocation.

## Verification

- Session security: 4 passed.
- Customer/supplier authorization and sales regressions: 6 passed.
- Python compileall: passed.
