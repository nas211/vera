# Yellow exchange stop orders — 2026-10-02

Yellow customer execution now requires a resting exchange stop, replacing the previous
server-dependent software stop. The venue capability is enabled in `YellowTransport`.

## Request contract and sources

Stops use `POST /perpetual/order`: `type=trigger_market`, `trigger_type=stop_loss`,
`trigger_price` as a decimal string, `reduce_only=true`, `time_in_force=ioc`, and the
position's market, long/short leg, opposite closing side and current quantity.
Sleeve stop distances and sizing rules are unchanged.

Sources fetched on 2026-10-02:

- https://docs.yellow.pro/api-and-programmatic-access/perpetuals-trading-api
- https://docs.yellow.pro/mcp/tools
- https://docs.yellow.pro/spot-trading/order-types
- https://github.com/layer-3/yellow-pro-mcp/blob/3d90bfaf9078a0479ac6f6829e53c7cbed3ecd9f/src/api/trading.ts

## Safety behavior

The ledger persists each stop intent and its position linkage before submission.
The engine installs the stop before the take-profit. It reads the order back from
paginated open orders and order history, confirming exchange order ID, market, leg,
closing side, remaining quantity, trigger price/type, live state and reduce-only flag.
A POST acknowledgement alone does not establish protection. Both target and stop are
verified before the position reaches `PROTECTED`.

Unknown submissions wait for reconciliation; they are not blindly retried. Missing,
canceled or unverifiable protection blocks further entries for the account. Definitive
protection failures are bounded by the existing attempt/timeout limits and invoke the
existing flatten policy. A flatten attempt can itself be blocked by an exchange outage
or an ambiguous order; this is recorded rather than falsely claiming the account is flat.

Partial entry/exit fills resize protection. Filling either exit cancels its sibling.
On restart, reconciliation recovers already-submitted stops without duplicate orders.
Previously stored software protection is migrated to a verified exchange stop before
the position can qualify as protected. The entry pause does not disable management.

## Validation

`tests/execution/test_venue_stops.py` covers long/short wire fields, execution while the
bot/feed are offline, restart recovery, stop and target response loss, cancellation,
unreadable or incorrect protection, bounded failures, partial fills, software-stop
migration, history-only conditional orders and pause/close behavior.

The execution suite denies external sockets. Customer tests use local test Postgres
and a fake Yellow verifier. No real orders are placed by these tests.

An authenticated read-only owner probe succeeded, with zero positions, open orders or
historical orders. This confirms credentials and read access; it is not certification
of a real trigger order. First production acceptance must verify a real resting stop
and its readback against an owner's filled position, then check cleanup after close.

Exchange stops can execute independently once accepted and verified. Entry and stop
placement are separate API requests, so the brief interval after an entry fill and
before stop acceptance is not atomic. Exchange outages and price gaps can still cause
losses or slippage; no stop guarantees its exact execution price.

Pre-change source backup:
`/root/audit/backups/venue-stops-20261002-090536/`.
