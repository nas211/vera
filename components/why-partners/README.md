# why-partners — WHY Partner Portal

Invite-only, READ-ONLY dashboard of the WHY master paper book at
https://whytradingbot.com/partners. Loopback service on 127.0.0.1:8099
(`why-partners.service`), proxied by nginx. Stdlib Python + bcrypt + SQLite.

* `book.py` — the ONE normalisation layer: reads the paper engine's closed-trade
  journal + open book O_RDONLY, reuses the public publisher's helpers, exposes
  no strategy IP (models are anonymous ordinals).
* `auth.py` / `partnersctl.py` — accounts (bcrypt), hashed session tokens,
  rate-limited generic login. No signup path exists; accounts come only from
  `partnersctl.py create <user>` on the box.
* `server.py` — routes under /partners, no-store + CSP headers, 401 on every
  unauthenticated API call.
* Runtime config: `/etc/why-partners/env` (not in git). DB: `/var/lib/why-partners/`.
* Tests: `python3 -m unittest discover -s tests`
