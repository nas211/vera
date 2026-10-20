"""Fail-closed host guard. The ONLY module allowed to mention the blocked hostnames.

Rules (checked at import, at every HTTP call, and by tests):
  * allowed host   : api-demo.bybit.com
  * blocked hosts  : api.bybit.com, api-testnet.bybit.com  (and anything else)
  * env BYBIT_DEMO_ONLY=1 must be present or the process terminates
  * every urllib request in this process passes through check_url(); a blocked
    or unknown host terminates the process (SystemExit), it never falls back.
  * source scan: no file in this package other than guard.py may contain a
    blocked hostname string.
"""
import os, sys, re, urllib.parse, urllib.request

ALLOWED_HOST = "api-demo.bybit.com"
ALLOWED_BASE = "https://" + ALLOWED_HOST
BLOCKED_HOSTS = ("api.bybit.com", "api-testnet.bybit.com")
LOCAL_OK = ("127.0.0.1", "localhost")           # mark-shadow health endpoint only
PKG = os.path.dirname(os.path.abspath(__file__))

class GuardViolation(SystemExit):
    pass

def die(msg):
    sys.stderr.write(f"GUARD VIOLATION: {msg}\n"); sys.stderr.flush()
    raise GuardViolation(97)

def check_url(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in BLOCKED_HOSTS: die(f"blocked host {host} in {url}")
    if host == ALLOWED_HOST or host in LOCAL_OK: return url
    die(f"unknown host {host} in {url}")

def assert_demo_base(base):
    if base != ALLOWED_BASE: die(f"base url {base!r} != {ALLOWED_BASE!r}")
    return base

def require_env():
    if os.environ.get("BYBIT_DEMO_ONLY") != "1": die("BYBIT_DEMO_ONLY=1 not set")

def scan_sources():
    bad = []
    for root, _, files in os.walk(PKG):
        if "/." in root or "__pycache__" in root or "/secrets" in root or "/state" in root or "/logs" in root: continue
        for f in files:
            if not f.endswith((".py", ".json", ".service", ".sh")) or f == "guard.py": continue
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
    assert_demo_base(ALLOWED_BASE)
    return True
