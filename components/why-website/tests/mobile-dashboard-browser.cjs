// Local UI verification of the mobile dashboard: account calls and Yellow Pro's public feed are fixtures;
// nothing reaches an exchange and no real order is ever submitted.
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = process.env.DASHBOARD_TEST_URL || 'http://127.0.0.1:3026';
assert.equal(new URL(base).hostname, '127.0.0.1');
let browser;
before(async()=>{browser=await chromium.launch({executablePath:process.env.CHROME_PATH || undefined,headless:true,args:['--no-sandbox']});});
after(async()=>{await browser?.close();});
const uid='00000000-0000-4000-8000-000000000001';
const user={id:uid,email:'alex@example.test',aud:'authenticated',role:'authenticated',app_metadata:{},user_metadata:{},created_at:'2026-01-01T00:00:00Z'};
const settings={strategy:'why-1',allocation_pct:25,position_size_pct:2,leverage:10,risk_preset:'conservative'};
const nowS=()=>Date.now()/1000;

async function setup(t, o={}) {
  const ctx=await browser.newContext({viewport:o.mobile?{width:390,height:844}:{width:1440,height:1000}});ctx.setDefaultTimeout(15000);t.after(()=>ctx.close());
  const expires=Math.floor(Date.now()/1000)+3600;
  const token=[Buffer.from(JSON.stringify({alg:'HS256',typ:'JWT'})).toString('base64url'),Buffer.from(JSON.stringify({sub:uid,aud:'authenticated',role:'authenticated',exp:expires})).toString('base64url'),'fixture'].join('.');
  if(!o.signedOut)await ctx.addInitScript(({user,token,expires})=>{localStorage.setItem('sb-dashboard-test-auth-token',JSON.stringify({access_token:token,refresh_token:'test-refresh',token_type:'bearer',expires_in:3600,expires_at:expires,user}));localStorage.setItem(`why_tutorial_v1_${user.id}`,'complete');},{user,token,expires});
  await ctx.route('https://dashboard-test.supabase.co/**',r=>r.fulfill({json:user}));
  const calls=[],posts=[];
  const me={account:{id:'fixture-yellow',state:o.paused?'PAUSED':'ACTIVE',verification_state:'VERIFIED',state_reason:null,api_key_masked:'test••••key',balance_equity:1532.18,balance_available:1482.18,balance_at:new Date().toISOString()},
    config:{position_size_usd:20,pending_position_size_usd:null,leverage:10,max_open_positions:8,entries_paused:!!o.paused,sizes_usd:[20,50],trading_style:'swing'},
    runtime:{loaded:true,venue:'READY',last_error:null,started:true,open_positions:2,ambiguous_orders:0,last_signal:null,positions:[
      {position_id:'why-long',market:'ETHUSDT-PERP',direction:'long',qty:'0.02',entry_price:'2500',state:'PROTECTED',strategy:'why-1',opened_ts:nowS()-1000,stop_loss:'2420.5',take_profit:'2600',leverage:'10'},
      {position_id:'why-short',market:'BTCUSDT-PERP',direction:'short',qty:'0.001',entry_price:'87000',state:'PROTECTED',strategy:'why-1',opened_ts:nowS()-900,stop_loss:'88500',take_profit:'85000',leverage:'10'}]},events:[]};
  if(o.flat){me.runtime.positions=[];me.runtime.open_positions=0;}
  const closed=(id,pnl,ago)=>({market:'SOLUSDT-PERP',direction:'long',qty:'1',entry_price:'150',exit_price:'151',pnl,fees:.05,reason:pnl>0?'target':'stop',opened_ts:nowS()-ago-600,closed_ts:nowS()-ago,strategy:'why-1',id});
  const trades={account_id:'fixture-yellow',mode:'live',trades:[closed('a',2.5,600),closed('b',-1.2,1200),closed('c',4,86400*3)],
    summary:{closed_trades:18,wins:16,losses:2,realized_pnl:-1.05,fees:1,volume:900,open_positions:2,last_trade_at:nowS()-600},
    today:{date:'2026-10-17',timezone:'Europe/Berlin',available:true,start_at:0,end_at:0,closed_trades:5,wins:5,losses:0,breakeven:0,pending_pnl:0,funding_pending:0,won_usd:5.68,lost_usd:0,net_pnl:5.68}};
  await ctx.route('**/yellow/api/**',async r=>{
    const q=r.request(),path=new URL(q.url()).pathname.replace('/yellow/api','');calls.push(q.method()+' '+path);
    if(q.method()!=='GET'){posts.push({path,body:q.postDataJSON()});
      if(path==='/me/positions/close')return r.fulfill({json:{result:'CLOSE_SUBMITTED'}});
      if(path==='/me/bot/pause'){me.account.state='PAUSED';me.config.entries_paused=true;return r.fulfill({json:me});}
      if(path==='/me/bot/resume'||path==='/me/bot/start'){me.account.state='ACTIVE';me.config.entries_paused=false;return r.fulfill({json:me});}
      return r.fulfill({status:404,json:{error:'unmocked'}});}
    let data;
    if(path==='/public-config')data={configured:true,supabase_url:'https://dashboard-test.supabase.co',supabase_anon_key:'fixture',sizes_usd:[20,50],leverage:10,max_open_positions:8,weex_status:'integrating'};
    else if(path==='/me')data=me;
    else if(path==='/me/subscription')data={subscription:{plan:'core',plan_name:'WHY Core',status:'active',live_allowed:true,exchange_limit:1,paper_starting_balance:25000},plans:[]};
    else if(path==='/me/paper')data={paper_enabled:true,running:true,paper_equity:25432.85,paper_available_balance:24000,paper_starting_balance:25000,paper_realized_pnl:312.4,paper_unrealized_pnl:120.45,paper_used_margin:1432.85,paper_return_pct:1.73,paper_total_fees:2,paper_total_funding:0,paper_open_positions:[{id:'paper1',market:'BTCUSDT-PERP',direction:'long',qty:.05,entry_price:86000,notional:4300,unrealized_pnl:120.45,opened_at:new Date(Date.now()-100000).toISOString()}],paper_trade_history:[],settings};
    else if(path==='/me/weex')data={id:'weex',state:'DISCONNECTED',status:'integrating',balance_equity:null,balance_available:null,settings,runtime:{positions:[]}};
    else if(path.startsWith('/me/trades'))data=trades;
    else return r.fulfill({status:404,json:{error:'unmocked '+path}});
    await r.fulfill({json:data});});
  const yellow={rest:[],sent:[]};
  await ctx.route('https://trade.api.yellow.pro/**',r=>{const u=new URL(r.request().url());yellow.rest.push(u.pathname);
    if(u.pathname==='/perpetual/exchangeInfo')return r.fulfill({json:{symbols:['BTC','ETH','SOL'].map(b=>({symbol:`${b}USDT-PERP`,base_asset:b,status:'TRADING',price_precision:b==='BTC'?1:2}))}});
    if(u.pathname==='/ticker/24hr')return r.fulfill({json:{last:'2550',priceChange:'1.25'}});
    if(u.pathname==='/klines'){const step=300000,s=Math.floor(Date.now()/step)*step;return r.fulfill({json:Array.from({length:120},(_,i)=>[s-(119-i)*step,'2500','2510','2490','2505','10'])});}
    return r.fulfill({status:404,json:{}});});
  await ctx.routeWebSocket('wss://trade.api.yellow.pro/ws',ws=>{ws.onMessage(raw=>{if(raw==='{}')return;const m=JSON.parse(raw);yellow.sent.push(m);
    if(m.connect)return ws.send(JSON.stringify({id:1,connect:{}}));
    const ch=m.subscribe?.channel;if(ch?.startsWith('public.mark_price.')){const mk=ch.split('.').at(-1);ws.send(JSON.stringify({push:{channel:ch,pub:{data:{mark_price:mk.startsWith('ETH')?'2550':'86000'}}}}));}});});
  const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
  await page.goto(base+(o.route||'/profile'));
  if(!o.signedOut)await page.getByTestId('mobile-dashboard').waitFor();
  return {page,ctx,calls,posts,yellow,me};
}
const tab=(page,name)=>page.getByRole('tab',{name:new RegExp('^'+name+'(, \\d+ open)?$')});
const noOverflow=page=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1);

for(const mobile of [true,false]) test(`${mobile?'phone':'desktop'}: lands on Trading, four tabs, chrome hidden, no overflow, view changes only read`,async t=>{
  const {page,calls}=await setup(t,{mobile});
  assert.equal(await page.getByTestId('mobile-dashboard').getAttribute('data-mode'),'trading');
  assert.equal((await page.getByTestId('mode-pill').textContent()).trim(),'Trading');
  for(const name of ['Trades','Chart','History','Account']){await tab(page,name).click();assert.equal(await tab(page,name).getAttribute('aria-selected'),'true');assert.ok(await noOverflow(page),name+' overflows');}
  assert.equal(await page.locator('nav[style*="sticky"]').first().isVisible().catch(()=>false),false,'marketing nav must be hidden');
  await page.getByRole('group',{name:'Account mode'}).getByRole('button',{name:'Paper',exact:true}).click();
  assert.equal(await page.getByTestId('mobile-dashboard').getAttribute('data-mode'),'paper');
  await page.getByRole('group',{name:'Account mode'}).getByRole('button',{name:'Trading',exact:true}).click();
  assert.deepEqual(calls.filter(c=>!c.startsWith('GET ')),[],'switching tabs or view must not start or stop trading');
});

test('Today is the Berlin day and kept apart from all-time results',async t=>{
  const {page}=await setup(t,{mobile:true});
  const today=page.getByTestId('pl-today'),all=page.getByTestId('pl-all');
  await today.getByText('+$5.68').waitFor();
  assert.match(await today.innerText(),/5 won · 0 lost · Berlin day/);
  assert.match(await all.innerText(),/All time[\s\S]*−\$1\.05[\s\S]*18 trades/i);
  await tab(page,'History').click();
  const sum=await page.getByTestId('history-summary').innerText();
  assert.match(await page.locator('#panel-history').innerText(),/All time/i);
  assert.match(sum,/16 won · 2 lost/);assert.match(sum,/\b18\b/);
});

test('long and short positions show live marks, and the chart shows the stored stop-loss and take-profit',async t=>{
  const {page,yellow}=await setup(t,{mobile:true});
  const cards=page.getByTestId('open-trade');await assert.doesNotReject(cards.nth(1).waitFor());
  assert.equal(await cards.count(),2);
  await page.getByRole('tab',{name:'Trades, 2 open',exact:true}).waitFor();
  await page.getByTestId('trade-mark').filter({hasText:'2,550'}).waitFor();
  assert.ok(yellow.sent.some(m=>m.connect),'feed must send connect first');
  assert.ok(yellow.sent.some(m=>m.subscribe?.channel==='public.mark_price.ETHUSDT-PERP'));
  await cards.filter({hasText:'ETH'}).click();
  assert.equal(await tab(page,'Chart').getAttribute('aria-selected'),'true');
  const pos=page.getByTestId('chart-position');await pos.waitFor();
  const text=await pos.innerText();
  assert.match(text,/Take profit\s+2,600\.00/i);assert.match(text,/Stop loss\s+2,420\.50/i);assert.match(text,/\+\$1\.00\s+\+20\.00%/);
  await page.locator('[data-testid="feed-status"][data-status="live"]').waitFor();
  await page.getByTestId('pair-button').click();await page.getByRole('dialog',{name:'Yellow Pro markets'}).getByRole('button',{name:/BTC\/USDT/}).click();
  const short=await page.getByTestId('chart-position').innerText();
  assert.match(short,/short/i);assert.match(short,/Take profit\s+85,000\.0/i);assert.match(short,/Stop loss\s+88,500\.0/i);
});

test('close at market asks first and posts only the selected position',async t=>{
  const {page,posts}=await setup(t,{mobile:true});
  await page.getByTestId('open-trade').filter({hasText:'ETH'}).click();
  await page.getByRole('button',{name:'Close at market'}).click();
  const dialog=page.getByRole('dialog',{name:/Close ETHUSDT-PERP long/});await dialog.waitFor();
  await dialog.getByRole('button',{name:'Cancel'}).click();assert.deepEqual(posts,[]);
  await page.getByRole('button',{name:'Close at market'}).click();
  await page.getByRole('dialog',{name:/Close ETHUSDT-PERP long/}).getByRole('button').last().click();
  await page.waitForFunction(()=>true);await new Promise(r=>setTimeout(r,500));
  assert.equal(posts.length,1);assert.equal(posts[0].path,'/me/positions/close');assert.equal(posts[0].body.position_id,'why-long');assert.equal(posts[0].body.confirm,true);
});

test('Pause stops new entries through the existing endpoint; Resume asks for confirmation',async t=>{
  const {page,posts}=await setup(t,{mobile:true});
  const bot=page.getByTestId('bot-status');await bot.getByText(/Bot running/).waitFor();
  await bot.getByRole('button',{name:'Pause'}).click();
  await bot.getByText(/Bot paused/).waitFor();
  assert.deepEqual(posts.map(p=>p.path),['/me/bot/pause']);
  await bot.getByRole('button',{name:'Resume'}).click();
  const dialog=page.getByRole('dialog',{name:/Start Swing Trading on Yellow Pro/});await dialog.waitFor();
  await dialog.getByRole('button',{name:'Cancel'}).click();
  assert.deepEqual(posts.map(p=>p.path),['/me/bot/pause'],'Resume must not post before confirmation');
});

test('signed-out visitors still get the sign-in screen',async t=>{
  const {page}=await setup(t,{signedOut:true});
  await page.getByRole('button',{name:'Sign in',exact:true}).waitFor();
  assert.equal(await page.getByTestId('mobile-dashboard').count(),0);
});
