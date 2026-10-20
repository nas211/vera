"""Runtime configuration. Everything comes from the process environment (systemd
EnvironmentFile=/opt/why-yellow/secrets/customer.env, mode 0600, gitignored).

Public values (safe for the browser): SUPABASE_URL, SUPABASE_ANON_KEY.
Server-only values: SUPABASE_DB_URL (direct Postgres), SUPABASE_JWT_SECRET (only for
legacy HS256 projects; asymmetric projects use the public JWKS instead).
The Supabase service-role key is NOT used by this platform and must not be configured."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_SIZES_USD = (20, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000)
FIXED_LEVERAGE = 10
ALLOWED_LEVERAGE = (2, 5, 10, 15, 20)
ALLOWED_POSITION_PCT = (1, 2, 5, 10, 15, 20)
MAX_OPEN_POSITIONS = 8
VENUE = "yellow"


@dataclass(frozen=True)
class Config:
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: Optional[str] = None
    supabase_db_url: Optional[str] = None
    api_host: str = "127.0.0.1"
    api_port: int = 8104
    state_dir: str = os.path.join(HERE, "state", "customer")
    journal_path: str = os.path.join(HERE, "state", "journal.jsonl")
    runner_enabled: bool = True
    feed_enabled: bool = True
    yellow_timeout_s: float = 15.0
    verify_rate_per_min: int = 5
    max_signal_age_s: float = 120.0
    manage_interval_s: float = 20.0
    weex_enabled: bool = False
    weex_base_url: str = "https://api-contract.weex.com"
    weex_broker_id: Optional[str] = None
    weex_broker_api_key: Optional[str] = None
    weex_broker_api_secret: Optional[str] = None
    weex_broker_api_passphrase: Optional[str] = None
    stripe_secret_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None
    stripe_price_core_monthly: Optional[str] = None
    stripe_price_core_annual: Optional[str] = None
    stripe_price_pro_monthly: Optional[str] = None
    stripe_price_pro_annual: Optional[str] = None
    stripe_price_prime_monthly: Optional[str] = None
    stripe_price_prime_annual: Optional[str] = None
    site_url: str = "https://whytradingbot.com"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        e = os.environ if env is None else env
        def b(k, d):
            return str(e.get(k, "1" if d else "0")).strip().lower() in ("1", "true", "yes", "on")
        if e.get("SUPABASE_SERVICE_ROLE_KEY"):
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY must not be configured for the customer platform (server uses SUPABASE_DB_URL)")
        return cls(
            supabase_url=str(e.get("SUPABASE_URL", "")).rstrip("/"),
            supabase_anon_key=str(e.get("SUPABASE_ANON_KEY", "")),
            supabase_jwt_secret=(e.get("SUPABASE_JWT_SECRET") or None),
            supabase_db_url=(e.get("SUPABASE_DB_URL") or None),
            api_host=str(e.get("WY_CUSTOMER_API_HOST", "127.0.0.1")),
            api_port=int(e.get("WY_CUSTOMER_API_PORT", "8104")),
            state_dir=str(e.get("WY_CUSTOMER_STATE_DIR", os.path.join(HERE, "state", "customer"))),
            journal_path=str(e.get("WY_SIGNAL_JOURNAL", os.path.join(HERE, "state", "journal.jsonl"))),
            runner_enabled=b("WY_CUSTOMER_RUNNER", True),
            feed_enabled=b("WY_CUSTOMER_FEED", True),
            yellow_timeout_s=float(e.get("WY_YELLOW_TIMEOUT_S", "15")),
            verify_rate_per_min=int(e.get("WY_VERIFY_RATE_PER_MIN", "5")),
            max_signal_age_s=float(e.get("WY_MAX_SIGNAL_AGE_S", "120")),
            manage_interval_s=float(e.get("WY_MANAGE_INTERVAL_S", "20")),
            weex_enabled=b("WEEX_LIVE_ENABLED", False),
            weex_base_url=str(e.get("WEEX_BASE_URL", "https://api-contract.weex.com")).rstrip("/"),
            weex_broker_id=(e.get("WEEX_BROKER_ID") or None),
            weex_broker_api_key=(e.get("WEEX_BROKER_API_KEY") or None),
            weex_broker_api_secret=(e.get("WEEX_BROKER_API_SECRET") or None),
            weex_broker_api_passphrase=(e.get("WEEX_BROKER_API_PASSPHRASE") or None),
            stripe_secret_key=(e.get("STRIPE_SECRET_KEY") or None),
            stripe_webhook_secret=(e.get("STRIPE_WEBHOOK_SECRET") or None),
            stripe_price_core_monthly=(e.get("STRIPE_PRICE_CORE_MONTHLY") or None),
            stripe_price_core_annual=(e.get("STRIPE_PRICE_CORE_ANNUAL") or None),
            stripe_price_pro_monthly=(e.get("STRIPE_PRICE_PRO_MONTHLY") or None),
            stripe_price_pro_annual=(e.get("STRIPE_PRICE_PRO_ANNUAL") or None),
            stripe_price_prime_monthly=(e.get("STRIPE_PRICE_PRIME_MONTHLY") or None),
            stripe_price_prime_annual=(e.get("STRIPE_PRICE_PRIME_ANNUAL") or None),
            site_url=str(e.get("WHY_SITE_URL", "https://whytradingbot.com")).rstrip("/"),
        )

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key and self.supabase_db_url)

    def public_view(self) -> dict:
        """The ONLY config the browser ever receives."""
        return dict(supabase_url=self.supabase_url, supabase_anon_key=self.supabase_anon_key,
                    sizes_usd=list(ALLOWED_SIZES_USD), leverage=FIXED_LEVERAGE,
                    leverages=list(ALLOWED_LEVERAGE), position_sizes_pct=list(ALLOWED_POSITION_PCT),
                    max_open_positions=MAX_OPEN_POSITIONS, weex_status=("live" if self.weex_live_configured else "integrating"),
                    billing_configured=False, scalp_live_enabled=True,
                    configured=self.supabase_configured)

    @property
    def billing_configured(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_webhook_secret and all((
            self.stripe_price_core_monthly, self.stripe_price_core_annual,
            self.stripe_price_pro_monthly, self.stripe_price_pro_annual,
            self.stripe_price_prime_monthly, self.stripe_price_prime_annual,
        )))

    @property
    def weex_live_configured(self) -> bool:
        return bool(self.weex_enabled and self.weex_broker_id)
