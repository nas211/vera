"""Team alerts: a poller that watches the customer platform for new signups and account events
(credential submitted / verified / trading activated / paused / disconnected) and pushes them to
Telegram and email. Watermarks live in state/affiliate/alerts.json so a restart never re-sends;
the very first run starts at "now" (no flood of history)."""
from __future__ import annotations
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

from customer.log import get_logger, safe_exc

from .notify import Resend, Telegram

log = get_logger("affiliate.alerts")

EVENT_TEXT = {
    "credential_submitted": ("🔑", "connected a Yellow API key"),
    "verified": ("✅", "Yellow key verified — ready to activate"),
    "activated": ("🚀", "ACTIVATED trading"),
    "paused": ("⏸", "paused new entries"),
    "disconnected": ("🔌", "disconnected Yellow"),
    "size_changed": ("📏", "changed position size"),
    "size_change_scheduled": ("📏", "scheduled a size change"),
    "verification_failed": ("⚠️", "Yellow verification failed"),
}


def _mask(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return "unknown"
    u, d = email.split("@", 1)
    return (u[:2] + "***@" + d) if len(u) > 2 else (u[:1] + "***@" + d)


class Alerts:
    def __init__(self, db, cfg, telegram: Optional[Telegram] = None, mailer: Optional[Resend] = None,
                 clock: Callable[[], float] = time.time, mask_emails: bool = False):
        self.db, self.cfg, self.clock = db, cfg, clock
        self.tg = telegram or Telegram(cfg.telegram_token, cfg.telegram_chat_ids)
        self.mail = mailer or Resend(cfg.resend_api_key, cfg.mail_from)
        self.mask = mask_emails
        self.path = os.path.join(cfg.state_dir, "alerts.json")
        self.state = self._load()
        self.sent = 0
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.schema_gate = None          # callable -> bool; set by the service so no poll runs before the migration

    # ---- watermark persistence
    def _load(self) -> dict:
        try:
            with open(self.path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.state, f)
            os.replace(tmp, self.path)
        except Exception as e:
            log.warning("alert state save failed: %s", safe_exc(e))

    @property
    def enabled(self) -> bool:
        return self.cfg.alerts_enabled and (self.tg.enabled or (self.mail.enabled and bool(self.cfg.alert_emails)))

    def channels(self) -> dict:
        return {"telegram": self.tg.enabled, "email": self.mail.enabled and bool(self.cfg.alert_emails), "sent": self.sent,
                "last_poll_at": self.state.get("last_poll_at")}

    # ---- one poll
    def poll(self) -> List[str]:
        """Returns the messages produced (also sends them). Safe to call from tests without channels."""
        msgs: List[str] = []
        first_run = "signup_after" not in self.state or "event_after_id" not in self.state
        with self.db.service() as c:
            if first_run:
                now = c.execute("select now() as t").fetchone()["t"]
                last = c.execute("select coalesce(max(id), 0) as m from public.yellow_account_events").fetchone()["m"]
                self.state["signup_after"] = now.isoformat()
                self.state["event_after_id"] = int(last)
                self.state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
                self._save()
                return msgs
            users = c.execute("""
                select u.id, u.email, u.created_at, coalesce(nullif(lower(u.raw_user_meta_data->>'partner'), ''), 'direct') as partner
                from auth.users u where u.created_at > %s::timestamptz and coalesce(u.raw_user_meta_data->>'why_test_user', '') <> 'true'
                order by u.created_at""", (self.state["signup_after"],)).fetchall()
            events = c.execute("""
                select e.id, e.kind, e.detail, e.created_at, u.email, coalesce(a.partner_code, nullif(lower(u.raw_user_meta_data->>'partner'), ''), 'direct') as partner,
                       c.position_size_usd
                from public.yellow_account_events e
                join public.yellow_accounts a on a.id = e.account_id
                join auth.users u on u.id = e.user_id
                left join public.yellow_bot_configs c on c.account_id = a.id
                where e.id > %s and e.kind = any(%s) order by e.id""", (int(self.state["event_after_id"]), list(EVENT_TEXT.keys()))).fetchall()
            last_id = c.execute("select coalesce(max(id), 0) as m from public.yellow_account_events").fetchone()["m"]
        for u in users:
            msgs.append(f"🆕 New signup: <b>{self._who(u['email'])}</b> via <b>{u['partner']}</b>")
            self.state["signup_after"] = u["created_at"].isoformat()
        for e in events:
            ico, txt = EVENT_TEXT[e["kind"]]
            extra = f" · ${e['position_size_usd']}/trade" if e["kind"] == "activated" and e["position_size_usd"] else ""
            if e["kind"] == "verification_failed" or (e["kind"] == "credential_submitted" and (e["detail"] or {}).get("rotated")):
                pass
            msgs.append(f"{ico} <b>{self._who(e['email'])}</b> ({e['partner']}) {txt}{extra}")
        self.state["event_after_id"] = max(int(self.state["event_after_id"]), int(last_id))
        self.state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
        if msgs:
            self._send(msgs)
        self._save()
        return msgs

    def _who(self, email):
        return _mask(email) if self.mask else (email or "unknown")

    def _send(self, msgs: List[str]) -> None:
        text = "\n".join(msgs)
        head = f"WHY · {len(msgs)} update{'s' if len(msgs) > 1 else ''}\n" if len(msgs) > 1 else ""
        try:
            self.sent += self.tg.send(head + text)
        except Exception as e:
            log.warning("telegram: %s", safe_exc(e))
        if self.cfg.alert_emails and self.mail.enabled:
            html = "<div style='font:14px/1.5 -apple-system,Segoe UI,sans-serif'>" + "<br>".join(msgs) + f"<p><a href='{self.cfg.base_url}/'>Open the dashboard</a></p></div>"
            plain = "\n".join(m.replace("<b>", "").replace("</b>", "") for m in msgs)
            if self.mail.send(self.cfg.alert_emails, f"WHY: {msgs[0].replace('<b>', '').replace('</b>', '')[:70]}", html, plain):
                self.sent += 1

    def test_message(self) -> dict:
        self._send(["🔔 Test alert from the WHY affiliate dashboard — alerts are working."])
        return self.channels()

    # ---- thread
    def start(self) -> None:
        if not self.enabled:
            log.info("alerts disabled (no telegram/email channel configured)")
            return
        self.thread = threading.Thread(target=self._run, name="affiliate-alerts", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.schema_gate is None or self.schema_gate():
                    self.poll()
            except Exception as e:
                log.error("alert poll error: %s", safe_exc(e))
            self.stop_event.wait(self.cfg.alert_interval_s)
