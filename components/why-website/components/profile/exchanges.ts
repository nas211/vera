// The exchange registry behind the profile page.
//
// One entry per venue WHY currently presents. Yellow is live; WEEX remains
// integrating until the server-side production flag is enabled.
// derived from the EXISTING Yellow customer backend (/yellow/api/me) and every
// action on it goes back through that backend. Every other entry is a roadmap
// card — `coming_soon` — with no credential fields, no backend and no calls.
//
// The ExchangeSlot shape is what a future venue will fill once its own backend
// integration exists: independent credentials, allocation, strategy, risk and
// running state per exchange. Nothing is shared between slots.

export type ExchangeId = "yellow" | "weex";

export type ConnectionState =
  | "not_connected"
  | "connecting"
  | "connected"
  | "error"
  | "coming_soon";

export type RunState = "running" | "stopped";

export type RiskLevel = "conservative" | "balanced" | "aggressive";

export type ExchangeMeta = {
  id: ExchangeId;
  name: string;
  live: boolean;            // true = real integration exists and can be connected
  blurb: string;
};

export const EXCHANGES: readonly ExchangeMeta[] = [
  { id: "yellow",  name: "Yellow Pro", live: true,  blurb: "Perpetuals on Yellow Pro. Trade-only API key, funds stay on Yellow." },
  { id: "weex",    name: "WEEX",       live: false, blurb: "Production integration in progress." },
] as const;

/** Per-exchange state as the profile renders it. For Yellow it is a pure view
 *  over the backend account; for roadmap venues it is the constant coming_soon. */
export type ExchangeSlot = {
  id: ExchangeId;
  name: string;
  live: boolean;
  connection: ConnectionState;
  /** Masked key from the backend (never the key itself), e.g. "ed8c••••••••a9c1". */
  keyMasked?: string | null;
  verification?: "verified" | "pending" | "failed" | "none";
  verificationNote?: string;
  strategy?: string;
  activationBlockedReason?: string | null;
  /** Position size per trade in USD — the lever the backend actually enforces. */
  allocationUsd?: number;
  pendingAllocationUsd?: number | null;
  /** Percent of account equity one position represents, when equity is known. */
  allocationPct?: number | null;
  risk?: RiskLevel;
  run?: RunState;
  runNote?: string;
  openPositions?: number;
  maxOpenPositions?: number;
  tradeMarginPct?: number | null;
  tradeSizeLabel?: string;
  leverage?: number;
  equity?: number | null;
  available?: number | null;
  /** Backend account state verbatim, for the detail drawer. */
  raw?: YellowAccount;
  rawWeex?: { id: string; state: string; state_reason: string | null; settings: { strategy: string; allocation_pct: number; position_size_pct: number; leverage: number; risk_preset: string }; runtime: { open_positions?: number; positions?: Record<string, unknown>[] }; status: "live" | "integrating" };
};

// ---- Yellow backend shapes (customer/accounts.py -> get_me). Read-only mirror; never edited here.
export type YellowAccount = {
  id: string;
  state: "DISCONNECTED" | "PENDING_YELLOW_VERIFICATION" | "VERIFIED" | "ACTIVE" | "PAUSED" | "DRAINING" | "ERROR";
  state_reason: string | null;
  api_key_masked: string | null;
  app_session_id: string | null;
  verification_state: "NONE" | "PENDING" | "VERIFIED" | "FAILED" | "FORBIDDEN_PERMISSION";
  verification_reason: string | null;
  permission_source: string | null;
  verified_at: string | null;
  balance_equity: string | number | null;
  balance_available: string | number | null;
  balance_at: string | null;
  connected_at: string | null;
};
export type ScalpProfile = {
  id: "scalp"; name: string; version: number;
  entry_margin_usd: number | null; leverage: number; entry_notional_usd: number | null;
  sizing_mode?: "exchange_minimum";
  minimum_entries?: { market: string; margin_usd: number; notional_usd: number }[];
  waiting_for_market?: string | null;
  strategy_entry_slots: number; max_open_positions: number; target_pct: number;
  gross_notional_cap_usd: number; per_market_notional_cap_usd: number; per_side_notional_cap_usd: number;
  quote_refresh_seconds: number; warmup_seconds: number; passive_exit_after_seconds: number;
  adverse_exit_pct: number; exit_refresh_seconds: number; levels_per_side: number;
  fee_tier: string; maker_fee_pct: number; taker_fee_pct: number;
  active_markets: string[]; exit_only_markets: string[];
  daily_trade_target: null; frequency_mode: "signal_driven";
  activation_blocked_reason: string | null;
  emergency_stop_usd?: number | null; emergency_stop_mode?: "bot_managed" | null;
};
export type YellowConfig = {
  strategy_profile?: ScalpProfile | { id: "swing"; name: string; leverage: number; max_open_positions: number };
  trading_style?: "swing" | "scalp";
  position_size_usd: number;
  pending_position_size_usd: number | null;
  leverage: number;
  max_open_positions: number;
  trade_margin_pct?: number | null;
  entries_paused: boolean;
  activated_at: string | null;
  sizes_usd: number[];
};
export type YellowRuntime = {
  open_positions: number;
  pending_entries?: number;
  ambiguous_orders: number;
  last_error?: string | null;
  started?: boolean;
  venue: string | null;
  loaded: boolean;
  last_signal?: { at: number; market: string; result: string; reason: string | null } | null;
  size_overrides?: Record<string, number>;
  positions: { position_id?: string; market: string; direction: string; qty: string; entry_price: string; state: string; strategy: string; opened_ts: number }[];
};
export type YellowEvent = { id: number; kind: string; detail: Record<string, unknown>; actor: string; at: string };
export type Me = { account: YellowAccount; config: YellowConfig; runtime: YellowRuntime; events: YellowEvent[] };

// The only strategy the Yellow backend runs today: every ACTIVE account mirrors the
// WHY book's canonical signals (customer/fanout.py). The dropdown is real but has one
// entry; more appear here when the backend gains them.
export const STRATEGIES = [{ id: "why-1", name: "WHY Strategy 1", note: "Pullback + revert setups on the Yellow Pro perp universe" }] as const;

/** Risk level is a preset over the Yellow account's total open-position budget.
 *  Conservative ≤ $100, Balanced ≤ $500, Aggressive above. The label is derived
 *  from the stored size, so there is exactly one source of truth (the backend). */
export function riskOf(sizeUsd: number): RiskLevel {
  if (sizeUsd <= 100) return "conservative";
  if (sizeUsd <= 500) return "balanced";
  return "aggressive";
}
export function riskPreset(level: RiskLevel, sizes: number[]): number {
  const pick = (want: number) => sizes.includes(want) ? want : sizes.reduce((a, b) => (Math.abs(b - want) < Math.abs(a - want) ? b : a), sizes[0]);
  return level === "conservative" ? pick(50) : level === "balanced" ? pick(200) : pick(750);
}
export const RISK_LABEL: Record<RiskLevel, string> = { conservative: "Conservative", balanced: "Balanced", aggressive: "Aggressive" };

/** Build the Yellow slot from the backend's /me answer. Pure. */
export function yellowSlot(me: Me | null): ExchangeSlot {
  const base: ExchangeSlot = { id: "yellow", name: "Yellow Pro", live: true, connection: "not_connected" };
  if (!me) return base;
  const a = me.account, c = me.config, rt = me.runtime;
  const scalp = c.trading_style === "scalp";
  const profile = c.strategy_profile?.id === "scalp" ? c.strategy_profile : undefined;
  const eq = a.balance_equity != null ? Number(a.balance_equity) : null;
  const slot: ExchangeSlot = {
    ...base,
    keyMasked: a.api_key_masked,
    strategy: c.trading_style === "scalp" ? "AI Agents — Scalp" : "AI Agents — Swing",
    activationBlockedReason: c.trading_style === "scalp" ? "Scalp Live — Coming soon. New entries are disabled; existing positions keep being managed." : null,
    allocationUsd: c.position_size_usd,
    pendingAllocationUsd: c.pending_position_size_usd,
    risk: riskOf(c.position_size_usd),
    openPositions: rt?.open_positions ?? 0,
    maxOpenPositions: scalp ? profile?.strategy_entry_slots : c.max_open_positions,
    tradeMarginPct: c.trade_margin_pct ?? null,
    tradeSizeLabel: scalp ? profile?.sizing_mode === "exchange_minimum" ? "Coin minimum" : profile ? `$${profile.entry_margin_usd} margin per entry` : "Scalp defaults unavailable" : c.trade_margin_pct != null ? `${c.trade_margin_pct}% available margin` : "Coin minimum",
    leverage: scalp ? profile?.leverage : c.leverage,
    allocationPct: eq && eq > 0 ? (c.position_size_usd / eq) * 100 : null,
    equity: eq,
    available: a.balance_available != null ? Number(a.balance_available) : null,
    raw: a,
  };
  switch (a.state) {
    case "DISCONNECTED":
      slot.connection = "not_connected";
      slot.verification = "none";
      slot.verificationNote =
        a.state_reason === "invalid_credential" ? "Yellow rejected the last credential. Nothing was kept — connect again with a valid key."
        : a.state_reason === "forbidden_permission" ? "The last key had withdrawal or transfer permission and was rejected. Create a trading-only key."
        : undefined;
      break;
    case "PENDING_YELLOW_VERIFICATION":
      slot.connection = "connected";
      slot.verification = "pending";
      slot.run = "stopped";
      slot.runNote = "Pending Yellow verification";
      slot.verificationNote =
        a.verification_reason === "permission_metadata_unavailable"
          ? "Your credential works, but Yellow does not expose its permission scopes to API keys, so WHY cannot yet confirm withdrawals are disabled. Trading stays locked until verification completes."
          : a.verification_reason && a.verification_reason.startsWith("yellow_unreachable")
            ? "Yellow could not be reached. Your credential is stored securely; re-check verification later."
            : "Verification in progress.";
      break;
    case "VERIFIED":
      slot.connection = "connected"; slot.verification = "verified"; slot.run = "stopped"; slot.runNote = "Ready to start";
      break;
    case "ACTIVE":
      slot.connection = "connected"; slot.verification = "verified"; slot.run = "running";
      break;
    case "PAUSED":
      slot.connection = "connected"; slot.verification = "verified"; slot.run = "stopped"; slot.runNote = "Stopped — open positions still managed";
      break;
    case "DRAINING":
      slot.connection = "connected"; slot.verification = "verified"; slot.run = "running"; slot.runNote = "Size change pending — no new entries until open positions close";
      break;
    case "ERROR":
      slot.connection = "error"; slot.verification = a.verification_state === "VERIFIED" ? "verified" : "failed"; slot.run = "stopped";
      slot.runNote = a.state_reason || "Needs attention";
      break;
  }
  return slot;
}

export function weexSlot(a: { id: string; state: string; state_reason: string | null; api_key_masked: string | null;
  balance_equity: number | null; balance_available: number | null; status: "live" | "integrating";
  settings: { strategy: string; allocation_pct: number; position_size_pct: number; leverage: number; risk_preset: "conservative" | "balanced" | "aggressive" | "custom" };
  runtime: { open_positions?: number; positions?: Record<string, unknown>[] } } | null): ExchangeSlot {
  if (!a || a.status !== "live") return { id: "weex", name: "WEEX", live: false, connection: "coming_soon" };
  return { id: "weex", name: "WEEX", live: true,
    connection: a.state === "DISCONNECTED" ? "not_connected" : a.state === "ERROR" ? "error" : "connected",
    keyMasked: a.api_key_masked, verification: a.state === "DISCONNECTED" ? "none" : "verified",
    strategy: a.settings.strategy, allocationPct: a.settings.allocation_pct, leverage: a.settings.leverage,
    risk: a.settings.risk_preset === "custom" ? undefined : a.settings.risk_preset,
    run: a.state === "ACTIVE" ? "running" : "stopped", runNote: a.state_reason || undefined,
    openPositions: a.runtime?.open_positions ?? 0, equity: a.balance_equity, available: a.balance_available,
    rawWeex: a as ExchangeSlot["rawWeex"] };
}

/** Roadmap venues: constant, disabled, no backend. */
export function comingSoonSlot(m: ExchangeMeta): ExchangeSlot {
  return { id: m.id, name: m.name, live: false, connection: "coming_soon" };
}

/** Match the activation states accepted by both exchange backends. */
export function liveStartBlockReason(slot: ExchangeSlot): string | null {
  if (!slot.live) return "Live trading is not available for this exchange yet.";
  if (slot.connection === "not_connected") return "Connect this exchange to start live trading.";
  if (slot.connection === "error") return "Your exchange connection needs attention. Open settings to reconnect or re-check it.";
  if (slot.connection !== "connected" || slot.verification !== "verified") {
    return slot.verificationNote || "Exchange verification must complete before live trading can start.";
  }
  const state = slot.raw?.state ?? slot.rawWeex?.state;
  if (state !== "VERIFIED" && state !== "PAUSED") return "Live trading cannot be started from the current account state.";
  if (slot.activationBlockedReason) return slot.activationBlockedReason;
  return null;
}
