"""Structured logging with mandatory secret redaction.

Every record that passes through a handler created here is scrubbed:
  * exact values registered with register_secret() (API keys/secrets, tokens, DSN passwords)
  * Authorization / Bearer values, X-API-KEY / X-SIGNATURE headers
  * JSON-ish fields named api_key / api_secret / secret / password / token
  * long hex or base64url runs (raw key material)
Exceptions are logged through safe_exc() (type + scrubbed message, no args)."""
from __future__ import annotations
import logging
import re
import sys
import threading
from typing import List

_SECRETS: set = set()
_LOCK = threading.Lock()
REDACTED = "[REDACTED]"

_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + REDACTED),
    (re.compile(r"(?i)((?:x-api-key|x-signature|authorization|apikey)\s*[:=]\s*)\S+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(['\"]?(?:api_secret|api_key|apiSecret|apiKey|secret|password|passwd|token|access_token|refresh_token)['\"]?\s*[:=]\s*['\"]?)([^'\",\s}\]]+)"), r"\1" + REDACTED),
    (re.compile(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"), r"\1" + REDACTED + r"\2"),
    (re.compile(r"\b[0-9a-fA-F]{40,}\b"), "[REDACTED_HEX]"),
    (re.compile(r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{16,}\b"), "[REDACTED_JWT]"),
]


def register_secret(value) -> None:
    """Mark a concrete value as secret so it can never appear in logs."""
    if value is None:
        return
    s = str(value)
    if len(s) >= 6:
        with _LOCK:
            _SECRETS.add(s)


def redact(text) -> str:
    s = str(text)
    with _LOCK:
        secrets = sorted(_SECRETS, key=len, reverse=True)
    for v in secrets:
        if v in s:
            s = s.replace(v, REDACTED)
    for pat, rep in _PATTERNS:
        s = pat.sub(rep, s)
    return s


def safe_exc(e: BaseException, limit: int = 240) -> str:
    return f"{type(e).__name__}: {redact(str(e))[:limit]}"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = ()
        if record.exc_info:
            # never emit raw tracebacks with possibly-secret locals/args
            record.msg += " | exc=" + safe_exc(record.exc_info[1]) if record.exc_info[1] else ""
            record.exc_info = None
            record.exc_text = None
        return True


class CaptureHandler(logging.Handler):
    """Test helper: collects scrubbed lines."""
    def __init__(self):
        super().__init__()
        self.lines: List[str] = []
        self.addFilter(RedactingFilter())

    def emit(self, record):
        self.lines.append(self.format(record))


_ROOT = logging.getLogger("why.customer")
_ROOT.setLevel(logging.INFO)
_ROOT.propagate = False
_ROOT.addFilter(RedactingFilter())
if not any(isinstance(h, logging.StreamHandler) for h in _ROOT.handlers):
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    h.addFilter(RedactingFilter())
    _ROOT.addHandler(h)


def get_logger(name: str = "") -> logging.Logger:
    lg = _ROOT.getChild(name) if name else _ROOT
    if not any(isinstance(f, RedactingFilter) for f in lg.filters):
        lg.addFilter(RedactingFilter())
    return lg


def attach(handler: logging.Handler) -> None:
    if not any(isinstance(f, RedactingFilter) for f in handler.filters):
        handler.addFilter(RedactingFilter())
    _ROOT.addHandler(handler)


def detach(handler: logging.Handler) -> None:
    _ROOT.removeHandler(handler)
