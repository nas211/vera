"""Outbound email for the Partners portal — verification codes and account notices.

Provider order: SMTP (Gmail App Password: SMTP_HOST/PORT/USER/PASS in
/etc/why-partners/env) then Resend (RESEND_API_KEY) if set. With neither,
send() returns False and the caller logs the code for the operator — signup
still works, delivery is manual until a sender exists. Credentials never leave
the env file; nothing here logs the recipient's code or the password.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

_log = logging.getLogger("why-partners.mailer")

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
RESEND_KEY = os.environ.get("RESEND_API_KEY", "").strip()
FROM = os.environ.get("PARTNERS_MAIL_FROM", "") or (SMTP_USER and f"WHY Protocol <{SMTP_USER}>") or "WHY Protocol <onboarding@resend.dev>"


def configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS) or bool(RESEND_KEY)


def _bodies(code: str) -> tuple[str, str, str]:
    subj = "Your WHY Partner Portal verification code"
    text = (f"Your verification code is {code}\n\nIt expires in 15 minutes. "
            "If you did not request access to the WHY Partner Portal, ignore this email.")
    html = (f"<div style='font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:32px 20px;color:#f0edf2;background:#0f0e14'>"
            f"<p style='font-family:monospace;color:#F0852A;font-weight:700;letter-spacing:.1em;margin:0 0 20px'>WHY PROTOCOL · PARTNER PORTAL</p>"
            f"<p style='margin:0 0 8px;color:#a8a2b5'>Your verification code</p>"
            f"<p style='font-family:monospace;font-size:34px;letter-spacing:.3em;margin:0 0 20px'>{code}</p>"
            f"<p style='color:#a8a2b5;font-size:13px'>It expires in 15 minutes. If you did not request access, ignore this email.</p></div>")
    return subj, text, html


def _smtp(to: str, subj: str, text: str, html: str) -> bool:
    msg = EmailMessage()
    name, addr = parseaddr(FROM)
    msg["From"] = formataddr((name or "WHY Protocol", addr or SMTP_USER))
    msg["To"] = to
    msg["Subject"] = subj
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
        s.ehlo(); s.starttls(context=ctx); s.ehlo()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    return True


def _resend(to: str, subj: str, text: str, html: str) -> bool:
    body = json.dumps({"from": FROM, "to": [to], "subject": subj, "text": text, "html": html}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return 200 <= r.status < 300


def _send(to: str, subj: str, text: str, html: str) -> bool:
    """Try each configured provider in order. Failures are LOGGED, not
    swallowed: a bare `except: pass` here is how the waitlist went two days
    without anyone noticing it sent nothing. The exception text can carry the
    recipient but never the code or the password, so it is safe to print."""
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        try:
            return _smtp(to, subj, text, html)
        except Exception as e:
            _log.warning("smtp send failed to %s: %s: %s", to, type(e).__name__, e)
    if RESEND_KEY:
        try:
            return _resend(to, subj, text, html)
        except Exception as e:
            _log.warning("resend send failed to %s: %s: %s", to, type(e).__name__, e)
    if not configured():
        _log.warning("no mail provider configured — dropped %r to %s", subj, to)
    return False


def send_code(to: str, code: str, purpose: str = "verify") -> bool:
    subj, text, html = _bodies(code)
    return _send(to, subj, text, html)


def send_exists(to: str, base_url: str) -> bool:
    """Sent when someone signs up with an address that already has an account.

    The HTTP response cannot say so — answering differently would turn the
    signup form into an email-enumeration oracle — so the browser is told the
    same thing either way and the truth goes to the mailbox, which only the
    real owner can read. Without this, an existing partner who signs up again
    is told to check their inbox for a code that was never generated.
    """
    login = base_url.rstrip("/") + "/login"
    subj = "You already have a WHY Partner Portal account"
    text = ("Someone (probably you) just tried to create a WHY Partner Portal account "
            f"with this address. You already have one, so no new account was made and "
            f"no code was sent.\n\nSign in here: {login}\n\n"
            "If you have lost your password, reply to this email and we will reset it.\n\n"
            "If this was not you, nothing has changed and you can ignore this.")
    html = ("<div style='font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:32px 20px;color:#f0edf2;background:#0f0e14'>"
            "<p style='font-family:monospace;color:#F0852A;font-weight:700;letter-spacing:.1em;margin:0 0 20px'>WHY PROTOCOL · PARTNER PORTAL</p>"
            "<p style='margin:0 0 14px'>You already have an account with this address, so no new one was created and no code was sent.</p>"
            f"<p style='margin:0 0 20px'><a href='{login}' style='color:#F0852A'>Sign in to the portal</a></p>"
            "<p style='color:#a8a2b5;font-size:13px'>Lost your password? Reply to this email and we will reset it. "
            "If this was not you, nothing has changed.</p></div>")
    return _send(to, subj, text, html)
