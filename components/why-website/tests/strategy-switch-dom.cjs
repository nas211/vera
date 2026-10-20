// DOM integration test. Account and authentication calls use local fixtures; no real trades.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');
const source=path.resolve(__dirname,'..'),req=createRequire(source+'/package.json');
const {JSDOM}=require(process.env.JSDOM_MODULE_PATH || 'jsdom');
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/profile',pretendToBeVisual:true});
for(const key of ['window','document','HTMLElement','localStorage','sessionStorage','location','history'])global[key]=dom.window[key];
global.requestAnimationFrame=dom.window.requestAnimationFrame.bind(dom.window);global.cancelAnimationFrame=dom.window.cancelAnimationFrame.bind(dom.window);
global.IS_REACT_ACT_ENVIRONMENT=true;
const React=req('react'),{createRoot}=req('react-dom/client'),ts=req('typescript');
const user={id:'strategy-test',email:'strategy@example.test'},session={user};
localStorage.setItem(`why_tutorial_v1_${user.id}`,'complete');
let me,wx,fail=false;const mutations=[];
const scalpProfile=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/scalp-profile.json'),'utf8'));
const settings={strategy:'why-1',allocation_pct:25,position_size_pct:2,leverage:10,risk_preset:'conservative'};
const clone=x=>JSON.parse(JSON.stringify(x));
async function api(_session,method,url,data){
 if(method==='POST'){
  mutations.push({url,data});
  if(url==='/me/bot/pause'){me.account.state='PAUSED';me.config.entries_paused=true;}
  else if(url==='/me/trading-style'){if(fail)throw Error('trading_style_positions_open');assert.equal(me.account.state,'PAUSED');assert.equal(me.config.entries_paused,true);me.config.trading_style=data.trading_style;me.config.strategy_profile=data.trading_style==='scalp'?clone(scalpProfile):{id:'swing',name:'AI Agents — Swing',leverage:10,max_open_positions:8};}
  else throw Error('Unexpected mutation '+url);
  return clone(me);
 }
 if(url==='/public-config')return {configured:true};
 if(url==='/me')return clone(me);
 if(url==='/me/subscription')return {subscription:{plan:'free',plan_name:'WHY Free',status:'active',live_allowed:true},plans:[]};
 if(url.startsWith('/me/trades'))return {trades:[],summary:{realized_pnl:0}};
 if(url.startsWith('/me/paper'))return {paper_enabled:true,running:false,paper_equity:25000,paper_available_balance:25000,paper_open_positions:[],paper_trade_history:[],settings};
 if(url==='/me/weex')return clone(wx);
 throw Error('Unexpected read '+url);
}
const cache=new Map();
function compile(file){
 if(cache.has(file))return cache.get(file).exports;
 const mod={exports:{}};cache.set(file,mod);
 const js=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020,esModuleInterop:true}}).outputText;
 new Function('require','module','exports',js)(name=>{
  if(name.endsWith('.css'))return {};
  if(!name.startsWith('.'))return req(name);
  const base=path.resolve(path.dirname(file),name),resolved=['.tsx','.ts'].map(ext=>base+ext).find(fs.existsSync);
  const exports=compile(resolved);
  if(resolved.endsWith('/profile/client.ts'))return {...exports,api,supabaseFor:()=>({auth:{onAuthStateChange:()=>({data:{subscription:{unsubscribe(){}}}}),getSession:async()=>({data:{session}})}})};
  return exports;
 },mod,mod.exports);return mod.exports;
}
const {ProfileApp}=compile(source+'/components/profile/ProfileApp.tsx');let root;
const {yellowSlot}=compile(source+'/components/profile/exchanges.ts');
const button=name=>[...document.querySelectorAll('button')].find(x=>x.textContent===name);
async function flush(fn){await React.act(async()=>{await fn?.();for(let i=0;i<8;i++)await Promise.resolve()});}
async function click(name){const el=button(name);assert(el,'Missing button '+name);assert(!el.disabled,'Disabled button '+name);await flush(()=>el.click());}
async function openYellow(){const ex=[...document.querySelectorAll('button')].find(el=>el.textContent.startsWith('Exchanges & API keys'));assert(ex,'Missing Exchanges & API keys');await flush(()=>ex.click());const manage=[...document.querySelectorAll('[role="dialog"][aria-label="Exchanges"] button')].find(el=>el.textContent==='Manage'&&el.parentElement.textContent.includes('Yellow Pro'));assert(manage);await flush(()=>manage.click());}
async function closeDialog(){await flush(()=>document.querySelector('[role="dialog"] button[aria-label="Close"]').click());}
async function mount(opts={}){
 if(root)await flush(()=>root.unmount());
 me={account:{id:'fixture',state:opts.paused?'PAUSED':'ACTIVE',verification_state:'VERIFIED',balance_equity:1000,balance_available:1000},config:{trading_style:'swing',position_size_usd:20,leverage:10,max_open_positions:8,entries_paused:!!opts.paused},runtime:{loaded:!opts.unloaded,venue:'READY',open_positions:opts.open||0,ambiguous_orders:opts.ambiguous||0,positions:[]},events:[]};
 me.config.trade_margin_pct=opts.tradeMargin??25;
 if(opts.scalp){me.config.trading_style='scalp';me.config.strategy_profile={...clone(scalpProfile),...opts.profile,activation_blocked_reason:opts.blocked??null};}
 wx={state:opts.weexActive?'ACTIVE':'DISCONNECTED',status:'integrating',settings,runtime:{positions:[]}};
 fail=false;mutations.length=0;root=createRoot(document.getElementById('root'));await flush(()=>root.render(React.createElement(ProfileApp)));await click('Trading');
}
(async()=>{
 await mount();assert(document.querySelector('#live-trading-style').disabled);
 await click('Pause');assert(button('Switch to Scalp'));
 const controls=document.querySelector('[data-testid="bot-status"]'),selection=document.querySelector('[aria-label="Live strategy selection"]');
 assert(controls.compareDocumentPosition(selection)&dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
 await click('Switch to Scalp');assert.match(document.querySelector('[role="dialog"]').textContent,/Warning: you are switching your trading strategy from Swing to Scalp/);
 assert.match(document.querySelector('[role="dialog"]').textContent,/Trading will remain paused/);
 await click('Cancel');assert.deepEqual(mutations.map(x=>x.url),['/me/bot/pause']);
 await click('Switch to Scalp');await click('Confirm switch to Scalp');assert(button('Switch to Swing'));
 await openYellow();assert.equal(document.querySelector('#pf-strategy').value,'scalp');
 assert.equal(yellowSlot(me).tradeSizeLabel,'Coin minimum');
 const defaults=document.querySelector('[aria-label="Scalp default settings"]');assert(defaults);
 for(const text of ['Coin minimum at 10×','No fixed daily count','Every 35 seconds','0.1%','120 seconds','up to 100','$10,000 notional','$1,000 notional','$500 per side'])assert(defaults.textContent.toLowerCase().includes(text.toLowerCase()),text);
 assert(!document.querySelector('#pf-position-limit'),'Swing position cap must not appear for Scalp');
 assert.equal(yellowSlot(me).maxOpenPositions,100);
 assert(!document.querySelector('[role="dialog"]').textContent.includes('up to 8'));
 assert(!document.querySelector('#pf-trade-size-mode'),'Swing sizing is hidden for Scalp');assert.equal(me.config.trade_margin_pct,25);
 await closeDialog();
 await flush(()=>root.unmount());root=createRoot(document.getElementById('root'));await flush(()=>root.render(React.createElement(ProfileApp)));assert(button('Switch to Swing'),'Saved style survives remount');
 await click('Switch to Swing');assert.match(document.querySelector('[role="dialog"]').textContent,/from Scalp to Swing/);await click('Confirm switch to Swing');assert(button('Switch to Scalp'));
 await openYellow();assert.equal(document.querySelector('#pf-strategy').value,'swing');assert(!document.querySelector('[aria-label="Scalp default settings"]'));
 assert.equal(yellowSlot(me).tradeSizeLabel,'25% available margin');
 assert.equal(yellowSlot(me).maxOpenPositions,8);
 assert.equal(document.querySelector('#pf-position-limit').value,'8');
 assert.equal(document.querySelector('#pf-trade-size-pct').value,'25');await closeDialog();
 assert.equal(me.account.state,'PAUSED');assert.equal(me.config.entries_paused,true);assert.deepEqual(mutations.map(x=>x.url),['/me/bot/pause','/me/trading-style','/me/trading-style']);
 await click('Paper');assert(!button('Switch to Scalp'));console.log('PASS pause, placement below controls, warning, cancel, both switch directions, persisted style, no activation, Paper isolation');
 for(const opts of [{open:1},{ambiguous:1},{unloaded:true},{weexActive:true}]){await mount({paused:true,...opts});assert(button('Switch to Scalp').disabled);assert.equal(mutations.length,0);}
 console.log('PASS blocking: open positions, ambiguous orders, unloaded runtime, another active exchange');
 await mount({paused:true});fail=true;await click('Switch to Scalp');await click('Confirm switch to Scalp');assert(button('Switch to Scalp'));assert(!button('Switch to Swing'));assert.match(document.querySelector('[role="alert"]').textContent,/pending orders are cleared/);console.log('PASS rejected switch preserves strategy and displays server error');
 await mount({paused:true,scalp:true,tradeMargin:0});await openYellow();assert(!button('Start Trading').disabled,'Unused Swing setting cannot block Scalp');await closeDialog();
 await mount({paused:true,scalp:true,blocked:'Scalp cannot start under this account’s mandatory planned-loss limit.'});assert(button('Resume').disabled);await openYellow();assert(button('Start Trading').disabled);assert.match(document.querySelector('[aria-label="Scalp default settings"]').textContent,/mandatory planned-loss limit/);
 await mount({paused:true,scalp:true,profile:{emergency_stop_usd:1,emergency_stop_mode:'bot_managed'}});await openYellow();
 const emergencySettings=document.querySelector('[aria-label="Scalp default settings"]').textContent;
 assert.match(emergencySettings,/Emergency loss trigger\$1 per entry/);
 assert.match(emergencySettings,/Coin minimum at 10×/);
 assert.match(emergencySettings,/Requires the bot and exchange connection/);
 assert.match(emergencySettings,/Waiting for available margin for BTC/);
 assert.match(emergencySettings,/Estimated minimum margin now/);
 console.log('PASS Yellow strategy label, exchange-minimum Scalp sizing, $1 bot-managed emergency trigger, preserved Swing settings, no fixed daily count, account-specific activation block');
 await flush(()=>root.unmount());dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>process.exit(process.exitCode||0));
