// Opt-in real model check. Uses only the server's private development configuration.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const origin=process.env.PREVIEW_URL||'http://127.0.0.1:8790';
const artifacts=process.env.CHECK_ARTIFACTS_DIR||'/tmp/vela-paper-ai-checks';
const symbol=process.env.AI_CHECK_SYMBOL||'BTC';
assert(['BTC','ETH','SOL'].includes(symbol));
fs.mkdirSync(artifacts,{recursive:true});

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:1440,height:1050}});
  const page=await context.newPage();
  const errors=[],requests=[],decisions=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>requests.push(r.url()));
  const api=(route,body)=>page.evaluate(async({route,body})=>{
    const headers={'Content-Type':'application/json','X-Vela-Paper':localStorage.getItem('vela:paper:session:v1')};
    const r=await fetch('/api/paper/'+route,{headers,method:body===undefined?'GET':'POST',...(body===undefined?{}:{body:JSON.stringify(body)})});
    if(!r.ok)throw Error('Paper API status '+r.status);
    return r.json();
  },{route,body});
  let result;
  try{
    await page.goto(origin+'/paper');
    const ready=Date.now()+45000;
    while(Date.now()<ready&&!await page.getByRole('button',{name:'Analyze & start AI'}).isEnabled())await page.waitForTimeout(300);
    const health=await api('health');
    assert.equal(health.mode,'PAPER');assert.equal(health.liveEnabled,false);assert.equal(health.modelReady,true);
    await page.getByRole('button',{name:new RegExp(symbol+'PERP')}).click();
    await page.getByRole('button',{name:'Analyze & start AI'}).click();
    await page.getByText('250 verified closed candles').waitFor();
    const end=Date.now()+180000;
    let state,outcome;
    while(Date.now()<end){
      state=await api('account');
      const d=state.decision;
      if(d&&!decisions.some(x=>x.at===d.at)){
        decisions.push({at:d.at,phase:d.phase,answer:d.answer});
        console.log('Observed actual AI phase:',d.phase);
      }
      if(state.positions.length){outcome='AI-approved paper position opened';break;}
      if(d?.phase==='research'&&d.answer.strategies?.length===0){outcome='Research abstained';break;}
      if(d?.phase==='review'&&d.answer.verdict!=='confirm'){outcome='Review '+d.answer.verdict;break;}
      if(d?.phase==='direction'&&d.answer.action!=='open'){outcome='Direction '+d.answer.action;break;}
      await page.waitForTimeout(500);
    }
    assert(decisions.length>0,'No genuine AI decision was received');
    outcome=outcome||'AI approved a conditional strategy; entry conditions did not fire during observation';
    assert(outcome!=='AI approved a conditional strategy; entry conditions did not fire during observation'||decisions.some(d=>d.phase==='direction'&&d.answer.action==='open'),'Incomplete model pipeline');
    await page.getByRole('button',{name:'Pause agent',exact:true}).click();
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
    await page.screenshot({path:path.join(artifacts,'ai-decision.png'),fullPage:true});
    const market=await api('markets');
    assert.equal(market.modelConnection?.ok,true);
    assert.deepEqual(errors,[]);
    assert(!requests.some(u=>/api\.openai|sendTx|wallet-|start-quote|whytradingbot|bybit/i.test(u)));
    result={verifiedAt:new Date().toISOString(),execution:'PAPER',liveTradingEnabled:false,
      livePublicMarketData:true,realModel:market.modelConnection.model,symbol,outcome,decisions,
      positions:state.positions.map(p=>({symbol:p.symbol,side:p.side,qty:p.qty,entry:p.entry,stop:p.stop,target:p.target,riskBudget:p.riskBudget})),
      scope:'One fresh engineering account with actual model decisions and live public data; not a strategy performance claim',errors};
  }finally{
    await api('agent',{symbol:symbol+'/USD',leverage:'2',autonomous:false}).catch(()=>{});
    let a=await api('account').catch(()=>({}));
    for(const o of a.orders||[])if(o.status==='pending'&&!o.payload.reduceOnly)await api('orders/'+o.id+'/cancel',{}).catch(()=>{});
    for(const p of a.positions||[])await api('positions/'+p.id+'/close',{clientId:require('node:crypto').randomUUID(),fraction:'1'}).catch(()=>{});
    const cleanupEnd=Date.now()+15000;
    while(Date.now()<cleanupEnd){
      a=await api('account').catch(()=>({}));
      if(a.positions?.length===0&&!a.orders?.some(o=>o.status==='pending'))break;
      await page.waitForTimeout(300);
    }
    if(result){
      result.agentPaused=a.preferences?.autonomous===false;
      result.positionsRemaining=a.positions?.length;
      result.closedFills=a.trades?.length;
      fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify(result,null,2)+'\n');
      assert.equal(result.agentPaused,true);assert.equal(result.positionsRemaining,0);
    }
    await browser.close();
  }
  console.log('PASS: actual '+result.realModel+' decisions, live public data, browser explanations, paper-only execution and cleanup. Outcome: '+result.outcome);
})().catch(e=>{console.error(e.message);process.exitCode=1;});
