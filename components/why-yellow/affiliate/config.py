"""Runtime configuration for the affiliate dashboard (why-affiliate.service).

Environment (systemd EnvironmentFile): SUPABASE_DB_URL from secrets/customer.env (same server
connection the customer platform uses) and, from secrets/affiliate.env (0600):
  AFFILIATE_SESSION_SECRET   random, signs the login cookie (required)
  RESEND_API_KEY             sending-only Resend key: password-reset mails + email alerts (optional)
  AFFILIATE_MAIL_FROM        default "WHY Trading <noreply@whytradingbot.com>"
  AFFILIATE_BASE_URL         where reset links point, default https://whytradingbot.com/affiliate
  ALERT_TELEGRAM_TOKEN       bot token for team alerts (optional; the @Whytradingbot token works)
  ALERT_TELEGRAM_CHAT_IDS    comma-separated chat/user ids that receive alerts
  ALERT_EMAILS               comma-separated team addresses that receive alerts by mail
  AFFILIATE_REV_SHARE_PCT    default revenue share on trading fees (25 = Yellow deal)"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List, Mapping, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_BASE = "https://whytradingbot.com"          # partner links are built on this


def _list(v: str) -> List[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@dataclass(frozen=True)
class Config:
    db_url: Optional[str] = None
    session_secret: str = ""
    host: str = "127.0.0.1"
    port: int = 8109
    session_ttl_s: int = 12 * 3600
    login_rate_per_min: int = 8
    public_base: str = PUBLIC_BASE
    cookie_secure: bool = True
    base_url: str = PUBLIC_BASE + "/affiliate"
    resend_api_key: Optional[str] = None
    mail_from: str = "WHY Trading <noreply@whytradingbot.com>"
    telegram_token: Optional[str] = None
    telegram_chat_ids: List[str] = field(default_factory=list)
    alert_emails: List[str] = field(default_factory=list)
    alerts_enabled: bool = True
    alert_interval_s: float = 60.0
    rev_share_pct: float = 25.0
    tenants_dir: str = os.path.join(HERE, "state", "customer", "tenants")
    state_dir: str = os.path.join(HERE, "state", "affiliate")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        e = os.environ if env is None else env
        return cls(
            db_url=(e.get("SUPABASE_DB_URL") or None),
            session_secret=str(e.get("AFFILIATE_SESSION_SECRET", "")),
            host=str(e.get("AFFILIATE_HOST", "127.0.0.1")),
            port=int(e.get("AFFILIATE_PORT", "8109")),
            session_ttl_s=int(e.get("AFFILIATE_SESSION_TTL_S", str(12 * 3600))),
            login_rate_per_min=int(e.get("AFFILIATE_LOGIN_RATE_PER_MIN", "8")),
            public_base=str(e.get("AFFILIATE_PUBLIC_BASE", PUBLIC_BASE)).rstrip("/"),
            cookie_secure=str(e.get("AFFILIATE_COOKIE_SECURE", "1")).lower() in ("1", "true", "yes"),
            base_url=str(e.get("AFFILIATE_BASE_URL", PUBLIC_BASE + "/affiliate")).rstrip("/"),
            resend_api_key=(e.get("RESEND_API_KEY") or None),
            mail_from=str(e.get("AFFILIATE_MAIL_FROM", "WHY Trading <noreply@whytradingbot.com>")),
            telegram_token=(e.get("ALERT_TELEGRAM_TOKEN") or None),
            telegram_chat_ids=_list(e.get("ALERT_TELEGRAM_CHAT_IDS", "")),
            alert_emails=_list(e.get("ALERT_EMAILS", "")),
            alerts_enabled=str(e.get("AFFILIATE_ALERTS", "1")).lower() in ("1", "true", "yes"),
            alert_interval_s=float(e.get("AFFILIATE_ALERT_INTERVAL_S", "60")),
            rev_share_pct=float(e.get("AFFILIATE_REV_SHARE_PCT", "25")),
            tenants_dir=str(e.get("AFFILIATE_TENANTS_DIR", os.path.join(HERE, "state", "customer", "tenants"))),
            state_dir=str(e.get("AFFILIATE_STATE_DIR", os.path.join(HERE, "state", "affiliate"))),
        )

    @property
    def ready(self) -> bool:
        return bool(self.db_url and len(self.session_secret) >= 32)
