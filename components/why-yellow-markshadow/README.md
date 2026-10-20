# WHY x Yellow — MARK-CANDLE SHADOW (isolated)

Separate from /opt/why-yellow. Public Yellow websocket data only. No orders, no keys.
Production paper book, customer platform, execution core, Bybit, Gold/MT5, Qwen: untouched.

Question it answers: can 5-minute candles built locally from Yellow's public mark-price stream
reproduce the signal book that the native Yellow kline channel produced before that channel
froze on 2026-09-18?

| piece | what |
|---|---|
| `wy_logic.py` | loads the UNCHANGED production strategy functions from `/opt/why-yellow/yellow_paper.py` by AST (read-only, sha256 recorded), binds them to this state dir; `MarkCandleBuilder` (first/max/min/last of real ticks, no interpolation, partial/gap flags) |
| `markshadow.py` | live service: records mark ticks + native klines, builds mark candles, runs the shadow paper book, `/health` + `/api/book` on 127.0.0.1:8105 |
| `replay.py` | offline replay over recorded ticks with `--candles mark` or `--candles native` |
| `compare.py` | exact (sleeve, market, side, 5m entry bar) signal comparison + performance (WR, net, PF, max DD) incl. exact-intersection |
| `selftest.py` | offline checks (builder, tick-vs-bar plumbing equivalence, CFG/sha/env fidelity) |
| `state/ticks/<m>/<day>.jsonl` | `[server_ms, recv_ms, mark, index]` per tick |
| `state/mark_candles/<m>.jsonl` | closed local 5m bars + coverage meta |
| `state/native_klines/<m>.jsonl` | native closed bars + `frozen` flag (for the future native-vs-shadow run) |
| `state/seed/<m>.jsonl` | Bybit 5m seed bars used at each start (same seed source/shape as production) |
| `state/journal.jsonl` | shadow paper book (production journal schema + `bar_ms` on OPEN) |

Service: `why-yellow-markshadow.service` (enabled). Pause shadow entries: `touch state/pause_entries.flag`.

Once Yellow's native klines unfreeze (native_klines rows with `frozen:false`), run:
```
python3 replay.py --candles native --from <ms> --to <ms> --out reports/native_X
python3 replay.py --candles mark   --from <ms> --to <ms> --out reports/mark_X
python3 compare.py --native reports/native_X/journal.jsonl --shadow reports/mark_X/journal.jsonl
```
That compares both candle sources on identical ticks, seed and clock, without needing the
production book un-paused. `compare.py --shadow state/journal.jsonl` compares the live shadow
book against the production journal (read-only) instead.

Do not tune anything here to raise the match rate.
