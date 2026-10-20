"""Read-only view of the WHY master PAPER book for the Partners portal.

ONE normalisation layer. Reads two engine files — the closed-trade journal and
the open book — and never anything else. Opens them O_RDONLY, imports nothing
from the execution path, and re-uses the public publisher's own helpers
(window, symbol mapping, clip rescale, per-row target/stop, exit-kind badges)
so the portal can never show a different record than whytradingbot.com does.

THE EDGE NEVER LEAVES THE BOX: no S-expressions, no spec hashes, no families,
no signal timestamps. Strategies are an anonymous "Model N" ordinal by first
appearance inside the window (investor_snapshot.py's rule).
"""
from __future__ import annotations

import calendar
import json
import os
import sys
import time
import threading
import urllib.request

_TGBOT = os.environ.get("PARTNERS_PUBLISHER_DIR", "/opt/alpha/tgbot")
if _TGBOT not in sys.path:
    sys.path.insert(0, _TGBOT)
import paper_snapshot as PS          # noqa: E402  (read-only helpers)
import investor_snapshot as IS       # noqa: E402

TRADES = os.environ.get("PARTNERS_TRADES", PS.TRADES)
STATE = os.environ.get("PARTNERS_STATE", PS.STATE)
SINCE_ISO = os.environ.get("PARTNERS_SINCE_ISO", PS.SINCE_ISO)
CAPITAL_USD = float(os.environ.get("PARTNERS_CAPITAL_USD", "25000"))
LEVERAGE_X = int(os.environ.get("PARTNERS_LEVERAGE_X", "11"))
CACHE_SEC = float(os.environ.get("PARTNERS_CACHE_SEC", "5"))
PRICE_TTL_SEC = float(os.environ.get("PARTNERS_PRICE_TTL_SEC", "6"))
TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

# Fees are MODELLED by the engine (bps per side) and netted into pnl_usd; the
# per-row amount is not journaled. Slippage is not modelled at all. Both are
# therefore reported as unavailable rather than reconstructed.
FEES_NOTE = ("P&L is net of modelled exchange fees; per-trade fees and "
             "slippage are not recorded and are not shown.")


def _read_json_ro(path: str):
    fd = os.open(path, os.O_RDONLY)
    with os.fdopen(fd, "r") as fh:
        return json.load(fh)


def _closed_rows() -> list[dict]:
    """investor_snapshot.closed() at the public clip, minus nothing it already
    strips (it never emits strat/spec_hash). Sorted newest first."""
    IS.TRADES = TRADES
    rows = PS.at_clip(IS.closed(SINCE_ISO), PS.notional_usd())
    out = []
    for r in rows:
        n = r.get("notional_usd") or 0.0
        e = r.get("entry_px") or 0.0
        pnl = float(r.get("pnl") or 0.0)
        out.append({
            "id": f"{int(r['closed_ms'] or 0)}-{r['symbol']}",
            "symbol": r["symbol"],
            "side": r["side"],
            "model": r.get("model"),
            "opened_ms": r.get("opened_ms"),
            "closed_ms": r.get("closed_ms"),
            "hold_min": r.get("hold_min"),
            "entry_px": r.get("entry_px"),
            "exit_px": r.get("exit_px"),
            "qty": round(n / e, 6) if n and e else None,       # derived
            "notional_usd": r.get("notional_usd"),
            "target_px": r.get("target_px"),
            "stop_px": r.get("stop_px"),
            "target_pct": r.get("target_pct"),
            "stop_pct": r.get("stop_pct"),
            "pnl_usd": round(pnl, 2),
            "pnl_pct": round(pnl / n * 100.0, 4) if n else None,   # of clip
            "move_pct": r.get("move_pct"),
            "exit_reason": r["kind"],       # TARGET / STOP / TIME / CLOSED
        })
    return out


def _open_rows() -> list[dict]:
    IS.STATE = STATE
    out = []
    for r in IS.open_positions():
        n = r.get("notional_usd") or 0.0
        e = r.get("entry_px") or 0.0
        out.append({
            "id": f"{int(r['opened_ms'] or 0)}-{r['symbol']}",
            "symbol": r["symbol"],
            "side": r["side"],
            "opened_ms": r.get("opened_ms"),
            "entry_px": e or None,
            "qty": round(n / e, 6) if n and e else None,
            "notional_usd": r.get("notional_usd"),
            "target_px": r.get("target_px"),
            "stop_px": r.get("stop_px"),
        })
    return out


# --- prices ------------------------------------------------------------------
_px_lock = threading.Lock()
_px: dict = {"ts": 0.0, "map": {}}


def prices(symbols: set[str]) -> dict:
    """Last price per symbol from the public ticker, cached PRICE_TTL_SEC.
    Public market data only; no credentials, ever. Empty on failure — the UI
    renders a dash rather than a stale or invented mark."""
    with _px_lock:
        now = time.time()
        if now - _px["ts"] < PRICE_TTL_SEC and symbols <= set(_px["map"]):
            return {s: _px["map"][s] for s in symbols if s in _px["map"]}
        try:
            req = urllib.request.Request(TICKER_URL,
                                         headers={"User-Agent": "why-partners/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                rows = json.loads(r.read())
            m = {r["symbol"]: {"px": float(r["lastPrice"]),
                               "pct24h": float(r["priceChangePercent"]),
                               "ts": now}
                 for r in rows if r.get("symbol") in symbols}
            _px["ts"], _px["map"] = now, m
        except Exception:
            m = _px["map"]
        return {s: m[s] for s in symbols if s in m}


def _mark(open_rows: list[dict]) -> list[dict]:
    px = prices({r["symbol"] for r in open_rows})
    out = []
    for r in open_rows:
        p = px.get(r["symbol"])
        r = dict(r)
        if p and r["entry_px"] and r["notional_usd"]:
            sign = 1.0 if r["side"] == "long" else -1.0
            move = sign * (p["px"] / r["entry_px"] - 1.0)
            r["mark_px"] = p["px"]
            r["mark_ts"] = p["ts"]
            r["unrealized_usd"] = round(move * r["notional_usd"], 2)
            r["unrealized_pct"] = round(move * 100.0, 4)
        else:
            r["mark_px"] = None
            r["unrealized_usd"] = None
            r["unrealized_pct"] = None
        out.append(r)
    return out


# --- metrics -----------------------------------------------------------------
def equity_curve(closed: list[dict]) -> list[dict]:
    """Realized cumulative P&L after each close, oldest first, plus running
    NAV on the configured starting capital and the drawdown from the running
    peak. Closed-trade granularity ONLY: there is no mark-to-market history."""
    total, peak = 0.0, 0.0
    # First point is the record start at starting capital, so the curve is
    # anchored where the window opens rather than at the first close.
    start_ms = int(calendar.timegm(time.strptime(SINCE_ISO, "%Y-%m-%dT%H:%M:%SZ")) * 1000) if SINCE_ISO else 0
    pts = [{"ts": start_ms, "cum_usd": 0.0, "nav_usd": CAPITAL_USD, "dd_usd": 0.0, "dd_pct": 0.0}]
    for r in sorted(closed, key=lambda r: r["closed_ms"] or 0):
        total += r["pnl_usd"]
        peak = max(peak, total)
        dd = total - peak
        pts.append({"ts": int(r["closed_ms"] or 0), "cum_usd": round(total, 2),
                    "nav_usd": round(CAPITAL_USD + total, 2),
                    "dd_usd": round(dd, 2),
                    "dd_pct": round(dd / (CAPITAL_USD + peak) * 100.0, 4)})
    return pts


def summary(closed: list[dict], marked: list[dict]) -> dict:
    s = IS.summarise([{"pnl": r["pnl_usd"], "hold_min": r["hold_min"],
                       "side": r["side"], "model": r["model"]} for r in closed])
    unreal = [r["unrealized_usd"] for r in marked if r.get("unrealized_usd") is not None]
    unreal_total = round(sum(unreal), 2) if unreal else None
    curve = equity_curve(closed)
    max_dd = min((p["dd_usd"] for p in curve), default=0.0)
    max_dd_pct = min((p["dd_pct"] for p in curve), default=0.0)
    realized = s["net_usd"]
    total = round(realized + (unreal_total or 0.0), 2)
    return {
        "capital_usd": CAPITAL_USD,
        "leverage_x": LEVERAGE_X,
        "nav_usd": round(CAPITAL_USD + total, 2),
        "realized_usd": realized,
        "unrealized_usd": unreal_total,
        "unrealized_marked": len(unreal),
        "unrealized_of": len(marked),
        "total_usd": total,
        "return_pct": round(total / CAPITAL_USD * 100.0, 4),
        "realized_return_pct": round(realized / CAPITAL_USD * 100.0, 4),
        "closed_trades": s["trades"],
        "open_positions": len(marked),
        "wins": s["wins"], "losses": s["losses"],
        "win_pct": s["win_pct"],
        "profit_factor": s["profit_factor"],
        "avg_win_usd": s["avg_win_usd"],
        "avg_loss_usd": s["avg_loss_usd"],
        "best_usd": s["best_usd"], "worst_usd": s["worst_usd"],
        "avg_hold_min": s["avg_hold_min"],
        "longs": s["longs"], "shorts": s["shorts"],
        "models": s["models"],
        "max_drawdown_usd": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "gross_win_usd": s["gross_win_usd"], "gross_loss_usd": s["gross_loss_usd"],
    }


# --- cached snapshot ---------------------------------------------------------
_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "snap": None}


def snapshot(force: bool = False) -> dict:
    with _lock:
        now = time.time()
        if not force and _cache["snap"] and now - _cache["ts"] < CACHE_SEC:
            return _cache["snap"]
        closed = _closed_rows()
        marked = _mark(_open_rows())
        snap = {
            "ts": now,
            "paper": True,
            "since_iso": SINCE_ISO,
            "clip_usd": PS.notional_usd(),
            "geometry": PS._geometry_pct(),
            "notes": {"fees": FEES_NOTE,
                      "drawdown": "Realized, closed-trade basis; no intraday mark-to-market history.",
                      "nav": f"Paper NAV on ${CAPITAL_USD:,.0f} starting capital at {LEVERAGE_X}x."},
            "summary": summary(closed, marked),
            "open": marked,
            "trades": closed,
            "equity": equity_curve(closed),
        }
        _cache["ts"], _cache["snap"] = now, snap
        return snap


if __name__ == "__main__":
    s = snapshot(True)
    print(json.dumps({"summary": s["summary"], "open": len(s["open"]),
                      "trades": len(s["trades"])}, indent=1))
