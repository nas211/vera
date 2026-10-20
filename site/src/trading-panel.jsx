import React, { useEffect, useRef, useState } from "react";
import { encodeFunctionData, parseEther } from "viem";
import { networkConfig, chain } from "./network.js";
import { prepareTransaction, submitTransaction, walletError } from "./transaction.js";

const apiBase = (window.VELA_BACKEND_URL || "").replace(/\/$/, "");
const usd = value => value === null || value === undefined ? "Unavailable" : Number(value).toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:4});
const short = value => `${value.slice(0, 8)}…${value.slice(-6)}`;
const explorer = hash => `${networkConfig.blockExplorerUrls[0]}/tx/${hash}`;
async function get(path) {
  const response = await fetch(`${apiBase}${path}`, {signal:AbortSignal.timeout(10000), cache:"no-store"});
  if (!response.ok) throw new Error("Service unavailable");
  return response.json();
}

export function TradingPanel({address, provider, onDogeOS, onSettled, embedded=false}) {
  const [config, setConfig] = useState(null);
  const [strategies, setStrategies] = useState([]);
  const [selected, setSelected] = useState(1);
  const [amount, setAmount] = useState("5");
  const [price, setPrice] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [board, setBoard] = useState(null);
  const [error, setError] = useState("");
  const [serviceError, setServiceError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const identity = useRef(null);
  const previousStatus = useRef(null);
  const current = `${address.toLowerCase()}:${onDogeOS}:${provider ? "connected" : "none"}`;
  const token = React.useMemo(() => ({current, provider}), [current, provider]);
  identity.current = token;
  const storageKey = `vela:${chain.id}:${networkConfig.deployment?.vault}:${address.toLowerCase()}`;
  const user = snapshot?.user;
  const status = user?.status || "idle";
  const active = status === "active", stopping = status === "stopping";

  useEffect(() => {
    setSnapshot(null); setError(""); setBusy(false); previousStatus.current = null;
    try {setPending(JSON.parse(sessionStorage.getItem(storageKey) || "null"));} catch {setPending(null);}
    let cancelled = false, running = false;
    const valid = () => !cancelled && identity.current === token;
    async function refresh() {
      if (running || !valid()) return;
      running = true;
      try {
        const [c, s, p, a, b] = await Promise.all([
          get("/api/config"), get("/api/strategies"), get("/api/prices"),
          get(`/api/users/${address}`), get("/api/leaderboard")
        ]);
        if (!valid()) return;
        if (c.chain.chainId !== chain.id || c.mode !== "testnet-paper" ||
            c.deployment?.vault?.toLowerCase() !== networkConfig.deployment?.vault?.toLowerCase()) {
          throw new Error("Network/deployment mismatch");
        }
        setConfig(c);setStrategies(s);setPrice(p.find(x=>x.symbol==="DOGE/USD" && x.safe)?.value || null);
        setSnapshot(a);setBoard(b);setServiceError("");
        const next = a.user?.status;
        if (next === "settled" && previousStatus.current !== "settled") onSettled?.();
        previousStatus.current = next;
        let saved;
        try {saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");} catch {}
        if (saved && ((saved.action === "start" && a.user?.start_tx?.toLowerCase() === saved.hash.toLowerCase()) ||
                      (saved.action === "stop" && a.user?.stop_tx?.toLowerCase() === saved.hash.toLowerCase()))) {
          sessionStorage.removeItem(storageKey);setPending(null);
        } else if (saved && onDogeOS) {
          const receipt = await provider.request({method:"eth_getTransactionReceipt", params:[saved.hash]});
          if (valid() && receipt && BigInt(receipt.status) === 0n) {
            sessionStorage.removeItem(storageKey);setPending(null);setError("Transaction reverted. Your wallet was not started/stopped; you can retry.");
          }
        }
      } catch {
        if (valid()) setServiceError("Live service unavailable. New starts are paused; any submitted transaction remains on-chain.");
      } finally {running = false;}
    }
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => {cancelled = true;clearInterval(timer);};
  }, [token, address, storageKey]);

  async function send(action) {
    if (busy || pending || !onDogeOS || !config?.deployment) return;
    const chosen = identity.current;
    setBusy(true);setError("");
    try {
      if (BigInt(await provider.request({method:"eth_chainId"})) !== BigInt(chain.id)) throw new Error("Switch to DogeOS first.");
      const accounts = await provider.request({method:"eth_accounts"});
      if (!accounts.some(a=>a.toLowerCase()===address.toLowerCase()) || identity.current !== chosen) throw new Error("Wallet account changed.");
      if (action === "start" && (!config.ready || serviceError)) throw new Error("Wait for the portfolio service to be ready.");
      const value = action === "start" ? parseEther(amount) : 0n;
      if (action === "start" && value <= 0n) throw new Error("Enter a positive DOGE amount.");
      const data = encodeFunctionData({abi:config.abi, functionName:action==="start" ? "startTrading" : "stopTrading", args:action==="start" ? [BigInt(selected)] : []});
      if (identity.current !== chosen) return;
      const transaction = await prepareTransaction({
        from:address, to:config.deployment.vault, chainId:`0x${chain.id.toString(16)}`, data,
        value:`0x${value.toString(16)}`
      }, config.abi);
      if (identity.current !== chosen) return;
      if (BigInt(await provider.request({method:"eth_chainId"})) !== BigInt(chain.id)) throw new Error("Wallet network changed. Reconnect to DogeOS.");
      const remember = hash => {
        if(!hash){sessionStorage.removeItem(storageKey);if(identity.current===chosen)setPending(null);return;}
        const entry={action,hash};
        sessionStorage.setItem(storageKey,JSON.stringify(entry));
        if(identity.current===chosen)setPending(entry);
      };
      const hash = await submitTransaction(provider, transaction, embedded, remember);
      // Preserve a submitted hash for its original wallet even if the account changed.
      if (typeof hash !== "string" || !/^0x[0-9a-f]{64}$/i.test(hash)) throw new Error("Wallet returned an invalid transaction hash.");
      const entry = {action, hash};
      sessionStorage.setItem(storageKey, JSON.stringify(entry));
      if (identity.current === chosen) setPending(entry);
    } catch (err) {
      if (identity.current === chosen) setError(walletError(err));
    } finally {
      if (identity.current === chosen) setBusy(false);
    }
  }

  return <div className="trading-workspace" aria-label="Vela portfolio dashboard">
    <div className="trading-heading"><div><p className="stage-eyebrow">YOUR DOGE. ONE AGENT TEAM.</p><h2>Make your <em>next move.</em></h2></div></div>
    <p className="trading-intro">Choose your amount. Research, signals and risk management come together in one portfolio, with every closed trade recorded on-chain.</p>
    {serviceError && <p role="status" className="trading-notice">{serviceError}</p>}
    {!config?.deployment && <p className="trading-notice">Waiting for the deployed vault and backend configuration.</p>}
    <div className="strategy-grid" role="radiogroup" aria-label="Choose a strategy">
      {strategies.map(s=><button key={s.id} role="radio" aria-checked={selected===s.id} disabled={!s.available || active || stopping || !!pending || busy}
        className={`strategy-card ${selected===s.id ? "selected" : ""}`} onClick={()=>setSelected(s.id)}>
        <span>{s.agent} / {s.risk}</span><strong>{s.name}</strong><small>{s.market}</small><p>{s.description}</p>
        <small>{s.availabilityReason}</small>
      </button>)}
    </div>
    {!active && !stopping && !pending && <div className="start-row">
      <label htmlFor="doge-amount">Amount in DOGE<input id="doge-amount" inputMode="decimal" type="number" min="0.0001" step="0.1" value={amount} onChange={e=>setAmount(e.target.value)} disabled={busy}/></label>
      <span className="usd-estimate">{price ? `≈ $${usd(Number(amount)*Number(price))}` : "Waiting for a trusted DOGE price"}</span>
      <button id="start-trading" className="stage-primary" disabled={busy || !onDogeOS || !config?.ready || !!serviceError || !price || !strategies.find(s=>s.id===selected)?.available} onClick={()=>send("start")}>{busy ? "Confirm in your wallet…" : "START EARNING ↗"}</button>
    </div>}
    <div className="session-status" role="status" aria-live="polite">
      {pending?.action==="start" ? "Converting DOGE → USDC · awaiting confirmed session" :
       pending?.action==="stop" || stopping ? "Closing positions · operator settlement follows automatically" :
       active ? "Agent active · waiting for valid signals or managing positions" :
       status==="settled" ? "DOGE returned" : "Ready when you are"}
      {pending && <a href={explorer(pending.hash)} target="_blank" rel="noopener noreferrer">View submitted transaction ↗</a>}
      {status==="settled" && user?.settlement_tx && <a id="settlement-link" href={explorer(user.settlement_tx)} target="_blank" rel="noopener noreferrer">{usd(Number(user.doge_out)/1e18)} DOGE · View settlement ↗</a>}
    </div>
    {active && <button id="stop-trading" className="stage-secondary stop-trading" disabled={busy || !!pending || !onDogeOS} onClick={()=>send("stop")}>{busy ? "Confirm stop in your wallet…" : "STOP TRADING"}</button>}
    {error && <div className="trading-notice" role="alert">{error}{/email wallet/.test(error) && <p><a href="/mydoge.html?wallet=metamask">Connect MetaMask ↗</a> · <a href="https://www.mydoge.com/" target="_blank" rel="noopener noreferrer">Open this site inside MyDoge ↗</a></p>}</div>}
    <p className="trading-footnote">One approval to start. One approval to stop. Leave DOGE in your wallet for gas.</p>
    {user && <>
      <div className="metric-grid">
        <div><small>Current session equity</small><strong>{usd(user.equity)} <span>TUSDC</span></strong></div>
        <div><small>DOGE equivalent</small><strong>{usd(user.dogeEquivalent)} <span>DOGE</span></strong></div>
        <div><small>Session P&amp;L · fees included</small><strong className={Number(user.pnl)<0 ? "negative" : "positive"}>{usd(user.pnl)} <span>USD</span></strong><small>{usd(user.pnlPercent)}%</small></div>
      </div>
      <section className="dashboard-section"><h3>Session equity <small>TUSDC · latest 500 observations</small></h3><EquityCurve rows={snapshot.equity}/></section>
      <section className="dashboard-section"><h3>Open positions</h3><div className="table-scroll"><table><thead><tr><th>Market</th><th>Side</th><th>Size</th><th>Entry</th><th>Stop</th><th>Target</th><th>P&amp;L</th></tr></thead><tbody>
        {snapshot.positions.map(p=><tr key={p.id}><td>{p.symbol}</td><td>{p.side}</td><td>{usd(p.qty)}</td><td>{usd(p.entry)}</td><td>{usd(p.stop)}</td><td>{usd(p.target)}</td><td>{usd(p.unrealized)}</td></tr>)}
        {!snapshot.positions.length && <tr><td colSpan="7">No open positions. Agents wait for a valid signal and trustworthy prices.</td></tr>}
      </tbody></table></div></section>
      <section className="dashboard-section"><h3>Trade history <small>Current session · latest 100 closes</small></h3><div className="table-scroll"><table><thead><tr><th>Closed</th><th>Market</th><th>Side</th><th>Net P&amp;L</th><th>On-chain log</th></tr></thead><tbody>
        {snapshot.trades.map(t=><tr key={t.id}><td>{new Date(t.closed*1000).toLocaleTimeString()}</td><td>{t.symbol}{t.reason==="engineering_smoke" && <small> · engineering check</small>}</td><td>{t.side}</td><td className={Number(t.pnl)<0 ? "negative" : "positive"}>{usd(t.pnl)}</td><td>{t.tx_hash ? <a href={explorer(t.tx_hash)} target="_blank" rel="noopener noreferrer">{short(t.tx_hash)} ↗</a> : t.chain_status}</td></tr>)}
        {!snapshot.trades.length && <tr><td colSpan="5">No closed trades in this session.</td></tr>}
      </tbody></table></div></section>
    </>}
    <section className="dashboard-section"><h3>Agent leaderboard</h3><p className="trading-footnote">All-time closed strategy trades across all users; engineering checks excluded. Net P&amp;L and win rate use the same trade set; funding is ignored.</p><div className="table-scroll"><table><thead><tr><th>Agent</th><th>Net P&amp;L</th><th>Win rate</th><th>Trades</th><th>Users</th></tr></thead><tbody>
      {board?.rows.map(r=><tr key={r.strategy}><td>{r.name}</td><td>{usd(r.pnl)}</td><td>{usd(r.winRate*100)}%</td><td>{r.trades}</td><td>{r.users}</td></tr>)}
      {!board?.rows.length && <tr><td colSpan="5">Results will appear after trades close.</td></tr>}
    </tbody></table></div></section>
    <details className="trading-footnote"><summary>How this portfolio works</summary><p>Trades are simulated with live prices, 0.05% fees per side and 0.02% slippage. TUSDC is simulated accounting, not real USDC. DOGE settlement is capped by the vault’s available funds. Each position has a fee-inclusive stop trigger budget of at most $2, preserving stricter limits; price gaps and slippage can exceed it.</p></details>
  </div>;
}

function EquityCurve({rows}) {
  if (!rows?.length) return <p className="empty-chart">The equity curve starts with your portfolio session.</p>;
  const values=rows.map(r=>Number(r.equity)), min=Math.min(...values), max=Math.max(...values);
  const spread=Math.max(max-min, .001), width=760, height=160;
  const points=values.map((v,i)=>`${20+i*(width-40)/Math.max(1,values.length-1)},${height-20-(v-min)*(height-40)/spread}`).join(" ");
  return <svg className="equity-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Session equity in TUSDC: ${usd(values[0])} to ${usd(values.at(-1))}`}>
    <line x1="20" y1="140" x2="740" y2="140" stroke="#45483e"/><polyline points={points} fill="none" stroke="#dfc18b" strokeWidth="2.5"/>
    <text x="20" y="14" fill="#afb3a5" fontSize="12">{usd(max)} TUSDC</text><text x="20" y="158" fill="#afb3a5" fontSize="12">{usd(min)}</text>
  </svg>;
}
