# WHY x Bybit — QWEN SHADOW book (forward experiment, SHADOW ONLY)

Built 2026-09-19. Service: `why-bybit-qwen-shadow.service` (enabled, Restart=always, user `whyshadow`).
NO orders, NO exchange keys (only a Groq key in /etc/why-bybit-shadow/groq.env, root 0600). Public Bybit data only.
Sandboxed: /opt/why-bybit, /opt/why-yellow, /opt/alpha, /root are INVISIBLE to the process; code dir is read-only; only ./state is writable.
No unit requires/orders against it; nothing in the live system reads its files.

Policy is FROZEN (config.json): WY candidate -> Qwen (blind to WY direction) LONG/SHORT/NO_TRADE ->
agree = approve (Book B enters) | NO_TRADE = veto | opposite = veto (+ simulated as counterfactual Q) | any error/timeout/malformed/late = no approval.
Do not tune thresholds/prompt from early results; raw Qwen output is recorded for every candidate.

Books: A = baseline WY (every candidate) · B = WY+Qwen approved · Q = Qwen-opposite counterfactual (never traded) · V = vetoed subset of A.
Sizing = the deferred-transition TARGET (194 / $20 / 10x / $200 / max_open 8), frozen from plan.json at build time.

Files (state/): candidates.jsonl (one row/candidate, incl. raw Qwen output + evidence text) · trades.jsonl (OPEN/CLOSE per book) ·
evals.jsonl (every completed bar x market, candidate or NONE) · events.jsonl · positions.json (atomic, restart-safe) · metrics.json (60s) · snapshot.json · probe.jsonl (NOT dataset)

Read results:   python3 /opt/why-bybit-shadow/shadow.py report      (or state/metrics.json)
Logs:           journalctl -u why-bybit-qwen-shadow -f
Tests:          python3 tests/test_parity.py | test_policy.py | test_isolation.py | test_e2e.py   (parity imports the live module READ-ONLY, test-only)
Remove:         systemctl disable --now why-bybit-qwen-shadow; rm /etc/systemd/system/why-bybit-qwen-shadow.service; rm -r /opt/why-bybit-shadow /etc/why-bybit-shadow; userdel whyshadow
