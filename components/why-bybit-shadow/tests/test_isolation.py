#!/usr/bin/env python3
"""STATIC isolation proof over the shadow SERVICE source (tests excluded).

Checks that no service module: imports the live engine/executor, references live
paths or state files, carries any signing / private-endpoint / order vocabulary,
or reads any exchange credential. AST-based: comments and docstrings are ignored,
so documentation can name the live files but code cannot touch them.

    python3 tests/test_isolation.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE = ["shadow.py", "qwen_agent.py", "sim.py", "wy_signals.py", "report.py", "agent_transport.py"]

BANNED_IMPORT = {"bybit_exec", "bybit_paper", "deferred_transition", "yellow_paper", "yellow_exec", "hmac", "hashlib",
                 "pybit", "ccxt", "subprocess", "socket", "os.system", "ctypes"}
BANNED_STR = [r"/opt/why-bybit\b", r"/opt/why-yellow", r"/opt/alpha", r"state-live", r"halt\.flag", r"live_risk", r"deferred_transition",
              r"transition/", r"positions\.json.*why", r"X-BAPI", r"api-key", r"apikey", r"api_secret", r"BYBIT_API", r"BYBIT_ARMED",
              r"/v5/order", r"/v5/position", r"/v5/account", r"/v5/asset", r"/v5/user", r"trading-stop", r"place_order", r"cancel",
              r"amend", r"\.secrets", r"BINANCE_", r"api\.binance", r"/private", r"yellow"]
ALLOWED_HOSTS = {"api.bybit.com", "stream.bybit.com", "api.groq.com"}
PUBLIC_V5 = re.compile(r"/v5/(?:market/(kline|tickers|instruments-info|funding/history)|public/linear)")


def docstring_nodes(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant) and isinstance(b.value.value, str):
                ids.add(id(b.value))
    return ids


def scan(fname):
    src = open(os.path.join(ROOT, fname)).read()
    tree = ast.parse(src)
    doc = docstring_nodes(tree)
    problems, hosts, v5 = [], set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in BANNED_IMPORT:
                    problems.append(f"{fname}:{n.lineno} imports {a.name}")
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] in BANNED_IMPORT:
                problems.append(f"{fname}:{n.lineno} imports from {n.module}")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc:
            s = n.value
            for pat in BANNED_STR:
                if re.search(pat, s, re.I):
                    problems.append(f"{fname}:{n.lineno} string matches /{pat}/: {s[:70]!r}")
            for h in re.findall(r"(?:https?|wss)://([A-Za-z0-9.\-]+)", s):
                hosts.add(h)
            for mt in PUBLIC_V5.findall(s):
                v5.add(mt)
            if "/v5/" in s and not PUBLIC_V5.search(s):
                problems.append(f"{fname}:{n.lineno} non-public /v5/ path: {s[:70]!r}")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("system", "Popen", "run", "call", "check_output"):
            if isinstance(n.func.value, ast.Name) and n.func.value.id in ("os", "subprocess"):
                problems.append(f"{fname}:{n.lineno} process execution {n.func.value.id}.{n.func.attr}")
    return problems, hosts, v5


def main():
    allp, allh, allv5 = [], set(), set()
    for f in SERVICE:
        p, h, v = scan(f)
        allp += p; allh |= h; allv5 |= v
    # Env reads: only the Groq key, the transport tuning vars, and shadow's own knobs
    envs = set()
    for f in SERVICE:
        for n in ast.walk(ast.parse(open(os.path.join(ROOT, f)).read())):
            if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                fn = ast.unparse(n.func)
                if fn in ("os.environ.get", "os.getenv", "environ.get"):
                    envs.add(n.args[0].value)
            if isinstance(n, ast.Subscript) and ast.unparse(n.value) == "os.environ" and isinstance(n.slice, ast.Constant):
                envs.add(n.slice.value)
    cred_like = {e for e in envs if re.search(r"KEY|SECRET|PASSW|PASSPHRASE|BEARER|API", e, re.I)}
    import json
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    allh |= set(re.findall(r"(?:https?|wss)://([A-Za-z0-9.\-]+)", json.dumps(cfg)))
    bad_hosts = allh - ALLOWED_HOSTS
    assert cfg["qwen"]["key_env"] == "GROQ_API_KEY"
    print("hosts contacted by code:", sorted(allh))
    print("public Bybit endpoints  :", sorted(allv5))
    print("env vars read           :", sorted(envs))
    if cred_like:
        print("credential-like env names (must be only the Groq key indirection):", sorted(cred_like))
    for p in allp:
        print("PROBLEM", p)
    assert not allp, "isolation violations in the service source"
    assert not bad_hosts, f"unexpected hosts {bad_hosts}"
    assert allv5 <= {"kline", "tickers", "instruments-info", "funding/history", ""}
    assert not cred_like, cred_like              # the ONLY credential is the Groq key, named in config.qwen.key_env, read by agent_transport from the environment
    print("ISOLATION (static) OK: no live imports/paths, no signing/private/order vocabulary, only public Bybit + Groq hosts")


if __name__ == "__main__":
    main()
