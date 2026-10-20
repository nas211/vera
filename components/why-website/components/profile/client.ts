// Browser client for the EXISTING WHY × Yellow customer backend (why-yellow-customer.service,
// nginx /yellow/api/). Auth is Supabase Auth with the project's anon key, exactly as the
// main WHY profile does; the access token goes to the backend as a bearer.
//
// Credentials: the Yellow API key + secret are posted ONCE to /me/yellow/connect and never
// kept — not in React state beyond the submit handler, not in storage, not in logs.
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import type { Me } from "./exchanges";

export const API = "/yellow/api";

export type PublicConfig = {
  supabase_url: string;
  supabase_anon_key: string;
  sizes_usd: number[];
  leverage: number;
  leverages: number[];
  position_sizes_pct: number[];
  max_open_positions: number;
  weex_status: "live" | "integrating";
  billing_configured: boolean;
  scalp_live_enabled?: boolean;
  configured: boolean;
};

export type Subscription = {
  plan: "free" | "core" | "pro" | "prime";
  plan_name: string;
  billing: "monthly" | "annual" | null;
  status: "active" | "past_due" | "cancelled" | "expired";
  activated_via: string;
  exchange_limit: number | null;
  paper_starting_balance: number;
  live_allowed: boolean;
  current_period_end: string | null;
  started_at: string;
  updated_at: string;
} | null;

export type PlanInfo = { id: "free" | "core" | "pro" | "prime"; name: string; monthly_usd: number; annual_usd: number; exchange_limit: number | null; paper_starting_balance: number };

export type PaperAccount = {
  paper_enabled: boolean; running: boolean; paper_session_id: string;
  paper_starting_balance: number; paper_cash_balance: number; paper_equity: number; paper_available_balance: number;
  paper_used_margin: number; paper_unrealized_pnl: number; paper_realized_pnl: number; paper_total_fees: number;
  paper_total_funding: number; paper_return_pct: number; paper_max_drawdown: number;
  paper_open_positions: Record<string, unknown>[]; paper_trade_history: Record<string, unknown>[];
  settings: { strategy: string; allocation_pct: number; position_size_pct: number; leverage: number; risk_preset: "conservative" | "balanced" | "aggressive" | "custom" };
};

export type WeexAccount = {
  id: string; state: "DISCONNECTED" | "VERIFIED" | "ACTIVE" | "PAUSED" | "ERROR";
  state_reason: string | null; api_key_masked: string | null;
  balance_equity: number | null; balance_available: number | null; connected_at: string | null;
  status: "live" | "integrating";
  settings: { strategy: string; allocation_pct: number; position_size_pct: number; leverage: number; risk_preset: "conservative" | "balanced" | "aggressive" | "custom" };
  runtime: { open_positions?: number; positions?: Record<string, unknown>[]; loaded?: boolean };
};

export type TradeRow = {
  market: string; direction: string; strategy: string; qty: string; entry_price: string; exit_price: string | null;
  pnl: number | null; fees: number; reason: string | null; opened_ts: number; closed_ts: number;
};
export type DailyResults = {
  date: string; timezone: string; available: boolean;
  start_at: number; end_at: number; closed_trades: number;
  wins: number; losses: number; breakeven: number; pending_pnl: number; funding_pending: number;
  won_usd: number | null; lost_usd: number | null; net_pnl: number | null;
};
export type TradesView = {
  account_id: string;
  mode: "live";
  summary: { closed_trades: number; wins: number; losses: number; realized_pnl: number | null; fees: number; volume: number; open_positions: number; last_trade_at: number | null; pending_pnl?: number };
  today?: DailyResults;
  trades: TradeRow[];
};

/** The shared WHY paper book as published for /trades (why-yellow-webui.service). Read-only. */
export type PaperBook = {
  ts: number; paper: true; disclosure: string; since_iso: string; label: string; notional_usd: number; leverage_x: number;
  summary: { trades: number; net_usd: number };
  trades: { ts: number; kind: string; symbol: string; side: string; pnl: number; notional_usd: number }[];
  open?: unknown[];
};

export class ApiError extends Error {
  status: number;
  code: string;
  body: Record<string, unknown> | null;
  constructor(status: number, code: string, body: Record<string, unknown> | null) {
    super(code);
    this.status = status; this.code = code; this.body = body;
  }
}

export async function api<T>(session: Session | null, method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (session) headers.Authorization = "Bearer " + session.access_token;
  const r = await fetch(API + path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, cache: "no-store" });
  let j: Record<string, unknown> | null = null;
  try { j = await r.json(); } catch { j = null; }
  if (!r.ok) {
    const code = j && typeof j.error === "string" ? j.error + (typeof j.code === "string" ? ":" + j.code : "") : "http_" + r.status;
    throw new ApiError(r.status, code, j);
  }
  return j as T;
}

let _sb: SupabaseClient | null = null;
export function supabaseFor(cfg: PublicConfig): SupabaseClient {
  if (!_sb) _sb = createClient(cfg.supabase_url, cfg.supabase_anon_key, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } });
  return _sb;
}

export const ERRORS: Record<string, string> = {
  scalp_coming_soon: "Scalp Trading — Coming soon. New entries are disabled; existing positions keep being managed.",
  payments_coming_soon: "Paid plans and payment options are Coming soon.",
  trading_style_not_allowed: "Choose Swing or Scalp.",
  trading_style_changed: "Your saved trading style changed. Refresh before starting.",
  trading_style_stop_first: "Pause live trading on all exchanges before switching strategies.",
  trading_style_positions_open: "Wait until all positions and pending orders are cleared and the paused account is checked before switching strategies.",
  scalp_account_loss_policy: "This account has a mandatory loss limit that requires protected execution. Scalp’s existing passive exits do not meet that policy. The limit has not been changed.",
  scalp_yellow_only: "AI Agents — Scalp executes on Yellow. Select Swing to use WEEX.",
  close_confirmation_required: "Confirm the position close before submitting.",
  position_busy: "This position is being checked or managed. Refresh and try again shortly.",
  position_not_found: "This position is no longer available. Refresh your positions.",
  position_reconciliation_required: "An exchange order is still unresolved. Wait for reconciliation before closing again.",
  position_close_incomplete: "The exchange has not confirmed the close. Refresh the position and check its status before trying again.",
  venue_close_unavailable: "Yellow could not complete the close request. Check the position directly on the exchange before retrying.",
  execution_unavailable: "The execution service is not ready. Refresh and try again shortly.",
  exchange_disconnected: "Reconnect the exchange before closing a live position.",
  market_price_unavailable: "A fresh market price is unavailable. Try again when the price feed recovers.",
  invalid_api_key_format: "That does not look like a Yellow API key.",
  invalid_api_secret_format: "That does not look like a Yellow API secret.",
  key_equals_secret: "API key and secret must be different.",
  credential_already_bound: "This Yellow API key is already connected to another WHY account.",
  pause_before_reconnect: "Stop trading before replacing the credential.",
  open_positions_managed: "Wait until WHY has no open positions for this account before replacing the credential.",
  not_verified: "The Yellow connection is not verified yet.",
  activation_not_allowed_from_state: "Trading cannot be started from the current state.",
  size_not_allowed: "Choose one of the listed position sizes.",
  rate_limited: "Too many attempts. Try again in a minute.",
  vault_unavailable: "The credential vault is unavailable. Nothing was stored. Try again later.",
  supabase_not_configured: "Sign-in is not configured on this server yet.",
  plan_not_allowed: "Choose one of the listed plans.",
  live_subscription_required: "Trading is temporarily unavailable for this account. Contact support.",
  billing_not_configured: "Checkout is not configured yet. Please contact support.",
  weex_not_enabled: "WEEX is still integrating and cannot accept credentials yet.",
  weex_credentials_rejected: "WEEX rejected those credentials. Check the key, secret, passphrase and IP allowlist.",
  weex_futures_permission_required: "WEEX could not confirm Futures Trading permission. Enable Read and Futures Trading, then try again.",
  invalid_weex_credential_format: "Check the WEEX key, secret and 8–31 character alphanumeric passphrase.",
  exchange_limit_reached: "Your plan's LIVE exchange limit has been reached.",
  invalid_risk_settings: "Choose supported allocation, position size and leverage values.",
  "unauthorized:token_expired": "Your session expired. Please sign in again.",
};
export function friendly(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  return ERRORS[msg] || ERRORS[msg.split(":")[0]] || `Request failed (${msg}).`;
}

// Partner attribution — the main WHY signup keeps the existing first-touch rules, so a
// /r/<code> link that lands here still tags the signup (first touch wins).
const REF_KEY = "why_ref";
export function captureRef(): void {
  try {
    const q = new URLSearchParams(location.search);
    const ref = (q.get("ref") || "").trim().toLowerCase();
    if (ref && /^[a-z0-9][a-z0-9_-]{0,31}$/.test(ref) && !localStorage.getItem(REF_KEY)) {
      localStorage.setItem(REF_KEY, JSON.stringify({ partner: ref, at: new Date().toISOString(), landing: location.pathname, referrer: (document.referrer || "").slice(0, 200) }));
    }
  } catch { /* storage unavailable: attribution is best-effort */ }
}
export function signupMeta(): Record<string, string> {
  try {
    const r = JSON.parse(localStorage.getItem(REF_KEY) || "null");
    return r && r.partner ? { partner: r.partner, partner_at: r.at, landing: r.landing, referrer: r.referrer } : {};
  } catch { return {}; }
}

export type { Me, Session };
