"use client";
// One shared connection to Yellow Pro's PUBLIC websocket (no auth, no account channels, no orders).
// Protocol: send {"id":1,"connect":{}} on open (else the server closes with 3501), then subscribe;
// a "{}" message is a ping and is answered with "{}"; messages may be newline-batched.
import { useEffect, useState } from "react";

export const YELLOW_API = "https://trade.api.yellow.pro";
const WS_URL = "wss://trade.api.yellow.pro/ws";
type Listener = (data: Record<string, unknown>) => void;
export type FeedStatus = "connecting" | "live" | "reconnecting";

class YellowFeed {
  private ws: WebSocket | null = null;
  private subs = new Map<string, Set<Listener>>();
  private statusListeners = new Set<(s: FeedStatus) => void>();
  private nextId = 2;
  private attempt = 0;
  private retry: ReturnType<typeof setTimeout> | undefined;
  private idle: ReturnType<typeof setTimeout> | undefined;
  private watchdog: ReturnType<typeof setInterval> | undefined;
  private lastMessage = 0;
  status: FeedStatus = "connecting";

  subscribe(channel: string, fn: Listener): () => void {
    clearTimeout(this.idle);
    let set = this.subs.get(channel);
    if (!set) { set = new Set(); this.subs.set(channel, set); this.send({ subscribe: { channel } }); }
    set.add(fn);
    this.ensure();
    return () => {
      const s = this.subs.get(channel); if (!s) return;
      s.delete(fn);
      if (!s.size) { this.subs.delete(channel); this.send({ unsubscribe: { channel } }); }
      if (!this.subs.size) this.idle = setTimeout(() => this.stop(), 5000);
    };
  }

  onStatus(fn: (s: FeedStatus) => void): () => void { this.statusListeners.add(fn); fn(this.status); return () => { this.statusListeners.delete(fn); }; }

  private setStatus(s: FeedStatus) { if (s === this.status) return; this.status = s; this.statusListeners.forEach(f => f(s)); }
  private ready() { return this.ws?.readyState === WebSocket.OPEN && this.status === "live"; }
  private send(body: Record<string, unknown>) { if (this.ready()) this.ws!.send(JSON.stringify({ id: this.nextId++, ...body })); }

  private ensure() {
    if (this.ws || this.retry || typeof WebSocket === "undefined") return;
    const ws = this.ws = new WebSocket(WS_URL);
    this.lastMessage = Date.now();
    ws.onopen = () => ws.send(JSON.stringify({ id: 1, connect: {} }));
    ws.onmessage = e => {
      if (ws !== this.ws || typeof e.data !== "string") return;
      this.lastMessage = Date.now();
      for (const line of e.data.split("\n")) {
        if (!line.trim()) continue;
        let m: Record<string, any>;
        try { m = JSON.parse(line); } catch { continue; }
        if (!Object.keys(m).length) { ws.send("{}"); continue; }
        if (m.id === 1 && m.connect) {
          this.attempt = 0; this.setStatus("live");
          for (const channel of this.subs.keys()) this.send({ subscribe: { channel } });
          continue;
        }
        const channel = m.push?.channel, data = m.push?.pub?.data;
        if (typeof channel === "string" && data && typeof data === "object") this.subs.get(channel)?.forEach(f => f(data));
      }
    };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (ws !== this.ws) return;
      this.ws = null;
      if (!this.subs.size) return;
      this.setStatus("reconnecting");
      this.retry = setTimeout(() => { this.retry = undefined; this.ensure(); }, Math.min(30000, 1000 * 2 ** Math.min(this.attempt++, 5)) + Math.random() * 500);
    };
    clearInterval(this.watchdog);
    // A silent socket is treated as dead; Yellow pings well inside this window.
    this.watchdog = setInterval(() => { if (this.ws && Date.now() - this.lastMessage > 25000) this.ws.close(); }, 5000);
  }

  private stop() {
    clearInterval(this.watchdog); clearTimeout(this.retry); this.retry = undefined;
    const ws = this.ws; this.ws = null;
    if (ws) { ws.onclose = null; ws.close(); }
    this.setStatus("connecting");
  }
}

let feed: YellowFeed | null = null;
export function yellowFeed(): YellowFeed { return feed ??= new YellowFeed(); }

export function useFeedStatus(): FeedStatus {
  const [s, setS] = useState<FeedStatus>("connecting");
  useEffect(() => yellowFeed().onStatus(setS), []);
  return s;
}

/** Live mark prices for the given Yellow perp markets. */
export function useMarks(markets: string[]): Record<string, number> {
  const key = [...new Set(markets.filter(x => /^[A-Z0-9]+USDT-PERP$/.test(x)))].sort().join(",");
  const [marks, setMarks] = useState<Record<string, number>>({});
  useEffect(() => {
    if (!key) return;
    let frame = 0; const pending: Record<string, number> = {};
    const flush = () => { frame = 0; setMarks(m => ({ ...m, ...pending })); };
    const offs = key.split(",").map(market => yellowFeed().subscribe(`public.mark_price.${market}`, d => {
      const p = Number(d.mark_price);
      if (!Number.isFinite(p) || p <= 0) return;
      pending[market] = p; if (!frame) frame = requestAnimationFrame(flush);
    }));
    return () => { offs.forEach(f => f()); cancelAnimationFrame(frame); };
  }, [key]);
  return marks;
}

// ---------------------------------------------------------------- public REST
export type MarketInfo = { market: string; base: string; precision: number };
const FALLBACK = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "SUI", "LINK", "HYPE", "TAO"].map(b => ({ market: `${b}USDT-PERP`, base: b, precision: 0 }));
let marketsPromise: Promise<MarketInfo[]> | null = null;
export function loadMarkets(): Promise<MarketInfo[]> {
  return marketsPromise ??= fetch(`${YELLOW_API}/perpetual/exchangeInfo`, { cache: "no-store", credentials: "omit" })
    .then(r => r.json())
    .then(d => {
      const list = (Array.isArray(d?.symbols) ? d.symbols : []).filter((s: any) => s.status === "TRADING" && /USDT-PERP$/.test(s.symbol))
        .map((s: any) => ({ market: s.symbol, base: s.base_asset || s.symbol.replace("USDT-PERP", ""), precision: Number(s.price_precision) || 0 }));
      const order = FALLBACK.map(f => f.base);
      return list.length ? list.sort((a: MarketInfo, b: MarketInfo) => (order.indexOf(a.base) + 1 || 99) - (order.indexOf(b.base) + 1 || 99)) : FALLBACK;
    })
    .catch(() => { marketsPromise = null; return FALLBACK; });
}
export function useMarkets(): MarketInfo[] {
  const [m, setM] = useState<MarketInfo[]>(FALLBACK);
  useEffect(() => { let alive = true; void loadMarkets().then(x => { if (alive) setM(x); }); return () => { alive = false; }; }, []);
  return m;
}

export type Ticker = { last: number; change: number };
export async function loadTicker(market: string): Promise<Ticker | null> {
  try {
    const t = await (await fetch(`${YELLOW_API}/ticker/24hr?symbol=${market}`, { cache: "no-store", credentials: "omit" })).json();
    const last = Number(t.last), change = parseFloat(String(t.priceChange));
    return Number.isFinite(last) ? { last, change: Number.isFinite(change) ? change : 0 } : null;
  } catch { return null; }
}
