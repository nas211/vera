"use client";
import type { BulkResearch } from "../../discovery/ResearchProgress";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import type { Row } from "./model";
export type AiCandidate = {
  id: string; state: string; version: string;
  spec: { name: string; market: string; side: string; max_hold_seconds: number; stop_bps: number; target_bps: number };
  score: { closed: number; wins: number; net: number };
  forward_score?: { closed: number; wins: number; net: number };
  validation?: { status: string; closed: number; minimum_trades: number; elapsed_seconds: number; minimum_seconds: number; expectancy: number | null; profit_factor: number | null; max_drawdown: number };
  review?: { reason?: string; next_retry_at?: number };
  expiry?: { reason: string };
  position: { qty: number; entry: number; opened: number; exit_reason: string } | null;
  unrealized: number | null;
  trades: { id: string; qty: number; entry: number; exit: number; net: number; closed: number; reason: string; valid: boolean; invalid_reason: string }[];
  revision?: { status: string; reason?: string; child?: string; proposal?: { stop_bps: number; target_bps: number; max_hold_seconds: number } };
};
export type ArchiveItem = { summary: { id: string; state: string; spec: AiCandidate["spec"]; screen: AiCandidate["score"]; forward: AiCandidate["score"] }; candidate: AiCandidate };
export type AiState = { bulk_research?: BulkResearch; mode: "paper"; live_enabled: false; running: boolean; kill: boolean; ai_ready: boolean; ai_error: string; feed_error: string; equity: number; realized: number; unrealized: number; fresh_markets: number; markets: number; candidates: AiCandidate[]; archived_versions?: number; review_context?: { ready: boolean; reason?: string; markets?: number }; risk_lock?: { day: string; reason: string } };
export function useDiscovery(session: Session | null, enabled: boolean) {
  const [snapshot, setSnapshot] = useState<{ identity: string; state: AiState } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<{identity: string; items: ArchiveItem[]; next: number | null} | null>(null);
  const [archiveError, setArchiveError] = useState("");
  const [archiveBusy, setArchiveBusy] = useState(false);
  const archiveSerial = useRef(0);
  const identity = enabled && session ? session.user.id + ":" + session.access_token : "";
  const current = useRef(identity); current.current = identity;
  const serial = useRef(0);
  const request = useCallback(async (action: "state" | "start" | "pause" | "close", candidate?: string) => {
    if (!identity || !session) return;
    const n = ++serial.current;
    const response = await fetch(`/api/discovery?action=${action}`, {
      method: action === "state" ? "GET" : "POST", cache: "no-store", signal: AbortSignal.timeout(15000),
      headers: { Authorization: `Bearer ${session.access_token}`, "Content-Type": "application/json" },
      body: action === "state" ? undefined : JSON.stringify(candidate ? { candidate } : {}),
    });
    const data = await response.json();
    if (current.current !== identity || n !== serial.current) return;
    if (!response.ok) throw new Error(data.error || "AI paper account unavailable.");
    if (data.mode !== "paper" || data.live_enabled !== false) throw new Error("Unexpected account mode.");
    setSnapshot({ identity, state: data }); setError("");
  }, [identity, session?.access_token]);
  useEffect(() => {
    setSnapshot(null); setError(""); setBusy(false);
    setHistory(null); setArchiveError(""); setArchiveBusy(false); archiveSerial.current++;
    if (!identity) return;
    let alive = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await request("state"); } catch (err) { if (alive) setError(err instanceof Error ? err.message : "AI paper account unavailable."); }
      if (alive) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); serial.current++; };
  }, [identity, request]);
  const act = async (action: "start" | "pause" | "close", candidate?: string) => {
    if (busy || !identity) return;
    setBusy(true);
    try { await request(action, candidate); } catch (err) { if (current.current === identity) setError(err instanceof Error ? err.message : "Action failed."); }
    finally { if (current.current === identity) setBusy(false); }
  };
  const loadArchive = async () => {
    if (!identity || !session || archiveBusy) return;
    const prior = history?.identity === identity ? history : null;
    if (prior?.next === null) return;
    const n = ++archiveSerial.current; setArchiveBusy(true); setArchiveError("");
    try {
      const response = await fetch(`/api/discovery?action=archive&offset=${prior?.next ?? 0}`, {headers: {Authorization: `Bearer ${session.access_token}`},cache: "no-store",signal: AbortSignal.timeout(15000)});
      const data = await response.json();
      if (current.current !== identity || n !== archiveSerial.current) return;
      if (!response.ok || data.mode !== "paper" || data.live_enabled !== false || !Array.isArray(data.items)) throw new Error("Archived paper history unavailable.");
      const items = new Map([...(prior?.items ?? []), ...data.items].map((x: ArchiveItem) => [x.summary.id,x]));
      setHistory({identity, items: [...items.values()], next: data.next_offset});
    } catch (err) { if (current.current === identity && n === archiveSerial.current) setArchiveError(err instanceof Error ? err.message : "Archived paper history unavailable."); }
    finally { if (current.current === identity && n === archiveSerial.current) setArchiveBusy(false); }
  };
  return { state: snapshot?.identity === identity ? snapshot.state : null, error, busy, act, archive: history?.identity === identity ? history : null, loadArchive, archiveError, archiveBusy };
}
export type AiController = ReturnType<typeof useDiscovery>;
export function discoveryRows(state: AiState | null): { open: Row[]; closed: Row[] } {
  const open: Row[] = [], closed: Row[] = [];
  for (const c of state?.candidates ?? []) {
    const base = { market: c.spec.market, side: c.spec.side, exchange: "AI Paper · simulated", mark: null };
    if (c.position) {
      const p = c.position;
      open.push({ ...base, id: c.id, qty: p.qty, entry: p.entry, exit: null, notional: p.qty * p.entry, pnl: c.unrealized, time: p.opened, result: p.exit_reason ? "CLOSE_PENDING" : "Open", kind: "Entry" });
    }
    for (const t of c.trades) closed.push({ ...base, id: t.id, qty: t.qty ?? null, entry: t.entry ?? null, exit: t.exit ?? null, notional: t.qty != null && t.entry != null ? t.qty * t.entry : null, pnl: t.net, time: t.closed, result: t.valid ? t.reason : `${t.reason} · Excluded: ${t.invalid_reason}`, kind: "Exit" });
  }
  return { open, closed };
}
