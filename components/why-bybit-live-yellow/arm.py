#!/usr/bin/env python3
"""ARM / DISARM the live executor. Only `python3 arm.py "START LIVE"` (the owner's literal command)
writes state/ARMED.json; the running engine picks it up within one poll and switches from DRY_RUN
to LIVE. Refuses to arm without available trading collateral or if the key policy fails.
`python3 arm.py --disarm` deletes the file (open positions stay under exchange TP/SL)."""
import json, os, sys, time
os.environ.setdefault("BYBIT_LIVE_YELLOW", "1")
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import guard; guard.install(check_env=False)
import engine
from bybit_live import from_secrets, verify_key

def main():
    if "--disarm" in sys.argv:
        if os.path.exists(guard.ARM_FILE): os.remove(guard.ARM_FILE); print("DISARMED: ARMED.json removed; engine returns to DRY_RUN on next poll")
        else: print("already disarmed")
        return 0
    if len(sys.argv) < 2 or sys.argv[1] != guard.ARM_PHRASE:
        print(f'refused: the only arming command is  python3 arm.py "{guard.ARM_PHRASE}"'); return 2
    c = from_secrets(os.path.join(BASE, engine.CFG["secrets_file"]))
    ok, problems, warnings, summary = verify_key(c.key_info(), engine.CFG["key_policy"], engine.CFG["expected_uid"])
    if not ok: print("refused: key policy failed:", problems); return 3
    w = engine.wallet_snapshot(c.wallet()[0])
    if w["equity"] <= 0 or w["available"] <= 0:
        print(f"refused: no available trading collateral (equity ${w['equity']:.2f}, available ${w['available']:.6f})"); return 4
    rec = dict(command=guard.ARM_PHRASE, armed_ms=int(time.time() * 1000), uid=summary["uid"], key_id=summary["key_id"], equity_at_arm=w["equity"], by="owner command via arm.py")
    os.makedirs(os.path.dirname(guard.ARM_FILE), exist_ok=True); open(guard.ARM_FILE, "w").write(json.dumps(rec, indent=1))
    print("ARMED:", json.dumps(rec)); print("engine switches to LIVE on its next poll (no restart needed); disarm with --disarm")
    return 0
if __name__ == "__main__": sys.exit(main())
