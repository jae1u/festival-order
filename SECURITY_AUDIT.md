# Security Audit Notes

Playwright audit command:

```bash
npm run test:security -- --project=chromium
```

Local bounded load test command:

```bash
npm run load:test -- --url https://localhost/ --duration 20 --rps 5 --concurrency 3
```

Production bounded load-balancer smoke test command:

```bash
npm run load:test -- --url https://csed-postech.n-e.kr/ --duration 30 --rps 2 --concurrency 2 --i-understand-prod
```

The load test script only allows `localhost`, `127.0.0.1`, `::1`, and `csed-postech.n-e.kr`. Production runs require the explicit `--i-understand-prod` flag and are capped at 5 RPS, 5 concurrency, and 60 seconds.

## Fixed Findings

1. Admin login has no throttling or lockout.
   - Test: `admin login should throttle repeated wrong passwords`
   - Fix: repeated wrong passwords now return `429` after the configured threshold.
   - Relevant code: `web/app.py` `/admin/login`

2. Admin state-changing POST routes accept tokenless cross-site requests.
   - Test: `state-changing admin endpoints should reject tokenless cross-site POSTs`
   - Fix: state-changing POST routes now require a session CSRF token.
   - Relevant code: `web/app.py` `/admin/toggle_menu/<menu_id>`, `/admin/update_check`, `/admin/delete_order/<order_id>`, `/admin/checkout/<session_id>`

3. Admin logout is a GET state-changing endpoint.
   - Test: `admin logout should not be reachable with GET`
   - Fix: `/admin/logout` is now POST-only and includes CSRF protection.
   - Relevant code: `web/app.py` `/admin/logout`

4. Customer login form labels are not programmatically associated with inputs.
   - Test: `customer login labels should be associated with their inputs`
   - Fix: login form labels now use `for`/`id` associations.
   - Relevant template: `web/templates/customer_login.html`

## Passing Checks

1. Anonymous users are redirected from admin pages to `/admin/login`.
2. The order API rejects malformed, negative, zero, oversized, and unknown-menu-only carts.
3. The app refuses to boot when `FLASK_SECRET_KEY` or `ADMIN_PASSWORD` is missing or set to known defaults.

## Accepted Design

1. Multiple customers can intentionally share one table from separate phones.
   - Test: `multiple customers can intentionally share one table from separate browsers`
   - Result: Browser B joined Browser A's table and saw the shared `펩시제로` order in `/my_orders`.
   - Product intent: one festival table can have several guests ordering from their own devices.

## Suggested Fix Order

1. Consider moving login throttling from Flask cookie session state to a shared store if multiple app instances are deployed behind a load balancer.
2. Add structured audit logging for failed admin login attempts.
3. Keep the shared-table behavior documented anywhere QR/table instructions are shown to staff.
