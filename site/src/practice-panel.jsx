import React, {useEffect, useState} from "react";

const base=(window.VELA_BACKEND_URL||"").replace(/\/$/,"");
const money=v=>v==null?"—":Number(v).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:4});
function sessionToken(){
  const name="vela:practice:v1";
  let token=localStorage.getItem(name);
  if(!/^[0-9a-f]{64}$/.test(token||"")){
    token=Array.from(crypto.getRandomValues(new Uint8Array(32)),x=>x.toString(16).padStart(2,"0")).join("");
    localStorage.setItem(name,token);
  }
  return token;
}
async function request(path,token,body){
  const r=await fetch(base+path,{method:body===undefined?"GET":"POST",
    headers:{"Content-Type":"application/json","X-Vela-Practice":token},
    body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000),cache:"no-store"});
  const value=await r.json();
  if(!r.ok)throw new Error(typeof value.detail==="string"?value.detail:"Portfolio service unavailable");
  return value;
}

export function PracticePanel(){
  const [token]=useState(sessionToken);
  const [data,setData]=useState(null),[board,setBoard]=useState(null),[price,setPrice]=useState(null);
  const [amount,setAmount]=useState("1000"),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [online,setOnline]=useState(false);
  async function refresh(){
    const [a,b,p]=await Promise.all([request("/api/practice",token),request("/api/practice/leaderboard",token),request("/api/prices",token)]);
    setData(a);setBoard(b);setPrice(p.find(x=>x.symbol==="DOGE/USD"&&x.safe)?.value);
    setOnline(true);
  }
  useEffect(()=>{
    let done=false,running=false;
    async function poll(){
      if(done||running)return;running=true;
      try{await refresh();}catch{if(!done)setOnline(false);}finally{running=false;}
    }
    poll();const id=setInterval(poll,3000);
    return()=>{done=true;clearInterval(id);};
  },[token]);
  async function act(action){
    if(busy)return;
    setBusy(true);setError("");
    try{
      const next=await request("/api/practice/"+action,token,action==="start"?{amountDoge:amount}:{});
      setData(next);await refresh();
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  const user=data?.user,active=user?.status==="active",stopping=user?.status==="stopping";
  const decision=data?.decision,answer=decision?.answer;
  return <div id="ai-portfolio" className="trading-workspace" aria-label="Vela AI portfolio">
    <div className="trading-heading"><div><p className="stage-eyebrow">LIVE MARKETS. ONE AGENT TEAM.</p><h2>Put your agents <em>to work.</em></h2></div></div>
    <p className="trading-intro">Research, signals and risk management in one portfolio. Follow every decision, position and result as prices move.</p>
    <p className="trading-footnote practice-disclosure"><strong>Practice portfolio.</strong> Your amount is virtual. No wallet funds move, no gas is charged, and simulated returns cannot be withdrawn. Saved in this browser.</p>
    <div className="strategy-grid"><div className="strategy-card selected">
      <span>Research · Review · Direction · Risk</span><strong>Vela AI Agents</strong>
      <small>DOGE · BTC · ETH · SOL</small><p>AI researches the market, a second stage reviews the proposal, and the direction agent decides whether to enter. The risk engine sizes and protects each position.</p>
    </div></div>
    {!online && <p className="trading-notice" role="status">Connecting to the portfolio service… Existing positions stay saved.</p>}
    {online&&!data?.ready&&<p className="trading-notice" role="status">{data?.reason}</p>}
    {!active&&!stopping&&<div className="start-row">
      <label htmlFor="practice-amount">Virtual amount in DOGE<input id="practice-amount" inputMode="decimal" type="number" min=".01" max="100000" value={amount} onChange={e=>setAmount(e.target.value)} disabled={busy}/></label>
      <span className="usd-estimate">{price?`≈ $${money(Number(amount)*Number(price))}`:"Waiting for DOGE price"}</span>
      <button id="practice-start" className="stage-primary" disabled={busy||!online||!data?.ready||!(Number(amount)>0)} onClick={()=>act("start")}>{busy?"Starting…":"START AI TRADING ↗"}</button>
    </div>}
    <div className="session-status" role="status" aria-live="polite">{stopping?"Closing positions at the current market price…":active?data.positions.length?"Agent active · managing your position":"Agent active · researching and waiting for an approved signal":user?.status==="settled"?"Portfolio stopped · results saved":"Ready when you are"}</div>
    {active&&<button id="practice-stop" className="stage-secondary stop-trading" disabled={busy} onClick={()=>act("stop")}>{busy?"Stopping…":"STOP TRADING"}</button>}
    {error&&<p className="trading-notice" role="alert">{error}</p>}
    {decision&&<section className="dashboard-section"><h3>Latest agent decision <small>{decision.phase} · {new Date(decision.at*1000).toLocaleTimeString()}</small></h3>
      <p>{answer?.reason||"Research completed."}</p>
      {answer?.strategies?.[0]&&<p>{answer.strategies[0].name} · {answer.strategies[0].market} · {answer.strategies[0].side}</p>}
    </section>}
    {user&&<>
      <div className="metric-grid">
        <div><small>Session equity · simulated USD</small><strong>{money(user.equity)}</strong></div>
        <div><small>DOGE equivalent · virtual</small><strong>{money(user.dogeEquivalent)}</strong></div>
        <div><small>Session P&amp;L · trading fees included</small><strong className={Number(user.pnl)<0?"negative":"positive"}>{money(user.pnl)} <span>USD</span></strong><small>{money(user.pnlPercent)}%</small></div>
      </div>
      <section className="dashboard-section"><h3>Session equity <small>Simulated USD · latest 500 observations</small></h3><Curve rows={data.equity}/></section>
      <section className="dashboard-section"><h3>Open positions</h3><div className="table-scroll"><table><thead><tr><th>Market</th><th>Side</th><th>Size</th><th>Entry</th><th>Stop</th><th>Target</th><th>P&amp;L</th></tr></thead><tbody>
        {data.positions.map(p=><tr key={p.id}><td>{p.symbol}</td><td>{p.side}</td><td>{money(p.qty)}</td><td>{money(p.entry)}</td><td>{money(p.stop)}</td><td>{money(p.target)}</td><td>{money(p.unrealized)}</td></tr>)}
        {!data.positions.length&&<tr><td colSpan="7">No open position. Agents enter only when a reviewed signal and fresh prices are available.</td></tr>}
      </tbody></table></div></section>
      <section className="dashboard-section"><h3>Trade history <small>Current session · latest 100 closes</small></h3><div className="table-scroll"><table><thead><tr><th>Closed</th><th>Market</th><th>Side</th><th>Entry</th><th>Exit</th><th>Fees</th><th>Net P&amp;L</th></tr></thead><tbody>
        {data.trades.map(t=><tr key={t.id}><td>{new Date(t.closed*1000).toLocaleTimeString()}</td><td>{t.symbol}</td><td>{t.side}</td><td>{money(t.entry)}</td><td>{money(t.exit)}</td><td>{money(t.fees)}</td><td className={Number(t.pnl)<0?"negative":"positive"}>{money(t.pnl)}</td></tr>)}
        {!data.trades.length&&<tr><td colSpan="7">Closed trades will appear here.</td></tr>}
      </tbody></table></div></section>
    </>}
    <section className="dashboard-section"><h3>Agent leaderboard</h3><p className="trading-footnote">All-time closed practice trades across all users. Net P&amp;L, win rate and trade count use the same trades; engineering checks are excluded.</p><div className="table-scroll"><table><thead><tr><th>Agent</th><th>Net P&amp;L</th><th>Win rate</th><th>Trades</th><th>Users</th></tr></thead><tbody>
      {board?.rows.map(r=><tr key={r.name}><td>{r.name}</td><td>{money(r.pnl)}</td><td>{money(r.winRate*100)}%</td><td>{r.trades}</td><td>{r.users}</td></tr>)}
      {!board?.rows.length&&<tr><td colSpan="5">Results appear after trades close.</td></tr>}
    </tbody></table></div></section>
    <details className="trading-footnote"><summary>Execution and risk</summary><p>Simulated fills use live market prices, 0.02% slippage and 0.05% fees per side. Maximum leverage is 3×. Every position has a stop-trigger budget of at most $2 including fees; gaps and slippage can exceed it. Funding is ignored. Practice records are stored in Vela’s database, not on-chain. The separate vault deposit flow is paused.</p></details>
  </div>;
}
function Curve({rows}){
  if(!rows?.length)return <p className="empty-chart">The equity curve starts with your portfolio.</p>;
  const v=rows.map(r=>Number(r.equity)),lo=Math.min(...v),hi=Math.max(...v),span=Math.max(hi-lo,.001);
  const points=v.map((x,i)=>`${20+i*720/Math.max(1,v.length-1)},${140-(x-lo)*110/span}`).join(" ");
  return <svg className="equity-chart" viewBox="0 0 760 170" role="img" aria-label={`Session equity: ${money(v[0])} to ${money(v.at(-1))} simulated USD`}>
    <line x1="20" y1="140" x2="740" y2="140" stroke="#45483e"/><polyline points={points} fill="none" stroke="#dfc18b" strokeWidth="2.5"/>
    <text x="20" y="16" fill="#afb3a5" fontSize="12">{money(hi)} USD</text><text x="20" y="163" fill="#afb3a5" fontSize="12">{money(lo)} USD</text>
  </svg>;
}
