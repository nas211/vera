# execution/ — WHY × Yellow execution-safety core (2026-09-22)

Built to close the execution defects in `/root/audit/FINDINGS.md` (F-02..F-09, F-17,
F-20, F-27) without touching strategy logic. `yellow_paper.py` is byte-identical to the
audited baseline (sha256 d4ebbad0…); the strategy sleeves found and preserved are
**WY-REVERT** (tp 0.30 % / sl 1.60 %) and **WY-PULLBACK** (tp 0.80 % / sl 2.00 %), 10x,
mirrored read-only in `geometry.py` and asserted equal to `yellow_paper.CFG` by test.

## Modules

| Module | Purpose |
|---|---|
| `types.py` | enums (`OrderState`, `PositionState`, `VenueState`, `OrderPurpose`), request/venue dataclasses, `EntrySignal`, Decimal helper |
| `state_machine.py` | legal transitions; illegal ones raise; same-state = idempotent no-op |
| `ledger.py` | SQLite (WAL) ledger: intents written before submit, deterministic `client_order_id = why-sha256(account|signal|purpose|market|direction)[:28]`, atomic `claim_for_submission`, event ids (duplicate delivery = no-op), fills by trade id, positions with a partial unique index (one open per account/market/direction), protection, flags, persisted switches (+ JSON mirror for the CLI) |
| `engine.py` | `ExecutionEngine`: `submit_entry`, `on_fill`, `ensure_protection`, `check_protection`, `close_position`, `cancel_order`, `reconcile`, `start` (restart recovery), `manage_existing`, `set_switches`. Yellow protection installs and verifies an exchange stop before the target; software stops remain only for transports that lack trigger support. |
| `transport.py` | venue interface + `TransportAmbiguous` / `VenueRejected` / `VenueUnavailable`; `all_pages` cursor walker |
| `mock_adapter.py` | deterministic in-memory venue with scripted failures (accept-lost, 429/5xx, partial/duplicate fills, manual close, cancel-stays-open, disconnect, pagination). No network. |
| `yellow_transport.py` | wrapper over `yellow_exec.YellowClient` (still gated by `YELLOW_ARMED=1` + credentials). Forwards trigger price/type and normalizes conditional order types/states and original/filled quantities for protection readback. |
| `metadata.py` / `validation.py` / `sizing.py` | venue metadata abstraction (no hard-coded Yellow numbers); strict Decimal validation; execution-side sizing (notional / margin / leverage / available balance / caps) |
| `market_data.py` | `FreshnessMonitor`: MAX_MARKET_DATA_AGE, bar continuity, duplicate / out-of-order / future / gap detection, `entry_allowed()` |
| `venue_state.py` | `VenueMonitor`: DISCONNECTED → CONNECTED/RECOVERING → RECONCILING → READY; READY only after a successful reconcile |
| `switches.py` | `allow_new_entries` vs `manage_existing_positions` (independent; default entries OFF, management ON) |

## Guarantees (tests/execution; run on system python3 and the venv)

```
cd /opt/why-yellow && python3 -m unittest discover -s tests/execution -t .
```
The harness installs an audit hook that raises on any socket/subprocess use and asserts
`YELLOW_ARMED` is not in the environment. Real Yellow orders placed by the suite: 0.

Exchange-stop behavior, implementation sources, deployment checks and remaining live
certification are documented in [deploy/venue-stop-orders.md](../deploy/venue-stop-orders.md).

## Adapter changes (`yellow_exec.py`)
Order POST and batch close: exactly one attempt; transport error or 5xx raises
`TransportAmbiguous`. `client_order_id` mandatory. Local validation before any order.
`open_orders/orders/trades` walk every cursor page. `cancel_verified` returns
`CANCELLED | FILLED | STILL_OPEN | UNKNOWN` and never trusts the DELETE 200. `fills_for`
de-duplicates by trade id and returns fees per currency. `_guard("entry")` also checks
`state/exec_switches.json`; `_guard("manage")` (close/cancel/flatten) does not. Arming
(`YELLOW_ARMED=1` + key file) still gates everything.

## Done in a later phase (2026-09-23): `customer/`
Supabase Auth + Vault, customer onboarding, multi-tenant DB (RLS), frontend, per-tenant engines and the
journal→tenant signal fan-out live in `customer/` (see `customer/README.md`). The core in this directory is unchanged.

## Not done here (later phases, by instruction)
Supabase Auth/Vault, customer onboarding, multi-tenant DB, frontend (now in `customer/`), 1k/10k load tests,
VPS SSH hardening, legal pages, permission onboarding, broad monitoring, billing/referrals.
Also not wired: a runner that feeds `yellow_paper`-style signals and the Yellow WS into
the engine (the engine is a library; the paper service keeps running unchanged).
