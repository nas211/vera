"use client";

// WHY profile / dashboard — the SaaS account layer on top of the existing site.
//
// Everything Yellow on this page is a VIEW over the existing Yellow customer backend
// (/yellow/api/me and friends): connect, verify, size, start, stop, disconnect all go to
// the existing secure endpoints. The main WHY account is the only login; there is no
// separate exchange-app login. Nothing is mocked for Yellow.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import {
  api, ApiError, captureRef, friendly, signupMeta, supabaseFor,
  type Me, type PaperAccount, type PlanInfo, type PublicConfig, type Subscription, type TradesView, type WeexAccount,
} from "./client";
import {
  EXCHANGES, STRATEGIES, comingSoonSlot, liveStartBlockReason, weexSlot, yellowSlot,
  type ExchangeId, type ExchangeSlot,
} from "./exchanges";
import type { Row } from "./dashboard/model";
import { MobileDashboard, TRADES_LIMIT } from "./app/MobileDashboard";
import { TodayResults } from "./dashboard/TodayResults";
import { ApiKeyGuide, EmailVerification, ONBOARDING_CSS, QuickTutorial } from "./Onboarding";
import { marginSelection } from "./tradeSize";
import { TradingStyle, type TradingStyleId } from "./TradingStyle";
import { useDiscovery } from "./dashboard/useDiscovery";
import { ScalpSettings } from "./ScalpSettings";

type AuthMode = "signin" | "signup" | "forgot" | "recover";
type Modal = null | { kind: "paper" } | { kind: "onboarding" } | { kind: "guide"; id: "yellow" | "weex" } | { kind: "connect"; id: "yellow" | "weex" } | { kind: "yellow" } | { kind: "weex" } | { kind: "soon"; id: ExchangeId } | { kind: "plans" } | { kind: "confirm"; title: string; text: string; ok: string; onOk: () => void };
type Mode = "paper" | "live";

const mono = 'var(--font-mono, "JetBrains Mono"), monospace';
const display = 'var(--font-display, "Bricolage Grotesque"), sans-serif';
const usd = (v: number | null | undefined, d = 2) => v == null || Number.isNaN(v) ? "—" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const usd0 = (v: number) => "$" + v.toLocaleString("en-US");
const when = (ts: number | string | null | undefined) => {
  if (ts == null) return "—";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
};
const ago = (ts: number | string | null | undefined) => {
  if (ts == null) return "—";
  const ms = Date.now() - (typeof ts === "number" ? ts * 1000 : Date.parse(ts));
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const m = Math.floor(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
};

export function ProfileApp({ initialView, initialDiscovery = false }: { initialView?: "yellow"; initialDiscovery?: boolean }) {
  const [showDiscovery, setShowDiscovery] = useState(initialDiscovery);
  const [discoverySelection, setDiscoverySelection] = useState(0);
  const openDiscovery = () => { setDiscoverySelection(n => n + 1); setShowDiscovery(true); setModal(null); setMode("paper"); };
  const [cfg, setCfg] = useState<PublicConfig | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const discovery = useDiscovery(session, showDiscovery);
  const [booted, setBooted] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [sub, setSub] = useState<Subscription>(null);
  const [plans, setPlans] = useState<PlanInfo[]>([]);
  const [trades, setTrades] = useState<TradesView | null>(null);
  const [paper, setPaper] = useState<PaperAccount | null>(null);
  const [weex, setWeex] = useState<WeexAccount | null>(null);
  const [mode, setMode] = useState<Mode | null>(null);
  const [modal, setModal] = useState<Modal>(initialView === "yellow" ? { kind: "yellow" } : null);
  const [authMode, setAuthMode] = useState<AuthMode>("signin");
  const [authErr, setAuthErr] = useState("");
  const [authInfo, setAuthInfo] = useState("");
  const [authEmail, setAuthEmail] = useState("");
  const [verificationEmail, setVerificationEmail] = useState("");
  const [tutorialTarget, setTutorialTarget] = useState<"paper" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [toast, setToast] = useState("");
  const [annual, setAnnual] = useState(false);
  const sessionRef = useRef<Session | null>(null);
  sessionRef.current = session;

  const say = useCallback((m: string) => { setToast(m); setTimeout(() => setToast(""), 4200); }, []);

  const closingPosition = useRef(false);
  function closePosition(row: Pick<Row, "id" | "market" | "side" | "exchange">) {
    const venue = row.exchange === "Yellow Pro" ? "yellow" : row.exchange === "WEEX" ? "weex" : "paper";
    setModal({ kind: "confirm", title: `Close ${row.market} ${row.side}?`,
      text: venue === "paper" ? "Close this Paper position at the latest available market price with simulated fees and slippage?" : "Submit a reduce-only market close for this position? The final fill price may differ from the displayed price. Other positions remain open.",
      ok: venue === "paper" ? "Close Paper position" : "Close at market",
      onOk: () => {
        if (closingPosition.current) return;
        closingPosition.current = true;
        setModal(null); setBusy(`close:${row.id}`); setErr("");
        api<{ result: string }>(sessionRef.current, "POST", "/me/positions/close", { venue, position_id: row.id, confirm: true })
          .then(async ({ result }) => {
            say(result === "CLOSED" || result === "ALREADY_CLOSED" ? "Position closed." : result === "CLOSE_PENDING" ? "A close is already pending. Waiting for exchange confirmation." : "Market close submitted. Waiting for exchange confirmation.");
            await Promise.all([loadMe(), loadPaper(), loadWeex()]);
          })
          .catch(e => setErr(friendly(e)))
          .finally(() => { closingPosition.current = false; setBusy(null); });
      } });
  }

  // ---------------------------------------------------------------- data
  const loadMe = useCallback(async (s: Session | null = sessionRef.current) => {
    if (!s) return;
    try {
      const m = await api<Me>(s, "GET", "/me");
      setMe(m); setErr("");
      // The plan is a separate state from the Yellow account; if its endpoint is unavailable
      // (e.g. the subscriptions migration not applied yet) the profile still loads.
      try { const sb = await api<{ subscription: Subscription; plans: PlanInfo[] }>(s, "GET", "/me/subscription"); setSub(sb.subscription); setPlans(sb.plans); }
      catch { setSub(null); setPlans([]); }
      if (m.account.state !== "DISCONNECTED") {
        api<TradesView>(s, "GET", `/me/trades?limit=${TRADES_LIMIT}`).then(setTrades).catch(() => setTrades(null));
      } else setTrades(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) { await signOut(); return; }
      setErr(friendly(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadPaper = useCallback(async (s: Session | null = sessionRef.current) => {
    if (!s) return;
    try { setPaper(await api<PaperAccount>(s, "GET", "/me/paper?limit=50")); } catch { setPaper(null); }
  }, []);
  const loadWeex = useCallback(async (s: Session | null = sessionRef.current) => {
    if (!s) return;
    try { setWeex(await api<WeexAccount>(s, "GET", "/me/weex")); } catch { setWeex(null); }
  }, []);

  async function signOut() {
    try { if (cfg) await supabaseFor(cfg).auth.signOut(); } catch { /* ignore */ }
    setSession(null); setMe(null); setSub(null); setTrades(null); setPaper(null); setWeex(null); setModal(null); setMode(null);
  }

  // ---------------------------------------------------------------- boot
  useEffect(() => {
    let alive = true;
    let unsubscribe: (() => void) | undefined;
    captureRef();
    try {
      const q = new URLSearchParams(location.search);
      const plan = (q.get("plan") || "").toLowerCase();
      // Discard saved checkout intent while paid plans are unavailable.
      try { localStorage.removeItem("why_pending_plan"); } catch { /* storage unavailable */ }
      if (["core", "pro", "prime"].includes(plan)) {
        setAuthInfo("Paid plans and payment options are Coming soon. You can create a Free account.");
      }
      if (q.get("signup") === "1" || ["core", "pro", "prime"].includes(plan)) setAuthMode("signup");
      if (q.has("plan") || q.has("billing") || q.has("ref")) history.replaceState(null, "", location.pathname + location.hash);
    } catch { /* ignore */ }
    (async () => {
      let c: PublicConfig;
      try { c = await api<PublicConfig>(null, "GET", "/public-config"); } catch { if (alive) { setErr("The WHY backend is unreachable."); setBooted(true); } return; }
      if (!alive) return;
      setCfg(c);
      if (!c.configured) { setErr("Sign-in is not configured on this server yet."); setBooted(true); return; }
      const sb = supabaseFor(c);
      const { data: listener } = sb.auth.onAuthStateChange((ev, s) => {
        if (!alive) return;
        setSession(s);
        if (ev === "PASSWORD_RECOVERY") setAuthMode("recover");
      });
      unsubscribe = () => listener.subscription.unsubscribe();
      if (/type=recovery/.test(location.hash)) setAuthMode("recover");
      const { data } = await sb.auth.getSession();
      if (!alive) return;
      setSession(data.session);
      if (!data.session) {
        try { setVerificationEmail(sessionStorage.getItem("why_verification_email") || ""); } catch { /* storage unavailable */ }
        const hash = new URLSearchParams(location.hash.slice(1));
        if (hash.get("error")) setAuthErr("This email link has expired or could not be used. Request a new link and open the latest email.");
      }
      setBooted(true);
    })();
    return () => { alive = false; unsubscribe?.(); };
  }, [loadMe, loadPaper, loadWeex]);

  // Also load the dashboard when confirmation in another tab produces a session.
  const userId = session?.user.id;
  useEffect(() => {
    if (!userId || authMode === "recover") return;
    let alive = true;
    setVerificationEmail("");
    try { sessionStorage.removeItem("why_verification_email"); } catch { /* storage unavailable */ }
    Promise.all([loadMe(), loadPaper(), loadWeex()]).then(() => {
      if (!alive) return;
      try {
        if (!initialView && (localStorage.getItem("why_onboarding_pending") === "1" || !localStorage.getItem(`why_tutorial_v1_${userId}`))) setModal({ kind: "onboarding" });
      } catch { /* the tutorial is always available from the dashboard */ }
    });
    return () => { alive = false; };
  }, [userId, authMode, initialView, loadMe, loadPaper, loadWeex]);

  useEffect(() => {
    if (!tutorialTarget || modal) return;
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById("pf-paper-tutorial");
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [tutorialTarget, modal]);

  function leaveTutorial(route?: "paper" | "yellow" | "weex") {
    try {
      localStorage.removeItem("why_onboarding_pending");
      if (userId) localStorage.setItem(`why_tutorial_v1_${userId}`, route ? "complete" : "dismissed");
    } catch { /* optional preference only */ }
    if (route === "yellow" || route === "weex") {
      setMode("live");
      const slot = route === "yellow" ? yellow : weexExchange;
      setModal(slot.connection === "not_connected" ? { kind: "connect", id: route } : { kind: route });
    } else {
      setMode("paper"); setModal(null);
      if (route) setTutorialTarget("paper");
    }
  }

  // Poll while signed in and visible — the backend is the source of truth.
  useEffect(() => {
    if (!session) return;
    const t = setInterval(() => { if (document.visibilityState === "visible") { loadMe(); loadPaper(); loadWeex(); } }, 20000);
    return () => clearInterval(t);
  }, [session, mode, loadMe, loadPaper, loadWeex]);

  // ---------------------------------------------------------------- derived
  const yellow = useMemo(() => yellowSlot(me), [me]);
  const weexExchange = useMemo(() => weexSlot(weex), [weex]);
  const slots: ExchangeSlot[] = useMemo(() => EXCHANGES.map((m) => m.id === "yellow" ? yellow : m.id === "weex" ? weexExchange : comingSoonSlot(m)), [yellow, weexExchange]);
  const connected = yellow.connection === "connected" || yellow.connection === "error";
  // Signed-in users land on Trading; Paper is one tap away in Account.
  const effMode: Mode = mode ?? (showDiscovery ? "paper" : "live");

  // ---------------------------------------------------------------- actions (all through the existing backend)
  async function act(key: string, fn: () => Promise<Me>, okMsg?: string) {
    setBusy(key); setErr("");
    try { const m = await fn(); setMe(m); if (okMsg) say(okMsg); if (sessionRef.current) api<TradesView>(sessionRef.current, "GET", `/me/trades?limit=${TRADES_LIMIT}`).then(setTrades).catch(() => {}); }
    catch (e) { setErr(friendly(e)); }
    finally { setBusy(null); }
  }
  const styleBlockReason = me && (["ACTIVE", "DRAINING"].includes(me.account.state) || weex?.state === "ACTIVE")
    ? "Pause live trading on all exchanges before switching strategies."
    : me && me.account.state !== "DISCONNECTED" && (me.runtime.open_positions > 0 || me.runtime.ambiguous_orders > 0)
      ? "Existing positions keep being managed while paused. Wait until all positions and pending orders are cleared before switching."
      : me && me.account.state !== "DISCONNECTED" && !me.runtime.loaded
        ? "Waiting for the paused account to be checked before switching strategies."
        : undefined;
  const saveTradingStyle = (style: TradingStyleId) => {
    if (["scalp"].includes(style)) { setErr("Scalp Trading — Coming soon."); return; }
    if (busy || styleBlockReason || !me?.config.trading_style || style === me.config.trading_style) return;
    const from = me.config.trading_style === "scalp" ? "Scalp" : "Swing";
    const to = style === "scalp" ? "Scalp" : "Swing";
    setModal({
      kind: "confirm", title: `Switch from ${from} to ${to}?`, ok: `Confirm switch to ${to}`,
      text: `Warning: you are switching your trading strategy from ${from} to ${to}. The strategies use different trading rules and risks. All existing positions and pending orders must be cleared first. Trading will remain paused after switching. Select Resume Trading separately when you are ready.${style === "scalp" ? " Scalp runs on Yellow Pro and remains subject to your account risk limits." : ""}`,
      onOk: () => act("trading-style", () => api<Me>(session, "POST", "/me/trading-style", { trading_style: style }), `Switched to ${to}. Trading has not started.`),
    });
  };
  const styleControl = <TradingStyle value={me?.config.trading_style} busy={busy !== null} paused={me?.account.state === "PAUSED"} blockedReason={styleBlockReason} onChange={saveTradingStyle} onDiscovery={openDiscovery} />;
  const startTrading = () => {
    if (["scalp"].includes(me?.config.trading_style ?? "")) { setErr("Scalp Trading — Coming soon. Existing positions keep being managed."); return; }
    setModal({
    kind: "confirm", title: `Start ${me?.config.trading_style === "scalp" ? "Scalp" : "Swing"} Trading on Yellow Pro?`, ok: "Start Trading",
    text: me?.config.trading_style === "scalp" ? `Scalp will submit real orders on Yellow using its saved defaults.${me.config.strategy_profile?.id === "scalp" ? ` Each entry uses ${me.config.strategy_profile.sizing_mode === "exchange_minimum" ? "the coin’s smallest valid order" : `up to $${me.config.strategy_profile.entry_margin_usd} margin`} at ${me.config.strategy_profile.leverage}× leverage, with up to ${me.config.strategy_profile.max_open_positions} simultaneous entries including pending orders. Available funds and exchange minimums determine actual capacity.` : " Review the current defaults in Yellow settings before starting."} Trade frequency depends on signals and fills; there is no fixed daily trade count. Your account risk limits still apply. Losses are possible.` : `WHY will use the available balance in your Yellow Pro account at ${me?.config.leverage ?? 10}x leverage. ${me?.config.trade_margin_pct != null ? `Each core trade targets ${me.config.trade_margin_pct}% of available margin, less fee and price buffers.` : `Each core trade uses the coin's minimum valid order size.`} At most ${me?.config.max_open_positions ?? 8} positions can be open. Derivatives trading involves substantial risk; losses are possible.`,
    onOk: () => act("start", () => api<Me>(session, "POST", "/me/bot/activate", { trading_style: me?.config.trading_style ?? "swing" }), "Trading started."),
  });
  };
  const stopTrading = () => act("stop", () => api<Me>(session, "POST", "/me/bot/pause"), "Trading paused. Open positions keep being managed. Strategy switching is available below the live controls.");
  async function updateWeex(key: string, path: string, message: string) {
    setBusy(key); setErr("");
    try { setWeex(await api<WeexAccount>(session, "POST", path)); say(message); }
    catch (e) { setErr(friendly(e)); }
    finally { setBusy(null); }
  }
  const startWeex = () => {
    if (me?.config.trading_style === "scalp") { setErr("Scalp runs on Yellow Pro. Choose Swing to trade on WEEX."); return; }
    setModal({
    kind: "confirm", title: "Start Trading on WEEX?", ok: "Start Trading",
    text: `WHY will use real funds in your WEEX account with ${weex?.settings.allocation_pct ?? 0}% allocation, ${weex?.settings.position_size_pct ?? 0}% position size and ${weex?.settings.leverage ?? 10}x leverage. Review your settings before starting. Derivatives trading involves substantial risk; losses are possible.`,
    onOk: () => updateWeex("weex-start", "/me/weex/start", "WEEX trading started."),
  });
  };
  const stopWeex = () => updateWeex("weex-stop", "/me/weex/stop", "WEEX trading stopped.");
  const recheck = () => act("verify", () => api<Me>(session, "POST", "/me/yellow/verify"), "Verification re-checked.");
  const disconnect = () => {
    const n = me?.runtime?.open_positions ?? 0;
    setModal({
      kind: "confirm", title: "Disconnect Yellow Pro?", ok: "Disconnect",
      text: n > 0 ? `You have ${n} open position(s). They stay open in your Yellow account and will NO LONGER be managed by WHY (no target, no stop). The stored credential is deleted.` : "The stored credential is deleted. You can reconnect at any time.",
      onOk: () => act("disconnect", () => api<Me>(session, "POST", "/me/yellow/disconnect", { confirm_unmanaged: n > 0 }), "Yellow Pro disconnected."),
    });
  };
  async function updatePaper(path: string, body?: unknown, message?: string) {
    setBusy("paper"); setErr("");
    try { setPaper(await api<PaperAccount>(session, "POST", path, body)); if (message) say(message); }
    catch (e) { setErr(friendly(e)); }
    finally { setBusy(null); }
  }
  const resetPaper = () => setModal({
    kind: "confirm", title: "Reset Paper trading?", ok: "Reset Paper trading",
    text: `This will close your simulated open positions, archive the current session and restore $${sub?.paper_starting_balance?.toLocaleString("en-US") ?? "25,000"} in simulated funds. Paper trading will stay stopped until you resume it. Your sizing settings are kept. Live accounts are unaffected.`,
    onOk: () => updatePaper("/me/paper/reset", undefined, "Paper account reset. Resume when you are ready."),
  });
  function choosePlan(plan: string) {
    if (plan !== "free") { say("Paid plans and payment options are Coming soon."); return; }
    setModal(null); setMode("paper");
  }

  const exchangeChooser = (<>
    <div className="pf-sec-head"><div className="eyebrow">Choose your exchange</div><span className="pf-dim mono" style={{ fontSize: 11 }}>{sub?.exchange_limit == null ? "Unlimited supported LIVE exchanges" : `${sub?.exchange_limit ?? 1} LIVE exchange${(sub?.exchange_limit ?? 1) === 1 ? "" : "s"} on your plan`}</span></div>
    <div className="pf-grid">{slots.map((s) => <ExchangeCard key={s.id} slot={s} onClick={() => s.live ? (s.connection === "not_connected" ? setModal({ kind: "connect", id: s.id as "yellow" | "weex" }) : setModal({ kind: s.id === "weex" ? "weex" : "yellow" })) : setModal({ kind: "soon", id: s.id })} />)}</div>
  </>);

  // ---------------------------------------------------------------- render
  if (!booted) return <Shell><p className="pf-dim" style={{ fontFamily: mono, fontSize: 13 }}>Loading your profile…</p></Shell>;
  if (!session || authMode === "recover") {
    return (
      <Shell>
        {verificationEmail && authMode !== "recover" ? <EmailVerification email={verificationEmail} busy={busy === "resend"} error={authErr} info={authInfo}
          onResend={async () => {
            if (!cfg) return false;
            setBusy("resend"); setAuthErr(""); setAuthInfo("");
            try {
              const result = await supabaseFor(cfg).auth.resend({ type: "signup", email: verificationEmail, options: { emailRedirectTo: location.origin + "/profile" } });
              if (result.error) throw result.error;
              setAuthInfo("Verification email requested. Check your inbox and open the newest link.");
              return true;
            } catch (e) { setAuthErr(e instanceof Error ? e.message : "Could not resend the email. Try again shortly."); return false; }
            finally { setBusy(null); }
          }}
          onSignIn={() => { setAuthEmail(verificationEmail); setVerificationEmail(""); setAuthMode("signin"); setAuthErr(""); setAuthInfo("Use your verified email and password to continue."); }}
          onChangeEmail={() => { setAuthEmail(""); setVerificationEmail(""); setAuthMode("signup"); setAuthErr(""); setAuthInfo(""); try { sessionStorage.removeItem("why_verification_email"); } catch { /* ignore */ } }} /> :
        <AuthCard cfg={cfg} mode={authMode} initialEmail={authEmail} setMode={(m) => { setAuthMode(m); setAuthErr(""); setAuthInfo(""); }} err={authErr} info={authInfo} busy={busy}
          onSubmit={async (kind, f) => {
            if (!cfg) return;
            const sb = supabaseFor(cfg); setAuthErr(""); setAuthInfo(""); setBusy("auth");
            try {
              if (kind === "signin") {
                const r = await sb.auth.signInWithPassword({ email: f.email, password: f.password });
                if (r.error?.code === "email_not_confirmed") {
                  setVerificationEmail(f.email); setAuthErr("Please confirm your email before signing in. Use the link in your inbox or request a new one below.");
                  try { sessionStorage.setItem("why_verification_email", f.email); } catch { /* optional persistence */ }
                  return;
                }
                if (r.error) throw r.error;
                setSession(r.data.session);
              } else if (kind === "signup") {
                const r = await sb.auth.signUp({ email: f.email, password: f.password, options: { data: signupMeta(), emailRedirectTo: location.origin + "/profile" } }); if (r.error) throw r.error;
                try { localStorage.setItem("why_onboarding_pending", "1"); } catch { /* optional preference */ }
                if (!r.data.session) {
                  setVerificationEmail(f.email); setAuthMode("signin");
                  try { sessionStorage.setItem("why_verification_email", f.email); } catch { /* optional persistence */ }
                  return;
                }
                setSession(r.data.session);
              } else if (kind === "forgot") {
                const r = await sb.auth.resetPasswordForEmail(f.email, { redirectTo: location.origin + "/profile" }); if (r.error) throw r.error;
                setAuthInfo("If an account exists for that email, a reset link is on its way. Check spam too.");
              } else {
                const r = await sb.auth.updateUser({ password: f.password }); if (r.error) throw r.error;
                setAuthMode("signin"); say("Password updated."); await loadMe();
              }
            } catch (e) { setAuthErr(e instanceof Error ? e.message : "Something went wrong."); }
            finally { setBusy(null); }
          }} />}
        {err && <p className="pf-err" style={{ marginTop: 14 }}>{err}</p>}
        <Toast msg={toast} />
      </Shell>
    );
  }

  const email = session.user?.email || "";
  const subStatus = !sub ? "none" : sub.status;

  return (
    <Shell wide>
      {initialView && !showDiscovery ? <>
      {/* ---- header: user · subscription · mode ------------------------------------ */}
      <div className="pf-head">
        <div>
          <div className="eyebrow" style={{ color: "var(--gold)" }}>Your WHY profile</div>
          <h1 className="pf-h1">{email}</h1>
        </div>
        <div style={{ display: "flex", gap: 20 }}><button type="button" className="pf-link" onClick={() => setModal({ kind: "onboarding" })}>Quick tutorial</button><button type="button" className="pf-link" onClick={signOut}>Sign out</button></div>
      </div>

      <div className="pf-tiles">
        <div className="pf-tile">
          <div className="pf-k">User</div>
          <div className="pf-v mono" title={email}>{email}</div>
        </div>
        <div className="pf-tile">
          <div className="pf-k">Subscription plan</div>
          <div className="pf-v">{sub?.plan_name ?? "WHY Free"}
            {sub && sub.plan !== "free" && <span className="pf-dim mono" style={{ fontSize: 11, marginLeft: 8 }}>{sub.billing === "annual" ? `$${plans.find((p) => p.id === sub.plan)?.annual_usd.toLocaleString("en-US")} / year` : `$${plans.find((p) => p.id === sub.plan)?.monthly_usd} / month`}</span>}
          </div>
          <button type="button" className="pf-link" style={{ marginTop: 6 }} onClick={() => setModal({ kind: "plans" })}>{sub?.plan === "free" ? "Upgrade plan →" : "Change plan"}</button>
        </div>
        <div className="pf-tile">
          <div className="pf-k">Subscription status</div>
          <div className="pf-v"><Pill tone={subStatus === "active" ? "green" : subStatus === "past_due" ? "red" : "amber"}>{subStatus === "active" ? "Active" : subStatus === "past_due" ? "Past due" : subStatus === "expired" ? "Expired" : subStatus === "cancelled" ? "Cancelled" : "Active"}</Pill></div>
        </div>
        <div className="pf-tile">
          <div className="pf-k">Trading mode</div>
          <div className="pf-seg" role="group" aria-label="Trading mode">
            <button type="button" aria-pressed={effMode === "paper"} onClick={() => setMode("paper")}>Paper</button>
            <button type="button" aria-pressed={effMode === "live"} onClick={() => setMode("live")}>Trading</button>
          </div>
          <div className="pf-dim mono" style={{ fontSize: 10.5, marginTop: 6, letterSpacing: ".04em" }}>{effMode === "paper" ? "Simulated account equity" : "Real funds on your exchange"}</div>
        </div>
      </div>

      <div className="pf-card pf-get-started">
        <div><div className="eyebrow pf-gold">Need a hand getting started?</div><p className="pf-guide-copy" style={{ marginBottom: 0 }}>Follow the quick tutorial, or open the exchange setup guide to get your API keys.</p></div>
        <div className="pf-actions"><button type="button" className="btn btn-ghost" onClick={() => setModal({ kind: "guide", id: "yellow" })}>How to get API keys</button><button type="button" className="btn" onClick={() => setModal({ kind: "onboarding" })}>Start tutorial →</button></div>
      </div>

      {err && <p className="pf-err">{err}</p>}


      <section style={{ marginTop: 40 }}>
        {effMode === "paper" ? (<>
          <div id="pf-paper-tutorial" tabIndex={-1} className={tutorialTarget === "paper" ? "pf-tutorial-target" : undefined}>
          {tutorialTarget === "paper" && <div className="pf-tutorial-hint"><strong>Your next step:</strong> Review the settings below, save any changes, then press <strong>Start Paper trading</strong>. Watch the status and positions here.<button type="button" className="pf-link" style={{ marginLeft: 12 }} onClick={() => setTutorialTarget(null)}>Got it</button></div>}
          <PaperPanel onDiscovery={openDiscovery} onClosePosition={closePosition} paper={paper} busy={busy}
            onSave={(settings) => updatePaper("/me/paper/settings", settings, "Paper settings saved.")}
            onStart={() => updatePaper("/me/paper/start", undefined, "Paper trading started.")}
            onStop={() => updatePaper("/me/paper/stop", undefined, "Paper trading stopped.")}
            onReset={resetPaper} />
          </div>
          <div style={{ marginTop: 40 }}>{exchangeChooser}</div>
        </>
        ) : (<>
          <div className="pf-live-accounts">
            {slots.filter((s) => s.live).map((s) => <LiveAccountPanel key={s.id} slot={s} busy={busy}
              onStart={s.id === "weex" ? startWeex : startTrading}
              onStop={s.id === "weex" ? stopWeex : stopTrading}
              onManage={() => setModal(s.connection === "not_connected" ? { kind: "connect", id: s.id as "yellow" | "weex" } : { kind: s.id === "weex" ? "weex" : "yellow" })} />)}
          </div>
          {styleControl}
          {exchangeChooser}
          {connected && <div style={{ marginTop: 24 }}><LivePanel trades={trades} me={me} busy={busy} onClosePosition={closePosition} /></div>}
        </>)}
      </section>

      </> : <MobileDashboard discoverySelection={discoverySelection} discoveryActive={showDiscovery} discovery={discovery} onCloseAiPosition={(row) => setModal({ kind: "confirm", title: `Close ${row.market} ${row.side}?`, text: "Close this AI paper position at the current market price?", ok: "Close paper position", onOk: () => { void discovery.act("close", row.id); } })} onCloseDiscovery={() => setShowDiscovery(false)} onDiscovery={openDiscovery} tradingStyleControl={styleControl} onClosePosition={closePosition} userId={session.user.id} email={email} mode={effMode} onMode={setMode}
        me={me} paper={paper} weex={weex} trades={trades} subscription={sub} slots={slots} error={err}
        tutorialTarget={tutorialTarget === "paper"} onDismissHint={() => setTutorialTarget(null)}
        onTutorial={() => setModal({ kind: "onboarding" })} onSignOut={signOut} onPlan={() => setModal({ kind: "plans" })}
        busy={busy}
        onPaperSave={(settings) => updatePaper("/me/paper/settings", settings, "Paper settings saved.")}
        onPaperStart={() => updatePaper("/me/paper/start", undefined, "Paper trading resumed.")}
        onPaperStop={() => updatePaper("/me/paper/stop", undefined, "Paper trading stopped.")}
        onPaperReset={resetPaper}
        onLiveStart={(slot) => slot.id === "weex" ? startWeex() : startTrading()}
        onLiveStop={(slot) => slot.id === "weex" ? stopWeex() : stopTrading()}
        onRefreshPositions={async () => {
          const current = sessionRef.current;
          if (!current) return;
          const results = await Promise.allSettled([
            api<Me>(current, "GET", "/me"), api<PaperAccount>(current, "GET", "/me/paper?limit=50"),
            api<WeexAccount>(current, "GET", "/me/weex"), api<TradesView>(current, "GET", `/me/trades?limit=${TRADES_LIMIT}`),
          ]);
          if (sessionRef.current?.user.id !== current.user.id) return;
          const [live, paperResult, weexResult, tradeResult] = results;
          if (live.status === "fulfilled") setMe(live.value);
          if (paperResult.status === "fulfilled") setPaper(paperResult.value);
          if (weexResult.status === "fulfilled") setWeex(weexResult.value);
          if (tradeResult.status === "fulfilled") setTrades(tradeResult.value);
          if (results.some(r => r.status === "rejected")) throw new Error("Position refresh unavailable");
        }}
        onPaperSettings={() => setModal({ kind: "paper" })}
        onExchange={(s) => s.live ? (s.connection === "not_connected" ? setModal({ kind: "connect", id: s.id as "yellow" | "weex" }) : setModal({ kind: s.id === "weex" ? "weex" : "yellow" })) : setModal({ kind: "soon", id: s.id })} />}
      {modal?.kind === "plans" && (
        <PlanChooser plans={plans} current={sub?.plan ?? "free"} annual={annual} setAnnual={setAnnual} busy={busy} onPick={choosePlan} onClose={() => setModal(null)} />
      )}

      {modal?.kind === "paper" && <Dialog title="Paper account settings" onClose={() => setModal(null)} wide>
        <PaperPanel onDiscovery={openDiscovery} onClosePosition={closePosition} paper={paper} busy={busy}
          onSave={(settings) => updatePaper("/me/paper/settings", settings, "Paper settings saved.")}
          onStart={() => updatePaper("/me/paper/start", undefined, "Paper trading started.")}
          onStop={() => updatePaper("/me/paper/stop", undefined, "Paper trading stopped.")}
          onReset={resetPaper} />
      </Dialog>}

      {/* ---- modals ---------------------------------------------------------------- */}
      {modal?.kind === "guide" && <Dialog title="Get your exchange API keys" onClose={() => setModal(null)} wide>
        {weexExchange.live && <div className="pf-seg" role="group" aria-label="Exchange API guide"><button type="button" aria-pressed={modal.id === "yellow"} onClick={() => setModal({ kind: "guide", id: "yellow" })}>Yellow Pro</button><button type="button" aria-pressed={modal.id === "weex"} onClick={() => setModal({ kind: "guide", id: "weex" })}>WEEX</button></div>}
        <ApiKeyGuide exchange={modal.id} />
        <div className="pf-actions"><button type="button" className="btn" onClick={() => {
          const slot = modal.id === "yellow" ? yellow : weexExchange;
          setModal(slot.connection === "not_connected" ? { kind: "connect", id: modal.id } : { kind: modal.id });
        }}>{(modal.id === "yellow" ? yellow : weexExchange).connection === "not_connected" ? "I have my keys — connect →" : "Open connected exchange →"}</button></div>
      </Dialog>}
      {modal?.kind === "onboarding" && (
        <Dialog title="Welcome to WHY · Quick tutorial" onClose={() => leaveTutorial()} wide>
          <QuickTutorial weexLive={weexExchange.live} paperBalance={paper?.paper_starting_balance ?? sub?.paper_starting_balance ?? 25000} onFinish={leaveTutorial} />
          <button type="button" className="pf-link" style={{ marginTop: 20 }} onClick={() => leaveTutorial()}>Do this later — go to Paper</button>
        </Dialog>
      )}
      {modal?.kind === "connect" && modal.id === "yellow" && (
        <ConnectModal busy={busy} onClose={() => setModal(null)}
          onConnect={async (key, secret) => {
            setBusy("connect"); setErr("");
            try {
              const m = await api<Me>(session, "POST", "/me/yellow/connect", { api_key: key, api_secret: secret });
              setMe(m);
              const slot = yellowSlot(m);
              if (slot.connection !== "connected") return slot.verificationNote || "Yellow Pro did not accept these credentials. Check your key and permissions, then try again.";
              try { localStorage.removeItem("why_onboarding_pending"); } catch { /* optional preference */ }
              setTrades(null); setModal(null); setMode("live");
              api<TradesView>(session, "GET", `/me/trades?limit=${TRADES_LIMIT}`).then(setTrades).catch(() => {});
              say(m.account.state === "VERIFIED" ? "Yellow Pro connected and verified." : "Credential stored securely. Pending Yellow verification.");
              return null;
            } catch (e) { return friendly(e); }
            finally { setBusy(null); }
          }} />
      )}
      {modal?.kind === "connect" && modal.id === "weex" && (
        <WeexConnectModal busy={busy} onClose={() => setModal(null)} onConnect={async (key, secret, passphrase) => {
          setBusy("weex-connect"); setErr("");
          try {
            const a = await api<WeexAccount>(session, "POST", "/me/weex/connect", { api_key: key, api_secret: secret, api_passphrase: passphrase });
            setWeex(a);
            if (a.state !== "VERIFIED") return "WEEX did not accept these credentials. Check your key and permissions, then try again.";
            setModal(null); setMode("live"); say("WEEX connected and verified."); return null;
          }
          catch (e) { return friendly(e); } finally { setBusy(null); }
        }} />
      )}
      {modal?.kind === "yellow" && me && (
        <YellowDrawer tradingStyleControl={<TradingStyle controlId="yellow-settings-trading-style" value={me.config.trading_style} busy={busy !== null} paused={me.account.state === "PAUSED"} blockedReason={styleBlockReason} onChange={saveTradingStyle} onDiscovery={openDiscovery} />} onClosePosition={closePosition} me={me} slot={yellow} trades={trades} busy={busy} onClose={() => { setModal(null); if (initialView === "yellow") history.replaceState(null, "", "/profile"); }}
          onStart={startTrading} onStop={stopTrading} onRecheck={recheck} onDisconnect={disconnect}
          onSaveTradeSize={(pct) => act("trade-size", () => api<Me>(session, "POST", "/me/bot/trade-size", { trade_margin_pct: pct }), "Trade size saved. Applies to new core trades after a short delay.")}
          onSavePositionLimit={(limit) => act("position-limit", () => api<Me>(session, "POST", "/me/bot/position-limit", { max_open_positions: limit }), "Position limit saved. New entries will use it after a short delay.")}
          onCancelSize={() => act("size-cancel", () => api<Me>(session, "POST", "/me/bot/size/cancel"), "Scheduled change cancelled.")}
          onConnect={() => setModal({ kind: "connect", id: "yellow" })} />
      )}
      {modal?.kind === "weex" && weex && <WeexDrawer account={weex} busy={busy} onClose={() => setModal(null)}
        onSave={(settings) => { setBusy("weex-risk"); api<WeexAccount>(session, "POST", "/me/weex/settings", settings).then((a) => { setWeex(a); say("WEEX settings saved."); }).catch((e) => setErr(friendly(e))).finally(() => setBusy(null)); }}
        onStart={startWeex}
        onStop={stopWeex}
        onDisconnect={() => { setBusy("weex-disconnect"); api<WeexAccount>(session, "POST", "/me/weex/disconnect", { confirm_unmanaged: (weex.runtime.open_positions ?? 0) > 0 }).then((a) => { setWeex(a); setModal(null); say("WEEX disconnected."); }).catch((e) => setErr(friendly(e))).finally(() => setBusy(null)); }} />}
      {modal?.kind === "soon" && <SoonModal id={modal.id} onClose={() => setModal(null)} />}
      {modal?.kind === "confirm" && (
        <Dialog title={modal.title} onClose={() => setModal(null)}>
          <p style={{ color: "var(--ash)", fontSize: 14, lineHeight: 1.6 }}>{modal.text}</p>
          <div className="pf-actions">
            <button type="button" className="btn btn-ghost" onClick={() => setModal(null)}>Cancel</button>
            <button type="button" className="btn" onClick={() => { const f = modal.onOk; setModal(null); f(); }}>{modal.ok}</button>
          </div>
        </Dialog>
      )}
      <Toast msg={toast} />
    </Shell>
  );
}

// ================================================================== pieces
function Shell({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <main className="wrap pf" style={{ maxWidth: wide ? "var(--maxw)" : 560, padding: "56px 28px 96px", minHeight: "70vh" }}>
      {children}
      <style>{CSS + ONBOARDING_CSS}</style>
    </main>
  );
}

function Pill({ tone, children }: { tone: "green" | "amber" | "red" | "muted" | "gold"; children: React.ReactNode }) {
  return <span className={`pf-pill pf-pill-${tone}`}>{children}</span>;
}

function Toast({ msg }: { msg: string }) {
  return msg ? <div className="pf-toast" role="status">{msg}</div> : null;
}

function Dialog({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    const keydown = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); closeRef.current(); }
      if (e.key !== "Tab") return;
      const elements = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]') ?? []).filter((el) => el.getClientRects().length > 0);
      const first = elements[0], last = elements[elements.length - 1];
      if (!first) { e.preventDefault(); return; }
      if (e.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", keydown);
    return () => { window.removeEventListener("keydown", keydown); document.body.style.overflow = previousOverflow; previousFocus?.focus({ preventScroll: true }); };
  }, []);
  return (
    <div className="pf-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={dialogRef} tabIndex={-1} className={"pf-dialog" + (wide ? " pf-dialog-wide" : "")} role="dialog" aria-modal="true" aria-label={title}>
        <div className="pf-dialog-head">
          <h2 className="pf-h2">{title}</h2>
          <button type="button" className="pf-x" aria-label="Close" onClick={onClose}>×</button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ---- auth --------------------------------------------------------------------------
function AuthCard({ cfg, mode, initialEmail, setMode, err, info, busy, onSubmit }: {
  cfg: PublicConfig | null; mode: AuthMode; initialEmail: string; setMode: (m: AuthMode) => void; err: string; info: string; busy: string | null;
  onSubmit: (kind: AuthMode, f: { email: string; password: string }) => Promise<void>;
}) {
  const [email, setEmail] = useState(initialEmail); const [pw, setPw] = useState(""); const [pw2, setPw2] = useState(""); const [terms, setTerms] = useState(false);
  const [local, setLocal] = useState("");
  const title = mode === "signup" ? "Create your WHY account" : mode === "forgot" ? "Reset your password" : mode === "recover" ? "Choose a new password" : "Sign in to WHY";
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setLocal("");
    if (mode !== "recover" && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) { setLocal("Enter a valid email address."); return; }
    if (mode !== "forgot" && pw.length < 8) { setLocal("Use a password of at least 8 characters."); return; }
    if ((mode === "signup" || mode === "recover") && pw !== pw2) { setLocal("Passwords don't match."); return; }
    if (mode === "signup" && !terms) { setLocal("Please accept the terms to continue."); return; }
    await onSubmit(mode, { email: email.trim(), password: pw });
    setPw(""); setPw2("");
  };
  return (
    <div className="pf-card" style={{ padding: "30px 28px" }}>
      <div className="eyebrow" style={{ color: "var(--gold)" }}>WHY account</div>
      <h1 className="pf-h1" style={{ marginTop: 10 }}>{title}</h1>
      <p className="pf-dim" style={{ fontSize: 13.5, marginTop: 8, lineHeight: 1.6 }}>
        {mode === "signup" ? "Create a Free account with no payment required. Connect an exchange for free Trading or start in PAPER with $25,000 simulated USD." : mode === "signin" ? "Your profile, plan and connected exchanges." : ""}
      </p>
      {mode === "signup" && <div className="pf-tutorial-hint" style={{ marginTop: 18 }}><strong>Next: check your email.</strong> After signup, open the confirmation link in your inbox to verify your account. Then we’ll guide you through setup.</div>}
      <form onSubmit={submit} style={{ marginTop: 22 }}>
        {mode !== "recover" && (<>
          <label className="eyebrow pf-label" htmlFor="pf-email">Email</label>
          <input id="pf-email" className="pf-input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@domain.com" />
        </>)}
        {mode !== "forgot" && (<>
          <label className="eyebrow pf-label" htmlFor="pf-pw">{mode === "recover" ? "New password" : "Password"}</label>
          <input id="pf-pw" className="pf-input" type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} required minLength={8} value={pw} onChange={(e) => setPw(e.target.value)} placeholder="At least 8 characters" />
        </>)}
        {(mode === "signup" || mode === "recover") && (<>
          <label className="eyebrow pf-label" htmlFor="pf-pw2">Confirm password</label>
          <input id="pf-pw2" className="pf-input" type="password" autoComplete="new-password" required minLength={8} value={pw2} onChange={(e) => setPw2(e.target.value)} placeholder="Repeat it" />
        </>)}
        {mode === "signup" && (
          <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13, color: "var(--ash)", marginTop: 16, lineHeight: 1.5 }}>
            <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} style={{ marginTop: 3 }} />
            <span>I accept the <a href="/terms" className="pf-a" target="_blank" rel="noopener">Terms of Use</a> and <a href="/privacy" className="pf-a" target="_blank" rel="noopener">Privacy Policy</a>.</span>
          </label>
        )}
        {(local || err) && <p className="pf-err">{local || err}</p>}
        {info && <p className="pf-ok">{info}</p>}
        <button type="submit" className="btn" style={{ width: "100%", marginTop: 20, padding: "13px 18px" }} disabled={busy === "auth" || !cfg?.configured}>
          {busy === "auth" ? "Working…" : mode === "signup" ? "Create account" : mode === "forgot" ? "Send reset link" : mode === "recover" ? "Save password" : "Sign in"}
        </button>
      </form>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 18, fontSize: 13, color: "var(--ash)", flexWrap: "wrap" }}>
        {mode === "signin" && (<><span>New to WHY? <button type="button" className="pf-link" onClick={() => setMode("signup")}>Create an account</button></span><button type="button" className="pf-link" onClick={() => setMode("forgot")}>Forgot password?</button></>)}
        {mode === "signup" && <span>Already have an account? <button type="button" className="pf-link" onClick={() => setMode("signin")}>Sign in</button></span>}
        {(mode === "forgot" || mode === "recover") && <button type="button" className="pf-link" onClick={() => setMode("signin")}>← Back to sign in</button>}
      </div>
      <p className="pf-dim mono" style={{ fontSize: 11, marginTop: 22, lineHeight: 1.6 }}>Free accounts include Paper trading and one supported LIVE exchange. No payment details are required.</p>
    </div>
  );
}

// ---- plans ---------------------------------------------------------------------------
function PlanChooser({ plans, current, annual, setAnnual, busy, onPick, onClose }: {
  plans: PlanInfo[]; current: string | null; annual: boolean; setAnnual: (b: boolean) => void; busy: string | null;
  onPick: (plan: string, billing: "monthly" | "annual") => void; onClose?: () => void;
}) {
  return (
    <div className="pf-card" style={{ marginTop: 26, padding: "24px 24px 22px" }}>
      <div className="pf-sec-head" style={{ marginBottom: 16 }}>
        <div>
          <div className="eyebrow" style={{ color: "var(--gold)" }}>{current ? "Change your plan" : "Choose your plan"}</div>
          <p className="pf-dim" style={{ fontSize: 13, marginTop: 6 }}>Free includes Paper and one supported LIVE exchange. Paid plans and payment options are Coming soon.</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div className="pf-seg" role="group" aria-label="Billing">
            <button type="button" disabled aria-pressed={!annual}>Monthly · Coming soon</button>
            <button type="button" disabled aria-pressed={annual}>Annual · Coming soon</button>
          </div>
          {onClose && <button type="button" className="pf-x" aria-label="Close" onClick={onClose}>×</button>}
        </div>
      </div>
      <div className="pf-plans">
        {plans.map((p) => {
          const hot = p.id === "pro";
          const price = annual ? p.annual_usd : p.monthly_usd;
          const isCur = current === p.id;
          return (
            <div key={p.id} className={"pf-plan" + (hot ? " pf-plan-hot" : "")}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="mono" style={{ fontSize: 12.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" }}>{p.name}</span>
                {p.id !== "free" && <span className="pf-tag-hot">Coming soon</span>}
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 5, marginTop: 12 }}>
                <span className={hot ? "molten-text" : undefined} style={{ fontFamily: mono, fontSize: 28, fontWeight: 700, letterSpacing: "-.03em", lineHeight: 1 }}>${price.toLocaleString("en-US")}</span>
                {p.id !== "free" && <span className="pf-dim mono" style={{ fontSize: 11 }}>/{annual ? "year" : "month"}</span>}
              </div>
              <div className="pf-dim mono" style={{ fontSize: 10.5, marginTop: 4 }}>${p.paper_starting_balance.toLocaleString("en-US")} simulated Paper balance · {p.exchange_limit == null ? "Unlimited LIVE exchanges" : `${p.exchange_limit} LIVE exchange${p.exchange_limit === 1 ? "" : "s"}`}</div>
              <button type="button" className={"btn" + (hot ? "" : " btn-ghost")} style={{ width: "100%", marginTop: 16, fontSize: 12.5, padding: "11px 12px" }} disabled={p.id !== "free" || busy === "plan:" + p.id || isCur} onClick={() => onPick(p.id, annual ? "annual" : "monthly")}>
                {p.id !== "free" ? "Coming soon" : isCur ? "Current plan" : "Start free"}
              </button>
            </div>
          );
        })}
      </div>
      <p className="pf-dim mono" style={{ fontSize: 10.5, marginTop: 14, lineHeight: 1.6 }}>Paid plans and all payment options are Coming soon. Free signup requires no payment. <a href="/#pricing" className="pf-a">Full plan comparison</a>.</p>
    </div>
  );
}

// ---- exchange cards ---------------------------------------------------------------
function ExchangeCard({ slot: s, onClick }: { slot: ExchangeSlot; onClick: () => void }) {
  const soon = s.connection === "coming_soon";
  const integrating = s.id === "weex";
  const conn = s.connection === "connected";
  return (
    <button type="button" className={"pf-card pf-xcard" + (soon ? " pf-xcard-soon" : "")} onClick={onClick} aria-disabled={soon}>
      <div className="pf-xcard-head">
        <span className="mono" style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase" }}>{s.name}</span>
        {soon ? <Pill tone={integrating ? "amber" : "muted"}>{integrating ? "Integrating" : "Coming soon"}</Pill> : <Pill tone="gold">Live · Free</Pill>}
      </div>
      {soon ? (
        <p className="pf-dim" style={{ fontSize: 13, marginTop: 10, lineHeight: 1.55 }}>{integrating ? "Production integration is in progress. Credentials are not collected until it is enabled." : "Integration on the roadmap. No connection yet."}</p>
      ) : !conn && s.connection !== "error" ? (<>
        <p className="pf-dim" style={{ fontSize: 13, marginTop: 10, lineHeight: 1.55 }}>Not connected. Trade-only API key, funds stay on {s.name}.</p>
        {s.verificationNote && <p className="pf-err" style={{ marginTop: 8 }}>{s.verificationNote}</p>}
        <span className="btn" style={{ marginTop: 16, fontSize: 12.5, padding: "10px 16px", alignSelf: "flex-start" }}>Connect Exchange →</span>
      </>) : (<>
        <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: s.connection === "error" ? "var(--red)" : "var(--green)", fontFamily: mono, fontSize: 13, fontWeight: 600 }}>{s.connection === "error" ? "Needs attention" : "Connected ✓"}</span>
          {s.verification === "pending" && <Pill tone="amber">Verification pending</Pill>}
        </div>
        <div className="pf-dim mono" style={{ fontSize: 11, marginTop: 4 }}>API · Connected securely{s.keyMasked ? ` · ${s.keyMasked}` : ""}</div>
        <dl className="pf-kv">
          <dt>Account balance</dt><dd className="mono">{usd(s.equity)}</dd>
          <dt>Available</dt><dd className="mono">{usd(s.available)}</dd>
          <dt>Strategy</dt><dd>{s.strategy}</dd>
          <dt>{s.rawWeex ? "Allocation" : "Trade size"}</dt><dd>{s.rawWeex ? <>{s.rawWeex.settings.allocation_pct}% total<span className="pf-dim"> · {s.rawWeex.settings.position_size_pct}% / position</span></> : s.tradeSizeLabel ?? "—"}</dd>
          {s.rawWeex && <><dt>Risk</dt><dd>{s.risk ? s.risk.charAt(0).toUpperCase() + s.risk.slice(1) : "—"}</dd></>}
          <dt>Status</dt><dd>{s.run === "running" ? <Pill tone="green">Active</Pill> : s.verification === "pending" ? <Pill tone="amber">Pending</Pill> : <Pill tone="muted">Stopped</Pill>}</dd>
        </dl>
        <span className="btn btn-ghost" style={{ marginTop: 14, fontSize: 12.5, padding: "9px 16px", alignSelf: "flex-start" }}>Manage →</span>
      </>)}
    </button>
  );
}

function SoonModal({ id, onClose }: { id: ExchangeId; onClose: () => void }) {
  const name = EXCHANGES.find((e) => e.id === id)?.name ?? id;
  const integrating = id === "weex";
  return (
    <Dialog title={name} onClose={onClose}>
      <Pill tone={integrating ? "amber" : "muted"}>{integrating ? "Integrating" : "Coming soon"}</Pill>
      <p style={{ color: "var(--ash)", fontSize: 14.5, lineHeight: 1.6, marginTop: 14 }}>{name} {integrating ? "production integration is in progress" : "integration is coming soon"}. It will appear here as a connectable exchange once the integration is live — no API keys are collected until then.</p>
      <div className="pf-actions"><button type="button" className="btn btn-ghost" onClick={onClose}>Close</button></div>
    </Dialog>
  );
}

// ---- connect Yellow Pro: posts ONCE to the existing backend, keeps nothing ------------
function ConnectModal({ busy, onClose, onConnect }: { busy: string | null; onClose: () => void; onConnect: (key: string, secret: string) => Promise<string | null> }) {
  const keyRef = useRef<HTMLInputElement>(null);
  const secRef = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState("");
  const [show, setShow] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    const key = keyRef.current?.value.trim() ?? "", secret = secRef.current?.value.trim() ?? "";
    if (key.length < 16 || secret.length < 16) { setErr("Paste both the API key and the API secret."); return; }
    const problem = await onConnect(key, secret);
    if (secRef.current) secRef.current.value = "";        // never keep the secret around
    if (keyRef.current && !problem) keyRef.current.value = "";
    setShow(false);
    if (problem) setErr(problem);
  };
  return (
    <Dialog title="Connect Yellow Pro" onClose={onClose}>
      <ApiKeyGuide exchange="yellow" compact />
      <p className="pf-guide-copy">Already have your keys? Paste them below. Connecting does not start trading; you’ll review your settings next.</p>
      <form onSubmit={submit} autoComplete="off" style={{ marginTop: 18 }}>
        <label className="eyebrow pf-label" htmlFor="pf-key">API Key</label>
        <input id="pf-key" ref={keyRef} className="pf-input mono" type="text" autoComplete="off" spellCheck={false} placeholder="Paste your Yellow API key" required />
        <label className="eyebrow pf-label" htmlFor="pf-secret">API Secret</label>
        <div style={{ position: "relative" }}>
          <input id="pf-secret" ref={secRef} className="pf-input mono" type={show ? "text" : "password"} autoComplete="new-password" spellCheck={false} placeholder="Paste your Yellow API secret" required style={{ paddingRight: 64 }} />
          <button type="button" className="pf-link" style={{ position: "absolute", right: 12, top: 13, fontSize: 11 }} onClick={() => setShow((v) => !v)}>{show ? "hide" : "show"}</button>
        </div>
        {err && <p className="pf-err">{err}</p>}
        <div className="pf-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy === "connect"}>Cancel</button>
          <button type="submit" className="btn" disabled={busy === "connect"}>{busy === "connect" ? "Verifying with Yellow…" : "Connect"}</button>
        </div>
      </form>
      <p className="pf-dim mono" style={{ fontSize: 10.5, marginTop: 14, lineHeight: 1.6 }}>The secret is sent once over HTTPS to WHY&apos;s backend, stored in an encrypted vault and never shown again. Only a masked key is displayed.</p>
    </Dialog>
  );
}

function WeexConnectModal({ busy, onClose, onConnect }: { busy: string | null; onClose: () => void; onConnect: (key: string, secret: string, passphrase: string) => Promise<string | null> }) {
  const keyRef = useRef<HTMLInputElement>(null), secretRef = useRef<HTMLInputElement>(null), passRef = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    const values = [keyRef.current?.value.trim() ?? "", secretRef.current?.value.trim() ?? "", passRef.current?.value.trim() ?? ""];
    if (values.some((v) => v.length < 8)) { setErr("Enter the API key, API secret and API passphrase."); return; }
    const problem = await onConnect(values[0], values[1], values[2]);
    if (secretRef.current) secretRef.current.value = "";
    if (passRef.current) passRef.current.value = "";
    if (!problem && keyRef.current) keyRef.current.value = "";
    if (problem) setErr(problem);
  };
  return <Dialog title="Connect WEEX" onClose={onClose}>
    <ApiKeyGuide exchange="weex" compact />
    <p className="pf-guide-copy">Paste all three credentials below. Connecting does not start trading; you’ll review your settings next.</p>
    <form onSubmit={submit} autoComplete="off">
      <label className="eyebrow pf-label" htmlFor="pf-weex-key">API Key</label><input id="pf-weex-key" ref={keyRef} className="pf-input mono" autoComplete="off" spellCheck={false} placeholder="Paste your WEEX API key" required />
      <label className="eyebrow pf-label" htmlFor="pf-weex-secret">API Secret</label><input id="pf-weex-secret" ref={secretRef} className="pf-input mono" type="password" autoComplete="new-password" placeholder="Paste your WEEX API secret" required />
      <label className="eyebrow pf-label" htmlFor="pf-weex-passphrase">API Passphrase</label><input id="pf-weex-passphrase" ref={passRef} className="pf-input mono" type="password" autoComplete="new-password" placeholder="The passphrase you chose on WEEX" required />
      {err && <p className="pf-err">{err}</p>}
      <div className="pf-actions"><button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button><button type="submit" className="btn" disabled={busy === "weex-connect"}>{busy === "weex-connect" ? "Validating…" : "Connect"}</button></div>
    </form>
    <p className="pf-dim mono" style={{ fontSize: 10.5, lineHeight: 1.6, marginTop: 14 }}>Secrets are sent directly to the server-side vault and are never returned to this browser.</p>
  </Dialog>;
}

type RiskSettings = { strategy: string; allocation_pct: number; position_size_pct: number; leverage: number; risk_preset: string };
const PRESETS: Record<string, Pick<RiskSettings, "position_size_pct" | "leverage">> = {
  conservative: { position_size_pct: 2, leverage: 5 }, balanced: { position_size_pct: 5, leverage: 10 }, aggressive: { position_size_pct: 10, leverage: 15 },
};

function RiskControls({ value, busy, onSave, paperOnly = false, onDiscovery }: { onDiscovery?: () => void; value: RiskSettings; busy: boolean; onSave: (v: RiskSettings) => void; paperOnly?: boolean }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value.strategy, value.allocation_pct, value.position_size_pct, value.leverage, value.risk_preset]);
  const setPreset = (p: string) => setV((old) => ({ ...old, ...(PRESETS[p] || {}), risk_preset: p }));
  const setCustom = (patch: Partial<RiskSettings>) => setV((old) => ({ ...old, ...patch, risk_preset: "custom" }));
  const dirty = JSON.stringify(v) !== JSON.stringify(value);
  return <div className="pf-field">
    <label className="eyebrow pf-label">Strategy</label>
    <select className="pf-input" aria-label="Trading strategy" value={v.strategy} onChange={(e) => { if (paperOnly && e.target.value === "why-discovery-paper") { onDiscovery?.(); return; } setV({ ...v, strategy: e.target.value }); }}>{STRATEGIES.map((s) => <option key={s.id} value={s.id}>{paperOnly && s.id === "why-1" ? "Swing" : s.name}</option>)}{paperOnly && <><option value="scalp" disabled>Scalp — Coming soon</option><option value="why-discovery-paper">AI Agents Trading · Paper</option></>}</select>
    {paperOnly && <p className="pf-dim" style={{ fontSize: 11, marginTop: 8 }}><button type="button" className="pf-link" onClick={onDiscovery}>Open AI Agents Trading</button> · separate paper balance and results.</p>}
    <label className="eyebrow pf-label">Total allocation</label>
    <div className="pf-seg" role="group" aria-label="Total allocation">{[25, 50, 75, 100].map((n) => <button key={n} type="button" aria-pressed={v.allocation_pct === n} onClick={() => setCustom({ allocation_pct: n })}>{n}%</button>)}</div>
    <label className="eyebrow pf-label">Position size</label>
    <div className="pf-seg pf-options" role="group" aria-label="Position size">{[1, 2, 5, 10, 15, 20].map((n) => <button key={n} type="button" aria-pressed={v.position_size_pct === n} onClick={() => setCustom({ position_size_pct: n })}>{n}%</button>)}</div>
    <label className="eyebrow pf-label">Leverage</label>
    <div className="pf-seg pf-options" role="group" aria-label="Leverage">{[2, 5, 10, 15, 20].map((n) => <button key={n} type="button" aria-pressed={v.leverage === n} onClick={() => setCustom({ leverage: n })}>{n}x</button>)}</div>
    <label className="eyebrow pf-label">Risk preset</label>
    <div className="pf-seg pf-options" role="group" aria-label="Risk preset">{["conservative", "balanced", "aggressive", "custom"].map((p) => <button key={p} type="button" aria-pressed={v.risk_preset === p} onClick={() => setPreset(p)}>{p}</button>)}</div>
    <p className="pf-dim mono" style={{ fontSize: 10.5, lineHeight: 1.55, marginTop: 9 }}>Your preference is an input to WHY&apos;s risk governor. The lower safe exchange, instrument, strategy or portfolio limit always wins.</p>
    {dirty && <button type="button" className="btn" style={{ marginTop: 10 }} disabled={busy} onClick={() => onSave(v)}>{busy ? "Saving…" : "Save settings"}</button>}
  </div>;
}

function WeexDrawer({ account, busy, onClose, onSave, onStart, onStop, onDisconnect }: { account: WeexAccount; busy: string | null; onClose: () => void; onSave: (v: RiskSettings) => void; onStart: () => void; onStop: () => void; onDisconnect: () => void }) {
  const connected = account.state !== "DISCONNECTED";
  const startBlocked = liveStartBlockReason(weexSlot(account));
  return <Dialog title="WEEX" onClose={onClose} wide>
    <div className="pf-kv2">
      <div><div className="pf-k">Exchange</div><div className="pf-v">WEEX</div></div>
      <div><div className="pf-k">Status</div><div className="pf-v">{connected ? <span className="pf-pos">Connected ✓</span> : "Not connected"}</div></div>
      <div><div className="pf-k">API</div><div className="pf-v mono">{account.api_key_masked || "—"}</div></div>
      <div><div className="pf-k">Equity</div><div className="pf-v mono">{usd(account.balance_equity)} <span className="pf-dim">· {usd(account.balance_available)} available</span></div></div>
    </div>
    {connected && <>
      <RiskControls value={account.settings} busy={busy === "weex-risk"} onSave={onSave} />
      <div className="pf-status-row"><div><div className="pf-k">Status</div><div style={{ marginTop: 7 }}>{account.state === "ACTIVE" ? <Pill tone="green">Running</Pill> : <Pill tone="muted">Stopped</Pill>}</div><div className="pf-dim mono" style={{ fontSize: 10.5, marginTop: 6 }}>{account.runtime.open_positions ?? 0} open WHY positions</div></div>{account.state === "ACTIVE" ? <button type="button" className="btn btn-ghost" onClick={onStop} disabled={busy !== null}>{busy === "weex-stop" ? "Stopping…" : "Stop Trading"}</button> : <button type="button" className="btn" onClick={onStart} disabled={!!startBlocked || busy !== null} title={startBlocked ?? ""}>{busy === "weex-start" ? "Starting…" : "Start Trading"}</button>}</div>
      {account.state !== "ACTIVE" && startBlocked && <p className="pf-note pf-note-amber">{startBlocked}</p>}
      <div className="pf-field" style={{ textAlign: "right" }}><button className="pf-link pf-danger" onClick={onDisconnect} disabled={busy === "weex-disconnect"}>Disconnect WEEX</button></div>
    </>}
  </Dialog>;
}

// ---- Yellow settings drawer: extends the existing backend state -----------------------
function YellowDrawer({ tradingStyleControl, me, slot: s, trades, busy, onClose, onStart, onStop, onRecheck, onDisconnect, onCancelSize, onConnect, onClosePosition, onSavePositionLimit, onSaveTradeSize }: {
  tradingStyleControl: React.ReactNode;
  onClosePosition: (row: Pick<Row, "id" | "market" | "side" | "exchange">) => void;
  onSavePositionLimit: (limit: number) => Promise<void>;
  onSaveTradeSize: (pct: number | null) => Promise<void>;
  me: Me; slot: ExchangeSlot; trades: TradesView | null; busy: string | null; onClose: () => void;
  onStart: () => void; onStop: () => void; onRecheck: () => void; onDisconnect: () => void; onCancelSize: () => void; onConnect: () => void;
}) {
  const isScalp = me.config.trading_style === "scalp";
  const scalpProfile = isScalp && me.config.strategy_profile?.id === "scalp" ? me.config.strategy_profile : undefined;
  const [positionLimit, setPositionLimit] = useState(me.config.max_open_positions);
  useEffect(() => setPositionLimit(me.config.max_open_positions), [me.config.max_open_positions]);
  const savedTradePct = me.config.trade_margin_pct ?? null;
  const [tradeMode, setTradeMode] = useState(savedTradePct == null ? "minimum" : "percentage");
  const [tradePct, setTradePct] = useState(String(savedTradePct ?? 1));
  const [tradeAmount, setTradeAmount] = useState<string | null>(null);
  useEffect(() => { setTradeMode(savedTradePct == null ? "minimum" : "percentage"); setTradePct(String(savedTradePct ?? 1)); setTradeAmount(null); }, [savedTradePct]);
  const selection = marginSelection(tradePct, tradeAmount, s.available);
  const selectedPct = tradeMode === "minimum" ? null : selection.percentage;
  const tradeSizeError = tradeMode === "minimum" ? null : selection.error;
  const validTradeSize = !tradeSizeError;
  const tradeBudget = tradeMode === "percentage" ? selection.budget : null;
  const choosePercentage = (value: string) => { setTradePct(value); setTradeAmount(null); };
  const tradeSizeChanged = validTradeSize && selectedPct !== savedTradePct;
  const pending = me.config.pending_position_size_usd;
  const verified = s.verification === "verified";
  const startBlocked = liveStartBlockReason(s);
  const notConnected = s.connection === "not_connected";
  const lastEvent = me.events?.[0];
  const lastActivity = trades?.summary.last_trade_at ?? (lastEvent ? lastEvent.at : null);

  return (
    <Dialog title="Yellow Pro" onClose={onClose} wide>
      <div className="pf-drawer-grid">
        <div className="pf-kv2">
          <div><div className="pf-k">Exchange</div><div className="pf-v">Yellow Pro</div></div>
          <div><div className="pf-k">Status</div><div className="pf-v">{notConnected ? <Pill tone="muted">Not connected</Pill> : s.connection === "error" ? <span style={{ color: "var(--red)" }}>Needs attention</span> : <span style={{ color: "var(--green)" }}>Connected ✓</span>}</div></div>
          <div><div className="pf-k">API</div><div className="pf-v mono" style={{ fontSize: 12.5 }}>{notConnected ? "—" : <>Connected securely{s.keyMasked ? <span className="pf-dim"> · {s.keyMasked}</span> : null}</>}</div></div>
          <div><div className="pf-k">Verification</div><div className="pf-v">{verified ? <Pill tone="green">Verified</Pill> : s.verification === "pending" ? <Pill tone="amber">Pending</Pill> : s.verification === "failed" ? <Pill tone="red">Failed</Pill> : <Pill tone="muted">—</Pill>}</div></div>
          {s.equity != null && <div><div className="pf-k">Equity</div><div className="pf-v mono">{usd(s.equity)}<span className="pf-dim" style={{ fontSize: 11 }}> · {usd(s.available)} available</span></div></div>}
        </div>

        {notConnected ? (
          <div className="pf-note"><p>Yellow Pro is not connected. {s.verificationNote}</p><button type="button" className="btn" style={{ marginTop: 12 }} onClick={onConnect}>Connect Yellow Pro →</button></div>
        ) : (<>
          <div className="pf-tutorial-hint"><strong>Next:</strong> {verified ? "Live trades use your available Yellow balance. Start trading when you are ready to use real funds." : "Your credentials are saved. Wait for verification before starting."}</div>
          {s.verification === "pending" && <div className="pf-note pf-note-amber"><p>{s.verificationNote}</p><button type="button" className="btn btn-ghost" style={{ marginTop: 12, fontSize: 12.5 }} disabled={busy === "verify"} onClick={onRecheck}>{busy === "verify" ? "Checking…" : "Re-check verification"}</button></div>}
          {s.connection === "error" && <div className="pf-note pf-note-red"><p>{s.runNote}</p><button type="button" className="btn btn-ghost" style={{ marginTop: 12, fontSize: 12.5 }} disabled={busy === "verify"} onClick={onRecheck}>Re-check</button></div>}

          <div className="pf-field">
            {tradingStyleControl}
          </div>
          <div className="pf-field">
            <div className="pf-alloc-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}><span className="eyebrow pf-label" style={{ margin: 0 }}>Available Yellow balance</span><span className="mono" style={{ fontSize: 15, fontWeight: 700 }}>{usd(s.available)}</span></div>
            <div className="pf-dim mono" style={{ fontSize: 10.5 }}>{isScalp ? scalpProfile ? `${scalpProfile.leverage}× leverage · up to ${scalpProfile.max_open_positions} simultaneous entries` : "Loading Scalp defaults" : `${me.config.leverage}× leverage · up to ${me.config.max_open_positions} open positions`}</div>
            {!isScalp && pending != null && <p className="pf-ok" style={{ marginTop: 8 }}>Scheduled: {usd0(pending)} applies once open positions have closed. <button type="button" className="pf-link" onClick={onCancelSize}>Cancel</button></p>}
            <p className="pf-dim" style={{ marginTop: 8 }}>{isScalp ? "Scalp uses its fixed strategy margin below. Your Swing percentage does not apply to Scalp." : savedTradePct == null ? "Each core trade uses its coin’s minimum valid order size." : `Each core trade targets ${savedTradePct}% of available margin, less fee and price buffers.`} Entries also respect your saved position limit.</p>
          </div>
          {isScalp && <ScalpSettings profile={scalpProfile} />}

          {me.config.trading_style !== "scalp" && <div className="pf-field">
            <label className="eyebrow pf-label" htmlFor="pf-trade-size-mode">Trade size</label>
            <select id="pf-trade-size-mode" className="pf-input" value={tradeMode} disabled={busy !== null} onChange={(e) => { setTradeMode(e.target.value); setTradeAmount(null); }}>
              <option value="minimum">Coin minimum</option>
              <option value="percentage">My available funds per trade</option>
            </select>
            {tradeMode === "percentage" && <div style={{ marginTop: 12 }}>
              <label htmlFor="pf-trade-size-usd" className="pf-label">My funds per trade ($)</label>
              <input id="pf-trade-size-usd" className="pf-input" style={{ maxWidth: 180 }} type="number" min="0" step="0.01" inputMode="decimal"
                value={tradeAmount ?? (tradeBudget == null ? "" : (Math.floor(tradeBudget * 100) / 100).toFixed(2))}
                disabled={busy !== null} aria-invalid={!!tradeSizeError} aria-describedby="pf-trade-budget-help pf-trade-size-error"
                onChange={(e) => setTradeAmount(e.target.value)} />
              <p id="pf-trade-budget-help" className="pf-dim" style={{ marginTop: 8 }}>Available to use: {usd(s.available)} of your own funds. This budget includes fee and price reserves.</p>
              <label htmlFor="pf-trade-size-slider" className="pf-label">Share of available funds</label>
              <input id="pf-trade-size-slider" className="pf-range" type="range" min="1" max="100" step="1"
                value={selectedPct ?? Math.max(1, Math.min(100, Number(tradePct) || 1))}
                disabled={busy !== null || s.available == null || !Number.isFinite(s.available) || s.available <= 0}
                aria-valuetext={tradeBudget == null ? undefined : `${selectedPct}% — ${usd(tradeBudget)} of your own funds`}
                onChange={(e) => choosePercentage(e.target.value)} />
              <label htmlFor="pf-trade-size-pct" className="pf-label">Available margin per trade (%)</label>
              <input id="pf-trade-size-pct" className="pf-input" style={{ maxWidth: 150 }} type="number" min="1" max="100" step="1" inputMode="numeric"
                value={tradeAmount == null ? tradePct : selectedPct ?? ""} disabled={busy !== null} aria-invalid={!!tradeSizeError}
                aria-describedby="pf-trade-size-error" onChange={(e) => choosePercentage(e.target.value)} />
              <div role="group" aria-label="Trade size percentages" style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
                {[10, 25, 50, 75, 100].map((pct) => <button type="button" key={pct} className="btn btn-ghost" disabled={busy !== null} aria-pressed={selectedPct === pct} onClick={() => choosePercentage(String(pct))}>{pct}%</button>)}
              </div>
              <div id="pf-trade-size-error" aria-live="polite">{tradeSizeError && <p role="alert" className="pf-note pf-note-red">{tradeSizeError}</p>}</div>
              {tradeBudget != null && <p className="pf-dim" style={{ marginTop: 10 }}>Selected budget: {usd(tradeBudget)} of your own funds ({selectedPct}%). Dollar entries round down to whole percentages.</p>}
              {tradeBudget != null && <p className="pf-dim">Leveraged position value: up to {usd(tradeBudget * me.config.leverage)} at {me.config.leverage}×, before fee reserves and rounding. This is exposure, not money available to spend.</p>}
              <p className="pf-dim" style={{ marginTop: 8 }}>100% means your available funds, not $100. The saved percentage uses the available balance when each trade opens, with reserves for fees and price changes.</p>
            </div>}
            <button type="button" className="btn" style={{ marginTop: 12 }} disabled={busy !== null || !tradeSizeChanged} onClick={() => { if (tradeSizeChanged) void onSaveTradeSize(selectedPct); }}>{busy === "trade-size" ? "Saving…" : "Save trade size"}</button>
            <p className="pf-dim" style={{ marginTop: 8 }}>Saved trade size: {savedTradePct == null ? "Coin minimum" : `${savedTradePct}% of available margin`}. Applies to new WY-PULLBACK and WY-REVERT trades. Existing positions and leverage stay unchanged. AI strategies retain their separate sizing limits.</p>
          </div>}

          {!isScalp && <div className="pf-field">
            <label className="eyebrow pf-label" htmlFor="pf-position-limit">Maximum open positions</label>
            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <select id="pf-position-limit" className="pf-input" style={{ width: 150 }} value={positionLimit}
                disabled={busy !== null} onChange={(event) => setPositionLimit(Number(event.target.value))}>
                {Array.from({ length: 8 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n} position{n === 1 ? "" : "s"}</option>)}
              </select>
              <button type="button" className="btn" disabled={busy !== null || positionLimit === me.config.max_open_positions}
                onClick={() => void onSavePositionLimit(positionLimit)}>{busy === "position-limit" ? "Saving…" : "Save position limit"}</button>
            </div>
            <p className="pf-dim" style={{ marginTop: 8 }}>Saved Swing limit: {me.config.max_open_positions}. Changes apply to new Swing entries after a short delay. Existing positions stay open. This limit does not change order size, leverage, or start trading.</p>
          </div>}

          {/* status + start/stop */}
          <div className="pf-field pf-status-row">
            <div>
              <div className="eyebrow pf-label" style={{ margin: 0 }}>Status</div>
              <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 10 }}>
                {s.run === "running" ? <Pill tone="green">Running</Pill> : <Pill tone="muted">Stopped</Pill>}
                {s.runNote && <span className="pf-dim" style={{ fontSize: 12.5 }}>{s.runNote}</span>}
              </div>
              <div className="pf-dim mono" style={{ fontSize: 10.5, marginTop: 6 }}>{isScalp ? `${me.runtime?.open_positions ?? 0} open lots · ${me.runtime?.pending_entries ?? 0} pending entries · ${scalpProfile?.strategy_entry_slots ?? "—"} entry slots` : `${me.runtime?.open_positions ?? 0} / ${me.config.max_open_positions} open positions`}</div>
            </div>
            {s.run === "running"
              ? <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={onStop}>{busy === "stop" ? "Stopping…" : "Stop Trading"}</button>
              : <button type="button" className="btn" disabled={!!startBlocked || busy !== null || (!isScalp && (!validTradeSize || tradeSizeChanged))} title={startBlocked ?? (!isScalp ? tradeSizeError ?? (tradeSizeChanged ? "Save your trade size before starting." : "") : "")} onClick={onStart}>{busy === "start" ? "Starting…" : "Start Trading"}</button>}
          </div>
          {s.run === "running" && <div className="pf-note" style={{ marginTop: 12 }}>
            {me.runtime.last_signal
              ? <>Last live entry signal: {me.runtime.last_signal.market} at {when(me.runtime.last_signal.at)} — {me.runtime.last_signal.result.toLowerCase().replace(/_/g, " ")}{me.runtime.last_signal.reason ? ` (${me.runtime.last_signal.reason.replace(/_/g, " ")})` : ""}.</>
              : <>Trading is on and waiting for the next strategy entry. Paper positions opened before live activation are not copied to Yellow.</>}
            <div style={{ marginTop: 6 }}>A signal opens when the saved trade size fits available exchange margin and the coin’s minimum order.</div>
          </div>}

          {/* results */}
          <div className="pf-field">
            <div className="pf-sec-head" style={{ marginBottom: 10 }}>
              <div className="eyebrow pf-label" style={{ margin: 0 }}>Recent WHY trades</div>
              <span className="pf-dim mono" style={{ fontSize: 10.5 }}>Live · Yellow Pro account</span>
            </div>
            <div className="pf-stats">
              <div><div className="pf-k">Overall WHY P&amp;L</div><div className={"pf-v mono " + ((trades?.summary.realized_pnl ?? 0) < 0 ? "pf-neg" : "pf-pos")}>{usd(trades?.summary.realized_pnl)}</div></div>
              <div><div className="pf-k">Closed · all time</div><div className="pf-v mono">{trades?.summary.closed_trades ?? 0}<span className="pf-dim" style={{ fontSize: 11 }}> · {trades?.summary.wins ?? 0} W / {trades?.summary.losses ?? 0} L</span></div></div>
              <div><div className="pf-k">Fees</div><div className="pf-v mono">{usd(trades?.summary.fees ?? 0)}</div></div>
              <div><div className="pf-k">Last activity</div><div className="pf-v mono" style={{ fontSize: 12.5 }}>{ago(lastActivity)}{lastEvent ? <span className="pf-dim"> · {lastEvent.kind.replace(/_/g, " ")}</span> : null}</div></div>
            </div>
            <TodayResults today={trades?.today}/>
            <TradeTable rows={(trades?.trades ?? []).map((t) => ({ ts: t.closed_ts, symbol: t.market, side: t.direction, kind: t.reason || "close", pnl: t.pnl }))} empty="No closed WHY trades on this account yet." />
            {(me.runtime?.positions?.length ?? 0) > 0 && (
              <div style={{ marginTop: 14 }}>
                <div className="pf-k">Open now</div>
                <ul className="pf-openlist">{me.runtime.positions.map((p) => <li key={p.position_id || p.market + p.direction} className="mono"><span>{p.market}</span><span className={p.direction === "long" ? "pf-pos" : "pf-neg"}>{p.direction}</span><span className="pf-dim">{p.qty} @ {p.entry_price}</span><span className="pf-dim">{p.state}</span><button type="button" className="btn btn-ghost" disabled={!!busy || !p.position_id || p.state === "CLOSE_PENDING"} onClick={() => p.position_id && onClosePosition({ id: p.position_id, market: p.market, side: p.direction, exchange: "Yellow Pro" })}>{p.state === "CLOSE_PENDING" ? "Close pending" : "Close at market"}</button></li>)}</ul>
              </div>
            )}
          </div>

          <div className="pf-field" style={{ borderTop: "1px solid var(--steel-soft)", paddingTop: 16, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span className="pf-dim" style={{ fontSize: 12.5 }}>Disconnecting deletes the stored credential. Open positions stop being managed.</span>
            <button type="button" className="pf-link pf-danger" disabled={busy === "disconnect"} onClick={onDisconnect}>Disconnect Yellow Pro</button>
          </div>
        </>)}
      </div>
    </Dialog>
  );
}

// ---- results panels: Paper and Live use isolated account ledgers -----------------------
function PaperPanel({ paper, busy, onSave, onStart, onStop, onReset, onClosePosition, onDiscovery }: { onDiscovery: () => void; onClosePosition: (row: Pick<Row, "id" | "market" | "side" | "exchange">) => void; paper: PaperAccount | null; busy: string | null; onSave: (v: RiskSettings) => void; onStart: () => void; onStop: () => void; onReset: () => void }) {
  const rows = (paper?.paper_trade_history ?? []).slice(0, 10).map((t) => ({ ts: String(t.closed_at ?? ""), symbol: String(t.market ?? "—"), side: String(t.direction ?? "—"), kind: String(t.reason ?? "close"), pnl: Number(t.net_pnl ?? 0) }));
  const open = (paper?.paper_open_positions ?? []).map((p) => ({
    id: String(p.id ?? p.signal_id ?? p.market ?? "position"), symbol: String(p.market ?? "—"),
    side: String(p.direction ?? "—"), strategy: String(p.strategy ?? "—"), margin: Number(p.margin ?? 0),
    notional: Number(p.notional ?? 0), leverage: Number(p.leverage ?? 0), entry: Number(p.entry_price ?? 0),
    mark: Number(p.mark_price ?? 0), pnl: Number(p.unrealized_pnl ?? 0), opened: String(p.opened_at ?? ""),
  }));
  return (
    <div className="pf-card" style={{ padding: "22px 24px" }}>
      <div className="pf-sec-head">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}><span className="mono" style={{ fontSize: 12.5, fontWeight: 700, letterSpacing: ".1em" }}>PAPER</span><Pill tone="amber">Simulated equity</Pill></div>
        <span className="pf-dim mono" style={{ fontSize: 10.5 }}>Simulated USD · no real funds</span>
      </div>
      <p className="pf-dim" style={{ fontSize: 13, marginTop: 8, lineHeight: 1.6 }}>Paper uses WHY&apos;s production strategy signals, risk logic and pricing feed, but execution is confined to this simulated ledger. It can never submit an exchange order.</p>
      <div className="pf-stats" style={{ marginTop: 16 }}>
        <div><div className="pf-k">Paper equity</div><div className="pf-v mono">{paper ? usd(paper.paper_equity) : "—"}</div></div>
        <div><div className="pf-k">Available</div><div className="pf-v mono">{paper ? usd(paper.paper_available_balance) : "—"}</div></div>
        <div><div className="pf-k">Used margin</div><div className="pf-v mono">{paper ? usd(paper.paper_used_margin) : "—"}</div></div>
        <div><div className="pf-k">Realized P&amp;L</div><div className={"pf-v mono " + ((paper?.paper_realized_pnl ?? 0) < 0 ? "pf-neg" : "pf-pos")}>{paper ? `${paper.paper_realized_pnl >= 0 ? "+" : ""}${usd(paper.paper_realized_pnl)}` : "—"}</div></div>
        <div><div className="pf-k">Unrealized P&amp;L</div><div className={"pf-v mono " + ((paper?.paper_unrealized_pnl ?? 0) < 0 ? "pf-neg" : "pf-pos")}>{paper ? `${paper.paper_unrealized_pnl >= 0 ? "+" : ""}${usd(paper.paper_unrealized_pnl)}` : "—"}</div></div>
        <div><div className="pf-k">Fees</div><div className="pf-v mono">{paper ? usd(paper.paper_total_fees) : "—"}</div></div>
      </div>
      {paper && <RiskControls value={paper.settings} busy={busy === "paper"} onSave={onSave} paperOnly onDiscovery={onDiscovery} />}
      <div className="pf-status-row"><div><div className="pf-k">Status</div><div style={{ marginTop: 7 }}>{paper?.running ? <Pill tone="green">Running</Pill> : <Pill tone="muted">Stopped</Pill>}</div></div><div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>{paper?.running ? <button className="btn btn-ghost" disabled={busy === "paper"} onClick={onStop}>Stop Paper trading</button> : <button className="btn" disabled={!paper?.paper_enabled || busy === "paper"} onClick={onStart}>Start Paper trading</button>}<button className="btn btn-ghost" disabled={busy === "paper"} onClick={onReset}>Reset Paper account</button></div></div>
      <div className="pf-sec-head" style={{ marginTop: 22 }}><div className="pf-k">Open Paper positions</div>{open.length > 0 && <Pill tone="green">{open.length} active</Pill>}</div>
      <div className="pf-table" style={{ marginTop: 8 }}>
        {open.length === 0 ? <div className="pf-dim mono" style={{ fontSize: 12, padding: "12px 0" }}>{paper?.running ? "Paper is running and waiting for the next production signal." : "No open Paper positions."}</div> : open.map((p) => (
          <div key={p.id} className="mk" style={{ padding: "9px 0" }}>
            <span className="mk-sym">{p.symbol}</span>
            <span className={"mk-st " + (p.side === "long" ? "mk-mint" : "mk-cut")}>{p.side.toUpperCase()} · {p.strategy}</span>
            <span className={p.pnl >= 0 ? "mk-mint" : "mk-cut"}>{p.pnl >= 0 ? "+" : ""}{usd(p.pnl)}</span>
            <span className="mk-ms">{p.margin.toFixed(2)} margin · {p.notional.toFixed(2)} notional · {p.leverage}x · {p.entry.toPrecision(6)} → {p.mark.toPrecision(6)} · {when(p.opened)}</span><button type="button" className="btn btn-ghost" disabled={!!busy} onClick={() => onClosePosition({ id: p.id, market: p.symbol, side: p.side, exchange: "WHY Paper · simulated" })}>Close at market</button>
          </div>
        ))}
      </div>
      <div className="pf-k" style={{ marginTop: 22 }}>Closed Paper trades</div>
      <TradeTable rows={rows} empty="No closed Paper trades in this session yet." />
    </div>
  );
}

function LiveAccountPanel({ slot: s, busy, onStart, onStop, onManage }: {
  slot: ExchangeSlot; busy: string | null; onStart: () => void; onStop: () => void; onManage: () => void;
}) {
  const running = s.run === "running";
  const connected = s.connection === "connected";
  const blocked = liveStartBlockReason(s);
  const starting = busy === (s.id === "weex" ? "weex-start" : "start");
  const stopping = busy === (s.id === "weex" ? "weex-stop" : "stop");
  return <div className="pf-card pf-live-account" aria-label={`${s.name} live account`}>
    <div className="pf-sec-head">
      <div><div className="eyebrow pf-gold">{s.name} · Live</div><h2 style={{ fontFamily: display, fontSize: 24, marginTop: 8 }}>Your exchange account</h2></div>
      <Pill tone={running ? "green" : s.connection === "error" ? "red" : "muted"}>{running ? "Running" : s.connection === "error" ? "Needs attention" : connected ? "Stopped" : "Not connected"}</Pill>
    </div>
    <div className="pf-stats" style={{ marginTop: 16 }} aria-live="polite">
      <div><div className="pf-k">Account balance</div><div className="pf-v mono">{usd(s.equity)}</div><div className="pf-dim mono" style={{ fontSize: 10.5, marginTop: 6 }}>Total exchange equity</div></div>
      <div><div className="pf-k">Available balance</div><div className="pf-v mono">{usd(s.available)}</div></div>
      <div><div className="pf-k">{s.rawWeex ? "Position size" : "Trade size"}</div><div className="pf-v mono">{s.rawWeex ? `${s.rawWeex.settings.position_size_pct}%` : s.tradeSizeLabel ?? "—"}</div></div>
      <div><div className="pf-k">Leverage</div><div className="pf-v mono">{s.leverage != null ? `${s.leverage}x` : "—"}</div></div>
    </div>
    {connected && (s.equity == null || s.available == null) && <p className="pf-note pf-note-amber">The exchange has not returned a complete balance. Open exchange settings to re-check the connection.</p>}
    {s.id === "yellow" && s.raw?.balance_at && <p className="pf-dim mono" style={{ fontSize: 10.5, marginTop: 10 }}>Balance checked {when(s.raw.balance_at)}</p>}
    <div className="pf-status-row">
      <p className="pf-dim" style={{ fontSize: 13, lineHeight: 1.6 }}>{running ? s.runNote || "WHY is managing this account." : "Connecting your API key does not start trading. Review your settings, then start when ready."}</p>
      <div className="pf-actions">
        <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={onManage}>{s.connection === "not_connected" ? "Connect Exchange" : "Exchange settings"}</button>
        {running
          ? <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={onStop}>{stopping ? "Stopping…" : "Stop Trading"}</button>
          : <button type="button" className="btn" disabled={!!blocked || busy !== null} onClick={onStart} title={blocked ?? ""}>{starting ? "Starting…" : "Start Trading"}</button>}
      </div>
    </div>
    {!running && blocked && <p className="pf-note pf-note-amber">{blocked}</p>}
  </div>;
}

function LivePanel({ trades, me, busy, onClosePosition }: { trades: TradesView | null; me: Me | null; busy: string | null; onClosePosition: (row: Pick<Row, "id" | "market" | "side" | "exchange">) => void }) {
  const rows = (trades?.trades ?? []).slice(0, 10).map((t) => ({ ts: t.closed_ts, symbol: t.market, side: t.direction, kind: t.reason || "close", pnl: t.pnl }));
  const open = me?.runtime?.positions ?? [];
  const lastSignal = me?.runtime?.last_signal;
  const lastEvent = me?.events?.[0];
  return (
    <div className="pf-card" style={{ padding: "22px 24px" }}>
      <div className="pf-sec-head">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}><span className="mono" style={{ fontSize: 12.5, fontWeight: 700, letterSpacing: ".1em" }}>Yellow Pro · Live results</span><Pill tone={me?.account.state === "ERROR" ? "red" : "green"}>{me?.account.state === "ERROR" ? "Needs attention" : "Connected account"}</Pill></div>
        <span className="pf-dim mono" style={{ fontSize: 10.5 }}>Your account only · never mixed with Paper results</span>
      </div>
      <div className="pf-stats" style={{ marginTop: 16 }}>
        <div><div className="pf-k">Overall WHY P&amp;L</div><div className={"pf-v mono " + ((trades?.summary.realized_pnl ?? 0) < 0 ? "pf-neg" : "pf-pos")}>{usd(trades?.summary.realized_pnl)}</div></div>
        <div><div className="pf-k">Closed trades · all time</div><div className="pf-v mono">{trades?.summary.closed_trades ?? 0}<span className="pf-dim" style={{ fontSize: 11 }}> · {trades?.summary.wins ?? 0} W / {trades?.summary.losses ?? 0} L</span></div></div>
        <div><div className="pf-k">Open positions</div><div className="pf-v mono">{me?.runtime?.open_positions ?? 0}</div></div>
        <div><div className="pf-k">Last activity</div><div className="pf-v mono" style={{ fontSize: 12.5 }}>{ago(trades?.summary.last_trade_at ?? lastEvent?.at ?? null)}{lastEvent ? <span className="pf-dim"> · {lastEvent.kind.replace(/_/g, " ")}</span> : null}</div></div>
      </div>
      <TodayResults today={trades?.today}/>
      <div className="pf-k" style={{ marginTop: 22 }}>Open live positions</div>
      {me && <p className="pf-dim mono" style={{ fontSize: 12 }}>{usd(me.account.balance_available == null ? null : Number(me.account.balance_available))} available at Yellow · {me.config.trading_style === "scalp" ? me.config.strategy_profile?.id === "scalp" ? `up to ${me.config.strategy_profile.strategy_entry_slots} Scalp entry slots, including pending entries` : "Scalp defaults unavailable" : `up to ${me.config.max_open_positions} open positions`}.</p>}
      {open.length === 0 ? <p className="pf-dim mono" style={{ fontSize: 12 }}>No open live positions.</p> :
        <ul className="pf-openlist">{open.map((p) => <li key={p.position_id || `${p.market}:${p.direction}`} className="mono"><span>{p.market}</span><span className={p.direction === "long" ? "pf-pos" : "pf-neg"}>{p.direction}</span><span className="pf-dim">{p.qty} @ {p.entry_price}</span><span className="pf-dim">{p.state}</span><button type="button" className="btn btn-ghost" disabled={!!busy || !p.position_id || p.state === "CLOSE_PENDING"} onClick={() => p.position_id && onClosePosition({ id: p.position_id, market: p.market, side: p.direction, exchange: "Yellow Pro" })}>{p.state === "CLOSE_PENDING" ? "Close pending" : "Close at market"}</button></li>)}</ul>}
      {lastSignal?.result === "REJECTED" &&
        <p className="pf-note pf-note-amber" style={{ marginTop: 12 }}>Live skipped {lastSignal.market} at {when(lastSignal.at)}: {lastSignal.reason?.replace(/_/g, " ") ?? "order rejected"}.</p>}
      <div className="pf-k" style={{ marginTop: 22 }}>Closed live trades</div>
      <TradeTable rows={rows} empty="No closed WHY trades on your Yellow Pro account yet." />
    </div>
  );
}

function TradeTable({ rows, empty }: { rows: { ts: number | string; symbol: string; side: string; kind: string; pnl: number | null }[]; empty: string }) {
  return (
    <div className="pf-table" style={{ marginTop: 16 }}>
      {rows.length === 0 ? <div className="pf-dim mono" style={{ fontSize: 12, padding: "12px 0" }}>{empty}</div> : rows.map((r, i) => (
        <div key={i} className="mk" style={{ padding: "7px 0" }}>
          <span className="mk-sym">{r.symbol}</span>
          <span className={"mk-st " + (r.side === "long" ? "mk-mint" : "mk-cut")}>{r.side.toUpperCase()} · {r.kind.toUpperCase()}</span>
          <span className={r.pnl == null ? "mk-ms" : r.pnl >= 0 ? "mk-mint" : "mk-cut"}>{r.pnl == null ? "—" : (r.pnl >= 0 ? "+" : "") + usd(r.pnl)}</span>
          <span className="mk-ms">{when(r.ts)}</span>
        </div>
      ))}
    </div>
  );
}

// ---- styles: same tokens as the rest of the site --------------------------------------
const CSS = `
.pf .mono{font-family:${mono}}
.pf-h1{font-family:${display};font-weight:700;font-size:clamp(26px,3.6vw,38px);letter-spacing:-.03em;line-height:1.05;margin:10px 0 0;word-break:break-all}
.pf-h2{font-family:${display};font-weight:700;font-size:22px;letter-spacing:-.02em;margin:0}
.pf-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}
.pf-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:28px}
.pf-tile{border:1px solid var(--steel-soft);border-radius:12px;background:rgba(255,255,255,.015);padding:16px 18px;min-width:0}
.pf-k{font-family:${mono};font-size:10.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--ash-dim)}
.pf-v{margin-top:6px;font-size:14.5px;color:var(--platinum);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pf-dim{color:var(--ash)}
.pf-pos{color:var(--green)} .pf-neg{color:var(--red)}
.pf-card{border:1px solid var(--steel-soft);border-radius:12px;background:rgba(255,255,255,.015);color:var(--platinum);text-align:left}
.pf-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-top:14px}
.pf-live-accounts{display:grid;gap:16px;margin-bottom:32px}
.pf-live-account{padding:22px 24px}
.pf-live-account .pf-status-row{align-items:flex-start}
.pf .btn:disabled{opacity:.45;cursor:not-allowed;transform:none}
.pf-xcard{display:flex;flex-direction:column;padding:18px 20px;cursor:pointer;transition:border-color .2s,background .2s,transform .2s;font:inherit;color:inherit}
.pf-xcard:hover{border-color:var(--steel);background:rgba(255,255,255,.04);transform:translateY(-2px)}
.pf-xcard-soon{opacity:.62;cursor:default}
.pf-xcard-soon:hover{transform:none;background:rgba(255,255,255,.02)}
.pf-xcard-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.pf-kv{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:14px 0 0;font-size:13px}
.pf-kv dt{font-family:${mono};font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--ash-dim);align-self:center}
.pf-kv dd{margin:0;color:var(--platinum)}
.pf-pill{display:inline-block;font-family:${mono};font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;border-radius:999px;padding:3px 8px;border:1px solid var(--steel-soft);color:var(--ash);white-space:nowrap;vertical-align:middle}
.pf-pill-green{color:var(--green);border-color:rgba(61,227,160,.35);background:rgba(61,227,160,.08)}
.pf-pill-amber{color:var(--gold);border-color:rgba(243,199,106,.35);background:rgba(243,199,106,.08)}
.pf-pill-red{color:var(--red);border-color:rgba(240,119,111,.35);background:rgba(240,119,111,.08)}
.pf-pill-gold{color:#1a0d05;background:var(--molten);border-color:transparent;font-weight:700}
.pf-tag-hot{font-family:${mono};font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:#1a0d05;background:var(--molten);border-radius:999px;padding:3px 8px;font-weight:700}
.pf-sec-head{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;flex-wrap:wrap}
.pf-seg{display:inline-flex;gap:3px;padding:3px;border:1px solid var(--steel);border-radius:999px;background:var(--carbon-2);margin-top:6px}
.pf-seg button{font-family:${mono};font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;padding:6px 12px;border-radius:999px;border:none;cursor:pointer;background:transparent;color:var(--ash);transition:background .2s,color .2s}
.pf-seg button[aria-pressed="true"]{background:var(--molten);color:#1a0d05}
.pf-seg button:disabled{opacity:.45;cursor:not-allowed}
.pf-seg-3{display:grid;grid-template-columns:repeat(3,1fr);width:100%;border-radius:8px}
.pf-seg-3 button{border-radius:6px;padding:9px 8px}
.pf-options{display:flex;flex-wrap:wrap;border-radius:8px}
.pf-options button{border-radius:6px}
.pf-link{background:none;border:none;padding:0;font:inherit;font-size:13px;color:var(--gold);cursor:pointer}
.pf-link:hover{text-decoration:underline}
.pf-link:disabled{opacity:.5;cursor:default}
.pf-danger{color:var(--red)}
.pf-a{color:var(--gold)} .pf-a:hover{text-decoration:underline}
.pf-label{display:block;margin:16px 0 8px}
.pf-input{width:100%;background:var(--carbon-2);border:1px solid var(--steel);border-radius:3px;padding:13px 14px;color:var(--platinum);font-family:${mono};font-size:13.5px;outline:none;transition:border-color .2s}
.pf-input:focus{border-color:var(--gold-dim)}
select.pf-input{appearance:none;-webkit-appearance:none;background-image:linear-gradient(45deg,transparent 50%,var(--ash) 50%),linear-gradient(135deg,var(--ash) 50%,transparent 50%);background-position:calc(100% - 18px) 50%,calc(100% - 13px) 50%;background-size:5px 5px;background-repeat:no-repeat}
select.pf-input:disabled{color:var(--platinum);opacity:1}
.pf-err{color:var(--red);font-size:13px;margin-top:12px;line-height:1.5}
.pf-ok{color:var(--green);font-size:13px;margin-top:12px;line-height:1.5}
.pf-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:20px;flex-wrap:wrap}
.pf-plans{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.pf-plan{border:1px solid var(--steel-soft);border-radius:10px;padding:18px 18px 16px;background:rgba(255,255,255,.015);display:flex;flex-direction:column}
.pf-plan-hot{border-color:var(--gold-dim);background:linear-gradient(180deg,#16120f,var(--carbon-2))}
.pf-backdrop{position:fixed;inset:0;background:rgba(6,7,10,.78);backdrop-filter:blur(8px);z-index:80;display:flex;align-items:flex-start;justify-content:center;padding:6vh 16px 16px;overflow-y:auto}
.pf-dialog{width:100%;max-width:520px;background:linear-gradient(180deg,var(--carbon),var(--carbon-2));border:1px solid var(--steel);border-radius:14px;padding:24px 24px 22px;box-shadow:0 30px 80px rgba(0,0,0,.6)}
.pf-dialog-wide{max-width:760px}
.pf-dialog-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}
.pf-x{background:none;border:1px solid var(--steel-soft);color:var(--ash);width:32px;height:32px;border-radius:50%;cursor:pointer;font-size:18px;line-height:1}
.pf-x:hover{color:var(--platinum);border-color:var(--steel)}
.pf-drawer-grid{display:grid;gap:6px}
.pf-kv2{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px 18px;padding:12px 0 6px;border-bottom:1px solid var(--steel-soft)}
.pf-field{padding:14px 0 6px}
.pf-status-row{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;border-top:1px solid var(--steel-soft);border-bottom:1px solid var(--steel-soft);padding:18px 0;margin-top:8px}
.pf-note{border:1px solid var(--steel);border-radius:10px;padding:14px 16px;margin-top:12px;background:rgba(255,255,255,.02);font-size:13.5px;line-height:1.6;color:var(--ash)}
.pf-note-amber{border-color:rgba(243,199,106,.35);background:rgba(243,199,106,.05)}
.pf-note-red{border-color:rgba(240,119,111,.35);background:rgba(240,119,111,.05)}
.pf-range{width:100%;margin:14px 0 6px;accent-color:#F0852A}
.pf-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.pf-stats>div{border:1px solid var(--steel-soft);border-radius:10px;padding:12px 14px;background:rgba(255,255,255,.015)}
.pf-table .mk{grid-template-columns:auto 1fr auto auto;padding-left:0;padding-right:0;border-bottom:1px solid var(--steel-soft)}
.pf-table .mk:last-child{border-bottom:none}
.pf-openlist{list-style:none;padding:0;margin:8px 0 0;display:grid;gap:6px}
.pf-openlist li{display:grid;grid-template-columns:auto auto 1fr auto;gap:12px;font-size:12px;color:var(--platinum)}
.pf-toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:var(--carbon);border:1px solid var(--gold-dim);color:var(--platinum);font-family:${mono};font-size:12.5px;padding:11px 16px;border-radius:8px;z-index:90;box-shadow:0 12px 40px rgba(0,0,0,.5)}
@media(max-width:900px){.pf-tiles{grid-template-columns:repeat(2,1fr)} .pf-plans{grid-template-columns:1fr}}
@media(max-width:600px){
  .pf{padding:36px 20px 72px!important}
  .pf-h1{font-size:20px;word-break:normal;overflow-wrap:anywhere}
  .pf-head{align-items:flex-start}
  .pf-tiles{grid-template-columns:1fr 1fr;gap:10px;margin-top:22px}
  .pf-tile{padding:13px 14px}
  .pf-v{font-size:13.5px;white-space:normal;overflow-wrap:anywhere}
  .pf-grid{grid-template-columns:1fr}
  .pf-input,select.pf-input{font-size:16px;padding:12px 13px}
  .pf-backdrop{padding:0;align-items:flex-end}
  .pf-dialog,.pf-dialog-wide{max-width:none;border-radius:16px 16px 0 0;border-bottom:none;max-height:94vh;overflow-y:auto;padding:0 16px calc(18px + env(safe-area-inset-bottom));box-shadow:0 -20px 60px rgba(0,0,0,.6)}
  .pf-dialog-head{position:sticky;top:0;background:var(--carbon);margin:0 -16px 14px;padding:16px 16px 12px;border-bottom:1px solid var(--steel-soft);z-index:1}
  .pf-alloc-head{flex-direction:column;align-items:flex-start!important;gap:6px}
  .pf-actions{flex-direction:column-reverse}
  .pf-actions .btn{width:100%;text-align:center;padding:13px 18px}
  .pf-status-row .btn{width:100%;text-align:center}
  .pf-stats{grid-template-columns:1fr 1fr;gap:8px}
  .pf-stats>div{padding:10px 12px}
  .pf-table .mk{grid-template-columns:auto 1fr auto;font-size:11.5px;gap:8px}
  .pf-table .mk .mk-ms{display:none}
  .pf-kv2{grid-template-columns:1fr 1fr}
  .pf-openlist li{grid-template-columns:auto auto 1fr}
  .pf-openlist li span:last-child{display:none}
  .pf-toast{left:12px;right:12px;transform:none;text-align:center}
}
@media(max-width:400px){.pf-tiles{grid-template-columns:1fr}}
`;
