"""Fail-closed host + ARM guard for the LIVE Yellow->Bybit executor. The ONLY module allowed to
mention the blocked hostnames.

Rules (checked at import, at every HTTP call, and by tests):
  * allowed host   : api.bybit.com (mainnet)      blocked: api-demo.bybit.com, api-testnet.bybit.com, anything else
  * env BYBIT_LIVE_YELLOW=1 must be present or the process terminates
  * every urllib request in this process passes through check_url(); unknown/blocked host -> SystemExit
  * ARM: no POST (order/leverage/trading-stop) is ever sent unless state/ARMED.json exists and holds
    {"command": "START LIVE"} written by arm.py on the owner's explicit command. Unarmed -> NotArmed
    (caught by the engine, recorded as DRY_RUN). Disarm = delete the file (arm.py --disarm).
  * source scan: no file in this package other than guard.py may contain a blocked hostname string.
"""
import os, sys, re, json, urllib.parse, urllib.request

ALLOWED_HOST = "api.bybit.com"
ALLOWED_BASE = "https://" + ALLOWED_HOST
BLOCKED_HOSTS = ("api-demo.bybit.com", "api-testnet.bybit.com")
LOCAL_OK = ("127.0.0.1", "localhost")           # mark-shadow health endpoint only
PKG = os.path.dirname(os.path.abspath(__file__))
ARM_FILE = os.path.join(PKG, "state", "ARMED.json")
ARM_PHRASE = "START LIVE"
ENV_FLAG = "BYBIT_LIVE_YELLOW"

class GuardViolation(SystemExit):
    pass
class NotArmed(Exception):
    """Raised on any write attempt while unarmed. Never fatal: the engine records a DRY_RUN."""

def die(msg):
    sys.stderr.write(f"GUARD VIOLATION: {msg}\n"); sys.stderr.flush()
    raise GuardViolation(97)

def check_url(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in BLOCKED_HOSTS: die(f"blocked host {host} in {url}")
    if host == ALLOWED_HOST or host in LOCAL_OK: return url
    die(f"unknown host {host} in {url}")

def assert_live_base(base):
    if base != ALLOWED_BASE: die(f"base url {base!r} != {ALLOWED_BASE!r}")
    return base

def require_env():
    if os.environ.get(ENV_FLAG) != "1": die(f"{ENV_FLAG}=1 not set")

def arm_state(arm_file=None):
    """Returns dict(armed=bool, reason=str, record=dict|None). Armed only if env flag AND file phrase match."""
    f = arm_file or ARM_FILE
    if os.environ.get(ENV_FLAG) != "1": return dict(armed=False, reason=f"env {ENV_FLAG}!=1", record=None)
    if not os.path.exists(f): return dict(armed=False, reason="no ARMED.json (owner has not said START LIVE)", record=None)
    try:
        with open(f) as fh: rec = json.load(fh)
    except Exception as e: return dict(armed=False, reason=f"ARMED.json unreadable: {e!r}", record=None)
    if rec.get("command") != ARM_PHRASE: return dict(armed=False, reason="ARMED.json command != START LIVE", record=rec)
    return dict(armed=True, reason="armed", record=rec)

def armed(arm_file=None): return arm_state(arm_file)["armed"]

def require_armed(what, arm_file=None):
    st = arm_state(arm_file)
    if not st["armed"]: raise NotArmed(f"{what}: {st['reason']}")
    return st

def scan_sources():
    bad = []
    for root, _, files in os.walk(PKG):
        if "/." in root or "__pycache__" in root or "/secrets" in root or "/state" in root or "/logs" in root: continue
        for f in files:
            if not f.endswith((".py", ".json", ".service", ".sh", ".md")) or f == "guard.py": continue
            src = open(os.path.join(root, f), errors="replace").read()
            for h in BLOCKED_HOSTS:
                if re.search(r"(?<![\w-])" + re.escape(h), src): bad.append(f"{f}: {h}")
    if bad: die("blocked hostname in sources: " + ", ".join(bad))

# ---- process-wide urllib gate --------------------------------------------------
_real_urlopen = urllib.request.urlopen
def _gated_urlopen(url, *a, **kw):
    check_url(url.full_url if isinstance(url, urllib.request.Request) else url)
    return _real_urlopen(url, *a, **kw)
urllib.request.urlopen = _gated_urlopen

def install(check_env=True):
    if check_env: require_env()
    scan_sources()
    assert_live_base(ALLOWED_BASE)
    return True
