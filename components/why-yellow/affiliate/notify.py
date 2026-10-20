"""Outbound channels for the affiliate CRM: Telegram (bot token + chat ids) and email via the
Resend HTTP API. Both are optional; a missing credential just disables that channel.
Nothing here is ever called with a customer's key or secret."""
from __future__ import annotations
import json
import urllib.error
import urllib.request
from typing import List, Optional

from customer.log import get_logger, register_secret, safe_exc

log = get_logger("affiliate.notify")


class Telegram:
    def __init__(self, token: Optional[str], chat_ids: List[str], timeout: float = 10.0):
        self.token = (token or "").strip()
        self.chat_ids = [c.strip() for c in chat_ids if c and c.strip()]
        self.timeout = timeout
        if self.token:
            register_secret(self.token)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_ids)

    def send(self, text: str) -> int:
        if not self.enabled:
            return 0
        n = 0
        for chat in self.chat_ids:
            body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=body, headers={"Content-Type": "application/json", "User-Agent": "why-affiliate/1"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    if r.status == 200:
                        n += 1
            except Exception as e:
                log.warning("telegram send to %s failed: %s", chat, safe_exc(e))
        return n


class Resend:
    def __init__(self, api_key: Optional[str], sender: str, timeout: float = 10.0):
        self.key = (api_key or "").strip()
        self.sender, self.timeout = sender, timeout
        if self.key:
            register_secret(self.key)

    @property
    def enabled(self) -> bool:
        return bool(self.key and self.sender)

    def send(self, to: List[str], subject: str, html: str, text: Optional[str] = None) -> bool:
        if not self.enabled or not to:
            return False
        body = json.dumps({"from": self.sender, "to": to, "subject": subject, "html": html, **({"text": text} if text else {})}).encode()
        req = urllib.request.Request("https://api.resend.com/emails", data=body,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}", "User-Agent": "why-affiliate/1 (+https://whytradingbot.com)"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return 200 <= r.status < 300
        except urllib.error.HTTPError as e:
            try:
                detail = e.read(300).decode("utf-8", "replace")
            except Exception:
                detail = ""
            log.warning("resend %s: %s %s", e.code, safe_exc(e), detail.strip())
        except Exception as e:
            log.warning("resend failed: %s", safe_exc(e))
        return False
