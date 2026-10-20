"""Reuse Vera's pure WY signals and bounded discovery model/rule evaluator."""
import os
import statistics
import time
import urllib.request
import urllib.parse
from decimal import Decimal as D
from .reuse import signals, rules, model_module
from .db import get, put, log

STRATEGIES = [
    dict(id=1, name="Vela AI Agents", market="DOGE · BTC · ETH · SOL · Gold",
         risk="Managed", source="Unified discovery, existing reversion/pullback signals and risk controls",
         description="One team researches opportunities, checks signals, manages risk and follows every position.",
         agent="Research · Signals · Risk · Execution", stats=None),
]


class VelaModel(model_module.Model):
    def __init__(self):
        # Deliberately do NOT call the parent's constructor or read any WHY variables.
        self.endpoint = os.getenv("VELA_MODEL_ENDPOINT", "https://api.openai.com/v1/responses")
        self.model = os.getenv("VELA_MODEL", "")
        self.key = os.getenv("VELA_MODEL_KEY", "")
        self.last = 0.
        self.requests = []
        self.opener = urllib.request.build_opener(model_module.NoRedirect())
        u = urllib.parse.urlsplit(self.endpoint)
        if u.scheme != "https" or u.username or u.password or u.query or u.fragment or not u.path.endswith(("/responses", "/chat/completions")):
            raise ValueError("Invalid Vela model endpoint")


def bars_for(db, symbol, now):
    rows = list(reversed(db.execute(
        "SELECT * FROM candles WHERE symbol=? AND ts+300<=? ORDER BY ts DESC LIMIT 250",
        (symbol, now)).fetchall()))
    if len(rows) != 250 or now - (rows[-1]["ts"] + 300) > 360:
        return []
    if any(b["ts"] - a["ts"] != 300 for a, b in zip(rows, rows[1:])):
        return []
    return [[r["ts"] * 1000, r["o"], r["h"], r["l"], r["c"]] for r in rows]


def features(bars):
    c = [b[4] for b in bars]
    returns = [(c[i] / c[i - 1] - 1) * 10000 for i in range(len(c) - 20, len(c))]
    lo, hi = min(b[3] for b in bars[-20:]), max(b[2] for b in bars[-20:])
    return dict(return_1=(c[-1] / c[-2] - 1) * 10000,
                return_5=(c[-1] / c[-6] - 1) * 10000,
                return_20=(c[-1] / c[-21] - 1) * 10000,
                volatility=statistics.pstdev(returns),
                range_position=(c[-1] - lo) / (hi - lo) if hi > lo else .5)


class Agents:
    def __init__(self, db, model=None):
        self.db, self.model = db, model or VelaModel()

    def signal_candidates(self, now):
        """Existing signal families are inputs to one portfolio, not products."""
        candidates = []
        for symbol in ("DOGE/USD", "BTC/USD", "ETH/USD", "SOL/USD"):
            bars = bars_for(self.db, symbol, now)
            if not bars:
                continue
            result = signals.wy_eval(bars)
            if not result or not result["fired"] or result["atr"] <= 0:
                continue
            side = result["fired"][0]
            stop_bps = min(120., max(40., result["atr"] / result["close"] * 10000))
            candidates.append(dict(symbol=symbol, side=side, stop_bps=str(stop_bps),
                target_bps=str(stop_bps * 1.5), hold=7200,
                signal_id=f"{symbol}:{result['fired'][1]}:{result['bar_ms']}"))
        return candidates

    def signal(self, user, now=None):
        now = time.time() if now is None else now
        if user["strategy"] not in (1, 2, 3):
            return None
        if not self.model.ready:
            # Report this state honestly in the app. Never claim model approval.
            from .prices import entries_allowed
            return next((s for s in self.signal_candidates(now)
                         if entries_allowed(self.db, s["symbol"])), None)
        candidate = get(self.db, "ai-approved:" + user["address"])
        if not candidate or now - candidate["at"] > 300:
            return None
        spec = candidate["spec"]
        bars = bars_for(self.db, spec["market"], now)
        if not bars or not rules.triggered(spec, features(bars)):
            return None
        return dict(symbol=spec["market"], side=spec["side"], stop_bps=str(spec["stop_bps"]),
                    target_bps=str(spec["target_bps"]), hold=spec["max_hold_seconds"],
                    signal_id=rules.identity(spec) + ":" + str(bars[-1][0]))

    def tick_ai(self):
        """One bounded model stage per tick; no fabricated AI when not configured."""
        if not self.model.ready:
            return
        now = time.time()
        if now - get(self.db, "ai-last-call", 0) < 5:
            return
        for user in self.db.execute("SELECT * FROM users WHERE status='active' AND strategy IN (1,2,3)"):
            key = "ai-stage:" + user["address"]
            stage = get(self.db, key, {})
            if now - stage.get("at", 0) > 300:
                stage = {}
            if not stage and now - get(self.db, "ai-last-research:" + user["address"], 0) < 180:
                continue
            try:
                from .prices import SYMBOLS, entries_allowed
                evidence = {}
                for symbol in SYMBOLS:
                    bars = bars_for(self.db, symbol, now)
                    if bars and entries_allowed(self.db, symbol):
                        evidence[symbol] = features(bars)
                if not evidence:
                    return
                put(self.db, "ai-last-call", now)
                if not stage:
                    answer = self.model.ask("research", dict(markets=evidence,
                        candidate_signals=self.signal_candidates(now), mode="Vela testnet paper only"), model_module.RESEARCH)
                    specs = [rules.validate(s, evidence) for s in answer["strategies"][:3]]
                    specs = [s for s in specs if all(c["feature"] in evidence[s["market"]] for c in s["conditions"])]
                    put(self.db, "ai-last-research:" + user["address"], now)
                    if specs:
                        put(self.db, key, dict(at=now, phase="review", spec=specs[0]))
                elif stage["phase"] == "review":
                    spec = stage["spec"]
                    answer = self.model.ask("review", dict(strategy=spec, markets=evidence), model_module.ASSESSMENT)
                    put(self.db, key, dict(stage, phase="direction") if answer["verdict"] == "confirm" else {})
                else:
                    spec = stage["spec"]
                    answer = self.model.ask("direction", dict(strategy=spec, markets=evidence), model_module.DIRECTION)
                    if answer["action"] == "open" and answer["side"] == spec["side"]:
                        put(self.db, "ai-approved:" + user["address"], dict(at=now, spec=spec))
                    put(self.db, key, {})
                log(self.db, "agents", "model_stage_completed", phase=stage.get("phase", "research"))
            except Exception as exc:
                put(self.db, key, {})
                put(self.db, "ai-last-research:" + user["address"], now)
                log(self.db, "agents", "proposal_rejected_or_unavailable", "warning", error=type(exc).__name__)
            return
