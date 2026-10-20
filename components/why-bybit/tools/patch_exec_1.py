p='/opt/why-bybit/bybit_exec.py'; s=open(p).read()
def rep(old,new):
    global s
    assert s.count(old)==1, f"count={s.count(old)} for: "+old[:90]
    s=s.replace(old,new)

# halt flag: either mode's state dir
rep('HALT_FLAG = os.path.join(STATE, "halt.flag")           # touch to make every open() refuse',
    'HALT_FLAGS = [os.path.join(HERE, d, "halt.flag") for d in ("state", "state-live")]   # touch either to make every open() refuse')
rep('        if os.path.exists(HALT_FLAG): raise RuntimeError("halt.flag present")',
    '        if any(os.path.exists(f) for f in HALT_FLAGS): raise RuntimeError("halt.flag present")')

# preflight: margin mode + strict IP whitelist
rep('''        acct = c.account_info()
        if acct.get("marginMode") != "REGULAR_MARGIN":
            raise PreflightError(f"marginMode={acct.get('marginMode')} (need REGULAR_MARGIN / cross, as the Yellow book assumes)")''','''        acct = c.account_info()
        if acct.get("marginMode") not in ("REGULAR_MARGIN", "ISOLATED_MARGIN"):
            raise PreflightError(f"marginMode={acct.get('marginMode')} (need REGULAR_MARGIN or ISOLATED_MARGIN; portfolio margin is not supported)")
        ips = info.get("ips")
        if os.environ.get("BYBIT_REQUIRE_IP_WHITELIST", "1") == "1" and (not ips or ips == ["*"]):
            raise PreflightError("API key is NOT IP-restricted (ips=['*']). Whitelist this server's IP on the key, or set BYBIT_REQUIRE_IP_WHITELIST=0 to override knowingly.")''')
rep('''        if info.get("ips") in (["*"], [], None): log("WARN: API key is not IP-restricted; whitelist this server's IP")
''','')
rep('''        rep = {"ips": info.get("ips"),''','''        rep = {"ips": info.get("ips"), "margin_mode": acct.get("marginMode"),''')

# open(): min-lot rule under a hard per-trade loss cap, margin + balance checks
a = s.index('    def open(self, market: str, side: str, strat: str, notional: float, stop_px: float, leverage: float,')
b = s.index('    def close(self, market: str, pos: Dict[str, Any], reason: str)')
s = s[:a] + '''    def open(self, market: str, side: str, strat: str, notional: float, stop_px: float, leverage: float,
             bar_ms: Optional[int], mark_ts: Optional[float], risk: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Size rule: qty = floor(notional/price to qtyStep). If that is below the venue minimum (e.g. BTC 0.001),
        bump to the minimum lot ONLY IF the planned worst-case loss of that lot is <= risk.max_loss_usd; else refuse."""
        self.c._guard()
        if not self.ready: raise PreflightError("preflight has not passed")
        if any(os.path.exists(f) for f in HALT_FLAGS): raise RuntimeError("halt.flag present")
        if mark_ts is None or time.time() - mark_ts > 30: raise RuntimeError("market data stale >30s — refusing to enter")
        if self.stream and not self.stream.connected: raise RuntimeError("private WS down — refusing to enter")
        risk = risk or {}
        sym = self.u[market]; f = self.c.filters(sym)
        px = float(self.c.orderbook(sym, 1)["a" if side == "long" else "b"][0][0])
        qty = Decimal(self.c.round_qty(sym, notional / px))
        min_lot = max(f["min_qty"], (f["min_notional"] / Decimal(str(px)) / f["step"]).to_integral_value(ROUND_CEILING) * f["step"])
        if qty < min_lot:
            planned = float(min_lot) * px * (risk.get("sl_pct", 2.0) / 100 + (risk.get("fee_bps_rt", 11) + risk.get("slip_bps_rt", 4)) / 1e4)
            cap = risk.get("max_loss_usd")
            if cap is None or planned > cap:
                raise RuntimeError(f"{sym} minimum lot {min_lot} (${float(min_lot)*px:.2f}) has planned worst-case loss ${planned:.2f} > cap ${cap}")
            qty = min_lot                                     # min-lot bump, inside the loss cap
        w = self.c.wallet(); usdt = next((float(x.get("walletBalance") or 0) for x in w.get("coin", []) if x["coin"] == "USDT"), 0.0)
        margin = float(qty) * px / float(leverage)
        if usdt < margin * 1.5: raise RuntimeError(f"USDT {usdt:.2f} < 1.5x required margin {margin:.2f}")
        self.c.set_leverage(sym, leverage)
        link = link_id("o", strat, market, side, bar_ms)
        sl = self.c.round_px(sym, stop_px, up=(side == "short"))
        res = self.c.create(sym, "Buy" if side == "long" else "Sell", _q(qty), link, stop_loss=sl)
        fill = self._fill(sym, res["orderId"], link); fill["adopted"] = bool(res.get("adopted")); fill["venue_stop"] = sl
        return fill

''' + s[b:]

# close(): audit the exchange's own funding settlements (best effort; accounting uses the rate history)
rep('''        res = self.c.create(sym, "Sell" if pos["side"] == "long" else "Buy", size, link, reduce_only=True)
        return self._fill(sym, res["orderId"], link)''','''        res = self.c.create(sym, "Sell" if pos["side"] == "long" else "Buy", size, link, reduce_only=True)
        out = self._fill(sym, res["orderId"], link)
        try:                                                  # exchange-reported funding for audit: expense = -sum(change)
            tl = self.c._req("GET", "/v5/account/transaction-log", {"accountType": "UNIFIED", "category": "linear", "type": "SETTLEMENT",
                             "startTime": pos["opened_ms"], "endTime": int(time.time() * 1000), "limit": 50})["list"]
            out["funding_usd"] = -sum(float(r.get("change") or 0) for r in tl if r.get("symbol") == sym)
        except Exception: pass
        return out''')

# fees CLI
rep('''def do_reconcile() -> int:''','''def do_fees() -> int:
    """Authenticated, read-only: pull the account's fee rates for the universe and write state*/fees.json."""
    c = BybitClient.from_secrets()
    if not c.has_keys: print("no API key on disk"); return 1
    info = c.api_info(); rates = {}
    for m, sym in UNIVERSE.items():
        r = c.fee_rate(sym); rates[sym] = {"maker_bps": float(r["makerFeeRate"]) * 1e4, "taker_bps": float(r["takerFeeRate"]) * 1e4}
    doc = {"source": "GET /v5/account/fee-rate (authenticated)", "vip_level": info.get("vipLevel"), "fetched_ms": int(time.time() * 1000), "rates": rates}
    for d in ("state", "state-live"):
        os.makedirs(os.path.join(HERE, d), exist_ok=True)
        with open(os.path.join(HERE, d, "fees.json"), "w") as f: json.dump(doc, f, indent=1)
    print(json.dumps({"vip": doc["vip_level"], "distinct_maker_bps": sorted({v["maker_bps"] for v in rates.values()}),
                      "distinct_taker_bps": sorted({v["taker_bps"] for v in rates.values()})}))
    return 0

def do_reconcile() -> int:''')
rep('''    sub.add_parser("reconcile", help''','''    sub.add_parser("fees", help="fetch authenticated fee rates -> state/fees.json (read-only)")
    sub.add_parser("reconcile", help''')
rep('"reconcile": do_reconcile}[a.cmd]()','"reconcile": do_reconcile, "fees": do_fees}[a.cmd]()')
open(p,'w').write(s); print("exec patched")
