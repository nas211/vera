"use client";

import Link from "next/link";
import { ResearchProgress, type BulkResearch } from "./ResearchProgress";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { api, supabaseFor, type PublicConfig } from "../profile/client";
import s from "./discovery.module.css";

type Trade = { id: string; chain_id: string; net: number; fees: number; opened: number; closed: number; reason: string; valid: boolean; invalid_reason: string; latency_seconds: number | null };
type Candidate = {
  id: string; version: string; state: string; created: number;
  spec: { name: string; thesis: string; market: string; mode: string; side: string; match: string; conditions: { feature: string; op: string; value: number }[]; stop_bps: number; target_bps: number; max_hold_seconds: number };
  score: { closed: number; wins: number; net: number; win_rate: number | null; qualified: boolean; excluded: number };
  revision?: { status: string; reason?: string; child?: string; proposal?: { stop_bps: number; target_bps: number; max_hold_seconds: number } };
  lineage?: { parent: string | null; generation: number };
  review: { reason?: string; news?: { verdict: string; reason: string } | null; direction?: { side: string; action: string; reason: string } | null; sizing?: { qty: number; planned_loss: number } | null; gate?: { approved: boolean; reason: string } | null };
  position: { qty: number; entry: number; stop: number; opened: number; valid: boolean; exit_reason: string } | null;
  entry: { price: number; remaining: number } | null; unrealized: number | null; trades: Trade[];
};
type State = {
  bulk_research?: BulkResearch; fees?: {maker: number; taker: number};
  mode: "paper"; running: boolean; kill: boolean; live_enabled: false; ai_ready: boolean; ai_error: string; feed_error: string; ai_model: string;
  realized: number; unrealized: number; equity: number; markets: number; fresh_markets: number; scanned_markets: number; last_scan: number;
  candidates: Candidate[]; events: { id: number; chain_id: string; ts: number; stage: string; data: unknown }[]; assumptions: string[];
};
const money = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const words = (v: string) => v.toLowerCase().replaceAll("_", " ");
const duration = (seconds: number) => seconds >= 3600 ? `${seconds / 3600}h` : seconds >= 60 ? `${seconds / 60}m` : `${seconds}s`;
const revisionStatus = (status: string) => ({ PENDING: "Waiting for failure analysis", ANALYZING: "Analyzing valid paper trades", WAITING_SLOT: "Tweaked settings ready · waiting for a paper slot", CREATED: "New version sent to paper testing", NO_CHANGE: "No supported tweak yet", NO_NEW_VERSION: "Proposed settings already tested", RETRY: "Analysis will retry" }[status] ?? words(status));
const when = (ts: number) => new Date(ts * 1000).toLocaleString();

export function Discovery({ embedded = false, onBack }: { embedded?: boolean; onBack?: () => void } = {}) {
  const Container = embedded ? "section" : "main";
  const [session, setSession] = useState<Session | null>(null);
  const [booted, setBooted] = useState(false);
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    let alive = true; let unsubscribe: (() => void) | undefined;
    (async () => {
      try {
        const cfg = await api<PublicConfig>(null, "GET", "/public-config");
        if (!cfg.configured) throw new Error("Sign-in service is not configured.");
        const sb = supabaseFor(cfg);
        const result = await sb.auth.getSession();
        if (!alive) return;
        setSession(result.data.session);
        const listener = sb.auth.onAuthStateChange((_event, next) => {
          if (alive) { generation.current++; setSession(next); setState(null); }
        });
        unsubscribe = () => listener.data.subscription.unsubscribe();
      } catch (err) { if (alive) setError(err instanceof Error ? err.message : "Sign-in service unavailable."); }
      finally { if (alive) setBooted(true); }
    })();
    return () => { alive = false; generation.current++; unsubscribe?.(); };
  }, []);
  const request = useCallback(async (action: string, candidate?: string) => {
    if (!session) return;
    const gen = generation.current;
    const response = await fetch(`/api/discovery?action=${action}`, {
      method: action === "state" ? "GET" : "POST", cache: "no-store", signal: AbortSignal.timeout(15000),
      headers: { Authorization: `Bearer ${session.access_token}`, "Content-Type": "application/json" },
      body: action === "state" ? undefined : JSON.stringify(candidate ? { candidate } : {}),
    });
    const data = await response.json();
    if (gen !== generation.current) return;
    if (!response.ok) throw new Error(data.error || "Paper service unavailable.");
    if (data.mode !== "paper" || data.live_enabled !== false) throw new Error("Unexpected account mode. Trading controls disabled.");
    setState(data); setError("");
  }, [session]);
  useEffect(() => {
    if (!session) return;
    let alive = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await request("state"); } catch (err) { if (alive) setError(err instanceof Error ? err.message : "Cannot refresh paper results."); }
      if (alive) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [session, request]);
  const act = async (action: string, candidate?: string) => {
    setBusy(true);
    try { await request(action, candidate); } catch (err) { setError(err instanceof Error ? err.message : "Action failed."); }
    finally { setBusy(false); }
  };
  const qualified = state?.candidates.filter(c => c.score.qualified).length ?? 0;
  const closed = state?.candidates.reduce((sum, c) => sum + c.score.closed, 0) ?? 0;
  const trades = state?.candidates.flatMap(c => c.trades.map(t => ({ ...t, market: c.spec.market, name: c.spec.name }))).sort((a, b) => b.closed - a.closed).slice(0, 30) ?? [];

  return <Container className={`${s.root} ${embedded ? s.embedded : ""}`}>
    {!embedded && <div className={s.topline}>{embedded ? <button type="button" className={s.back} onClick={onBack}>← Your trading account</button> : <Link href="/profile">← Your trading account</Link>}<span className={s.badge}>PAPER ONLY · YELLOW</span></div>}
    <header className={s.hero}>
      <div><p className={s.eyebrow}>WHY / AI STRATEGY DISCOVERY</p>{embedded ? <h2>AI Agents Trading</h2> : <h1>Find the setup.<br /><span>Prove it on paper.</span></h1>}<p className={s.intro}>AI scans Yellow&apos;s supported markets and writes new trading rules. Every frozen strategy gets 10 paper trades. At least 6 green trades unlock agent review.</p></div>
      <div className={s.controls}><div className={s.status}><i className={state?.running ? s.pulse : ""} />{state?.kill ? "Loss limit reached" : state?.running ? "Paper discovery running" : "Paper discovery paused"}</div><button className={s.primary} disabled={busy || !state || (!state.running && (!state.ai_ready || state.kill))} onClick={() => void act(state?.running ? "pause" : "start")}>{busy ? "Updating…" : state?.running ? "Pause new experiments" : "Start paper discovery"}</button><small>Separate paper balance · no real funds<br />Pausing keeps exits active for open positions.</small></div>
    </header>
    {error && <div role="alert" className={s.alert}>{error} {session && <button onClick={() => void request("state").catch(err => setError(String(err.message)))}>Retry</button>}</div>}
    {!booted ? <p className={s.empty}>Connecting to your account…</p> : !session ? <div className={s.empty}><h2>Your own paper experiments</h2><p>Sign in with your WHY account to start and track AI strategies.</p><Link className={s.primary} href="/profile">Sign in to WHY</Link></div> : null}
    {state && <>
      <ResearchProgress research={state.bulk_research} />
      <p className={s.note}>Selected AI model: <strong>{state.ai_model}</strong> · shared by Strategy, swing News and swing Direction.</p>
      {(!state.ai_ready || state.ai_error || state.feed_error) && <div className={s.notice} role="status">{!state.ai_ready ? "AI model connection is not configured. No strategies will be generated until it is connected." : state.ai_error ? words(state.ai_error) : ""}{state.feed_error && <span> Market data: {words(state.feed_error)}. New paper entries wait for fresh data.</span>}</div>}
      <section className={s.stats} aria-label="Paper discovery results">{[
        ["PAPER EQUITY", money(state.equity)], ["PAPER REALIZED P&L", money(state.realized)], ["FRESH MARKETS", `${state.fresh_markets} / ${state.markets}`], ["SCREENED TRADES", String(closed)], ["QUALIFIED FOR REVIEW", String(qualified)], ["ASSUMED MAKER / TAKER FEES", state.fees ? `${(state.fees.maker * 100).toFixed(2)}% / ${(state.fees.taker * 100).toFixed(2)}%` : "Unavailable"],
      ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</section>
      <section className={s.flow} aria-label="Discovery process">{[["01", "Discover", "Original rules from public market data"], ["02", "Paper test", "10 completed trades for each version"], ["03", "Screen", "6+ green trades qualify for review"], ["04", "Agent review", "News → Direction → Sizing → Risk gate"]].map(([number, title, desc]) => <div key={number}><span>{number}</span><h3>{title}</h3><p>{desc}</p></div>)}</section>
      <div className={s.sectionHead}><div><p className={s.eyebrow}>THE RESEARCH DESK</p><h2>Strategy experiments</h2></div><p>{state.scanned_markets} markets analysed{state.last_scan > 0 && <><br />Last scan {when(state.last_scan)}</>}</p></div>
      {state.candidates.length === 0 ? <div className={s.empty}><h3>No strategies generated yet</h3><p>{state.running ? "The AI is waiting for usable market evidence. New strategies and their exact rules will appear here." : "Start discovery to scan markets and create the first paper experiments."}</p></div> : <div className={s.candidates}>{state.candidates.map(c => <article className={s.candidate} key={c.id} id={`strategy-${c.id}`}>
        <div className={s.cardHead}><span>{c.spec.market} · {c.spec.side} · {c.spec.mode}</span><span className={c.score.qualified ? s.good : s.label}>{words(c.state)}</span></div><h3>{c.spec.name}</h3><p className={s.thesis}>{c.spec.thesis}</p>
        <div className={s.score}><strong>{c.score.closed}<small>/10 trades</small></strong><span className={c.score.wins >= 6 ? s.good : ""}>{c.score.wins} green · {c.score.win_rate === null ? "—" : `${Math.round(c.score.win_rate * 100)}%`}</span><span className={c.score.net < 0 ? s.loss : s.good}>{money(c.score.net)}</span></div>
        <div className={s.progress} aria-label={`${c.score.closed} of 10 completed, ${c.score.wins} green`}>{Array.from({ length: 10 }, (_, i) => <i key={i} className={i < c.score.closed ? s.filled : ""} />)}</div>
        <div className={s.geometry}><span>Stop {c.spec.stop_bps}bp</span><span>Target {c.spec.target_bps}bp</span><span>Max hold {duration(c.spec.max_hold_seconds)}</span></div>
        {c.position && <div className={s.position}><span>Open paper position · {c.position.qty} units<br />{c.position.exit_reason ? `Exiting: ${words(c.position.exit_reason)}` : `Unrealized ${c.unrealized === null ? "unavailable" : money(c.unrealized)}`}</span><button disabled={busy} onClick={() => void act("close", c.id)}>Close paper position</button></div>}
        {c.entry && <p className={s.note}>Maker entry waiting for a later public trade to fill.</p>}
        {c.score.excluded > 0 && <p className={s.note}>{c.score.excluded} excluded trades · manual close, feed gap or unknown funding.</p>}
        {c.lineage?.parent && <p className={s.note}>Revision {c.lineage.generation} · <a href={`#strategy-${c.lineage.parent}`}>original retired version</a> · fresh 10-trade test.</p>}
        {c.state === "RETIRED" && <div className={s.review}><strong>Retired from paper trading</strong><p>{revisionStatus(c.revision?.status ?? "PENDING")}</p>{c.revision?.reason && <p>{c.revision.reason}</p>}{c.revision?.proposal && <p>Proposed: stop {c.revision.proposal.stop_bps}bp · target {c.revision.proposal.target_bps}bp · max hold {duration(c.revision.proposal.max_hold_seconds)}.</p>}{c.revision?.child && <a href={`#strategy-${c.revision.child}`}>View new paper version →</a>}</div>}
        {c.review.reason && <div className={s.review}><strong>Agent review</strong><p>{words(c.review.reason)}</p><dl><dt>News</dt><dd>{c.review.news?.verdict ?? "Waiting"}</dd><dt>Direction</dt><dd>{c.review.direction ? `${c.review.direction.side} / ${c.review.direction.action}` : "Waiting"}</dd><dt>Sizing</dt><dd>{c.review.sizing ? `${money(c.review.sizing.planned_loss)} planned loss` : "Waiting"}</dd><dt>Risk gate</dt><dd>{c.review.gate ? c.review.gate.approved ? "Approved · paper" : c.review.gate.reason : "Waiting"}</dd></dl></div>}
        <details><summary>Frozen rules · version {c.version}</summary><p>Match {c.spec.match} conditions:</p><ul>{c.spec.conditions.map((rule, i) => <li key={i}>{words(rule.feature)} {rule.op === "gt" ? ">" : "<"} {rule.value}</li>)}</ul><small>Created {when(c.created)}. Editing rules creates a new version and a new 10-trade test.</small></details>
      </article>)}</div>}
      <div className={s.sectionHead}><h2>Completed paper trades</h2><p>Green means P&amp;L above $0. Break-even is not a win.</p></div><div className={s.tableWrap}><table><thead><tr><th>Closed</th><th>Strategy / market</th><th>P&amp;L</th><th>Fees</th><th>Result</th><th>Chain ID</th></tr></thead><tbody>{trades.length ? trades.map(t => <tr key={t.id}><td>{when(t.closed)}</td><td>{t.name}<small>{t.market}</small></td><td className={t.net < 0 ? s.loss : s.good}>{money(t.net)}</td><td>{money(t.fees)}</td><td>{words(t.reason)}{!t.valid && <small>Excluded: {words(t.invalid_reason)}</small>}</td><td><code title={t.chain_id}>{t.chain_id.slice(0, 12)}</code></td></tr>) : <tr><td colSpan={6}>No completed trades yet.</td></tr>}</tbody></table></div>
      <details className={s.audit}><summary>Decision trail · latest 100 events</summary>{state.events.map(event => <details key={event.id}><summary>{when(event.ts)} · {words(event.stage)} · {event.chain_id.slice(0, 12)}</summary><pre>{JSON.stringify(event.data, null, 2)}</pre></details>)}</details>
      <div className={s.disclosure}><strong>How results are measured</strong><ul>{state.assumptions.map(a => <li key={a}>{a}</li>)}</ul><p>Six wins in ten is the first screening threshold. Net P&amp;L and loss size remain visible for the agents&apos; review. This version has no live activation control.</p></div>
    </>}
  </Container>;
}
