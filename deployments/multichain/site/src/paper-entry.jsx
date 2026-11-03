import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

const usd = (value, digits=2) => value === null || value === undefined ? "—" : (Math.abs(Number(value))<.5*10**-digits?0:Number(value)).toLocaleString("en-US", {style:"currency",currency:"USD",minimumFractionDigits:digits,maximumFractionDigits:digits});
const num = (value, digits=4) => value === null || value === undefined ? "—" : Number(value).toLocaleString("en-US", {maximumFractionDigits:digits});
const percent = value => value == null ? "—" : `${num(Number(value)*100,5)}%`;
const positive = value => Number(value)>=0 ? "positive" : "negative";
const id = () => crypto.randomUUID();
function sessionToken(){
  const key="vela:paper:session:v1";
  let value=localStorage.getItem(key);
  if(!/^[a-f0-9]{64}$/.test(value||"")){
    value=Array.from(crypto.getRandomValues(new Uint8Array(32)),x=>x.toString(16).padStart(2,"0")).join("");
    localStorage.setItem(key,value);
  }
  return value;
}
async function request(path,token,body){
  const response=await fetch(`/api/paper/${path}`,{
    method:body===undefined?"GET":"POST",signal:AbortSignal.timeout(12000),
    headers:{"Content-Type":"application/json","X-ADP-Client":"web",...(token?{"X-Vela-Paper":token}:{})},
    ...(body===undefined?{}:{body:JSON.stringify(body)})
  });
  if(!response.ok){
    const data=await response.json().catch(()=>({}));
    throw new Error(typeof data.error==="string"?data.error:typeof data.detail==="string"?data.detail:`Request unavailable (${response.status})`);
  }
  return response.json();
}

function Chart({rows,field="c",label="Market price",currency=true}){
  if(!rows?.length)return <div className="paper-chart-empty">Waiting for observed market history<span>No generated prices or performance.</span></div>;
  const values=rows.map(r=>Number(r[field])).filter(Number.isFinite);
  if(!values.length)return null;
  const lo=Math.min(...values),hi=Math.max(...values),span=Math.max(hi-lo,Math.abs(hi)*.00001,.00001);
  const points=values.map((v,i)=>[16+i*784/Math.max(1,values.length-1),166-(v-lo)/span*132]);
  const line=points.map(([x,y],i)=>`${i?"L":"M"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  return <svg className="paper-chart" viewBox="0 0 820 210" role="img" aria-label={`${label}: ${usd(values[0])} to ${usd(values.at(-1))}`}>
    {[35,100,166].map(y=><line key={y} x1="16" x2="800" y1={y} y2={y} stroke="#31362e" strokeDasharray="3 6"/>)}
    <path d={`${line} L800,184 L16,184 Z`} fill="#dfc18b" opacity=".045"/>
    <path d={line} fill="none" stroke="#dfc18b" strokeWidth="2.2"/>
    <circle cx={points.at(-1)[0]} cy={points.at(-1)[1]} r="3.5" fill="#dfc18b"/>
    <text x="16" y="18" fill="#b7b8aa" fontSize="11">{currency?usd(hi):num(hi)}</text>
    <text x="16" y="204" fill="#838a7c" fontSize="11">{new Date(rows[0].ts*1000).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}</text>
    <text x="800" y="204" textAnchor="end" fill="#838a7c" fontSize="11">{new Date(rows.at(-1).ts*1000).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}</text>
  </svg>;
}

function App(){
  const [token]=useState(sessionToken);
  const [data,setData]=useState(null),[account,setAccount]=useState(null),[symbol,setSymbol]=useState("BTC/USD");
  const [side,setSide]=useState("long"),[type,setType]=useState("market"),[notional,setNotional]=useState("100");
  const [leverage,setLeverage]=useState("2"),[limit,setLimit]=useState(""),[stop,setStop]=useState("50"),[target,setTarget]=useState("75");
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(""),[analysis,setAnalysis]=useState(null);
  const [connected,setConnected]=useState(false),[accountOnline,setAccountOnline]=useState(false),[clock,setClock]=useState(Date.now());
  const [tab,setTab]=useState("positions"),[menu,setMenu]=useState(false);
  const received=useRef(0),alive=useRef(true),analysisGeneration=useRef(0);

  useEffect(()=>{
    let stopped=false,socket,retry,timer,poll,attempt=0;
    alive.current=true;
    async function load(){
      try{
        const result=await request("session",token,{});
        if(stopped)return;
        setAccount(result);setAccountOnline(true);
        if(result.preferences?.symbol)setSymbol(result.preferences.symbol);
        if(result.preferences?.leverage)setLeverage(result.preferences.leverage);
      }catch(e){if(!stopped){setMessage(e.message);setAccountOnline(false);}}
    }
    function open(){
      if(stopped)return;
      socket=new WebSocket(`${location.protocol==="https:"?"wss:":"ws:"}//${location.host}/api/paper/stream`);
      socket.onmessage=e=>{
        try{const d=JSON.parse(e.data);if(d.mode!=="PAPER"||d.liveEnabled)throw Error();
          received.current=Date.now();setData(d);setConnected(true);attempt=0;
        }catch{setMessage("Unexpected execution environment; trading paused.");setConnected(false);socket.close();}
      };
      socket.onerror=()=>socket.close();
      socket.onclose=()=>{if(!stopped){setConnected(false);retry=setTimeout(open,Math.min(15000,1000*2**attempt++));}};
    }
    async function refresh(){
      try{const a=await request("account",token);if(!stopped){setAccount(a);setAccountOnline(true);}}
      catch{if(!stopped)setAccountOnline(false);}
      if(!stopped)poll=setTimeout(refresh,1000);
    }
    load().then(()=>{if(!stopped)refresh();});open();
    timer=setInterval(()=>setClock(Date.now()),1000);
    return()=>{stopped=true;alive.current=false;clearTimeout(retry);clearTimeout(poll);clearInterval(timer);socket?.close();};
  },[token]);
  const market=data?.markets?.find(m=>m.symbol===symbol);
  const transport=connected&&clock-received.current<4000;
  const fresh=Boolean(transport&&market?.fresh&&accountOnline&&!data?.error&&data?.serverTime-data?.engineAt<3);
  const config=data?.config;
  const prefs=account?.preferences;

  async function act(work){
    if(busy)return;
    setBusy(true);setMessage("");
    try{await work();const a=await request("account",token);if(alive.current)setAccount(a);}
    catch(e){if(alive.current)setMessage(e.message);}
    finally{if(alive.current)setBusy(false);}
  }
  async function choose(value){
    setSymbol(value);setAnalysis(null);setLimit("");analysisGeneration.current++;
    if(account)await act(()=>request("agent",token,{symbol:value,leverage,autonomous:Boolean(prefs?.autonomous)}));
  }
  async function analyze(){
    const generation=++analysisGeneration.current;
    await act(async()=>{
      const a=await request(`analysis/${symbol.split("/")[0]}`);
      if(generation===analysisGeneration.current)setAnalysis(a);
      if(data?.modelReady)await request("agent",token,{symbol,leverage,autonomous:true});
    });
  }
  function submit(e){
    e.preventDefault();
    act(async()=>{
      await request("orders",token,{clientId:id(),symbol,side,type,notional,leverage,stopBps:stop,targetBps:target,
        ...(type==="limit"?{limitPrice:limit}:{}),riskBudget:config.maxLossBudget});
      setMessage("Paper order queued. The position appears after simulated execution latency.");setTab("positions");
    });
  }
  const answer=account?.decision?.answer;
  const decisionText=answer?.reason||answer?.summary||answer?.action||answer?.verdict;
  const displayOrders=account?.orders||[];
  const pending=displayOrders.filter(o=>o.status==="pending");

  return <>
    <header className="nav-wrap"><nav className="nav shell" aria-label="Main navigation">
      <a className="brand" href="/" aria-label="Vela home"><svg viewBox="0 0 30 34" aria-hidden="true"><path d="M0 2h10l7 18-4.5 12L0 2Z" fill="currentColor"/><path d="M20 2h10L18 32h-9L20 2Z" fill="currentColor" opacity=".55"/></svg>vela<span className="brand-period">.</span></a>
      <div className={`nav-links ${menu?"open":""}`} id="paper-navigation"><a href="/paper" aria-current="page">AI Trading</a><a href="/borrow">Lending &amp; Borrowing <small className="nav-soon">SOON</small></a><a href="/earn">Staking</a></div>
      <span className="paper-badge">PAPER TRADING</span><button className="menu-toggle" aria-label="Toggle navigation" aria-expanded={menu} aria-controls="paper-navigation" onClick={()=>setMenu(!menu)}><span/><span/></button>
    </nav></header>
    <main className="paper-shell shell" id="main">
      <div className="paper-heading"><div><div className="eyebrow">VELA / YOUR INTELLIGENT TRADING DESK</div><h1>Real markets.<br className="paper-mobile-break"/> Room to <em>explore.</em></h1><p>Live prices. Virtual capital. Every decision in view.</p></div><div className={`feed-status ${fresh?"positive":"muted"}`} role="status"><i className={fresh?"live-dot":"offline-dot"}/>{fresh?"Market data live":transport?"Waiting for fresh market data":"Reconnecting to market data"}<small>LIVE execution disabled · No wallet required</small></div></div>

      <section className="paper-metrics" aria-label="Simulated portfolio performance">
        <Metric title="Paper equity" value={usd(account?.equity)} note="Virtual USD · no deposits"/>
        <Metric title="All-time net P&L" value={usd(account?.pnl)} note="Realized + unrealized · funding included" tone={positive(account?.pnl)}/>
        <Metric title="Unrealized P&L" value={usd(account?.unrealized)} note={`${account?.positions?.length||0} open positions · marked to market`} tone={positive(account?.unrealized)}/>
        <Metric title="Available balance" value={usd(account?.available)} note="After open position margin"/>
      </section>

      <div className="paper-workspace">
        <section className="paper-card paper-market" aria-label="Market overview">
          <div className="market-tabs" role="group" aria-label="Select perpetual market">{["BTC","ETH","SOL"].map(s=>{
            const m=data?.markets?.find(m=>m.symbol===s+"/USD");return <button key={s} className={symbol===s+"/USD"?"selected":""} aria-pressed={symbol===s+"/USD"} onClick={()=>choose(s+"/USD")} disabled={busy}>
              <span className={`asset-icon asset-${s.toLowerCase()}`}>{s==="BTC"?"₿":s==="ETH"?"Ξ":"◎"}</span><span><strong>{s}<small>PERP</small></strong><span>{m?usd(m.price,s==="SOL"?3:2):"Connecting…"}</span></span>
            </button>;
          })}</div>
          <div className="market-head"><div><span className="paper-label">{symbol.split("/")[0]} PERPETUAL</span><h2>{usd(market?.price,market?.tick==="0.001"?3:2)}</h2></div><div><span className={`market-change ${positive(market?.change)}`}>{market?`${Number(market.change)>0?"+":""}${num(market.change,2)}%`:"—"}</span><small>24h market change</small></div></div>
          <div className="market-context"><span>Mark price</span><span>Funding estimate / hour <b className={positive(market?.fundingRate)}>{percent(market?.fundingRate)}</b></span><span>5m closed candles</span></div>
          <Chart rows={market?.candles} label={`${symbol} observed closed candle price`}/>
          <div className="market-specs"><span>Tick <b>{market?.tick||"—"}</b></span><span>Size step <b>{market?.step||"—"}</b></span><span>Min. notional <b>{usd(market?.minNotional)}</b></span><span>Risk ceiling <b>{config?.maxLeverage||3}×</b></span></div>
        </section>

        <section className="paper-card paper-ticket" aria-labelledby="ticket-heading"><div className="card-title"><h2 id="ticket-heading">Your next move</h2><span className="small-tag">SIMULATED</span></div>
          <form onSubmit={submit}>
            <div className="paper-segment" role="group" aria-label="Position direction">{["long","short"].map(v=><button type="button" key={v} className={side===v?`selected ${v}`:""} aria-pressed={side===v} onClick={()=>setSide(v)}>{v==="long"?"↗ Long":"↘ Short"}</button>)}</div>
            <div className="paper-order-types" role="group" aria-label="Order type">{["market","limit"].map(v=><button type="button" key={v} className={type===v?"active":""} aria-pressed={type===v} onClick={()=>setType(v)}>{v[0].toUpperCase()+v.slice(1)}</button>)}</div>
            <label htmlFor="paper-notional">Requested notional <span>USD</span><input id="paper-notional" type="number" min="10" max={config?.maxOrderNotional||200} step="1" value={notional} onChange={e=>setNotional(e.target.value)} required/></label>
            {type==="limit"&&<label htmlFor="paper-limit">Limit price <span>USD</span><input id="paper-limit" type="number" min={market?.tick||"0.001"} step={market?.tick||"0.001"} value={limit} onChange={e=>setLimit(e.target.value)} required/></label>}
            <label htmlFor="paper-leverage" className="leverage-label">Leverage <strong>{leverage}×</strong><input id="paper-leverage" type="range" min="1" max={config?.maxLeverage||3} step="1" value={leverage} onChange={e=>setLeverage(e.target.value)}/></label>
            <div className="paper-two-fields"><label htmlFor="paper-stop">Stop distance <span>bps</span><input id="paper-stop" type="number" min="1" max="5000" value={stop} onChange={e=>setStop(e.target.value)} required/></label><label htmlFor="paper-target">Take profit <span>bps</span><input id="paper-target" type="number" min="1" max="5000" value={target} onChange={e=>setTarget(e.target.value)} required/></label></div>
            <div className="paper-risk"><span>◇</span><div><strong>At most {usd(config?.maxLossBudget||2)} stop-trigger budget</strong><small>Fees included. Size may reduce to fit risk limits. Slippage, gaps and funding can exceed this budget.</small></div></div>
            <button className="button button-gold paper-submit" disabled={!fresh||busy||!account}>{busy?"Processing…":`Place paper ${side}`} <span>↗</span></button>
          </form>
          <div className="paper-fees"><span>Maker fee <b>{percent(config?.makerFee??0)}</b></span><span>Taker fee <b>{percent(config?.takerFee??0)}</b></span><span>Vela platform fee <b>{percent(config?.platformFee??0)}</b></span></div>
          <p className="paper-fine">Initial simulation assumptions. Future live trading conditions may differ.</p>
        </section>

        <section className="paper-card paper-depth" aria-label="Live orderbook depth"><div className="card-title"><h2>Orderbook</h2><span className="paper-label">{fresh?"LIVE DEPTH":"WAITING / STALE"}</span></div><div className="depth-columns"><div><div className="depth-label"><span>Bid / USD</span><span>Size</span></div><Depth rows={market?.bids} side="bid"/></div><div><div className="depth-label"><span>Ask / USD</span><span>Size</span></div><Depth rows={market?.asks} side="ask"/></div></div><div className="depth-bottom"><span>Observed spread</span><strong>{market?.asks?.length&&market?.bids?.length?usd(Number(market.asks[0][0])-Number(market.bids[0][0]),4):"—"}</strong></div></section>

        <section className="paper-card paper-ai" aria-labelledby="ai-heading"><div className="card-title"><h2 id="ai-heading"><span className="ai-star">✳</span> Vela AI Agents</h2><span className={`small-tag ${data?.modelReady?"positive":""}`}>{data?.modelReady?"MODEL CONFIGURED":"MODEL OFFLINE"}</span></div>
          <div className="ai-flow">{["Research","Review","Direction","Risk"].map((v,i)=><React.Fragment key={v}><span className={account?.decision?.phase===v.toLowerCase()?"current":""}>{v}</span>{i<3&&<b>→</b>}</React.Fragment>)}</div>
          <h3>{data?.modelReady?(prefs?.autonomous?"Watching for an approved opportunity.":"A team behind your next decision."):"Waiting for the research model."}</h3>
          <p className="ai-reason" data-testid="ai-reason">{decisionText|| (data?.modelReady?"Analyze the selected market to start the existing Vela research, review and direction workflow. A hold decision is a valid outcome.":"Live market data and risk controls are active. Autonomous AI execution waits for a connected model; manual paper trading is available.")}</p>
          {analysis&&<div className="analysis-evidence"><span>{analysis.closedCandles} verified closed candles</span><span>{analysis.candidates.length} existing strategy signals</span><span>{analysis.status}</span></div>}
          <div className="ai-actions"><button className="button button-outline" disabled={!fresh||busy} onClick={analyze}>{prefs?.autonomous?"Analyze market":"Analyze & start AI"} <span>✳</span></button>{prefs?.autonomous&&<button className="paper-text-button" disabled={busy} onClick={()=>act(()=>request("agent",token,{symbol,leverage,autonomous:false}))}>Pause agent</button>}</div>
          <small className="paper-fine">Existing reversion / pullback signals. Model review required. No forced trades or invented results.</small>
        </section>
      </div>

      {message&&<div className="paper-message" role="status">{message}<button aria-label="Dismiss message" onClick={()=>setMessage("")}>×</button></div>}
      {(!transport||!accountOnline||data?.error)&&<div className="paper-paused" role="status">{data?.error||"Connection interrupted. Trading is paused until fresh data returns."} Existing positions stay visible; fills are never invented.</div>}
      {account?.fundingIncomplete&&<div className="paper-paused" role="status">Some funding intervals were not observed. Reported P&amp;L excludes those intervals; they are not assumed to cost zero.</div>}

      <section className="paper-card paper-ledger"><div className="paper-tabs" role="tablist" aria-label="Portfolio records">{[["positions",`Positions (${account?.positions?.length||0})`],["orders",`Orders${pending.length?` (${pending.length})`:""}`],["history","Trade history"],["performance","Performance"],["activity","Agent activity"]].map(([value,label])=><button role="tab" key={value} id={`tab-${value}`} aria-controls="paper-records" aria-selected={tab===value} className={tab===value?"active":""} onClick={()=>setTab(value)}>{label}</button>)}</div>
        <div id="paper-records" role="tabpanel" aria-labelledby={`tab-${tab}`}>
          {tab==="positions"&&(account?.positions?.length?<div className="paper-table-scroll"><table><thead><tr><th>Market / side</th><th>Size / leverage</th><th>Entry / mark</th><th>Unrealized P&amp;L</th><th>Stop / take profit</th><th>Funding cost</th><th>Actions</th></tr></thead><tbody>{account.positions.map(p=><tr key={p.id}><td><strong>{p.symbol.split("/")[0]} PERP</strong><small className={p.side==="long"?"positive":"negative"}>{p.side.toUpperCase()}</small></td><td>{num(p.qty,8)}<small>{p.leverage}× isolated simulation</small></td><td>{usd(p.entry,3)}<small>{usd(p.mark,3)}{!p.fresh?" · stale":""}</small></td><td className={positive(p.unrealized)}>{usd(p.unrealized,4)}</td><td>{usd(p.stop,3)}<small>TP {usd(p.target,3)}</small><small>Budget ≤ {usd(p.riskBudget)}</small></td><td>{usd(p.funding,6)}{p.fundingGapSeconds>1&&<small>Unobserved funding interval</small>}</td><td><div className="position-actions"><button disabled={busy||!fresh} onClick={()=>act(()=>request(`positions/${p.id}/close`,token,{clientId:id(),fraction:"0.5"}))}>Close 50%</button><button disabled={busy||!fresh} onClick={()=>act(()=>request(`positions/${p.id}/close`,token,{clientId:id(),fraction:"1"}))}>Close</button></div></td></tr>)}</tbody></table></div>:<Empty title={pending.length?"Your paper order is waiting.":"Your next position starts here."} text={pending.length?"Execution follows the configured latency, limit price and risk checks.":"Choose a market and place a paper order, or let the connected AI team evaluate it."}/>)}
          {tab==="orders"&&(displayOrders.length?<div className="paper-table-scroll"><table><thead><tr><th>Time</th><th>Market</th><th>Order</th><th>Status</th><th>Detail</th><th/></tr></thead><tbody>{displayOrders.map(o=><tr key={o.id}><td>{new Date(o.created*1000).toLocaleTimeString()}</td><td>{o.payload.symbol}</td><td>{o.payload.reduceOnly?"Close":o.payload.side} · {o.payload.type}</td><td>{o.status}</td><td>{o.error|| (o.payload.limitPrice?`Limit ${usd(o.payload.limitPrice,3)}`:"Paper execution")}</td><td>{o.status==="pending"&&!o.payload.reason&&<button className="paper-text-button" disabled={busy} onClick={()=>act(()=>request(`orders/${o.id}/cancel`,token,{}))}>Cancel</button>}</td></tr>)}</tbody></table></div>:<Empty title="No orders yet." text="Every submission, fill, cancellation and rejection appears here."/>)}
          {tab==="history"&&(account?.trades?.length?<div className="paper-table-scroll"><table><thead><tr><th>Closed</th><th>Market / side</th><th>Size</th><th>Entry → exit</th><th>Fees</th><th>Funding</th><th>Net realized P&amp;L</th><th>Reason</th></tr></thead><tbody>{account.trades.map(t=><tr key={t.id}><td>{new Date(t.closed*1000).toLocaleString()}</td><td>{t.symbol}<small>{t.side}</small></td><td>{num(t.qty,8)}</td><td>{usd(t.entry,3)} → {usd(t.exit,3)}</td><td>{usd(t.fees,4)}</td><td>{usd(t.funding,6)}</td><td className={positive(t.pnl)}>{usd(t.pnl,4)}</td><td>{t.reason}</td></tr>)}</tbody></table><p className="paper-fine">Latest 100 closed fills. Partial exits are separate rows. Net realized P&amp;L includes trading fees and allocated funding.</p></div>:<Empty title="A clean start." text="No trading results exist until positions actually close in the simulation."/>)}
          {tab==="performance"&&<div className="paper-performance"><div><h3>Recent observed equity</h3><Chart rows={account?.history} field="equity" label="Observed simulated account equity"/></div><div><h3>Same scope. Clear numbers.</h3><table><thead><tr><th>Closed fills</th><th>Today (UTC)</th><th>All time</th></tr></thead><tbody>{[["trades","Count"],["wins","Wins"],["losses","Losses"],["realized","Net realized"]].map(([key,label])=><tr key={key}><td>{label}</td><td>{key==="realized"?usd(account?.today?.[key],4):account?.today?.[key]||0}</td><td>{key==="realized"?usd(account?.totals?.[key],4):account?.totals?.[key]||0}</td></tr>)}</tbody></table><p className="paper-fine">All values are simulated. Open position P&amp;L is separate from closed-fill statistics.</p></div></div>}
          {tab==="activity"&&(account?.activity?.length?<ol className="paper-activity">{account.activity.map((a,i)=><li key={`${a.ts}-${i}`}><time>{new Date(a.ts*1000).toLocaleTimeString()}</time><div><strong>{a.kind.replaceAll("_"," ")}</strong><p>{a.detail}</p></div><span>●</span></li>)}</ol>:<Empty title="The activity log is ready." text="Actual model decisions, risk actions and paper execution events appear here."/>)}
        </div>
      </section>

      <section className="paper-cost-summary" aria-label="Separate execution cost accounting"><div><span className="paper-label">ALL-TIME EXECUTION COSTS</span><p>Spread, slippage and funding stay visible.</p></div>{[["tradingFee","Trading fees"],["spread","Spread"],["depthImpact","Depth impact"],["slippage","Slippage"],["funding","Funding ±"]].map(([key,label])=><div key={key}><span>{label}</span><strong>{usd(account?.costs?.[key]||0,4)}</strong></div>)}</section>
      <details className="paper-disclosure"><summary>Simulation assumptions &amp; risk controls <span>+</span></summary><p>Orders execute against observed public depth after {config?.latencyMs??300}ms simulated latency, with {percent(config?.slippage??.0002)} adverse slippage and {percent(config?.additionalHalfSpread??0)} additional half-spread. Actual orderbook spread and depth impact are included. Limit fills require enough executable depth and respect the limit; queue position is not reproduced. Order margin is checked at execution, so competing pending orders can be rejected.</p><p>Funding uses a prorated hourly estimate during fresh observations, based on position side and index price. Missing observation intervals are flagged rather than fabricated. Liquidation uses an isolated-margin approximation and market maintenance requirements. Open P&amp;L uses mark price, with entry fees deducted; exit costs apply when filled. Spread and slippage already affect fill prices and are not charged twice.</p><p>Each position has a fee-inclusive stop trigger budget of {usd(config?.maxLossBudget||2)} or less, preserving stricter limits and inward rounding. The inherited risk reserve also keeps a minimum 0.04% exit-fee allowance, even when simulated trading fees are zero. Partial exits and persisted positions are reconciled. Slippage, market gaps, funding and feed outages can exceed the trigger budget. Simulated balances are not money and cannot be withdrawn. LIVE execution is disabled.</p></details>
    </main>
    <footer className="footer shell"><a className="brand" href="/" aria-label="Vela home">vela<span className="brand-period">.</span></a><span>Vela · PAPER TRADING</span><a className="powered-by" href="https://lighter.xyz" target="_blank" rel="noopener noreferrer">Powered by Lighter</a></footer>
  </>;
}
function Metric({title,value,note,tone=""}){return <div className="paper-metric"><span>{title}</span><strong className={tone}>{value}</strong><small>{note}</small></div>;}
function Empty({title,text}){return <div className="paper-empty"><span>◇</span><h3>{title}</h3><p>{text}</p></div>;}
function Depth({rows=[],side}){const max=Math.max(1,...rows.map(r=>Number(r[1])));return <div className={`depth-rows ${side}`}>{rows.slice(0,6).map(([p,q])=><div key={p} style={{"--depth":`${Number(q)/max*100}%`}}><span>{num(p,4)}</span><span>{num(q,5)}</span></div>)}{!rows.length&&<p className="paper-fine">Waiting for depth…</p>}</div>;}
class Boundary extends React.Component{state={error:false};static getDerivedStateFromError(){return {error:true};}render(){return this.state.error?<main className="paper-shell shell"><h1>Let’s reconnect.</h1><p>The paper dashboard could not open. Enable browser storage and refresh.</p><a href="/paper">Reload paper trading</a></main>:this.props.children;}}
createRoot(document.getElementById("paper-root")).render(<Boundary><App/></Boundary>);
