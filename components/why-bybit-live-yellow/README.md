# WHY x Yellow -> Bybit LIVE (minimum lot) executor

Reads OPEN events from /opt/why-bybit (Bybit paper book). Isolated from /opt/why-yellow
(production Yellow paper), /opt/why-bybit-demo-yellow (Bybit DEMO track), Gold/MT5.

Flow: Bybit paper OPEN event -> affordable Bybit lot -> REAL order on uid 39279297 (exchange-side
TP/SL from the unchanged sleeve geometry) -> Loculus reads the same account through its read-only key.

| file | role |
|---|---|
| `guard.py` | host guard (api.bybit.com only; demo/testnet blocked) + ARM gate (`state/ARMED.json` must say `START LIVE`) |
| `bybit_live.py` | signed client; every POST is refused unless armed; `verify_key` = key policy (uid, read-write, no withdraw, no transfer) |
| `engine.py` | signal tail, min-lot sizing, fail-closed gates, DRY_RUN/LIVE, exits, ACTUAL + Yellow-adjusted accounting, :8108 |
| `capital.py` | minimum bankroll calculator from CURRENT live instrument rules |
| `validate.py` | read-only preflight + FINAL REPORT block |
| `arm.py` | `python3 arm.py "START LIVE"` arms (checks available collateral); `--disarm` |
| `report.py` | status from state/ |
| `tests/` | offline tests with a fake client |

Each paper OPEN is sized down to the live account's available collateral and the exchange lot step, never below Bybit's minimum lot. Gates before every order (fresh wallet read): sum(open notional)+new <= equity x
max_exposure_x_equity; available >= IM + full SL + round-trip fees + overshoot + buffer; no external position on the
symbol; exchange open count < max_open; leverage == 10 on the symbol; SL far from liquidation. Any failure -> SKIP.
Pause new entries: `touch state/halt.flag`. Signal IDs are durable (bar|market|sleeve|side) -> never re-sent.
