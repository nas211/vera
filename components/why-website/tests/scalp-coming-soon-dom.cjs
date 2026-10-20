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
async function choose(style, id='live-trading-style') {
 const select=document.getElementById(id);assert(select);assert(!select.disabled);
 await flush(()=>{select.value=style;select.dispatchEvent(new window.Event('change',{bubbles:true}));});
}
(async()=>{
 await mount({paused:true});
 let select=document.getElementById('live-trading-style');
 assert.deepEqual([...select.options].map(x=>x.textContent),['Swing','Scalp — Coming soon','AI Agents Trading · Paper']);
 assert(select.querySelector('[value="scalp"]').disabled);
 await choose('scalp');assert.equal(mutations.length,0);
 await mount({paused:true,scalp:true});
 assert(button('Resume').disabled);
 await openYellow();assert(button('Start Trading').disabled);await closeDialog();
 assert.equal(mutations.length,0);
 await mount({paused:true,scalp:true,open:1});
 select=document.getElementById('live-trading-style');assert(!select.disabled);
 assert(select.querySelector('[value="swing"]').disabled);
 assert(!select.querySelector('[value="why-discovery-paper"]').disabled);
 await choose('swing');assert.equal(mutations.length,0);assert.equal(me.config.trading_style,'scalp');
 await mount({paused:false,scalp:true,open:1});
 await click('Pause');assert.equal(me.config.trading_style,'scalp');
 assert.deepEqual(mutations.map(x=>x.url),['/me/bot/pause']);
 await mount({paused:true,scalp:true});
 await choose('swing');assert.equal(me.config.trading_style,'scalp');
 await click('Cancel');assert.equal(me.config.trading_style,'scalp');assert.equal(mutations.length,0);
 await mount({paused:true,scalp:true});await openYellow();
 await choose('swing','yellow-settings-trading-style');await click('Confirm switch to Swing');
 assert.equal(me.config.trading_style,'swing');assert.equal(me.account.state,'PAUSED');
 assert.deepEqual(mutations.map(x=>x.url),['/me/trading-style']);
 await mount({paused:true});assert(!button('Resume').disabled);
 await click('Paper');
 const paper=document.getElementById('paper-strategy-choice');assert(paper);
 assert.deepEqual([...paper.options].map(x=>x.textContent),['Swing','Scalp — Coming soon','AI Agents Trading · Paper']);
 assert(paper.querySelector('[value="scalp"]').disabled);
 assert.equal(mutations.length,0);
 console.log('PASS profile/Paper three-choice dropdown, Scalp lock, live confirmation/guards and Paper access');
 await flush(()=>root.unmount());dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>process.exit(process.exitCode||0));
