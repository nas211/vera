"""Publish the Yellow book's closed-trade record for the /trades web UI.

/trades now shows the Yellow book instead of the AlphaSprint paper book
(owner's call, 2026-09-16). The PAGE is untouched (still
/opt/alpha/tgbot/webui's "WHY Protocol - Alpha V2" markup, copied verbatim
into /opt/why-yellow/webui/ with one line changed: it fetches
/trades-data/paper.json instead of the relative data/paper.json, so it can
run side by side with the AlphaSprint page without the two colliding on the
same URL). This script is the DATA side of that swap: same JSON schema the
page already knows how to render, sourced from the Yellow book instead.

Source: /opt/why-yellow/state/journal.jsonl (per-trade CLOSE events) and
snapshot.json (aggregate stats + currently open positions), both written by
yellow_paper.py. No orders are placed here and no credentials are read;
this script only reads why-yellow's own public state.

Output: /opt/why-yellow/webui/data/paper.json, served by nginx at
/trades-data/paper.json (no-store).
"""
from __future__ import annotations

import json
import os
import tempfile
import time

JOURNAL = "/opt/why-yellow/state/journal.jsonl"
SNAPSHOT = "/opt/why-yellow/state/snapshot.json"
OUT = "/opt/why-yellow/webui/data/paper.json"


def venue_symbol(market: str) -> str:
    # "BTCUSDT-PERP" -> "BTCUSDT". The page does `.replace('USDT','')` itself.
    return market[:-5] if market.endswith("-PERP") else market


def _load_json(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def closed_trades() -> list[dict]:
    rows = []
    try:
        fh = open(JOURNAL)
    except OSError:
        return rows
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("event") != "CLOSE":
                continue
            side = str(r.get("side") or "")
            if side not in ("long", "short"):
                continue
            rows.append({
                "ts": float(r["ts"]) / 1000.0,
                "kind": "TP" if r.get("reason") == "TARGET" else "SL",
                "symbol": venue_symbol(str(r.get("market") or "")),
                "side": side,
                "pnl": float(r.get("pnl_usd") or 0.0),
                "notional_usd": float(r.get("notional") or 0.0),
                "paper": True,
            })
    rows.sort(key=lambda x: x["ts"], reverse=True)
    return rows


def open_positions(snap: dict) -> list[dict]:
    out = []
    for p in snap.get("open") or []:
        side = str(p.get("side") or "")
        if side not in ("long", "short"):
            continue
        out.append({
            "symbol": venue_symbol(str(p.get("market") or "")),
            "side": side,
            "entry_px": p.get("entry"),
            "tp_px": p.get("target"),
            "sl_px": p.get("stop"),
            "notional_usd": p.get("notional"),
            "opened_ms": p.get("opened_ms"),
            "paper": True,
        })
    return out


def since_label(first_ts_iso: str) -> tuple[str, str]:
    # "2026-09-11T16:00:58Z" -> ("2026-09-11T16:00:58Z", "since 16:00 UTC, 11 Sep")
    try:
        hh_mm = first_ts_iso[11:16]
        y, mo, d = first_ts_iso[0:4], int(first_ts_iso[5:7]), int(first_ts_iso[8:10])
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return first_ts_iso, f"since {hh_mm} UTC, {d} {months[mo - 1]}"
    except (ValueError, IndexError):
        return first_ts_iso, ""


def main() -> int:
    snap = _load_json(SNAPSHOT)
    rows = closed_trades()
    net = round(sum(r["pnl"] for r in rows), 2)

    # First OPEN in the journal is the book's start.
    since_iso = ""
    try:
        with open(JOURNAL) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_ms = r.get("ts")
                if ts_ms:
                    since_iso = time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_ms / 1000.0))
                    break
    except OSError:
        pass
    since_iso, label = since_label(since_iso)

    fees = snap.get("fees") or {}
    rv_tp, rv_sl = 0.30, 1.60   # WY-REVERT, matches yellow_paper.py defaults
    pb_tp, pb_sl = 0.80, 2.00   # WY-PULLBACK, matches yellow_paper.py defaults
    disclosure = (
        "L. No orders were placed and no money was at risk. "
        f"Pullback setups take profit {pb_tp:.2f}% / stop {pb_sl:.2f}%; "
        f"revert setups take profit {rv_tp:.2f}% / stop {rv_sl:.2f}% of position."
    )

    out = {
        "ts": time.time(),
        "paper": True,
        "disclosure": disclosure,
        "since_iso": since_iso,
        "label": label,
        "notional_usd": snap.get("clip_usd", 2000.0),
        "leverage_x": snap.get("leverage", 10.0),
        "geometry": {
            "short": {"tp_pct": pb_tp, "sl_pct": pb_sl},
            "long": {"tp_pct": pb_tp, "sl_pct": pb_sl},
        },
        "summary": {"trades": len(rows), "net_usd": net},
        "trades": rows,
        "open": open_positions(snap),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT))
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh)
    os.replace(tmp, OUT)
    os.chmod(OUT, 0o644)
    return 0


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            print(f"snapshot error: {e}")
        time.sleep(8)
