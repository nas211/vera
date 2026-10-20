import ast, hashlib, unittest
from decimal import Decimal
from .harness import Env, ACCT
from execution import geometry

# Historical audited baseline (2026-09-22, /root/audit/evidence/baseline_manifest.json).
AUDITED_BASELINE = {"yellow_paper.py": "d4ebbad0f65cd3867fb96528656a0dbc7a85cbb4423447197673f45b72fad9f2",
                    "yellow_webui_snapshot.py": "716d961e567422f86b5769924b2dfcd8a9376ee8444f16fc2f545daf27a8d7d0"}
# Approved operational change 2026-09-23 (owner): new-entry pause gate on state/pause_entries.flag in
# yellow_paper.open_position. Exactly these four lines, nothing else (/root/audit/evidence/approved_paper_changes.json).
APPROVED_PAUSE_GATE_LINES = [
    'PAUSE_FLAG = os.path.join(STATE, "pause_entries.flag")   # touch to pause NEW paper entries; exits unaffected\n',
    '\n',
    '    if os.path.exists(PAUSE_FLAG):\n',
    '        log(f"entry PAUSED ({PAUSE_FLAG} present): skip {strat} {side} {m}"); return\n',
]
APPROVED = {"yellow_paper.py": "83422260f8f1f59828eead82dd38b0fd75d1057adb023672d68a829aef06fdc4",
            "yellow_webui_snapshot.py": AUDITED_BASELINE["yellow_webui_snapshot.py"]}
BASELINE = APPROVED


def paper_without_pause_gate() -> bytes:
    """The live file with exactly the approved pause-gate lines removed, in order. Anything
    else that differs from the audited baseline makes the hash mismatch."""
    lines = open("/opt/why-yellow/yellow_paper.py").read().splitlines(keepends=True)
    out, wanted = [], list(APPROVED_PAUSE_GATE_LINES)
    for ln in lines:
        if wanted and ln == wanted[0]:
            wanted.pop(0)
            continue
        out.append(ln)
    assert not wanted, f"approved pause-gate lines missing: {wanted}"
    return "".join(out).encode()


class TestStrategyIntegrity(unittest.TestCase):
    def test_19_strategy_logic_unchanged(self):
        """Live files must equal the approved version byte-for-byte AND, with the four approved
        pause-gate lines stripped, equal the historical audited baseline byte-for-byte."""
        for f, h in APPROVED.items():
            self.assertEqual(hashlib.sha256(open(f"/opt/why-yellow/{f}", "rb").read()).hexdigest(), h, f)
        self.assertEqual(hashlib.sha256(paper_without_pause_gate()).hexdigest(), AUDITED_BASELINE["yellow_paper.py"],
                         "yellow_paper.py differs from the audited baseline by more than the approved pause gate")

    def test_19b_pause_gate_touches_only_open_position(self):
        tree = ast.parse(open("/opt/why-yellow/yellow_paper.py").read())
        users = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and any(isinstance(x, ast.Name) and x.id == "PAUSE_FLAG" for x in ast.walk(n))]
        self.assertEqual(users, ["open_position"])
        for name in ("on_mark", "close_position", "on_bar_close", "ema_last", "rsi_last", "atr_last"):
            fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertFalse(any(isinstance(x, ast.Name) and x.id in ("PAUSE_FLAG", "FLATTEN_FLAG") for x in ast.walk(fn)), name)

    def test_geometry_mirror_matches_paper_cfg_defaults(self):
        tree = ast.parse(open("/opt/why-yellow/yellow_paper.py").read())
        cfg = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CFG" for t in node.targets):
                for kw in node.value.keywords:
                    if isinstance(kw.value, ast.Call) and len(kw.value.args) == 2:
                        cfg[kw.value.args[0].value] = kw.value.args[1].value
        s = geometry.sleeves()
        self.assertEqual(s["WY-REVERT"].tp_pct, Decimal(str(cfg["WY_RV_TP_PCT"])))
        self.assertEqual(s["WY-REVERT"].sl_pct, Decimal(str(cfg["WY_RV_SL_PCT"])))
        self.assertEqual(s["WY-PULLBACK"].tp_pct, Decimal(str(cfg["WY_PB_TP_PCT"])))
        self.assertEqual(s["WY-PULLBACK"].sl_pct, Decimal(str(cfg["WY_PB_SL_PCT"])))
        self.assertEqual(geometry.leverage(), int(cfg["WY_LEVERAGE"]))
        self.assertEqual(geometry.timeout_bars(), int(cfg["WY_TIMEOUT_BARS"]))
        self.assertEqual(set(s), {"WY-REVERT", "WY-PULLBACK"})

    def test_18_sleeves_distinct(self):
        e = Env(); e.ready()
        c1, _ = e.open_and_fill("sig-1", strategy="WY-REVERT")
        c2, _ = e.open_and_fill("sig-2", strategy="WY-PULLBACK", market="ETHUSDT-PERP", price="2500")
        p1, p2 = e.ledger.get_protection(c1), e.ledger.get_protection(c2)
        self.assertEqual(e.pos(c1)["strategy"], "WY-REVERT"); self.assertEqual(e.pos(c2)["strategy"], "WY-PULLBACK")
        r1 = Decimal(p1["tp_price"]) / Decimal(e.pos(c1)["entry_price"]); r2 = Decimal(p2["tp_price"]) / Decimal(e.pos(c2)["entry_price"])
        self.assertLess(abs(r1 - Decimal("1.003")), Decimal("0.0001")); self.assertLess(abs(r2 - Decimal("1.008")), Decimal("0.0001"))
        s1 = Decimal(p1["stop_price"]) / Decimal(e.pos(c1)["entry_price"]); s2 = Decimal(p2["stop_price"]) / Decimal(e.pos(c2)["entry_price"])
        self.assertLess(abs(s1 - Decimal("0.984")), Decimal("0.0001")); self.assertLess(abs(s2 - Decimal("0.98")), Decimal("0.0001"))

    def test_17_strategy_identifier_attached_throughout(self):
        e = Env(); e.ready()
        coid, _ = e.open_and_fill("sig-1", strategy="WY-PULLBACK")
        self.assertEqual(e.order(coid)["strategy"], "WY-PULLBACK")
        self.assertEqual(e.pos(coid)["strategy"], "WY-PULLBACK")
        target = e.ledger.get_protection(coid)["target_coid"]
        self.assertEqual(e.order(target)["strategy"], "WY-PULLBACK")
        e.eng.close_position(coid, "TIMEOUT")
        close = [o for o in e.ledger.orders_for_position(coid) if o["purpose"] == "CLOSE"][0]
        self.assertEqual(close["strategy"], "WY-PULLBACK")
        self.assertEqual(close["position_id"], coid)

    def test_short_geometry(self):
        e = Env(); e.ready()
        coid, _ = e.open_and_fill("sig-1", direction="short", strategy="WY-REVERT")
        p = e.ledger.get_protection(coid); entry = Decimal(e.pos(coid)["entry_price"])
        self.assertLess(Decimal(p["tp_price"]), entry); self.assertGreater(Decimal(p["stop_price"]), entry)
        t = e.order(p["target_coid"]); self.assertEqual((t["side"], t["direction"], t["reduce_only"]), ("buy", "short", 1))
