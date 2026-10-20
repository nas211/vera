"use client";
// The signed-in WHY dashboard: a mobile-first app with four tabs (Trades · Chart · History · Account).
// Account data comes only from the WHY backend (props); prices and candles come from Yellow Pro's
// public feed. Changing tabs or the Trading/Paper view never starts or stops trading.
import { useEffect, useMemo, useRef, useState } from "react";
import type { Me, PaperAccount, Subscription, TradesView, WeexAccount } from "../client";
import { liveStartBlockReason, type ExchangeSlot } from "../exchanges";
import type { Row } from "../dashboard/model";
import { discoveryRows, type AiController } from "../dashboard/useDiscovery";
import { ResearchProgress } from "../../discovery/ResearchProgress";
import { AiStrategies } from "../dashboard/AiStrategies";
import { PaperSizing } from "../dashboard/PaperSizing";
import { closedTrade, livePnl, num, openTotal, openTrade, pct, price, splitUsd, summary, today, tone, usd, when, winRate, type ClosedTrade, type OpenTrade, type Timeframe } from "./model";
import { loadTicker, useFeedStatus, useMarks, useMarkets, type Ticker } from "./yellowFeed";
import { TIMEFRAMES, YellowChart } from "./YellowChart";
import { yellowBlock } from "./yellowBlock";
import s from "./app.module.css";

/** Closed trades requested from /me/trades (the backend's maximum page). */
export const TRADES_LIMIT = 100;
const PAPER_LIMIT = 50;
const TABS = ["trades", "chart", "history", "account"] as const;
type Tab = typeof TABS[number];
type CloseTarget = Pick<Row, "id" | "market" | "side" | "exchange">;

type Props = {
  discoverySelection?: number; discoveryActive?: boolean; discovery?: AiController;
  onCloseAiPosition?: (row: Row) => void; onCloseDiscovery?: () => void; onDiscovery: () => void;
  tradingStyleControl?: React.ReactNode; onClosePosition?: (row: CloseTarget) => void;
  userId: string; email: string; mode: "paper" | "live"; onMode: (mode: "paper" | "live") => void;
  me: Me | null; paper: PaperAccount | null; weex: WeexAccount | null; trades: TradesView | null;
  subscription: Subscription; slots: ExchangeSlot[]; error: string; tutorialTarget: boolean; busy?: string | null;
  onPaperSave?: (settings: PaperAccount["settings"]) => void; onPaperStart?: () => void; onPaperStop?: () => void; onPaperReset?: () => void;
  onLiveStart?: (slot: ExchangeSlot) => void; onLiveStop?: (slot: ExchangeSlot) => void; onRefreshPositions?: () => Promise<void>;
  onDismissHint: () => void; onTutorial: () => void; onSignOut: () => void; onPlan: () => void; onPaperSettings: () => void; onExchange: (slot: ExchangeSlot) => void;
};

const toneClass = (v: number | null | undefined) => s[tone(v)];
const rowOpen = (r: Row): OpenTrade => openTrade({ id: r.id, market: r.market, direction: r.side, qty: r.qty, entry_price: r.entry, mark_price: r.mark, notional: r.notional, unrealized_pnl: r.pnl, opened_at: r.time, state: r.result }, r.exchange, null);
const rowClosed = (r: Row): ClosedTrade => closedTrade({ id: r.id, market: r.market, direction: r.side, qty: r.qty, entry_price: r.entry, exit_price: r.exit, notional: r.notional, pnl: r.pnl, reason: r.result, closed_at: r.time }, r.exchange, null);

export function MobileDashboard(p: Props) {
  const [tab, setTab] = useState<Tab>("trades");
  const [chartMarket, setChartMarket] = useState("BTCUSDT-PERP");
  const [tf, setTf] = useState<Timeframe>("5m");
  const [sheet, setSheet] = useState<"markets" | "exchanges" | null>(null);
  const [filter, setFilter] = useState<"all" | "win" | "loss">("all");
  const [shown, setShown] = useState(20);
  const [chartSeen, setChartSeen] = useState(false);
  useEffect(() => { if (tab === "chart") setChartSeen(true); }, [tab]);
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 30000); return () => clearInterval(t); }, []);

  // The app owns the whole screen: the marketing nav, footer and floating help button step aside.
  useEffect(() => { const root = document.documentElement; root.dataset.whyApp = "1"; return () => { delete root.dataset.whyApp; }; }, []);
  useEffect(() => { const h = location.hash.slice(1) as Tab; if (TABS.includes(h)) setTab(h); }, []);
  useEffect(() => { if (p.discoveryActive) p.onMode("paper"); /* AI Agents is a Paper strategy */ // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.discoveryActive, p.discoverySelection]);
  const go = (t: Tab) => { setTab(t); setSheet(null); try { history.replaceState(null, "", location.pathname + location.search + "#" + t); } catch { /* optional */ } window.scrollTo(0, 0); };
  const changeMode = (mode: "paper" | "live") => { p.onCloseDiscovery?.(); p.onMode(mode); };

  const ai = p.mode === "paper" && !!p.discoveryActive;
  const aiState = ai ? p.discovery?.state ?? null : null;
  const live = p.mode === "live";
  const cfg = p.me?.config;
  const liveLeverage = cfg?.strategy_profile?.id === "scalp" ? cfg.strategy_profile.leverage : cfg?.leverage ?? null;

  const { open, closed } = useMemo(() => {
    let o: OpenTrade[], c: ClosedTrade[];
    if (ai) { const r = discoveryRows(aiState); o = r.open.map(rowOpen); c = r.closed.map(rowClosed); }
    else if (!live) {
      const lev = p.paper?.settings.leverage ?? null;
      o = (p.paper?.paper_open_positions ?? []).map(x => openTrade(x, "WHY Paper · simulated", lev));
      c = (p.paper?.paper_trade_history ?? []).map(x => closedTrade(x, "WHY Paper · simulated", lev));
    } else {
      o = [...(p.me?.runtime.positions ?? []).map(x => openTrade(x as unknown as Record<string, unknown>, "Yellow Pro", liveLeverage)),
        ...(p.weex?.runtime.positions ?? []).map(x => openTrade(x, "WEEX", p.weex?.settings.leverage ?? null))];
      c = (p.trades?.trades ?? []).map(x => closedTrade(x as unknown as Record<string, unknown>, "Yellow Pro", liveLeverage));
    }
    c.sort((a, b) => (b.closedAt ?? 0) - (a.closedAt ?? 0));
    return { open: o, closed: c };
  }, [ai, aiState, live, p.paper, p.me, p.weex, p.trades, liveLeverage]);

  const marks = useMarks([...open.filter(t => t.exchange !== "WEEX").map(t => t.market), ...(tab === "chart" ? [chartMarket] : [])]);
  const feed = useFeedStatus();
  const quotes = open.map(t => ({ t, ...livePnl(t, t.exchange === "WEEX" ? null : marks[t.market]) }));

  // Positions refresh while the Trades or Chart tab is on screen (the existing 5 s account poll).
  const refreshRef = useRef(p.onRefreshPositions); refreshRef.current = p.onRefreshPositions;
  useEffect(() => {
    if (ai || (tab !== "trades" && tab !== "chart") || !refreshRef.current) return;
    let cancelled = false, timer: ReturnType<typeof setTimeout>;
    const tick = async () => { if (!document.hidden) { try { await refreshRef.current?.(); } catch { /* last snapshot stays on screen */ } } if (!cancelled) timer = setTimeout(tick, 5000); };
    timer = setTimeout(tick, 5000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [tab, ai, p.userId]);

  // ---------------------------------------------------------------- numbers
  const connected = p.slots.filter(x => x.live && (x.connection === "connected" || x.connection === "error"));
  const sum = (key: "equity" | "available") => connected.length && connected.every(x => num(x[key]) != null) ? connected.reduce((a, x) => a + num(x[key])!, 0) : null;
  const equity = ai ? aiState?.equity ?? null : live ? sum("equity") : p.paper?.paper_equity ?? null;
  const equityLabel = ai ? "AI Paper equity · simulated" : live ? `Total equity · ${connected.map(x => x.name).join(" + ") || "No exchange connected"}` : "Paper equity · simulated";
  const openSum = openTotal(quotes.map(q => q.pnl));
  const day = today(closed, now, ai ? Infinity : live ? TRADES_LIMIT : PAPER_LIMIT);
  const backendDay = p.trades?.today;
  // Live "Today" is the backend's Berlin day (same scope as the deployed results page) whenever it is available.
  const dayFromBackend = live && !!backendDay?.available;
  const todayValue = dayFromBackend ? backendDay!.net_pnl : day.complete ? day.net : null;
  const todayPending = dayFromBackend ? backendDay!.pending_pnl : day.pending;
  const todayWon = dayFromBackend ? backendDay!.wins : day.won, todayLost = dayFromBackend ? backendDay!.losses : day.lost;
  const liveSummary = p.trades?.summary;
  const sessionSummary = summary(closed);
  const allPending = live ? liveSummary?.pending_pnl ?? 0 : sessionSummary.pending;
  const allTime = live ? (allPending ? null : liveSummary?.realized_pnl ?? null) : ai ? aiState?.realized ?? null : p.paper?.paper_realized_pnl ?? null;
  const histStats = live
    ? { realized: allTime, wins: liveSummary?.wins ?? 0, losses: liveSummary?.losses ?? 0, trades: liveSummary?.closed_trades ?? closed.length, scope: "All time" }
    : { realized: allTime, wins: sessionSummary.wins, losses: sessionSummary.losses, trades: sessionSummary.trades, scope: ai ? "AI Paper session" : "Paper session" };

  // ---------------------------------------------------------------- bot status
  const strategyName = cfg?.trading_style === "scalp" ? "Scalp" : "Swing";
  const yellowBlocked = live && !!p.me && ["ACTIVE", "DRAINING"].includes(p.me.account.state) && (p.me.runtime.venue !== "READY" || p.me.runtime.started === false || p.me.runtime.positions.some(x => ["PROTECTION_PENDING", "RECONCILIATION_REQUIRED"].includes(x.state)));
  type Bot = { key: string; on: boolean; text: React.ReactNode; button?: string; disabled?: boolean; title?: string; act?: () => void };
  const bots: Bot[] = ai ? [{
    key: "ai", on: !!aiState?.running,
    text: !aiState ? "AI Paper · loading" : aiState.kill ? "AI Paper · loss limit reached" : aiState.running ? <>AI Paper running · <b>simulated</b></> : "AI Paper paused · open trades still managed",
    button: p.discovery?.busy ? "Updating…" : aiState?.running ? "Pause" : "Resume",
    disabled: !!p.busy || !!p.discovery?.busy || !aiState || !!p.discovery?.error || (!aiState.running && (!aiState.ai_ready || aiState.kill)),
    act: () => void p.discovery?.act(aiState?.running ? "pause" : "start"),
  }] : !live ? [{
    key: "paper", on: !!p.paper?.running,
    text: !p.paper ? "Paper · loading" : p.paper.running ? <>Paper bot running · <b>{strategyName}</b> · simulated</> : "Paper bot paused · open trades still managed",
    button: p.busy === "paper" ? "Updating…" : p.paper?.running ? "Pause" : "Resume",
    disabled: !!p.busy || !p.paper || (!p.paper.running && !p.paper.paper_enabled),
    act: p.paper?.running ? p.onPaperStop : p.onPaperStart,
  }] : connected.length ? connected.map(slot => {
    const running = slot.run === "running";
    const blocked = running ? null : !p.subscription?.live_allowed ? "An eligible active subscription is required." : liveStartBlockReason(slot);
    const paused = slot.raw?.state === "PAUSED" || slot.rawWeex?.state === "PAUSED";
    return { key: slot.id, on: running && !(slot.id === "yellow" && yellowBlocked),
      text: running ? <>{slot.id === "yellow" && yellowBlocked ? "Bot enabled · waiting for Yellow" : "Bot running"} · <b>{slot.id === "yellow" ? strategyName : slot.strategy}</b>{connected.length > 1 ? ` · ${slot.name}` : ""}</> : paused ? "Bot paused · open trades still managed" : `Bot stopped${connected.length > 1 ? ` · ${slot.name}` : ""}`,
      button: p.busy ? "Updating…" : running ? "Pause" : "Resume", disabled: !!p.busy || !!blocked, title: blocked ?? undefined,
      act: () => running ? p.onLiveStop?.(slot) : p.onLiveStart?.(slot) };
  }) : [{ key: "none", on: false, text: "No exchange connected", button: "Connect", act: () => { const y = p.slots.find(x => x.id === "yellow"); if (y) p.onExchange(y); } }];

  const block = live && p.me && yellowBlocked ? yellowBlock(p.me.runtime) : null;
  const notices: { text: React.ReactNode; serious?: boolean }[] = [];
  const err = ai ? p.discovery?.error : p.error;
  if (err) notices.push({ text: err, serious: true });
  if (block) notices.push({ serious: block.serious, text: <><strong>{block.title}</strong> {block.detail} New entries resume automatically once Yellow confirms your account; open trades stay managed.{block.checkPositions && " If a close can't be confirmed, check your positions on Yellow."}</> });
  if (live && p.subscription?.live_allowed === false) notices.push({ text: "Trading is unavailable on your current subscription. Review your plan in Account." });
  if (ai && aiState?.risk_lock) notices.push({ text: `Paper daily loss limit reached for ${aiState.risk_lock.day} UTC. New entries stay locked until the next UTC day.` });
  if (ai && aiState && (aiState.feed_error || aiState.ai_error)) notices.push({ text: (aiState.feed_error || aiState.ai_error).toLowerCase().replaceAll("_", " ") });

  // ---------------------------------------------------------------- chart
  const markets = useMarkets();
  const marketInfo = markets.find(m => m.market === chartMarket);
  const precision = marketInfo?.precision || 2;
  const [ticker, setTicker] = useState<Record<string, Ticker>>({});
  const [lastClose, setLastClose] = useState<number | null>(null);
  useEffect(() => {
    if (tab !== "chart") return;
    let alive = true;
    const load = () => void loadTicker(chartMarket).then(t => { if (alive && t) setTicker(x => ({ ...x, [chartMarket]: t })); });
    load(); const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [tab, chartMarket]);
  useEffect(() => {
    if (sheet !== "markets") return;
    let alive = true;
    for (const m of markets) void loadTicker(m.market).then(t => { if (alive && t) setTicker(x => ({ ...x, [m.market]: t })); });
    return () => { alive = false; };
  }, [sheet, markets]);
  useEffect(() => { setLastClose(null); }, [chartMarket]);
  const chartPosition = quotes.find(q => q.t.market === chartMarket);
  const openChart = (market: string) => { setChartMarket(market); go("chart"); };
  const chartPrice = marks[chartMarket] ?? lastClose ?? ticker[chartMarket]?.last ?? null;
  const change = ticker[chartMarket]?.change ?? null;
  const precisionOf = (market: string) => markets.find(m => m.market === market)?.precision || undefined;

  // ---------------------------------------------------------------- render helpers
  const card = (q: typeof quotes[number], onClick?: () => void, footer: "meta" | "levels" = "meta") => {
    const { t } = q, pr = precisionOf(t.market);
    const inner = <>
      <div className={s.cRow}>
        <div className={s.pair}><Coin base={t.base} /><div>
          <div className={s.pairName}>{t.base}<small>/USDT</small></div>
          <div className={s.tags}><span className={`${s.tag} ${s[t.side]}`}>{t.side}</span>{t.leverage ? <span className={`${s.tag} ${s.lev}`}>{t.leverage}x</span> : null}{t.exchange !== "Yellow Pro" && <span className={`${s.tag} ${s.lev}`}>{t.exchange.replace(" · simulated", "")}</span>}</div>
        </div></div>
        <div className={s.pnl}><div className={`${s.amt} ${toneClass(q.pnl)}`} data-testid="trade-pnl">{usd(q.pnl, true)}</div><div className={`${s.pct} ${toneClass(q.pct)}`} data-testid="trade-pct">{pct(q.pct)}</div></div>
      </div>
      <div className={s.cMeta}>
        <div><label>Size</label><span>{usd(t.notional)}</span></div>
        {footer === "meta" ? <>
          <div><label>Entry</label><span>{price(t.entry, pr)}</span></div>
          <div><label>Mark</label><span data-testid="trade-mark">{price(q.mark, pr)}</span></div>
        </> : <>
          <div><label>Take profit</label><span className={t.takeProfit != null ? s.up : s.zero}>{price(t.takeProfit, pr)}</span></div>
          <div><label>Stop loss</label><span className={t.stopLoss != null ? s.down : s.zero}>{price(t.stopLoss, pr)}</span></div>
        </>}
      </div>
    </>;
    const cls = `${s.card} ${q.pnl != null && q.pnl < 0 ? s.loss : q.pnl != null && q.pnl > 0 ? "" : s.flat}`;
    return onClick ? <button type="button" key={t.id} className={cls} onClick={onClick} data-testid="open-trade" aria-label={`${t.base} ${t.side} — open chart`}>{inner}</button> : <div key={t.id} className={cls}>{inner}</div>;
  };
  const histRow = (r: ClosedTrade) => <div className={s.hRow} key={r.id} data-testid="history-row">
    <div className={s.hL}><Coin base={r.base} /><div>
      <div className={s.pairName}>{r.base}<small>/USDT</small></div>
      <div className={s.hSub}><span className={`${s.res} ${s[r.result]}`}>{r.result}</span> · {r.side} · {when(r.closedAt, now)}</div>
    </div></div>
    <div className={s.hR}><div className={`${s.amt} ${toneClass(r.pnl)}`}>{r.pnl == null ? "Pending" : usd(r.pnl, true)}</div><div className={`${s.pctText} ${toneClass(r.pct)}`}>{r.pnl == null || r.pnl === 0 ? "—" : pct(r.pct)}</div></div>
  </div>;
  const filtered = closed.filter(r => filter === "all" || (filter === "win" ? (r.pnl ?? 0) > 0 : (r.pnl ?? 0) < 0));
  const eq = splitUsd(equity);
  const plan = p.subscription ? `${p.subscription.plan_name}` : "Plan unavailable";
  const planStatus = p.subscription?.status.replace(/_/g, " ");
  const exchangeText = connected.length ? connected.map(x => `${x.name} · ${x.connection === "error" ? "needs attention" : x.verification === "verified" ? "connected" : "verifying"}`).join(", ") : "Not connected";
  const exchangeOk = connected.length > 0 && connected.every(x => x.connection === "connected" && x.verification === "verified");
  const yellowSlot = p.slots.find(x => x.id === "yellow");

  return <div className={s.app} data-testid="mobile-dashboard" data-mode={live ? "trading" : "paper"}>
    <header className={s.top}>
      <div className={s.brand}><img src="/why-mark.svg" alt="" width={24} height={24} /> WHY TRADE</div>
      <div className={s.topR}>
        <span className={`${s.mode} ${live ? "" : s.paperMode}`} data-testid="mode-pill"><span className={s.dot} />{live ? "Trading" : ai ? "AI Paper" : "Paper"}</span>
        <button type="button" className={s.avatar} aria-label="Account" onClick={() => go("account")}>{p.email.slice(0, 1).toUpperCase()}</button>
      </div>
    </header>

    <div className={s.main}>
      {notices.map((n, i) => <p key={i} className={n.serious ? s.alert : s.notice} role={n.serious ? "alert" : "status"}>{n.text}</p>)}

      {/* ===== TRADES ===== */}
      <section role="tabpanel" id="panel-trades" aria-labelledby="tab-trades" hidden={tab !== "trades"} className={s.view}>
        <div className={s.hero}>
          <div className={s.eyebrow}>{equityLabel}</div>
          <div className={s.equity} data-testid="equity">{eq ? <>{eq.whole}<span className={s.cents}>{eq.cents}</span></> : "—"}</div>
          <div className={s.plStrip} data-testid="pl-strip">
            <div data-testid="pl-open"><label>Open</label><b className={toneClass(openSum)}>{usd(openSum, true)}</b><small>{open.length ? `${open.length} trade${open.length === 1 ? "" : "s"} · est.` : "No open trades"}</small></div>
            <div data-testid="pl-today" className={todayPending ? s.pending : ""}><label>Today</label><b className={toneClass(todayPending ? null : todayValue)}>{usd(todayValue, true)}</b><small>{todayPending ? `${todayPending} not counted yet` : `${todayWon} won · ${todayLost} lost${dayFromBackend ? " · Berlin day" : " · your local day"}`}</small></div>
            <div data-testid="pl-all" className={allPending ? s.pending : ""}><label>{live ? "All time" : "Session"}</label><b className={toneClass(allPending ? null : allTime)}>{usd(allTime, true)}</b><small>{allPending ? `${allPending} not counted yet` : live ? `${histStats.trades} trade${histStats.trades === 1 ? "" : "s"}` : ai ? "before funding" : "before fees"}</small></div>
          </div>
        </div>

        {bots.map(b => <div className={s.bot} key={b.key} data-testid="bot-status">
          <div className={s.botL}><span className={`${s.dot} ${b.on ? s.dotOn : s.dotOff}`} /><span>{b.text}</span></div>
          {b.button && <button type="button" className={s.btnGhost} disabled={b.disabled} title={b.title} onClick={b.act}>{b.button}</button>}
        </div>)}

        <div className={s.sh}><h2>Open trades</h2><span className={s.count}>{open.length}</span></div>
        <div className={s.trades}>
          {open.length ? quotes.map(q => card(q, () => openChart(q.t.market))) : <div className={s.empty}><b>No open trades</b>{live && !connected.length ? "Connect Yellow Pro in Account to start trading." : "The bot is scanning Yellow Pro markets. New trades appear here."}</div>}
        </div>
        {open.length > 0 && <p className={s.foot}>Open P&amp;L is estimated from the Yellow Pro mark price, before fees and funding. % is return on margin.</p>}

        <div className={s.sh} style={{ marginTop: 26 }}><h2>Recently closed</h2>{closed.length > 0 && <button type="button" className={s.link} onClick={() => go("history")}>All →</button>}</div>
        <div className={s.hist}>{closed.length ? closed.slice(0, 3).map(histRow) : <div className={s.empty}><b>No closed trades yet</b>Finished trades and their results appear here.</div>}</div>
      </section>

      {/* ===== CHART ===== */}
      <section role="tabpanel" id="panel-chart" aria-labelledby="tab-chart" hidden={tab !== "chart"} className={s.view}>
        <div className={s.chHead}>
          <button type="button" className={s.symBtn} onClick={() => setSheet("markets")} aria-haspopup="dialog" data-testid="pair-button">{chartMarket.replace("USDT-PERP", "")}/USDT
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg></button>
          <div className={s.chPrice}><div className={s.p} data-testid="chart-price">{price(chartPrice, marketInfo?.precision || undefined)}</div><div className={`${s.c} ${toneClass(change)}`}>{change == null ? "24h —" : `${pct(change)} 24h`}</div></div>
        </div>
        <div className={s.tfs} role="group" aria-label="Chart timeframe">
          {TIMEFRAMES.map(x => <button type="button" key={x} className={`${s.tf} ${tf === x ? s.on : ""}`} aria-pressed={tf === x} onClick={() => setTf(x)}>{x === "1h" || x === "4h" || x === "1d" ? x.toUpperCase() : x}</button>)}
        </div>
        {chartSeen ? <YellowChart market={chartMarket} tf={tf} precision={precision} position={chartPosition?.t} closed={closed} open={open} onLast={setLastClose} /> : <div className={s.chartWrap} />}
        <div className={s.chFoot} data-testid="feed-status" data-status={feed}><span><span className={`${s.dot} ${feed === "live" ? s.dotOn : s.dotOff}`} />{feed === "live" ? "Live · Yellow Pro feed" : feed === "reconnecting" ? "Reconnecting…" : "Connecting…"}</span><span>Yellow Pro · Perp</span></div>
        {chartPosition && <div className={s.chPos} data-testid="chart-position">
          <div className={s.sh}><h2>Your position</h2></div>
          {card(chartPosition, undefined, "levels")}
          {chartPosition.t.stopLoss == null && chartPosition.t.takeProfit == null && <p className={s.foot}>Stop-loss and take-profit levels are not reported for this position yet.</p>}
          {(ai ? p.onCloseAiPosition : p.onClosePosition) && <button type="button" className={s.closeBtn} disabled={!!p.busy || chartPosition.t.state === "CLOSE_PENDING"}
            onClick={() => { const t = chartPosition.t, row = { id: t.id, market: t.market, side: t.side, exchange: t.exchange }; if (ai) p.onCloseAiPosition?.(row as Row); else p.onClosePosition?.(row); }}>
            {p.busy === `close:${chartPosition.t.id}` ? "Closing…" : chartPosition.t.state === "CLOSE_PENDING" ? "Close pending" : "Close at market"}</button>}
        </div>}
      </section>

      {/* ===== HISTORY ===== */}
      <section role="tabpanel" id="panel-history" aria-labelledby="tab-history" hidden={tab !== "history"} className={s.view}>
        <div className={s.sh}><h2>Trade history</h2><span className={s.count}>{closed.length} loaded</span></div>
        <div className={s.eyebrow} style={{ margin: "0 2px 8px" }}>{histStats.scope}</div>
        <div className={s.summary} data-testid="history-summary">
          <div className={`${s.sBox} ${allPending ? s.pending : ""}`}><label>Realized</label><div className={toneClass(allPending ? null : histStats.realized)}>{usd(histStats.realized, true)}</div>{allPending > 0 && <small>{allPending} not counted yet</small>}</div>
          <div className={s.sBox}><label>Win rate</label><div>{winRate(histStats.wins, histStats.losses) == null ? "—" : `${Math.round(winRate(histStats.wins, histStats.losses)!)}%`}</div><small>{histStats.wins} won · {histStats.losses} lost</small></div>
          <div className={s.sBox}><label>Trades</label><div>{histStats.trades}</div></div>
        </div>
        <div className={s.filters} role="group" aria-label="Filter trades">
          {([["all", "All"], ["win", "Profit"], ["loss", "Loss"]] as const).map(([k, l]) => <button type="button" key={k} className={`${s.f} ${filter === k ? s.on : ""}`} aria-pressed={filter === k} onClick={() => { setFilter(k); setShown(20); }}>{l}</button>)}
        </div>
        <div className={s.hist}>{filtered.length ? filtered.slice(0, shown).map(histRow) : <div className={s.empty}><b>{closed.length ? "No trades match" : "No closed trades yet"}</b>{closed.length ? "Try another filter." : "Finished trades and their results appear here."}</div>}</div>
        {filtered.length > shown && <button type="button" className={s.more} onClick={() => setShown(n => n + 20)}>Show more</button>}
        {live && closed.length >= TRADES_LIMIT && <p className={s.foot}>Showing your latest {TRADES_LIMIT} closed trades. Summary figures cover all time.</p>}
      </section>

      {/* ===== ACCOUNT ===== */}
      <section role="tabpanel" id="panel-account" aria-labelledby="tab-account" hidden={tab !== "account"} className={s.view}>
        <div className={s.sh}><h2>Account</h2><span className={s.count}>{p.email}</span></div>
        <div className={s.seg} role="group" aria-label="Account mode">
          <button type="button" className={live ? `${s.on} ${s.trading}` : ""} aria-pressed={live} onClick={() => changeMode("live")}>Trading</button>
          <button type="button" className={!live ? s.on : ""} aria-pressed={!live} onClick={() => changeMode("paper")}>Paper</button>
        </div>
        <p className={s.hint}>{live ? "Real funds on your connected Yellow Pro account. Funds stay on the exchange." : "Practice with $25,000 simulated USD. No real orders are placed."} Changing this view never starts trading.</p>

        <div className={s.list}>
          <div className={s.li}><span className={s.k}>Exchange</span><span className={`${s.v} ${live ? exchangeOk ? s.ok : s.warn : ""}`}>{live ? <>● {exchangeText}</> : "WHY Paper · simulated"}</span></div>
          <div className={s.li}><span className={s.k}>Strategy</span><span className={s.v}>{ai ? "AI Agents · Paper" : strategyName}</span></div>
          <div className={s.li}><span className={s.k}>Bot</span><span className={`${s.v} ${bots[0]?.on ? s.ok : ""}`}>{bots.map(b => b.on ? "Running" : b.key === "none" ? "Not connected" : "Paused").join(" · ")}</span></div>
          <button type="button" className={s.li} onClick={p.onPlan}><span className={s.k}>Plan</span><span className={s.v}>{plan}{planStatus && <> · <span className={p.subscription?.status === "active" ? s.ok : s.warn}>{planStatus}</span></>} <span className={s.muted}>↗</span></span></button>
        </div>

        {live && p.tradingStyleControl && <div className={s.panel}>{p.tradingStyleControl}</div>}
        {!live && p.paper && p.onPaperSave && <div className={s.panel}><PaperSizing discoveryActive={ai} onCloseDiscovery={p.onCloseDiscovery} onDiscovery={p.onDiscovery} value={p.paper.settings} busy={!!p.busy} onSave={p.onPaperSave} /></div>}
        {ai && aiState && <div className={s.panel}><ResearchProgress research={aiState.bulk_research} /><AiStrategies candidates={aiState.candidates} controller={p.discovery} archivedVersions={aiState.archived_versions ?? 0} /></div>}

        <div className={s.list}>
          <button type="button" className={s.li} onClick={() => live ? (yellowSlot && p.onExchange(yellowSlot)) : p.onPaperSettings()}><span>Manage trading account</span><span className={s.muted}>↗</span></button>
          <button type="button" className={s.li} onClick={() => setSheet("exchanges")}><span>Exchanges &amp; API keys</span><span className={s.muted}>→</span></button>
          <button type="button" className={s.li} onClick={p.onTutorial}><span>Quick tutorial</span><span className={s.muted}>↗</span></button>
          <button type="button" className={s.li} onClick={() => window.dispatchEvent(new CustomEvent("why:open-help", { detail: { human: false } }))}><span>Help desk</span><span className={s.muted}>→</span></button>
          {!live && !ai && p.onPaperReset && <button type="button" className={s.li} disabled={!!p.busy || !p.paper} onClick={p.onPaperReset}><span>Reset Paper trading</span><span className={s.muted}>↺</span></button>}
          <button type="button" className={s.li} onClick={p.onSignOut}><span className={s.down}>Sign out</span><span /></button>
        </div>
        <p className={s.foot}>Pause stops new entries. Existing trades stay open and keep being managed. Derivatives trading involves substantial risk; losses are possible.</p>
      </section>
    </div>

    <nav className={s.nav} aria-label="Dashboard"><div className={s.navIn} role="tablist">
      {TABS.map(t => <button type="button" key={t} id={`tab-${t}`} role="tab" aria-selected={tab === t} aria-controls={`panel-${t}`} className={`${s.nb} ${tab === t ? s.on : ""}`} onClick={() => go(t)}
        aria-label={t === "trades" && open.length > 0 ? `Trades, ${open.length} open` : undefined}>
        {t === "trades" && open.length > 0 && <span className={s.badge} data-testid="nav-badge" aria-hidden="true">{open.length}</span>}
        <TabIcon tab={t} />{t[0].toUpperCase() + t.slice(1)}</button>)}
    </div></nav>

    <div className={`${s.sheetBg} ${sheet ? s.on : ""}`} onClick={() => setSheet(null)} aria-hidden="true" />
    <div className={`${s.sheet} ${sheet ? s.on : ""}`} role="dialog" aria-modal="true" aria-label={sheet === "exchanges" ? "Exchanges" : "Yellow Pro markets"} hidden={!sheet} onKeyDown={e => { if (e.key === "Escape") setSheet(null); }}>
      <div className={s.grab} />
      {sheet === "markets" && <><h3>Yellow Pro markets</h3>{markets.map(m => { const t = ticker[m.market]; return <button type="button" key={m.market} className={s.mk} onClick={() => { setSheet(null); setChartMarket(m.market); }}>
        <span className={s.mkL}><Coin base={m.base} />{m.base}/USDT</span>
        <span className={s.mkR}>{price(marks[m.market] ?? t?.last ?? null, m.precision || undefined)}<br /><span className={toneClass(t?.change)}>{t ? pct(t.change) : ""}</span></span></button>; })}</>}
      {sheet === "exchanges" && <><h3>Exchanges</h3><p className={s.hint} style={{ margin: "0 2px 10px" }}>Connecting an exchange does not start trading.</p>{p.slots.map(slot => <div key={slot.id} className={s.mk}>
        <span className={s.mkL}><Coin base={slot.name.slice(0, 2)} /><span>{slot.name}<br /><small className={s.muted}>{!slot.live ? slot.id === "weex" ? "Integration in progress" : "Coming soon" : slot.connection === "connected" ? `Connected · ${usd(slot.equity)}` : slot.connection.replace(/_/g, " ")}</small></span></span>
        {slot.live && <button type="button" className={s.btnGhost} onClick={() => { setSheet(null); p.onExchange(slot); }}>{slot.connection === "not_connected" ? "Connect" : "Manage"}</button>}
      </div>)}</>}
    </div>
  </div>;
}

function Coin({ base }: { base: string }) { return <div className={s.coin} aria-hidden="true">{base.slice(0, 4)}</div>; }

function TabIcon({ tab }: { tab: Tab }) {
  const paths: Record<Tab, React.ReactNode> = {
    trades: <><path d="M3 17l5-5 4 4 8-8" /><path d="M15 8h5v5" /></>,
    chart: <><path d="M7 4v16M17 4v16" /><rect x="5" y="8" width="4" height="7" rx="1" /><rect x="15" y="6" width="4" height="9" rx="1" /></>,
    history: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
    account: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c1.2-3.5 4-5 7-5s5.8 1.5 7 5" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">{paths[tab]}</svg>;
}
