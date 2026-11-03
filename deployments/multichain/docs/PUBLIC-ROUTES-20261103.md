# Public route repair — 2026-11-03

`/trade` returned JSON 404 because the current application and Nginx configuration use `/trading`; no compatibility redirect had been installed. Nginx now redirects `/trade` and `/trade/` to the existing `/trading` screen with HTTP 308, preserving query parameters. The app HTML, JavaScript, CSS and navigation are unchanged.

The original Nginx configuration was backed up privately before replacement. `nginx -t` passed and Nginx was reloaded gracefully. Application services were not restarted. All 42 observed Vela/WHY service PID and active-state pairs remained unchanged, as did the nine served UI files. No database writes were made by this route repair.

Verification against the public HTTPS deployment:

- 39 HTTP checks passed: all seven currently advertised pages, trailing-slash handling, `/trade` variants and query strings, HEAD behavior, all eight deployed assets with byte checks, API readiness/config/market data, expected private-route rejection, and unknown-route 404 behavior.
- 20 Chromium checks passed at 1440px and 390px viewport widths, including direct navigation, the trading alias, homepage trading navigation and refresh. No JavaScript errors, broken same-origin asset requests or horizontal overflow were observed. These are browser viewport checks, not physical-device wallet approval tests.
- Public WebSocket market streaming passed with three markets and no private data. Foreign-origin WebSockets and writes were rejected; anonymous Start returned 401; DogeOS writes remained disabled.
- The existing health timer now also runs `tools/check_public_routes.py`, covering 22 public route and API boundary cases every two minutes. Both its service run and a direct invocation passed. The monitor is read-only and logs failures; it does not restart trading or send external alerts.

**Subsequent completion:** `/paper` and `/earn` now serve their own working demo products. The read-only route monitor now covers 26 cases. See [paper, earn and DogeOS acceptance](PAPER-EARN-20261103.md) for the later deployment; the preceding measurements describe the initial route repair.

Tracked deployment artifacts:

- `ops/nginx-defivela.xyz.conf.pending`: exact deployed domain configuration, including the aliases. Reconcile paths and existing configuration before reusing it; do not overwrite a newer live configuration.
- `ops/vela-backend-health-routes.override.conf.pending`: installed at `vela-backend-health.service.d/70-public-routes.conf`.
- `snapshot/public-routes-20261103.json`: timestamped verification evidence.

Rollback restores the private pre-change Nginx file, validates it with `nginx -t`, and reloads Nginx. The monitoring addition can be reverted by removing only its dedicated drop-in and running `systemctl daemon-reload`. Neither action requires restoring or modifying the ledger.
