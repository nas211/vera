import {build} from 'esbuild';import {spawn} from 'node:child_process';import {mkdir,rm} from 'node:fs/promises';import {pathToFileURL} from 'node:url';import path from 'node:path';import assert from 'node:assert/strict';
import React from 'react';import {renderToStaticMarkup} from 'react-dom/server';
const dir='checks/.test-render';await mkdir(dir,{recursive:true});
try{
 await build({entryPoints:['src/app.jsx','src/guided-app.jsx','src/tutorial.jsx','src/network-selector.jsx'],outdir:dir,bundle:true,platform:'node',format:'esm',outExtension:{'.js':'.mjs'},packages:'external',loader:{'.css':'empty'},plugins:[{name:'public-view-only',setup(b){b.onResolve({filter:/mydoge\.jsx$/},args=>({path:args.path,external:true}));}}]});
 const {App,Welcome}=await import(pathToFileURL(path.resolve(dir+'/app.mjs')));
 const {GuidedDashboard}=await import(pathToFileURL(path.resolve(dir+'/guided-app.mjs')));
 const {WalkthroughPlayer}=await import(pathToFileURL(path.resolve(dir+'/tutorial.mjs')));
 const {tutorials}=await import('../src/tutorials.js');
 const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));
 const home=render(App,{page:'/'}),portal=render(App,{page:'/vault'});
 assert.match(home,/DeFi,/);assert.match(home,/Reimagined\./);assert.match(home,/Intelligent Trading\. Limitless Possibilities\./);
 assert.equal((home.match(/<section/g)||[]).length,3);
 assert.equal((home.match(/href="\/vault"/g)||[]).length,5);
 assert.match(home,/class="protocol-vault"/);assert.match(home,/class="hero-editorial"/);assert.match(home,/class="module-strip"/);
 assert.match(home,/Protocol animation · not account activity/);assert.match(home,/Explore how VELA works/);
 assert.match(home,/Pause protocol animation|Play protocol animation/);
 assert.match(home,/Put your crypto to work with AI-powered trading/);assert.match(home,/Upcoming:<\/strong> collateral-backed liquidity and ecosystem rewards/);assert.match(home,/MULTICHAIN AI-POWERED DEFI TRADING/);assert.equal((home.match(/START TRADING/g)||[]).length,3);assert.equal((home.match(/<li>/g)||[]).length,7);for(const network of ['DogeOS','Solana','Base','Ethereum','BNB Chain','Arbitrum','Robinhood Chain Testnet'])assert.ok(home.includes(network));assert.match(home,/All trading is simulated/);assert.match(home,/ONE PROTOCOL. FOUR PRODUCTS/);
 assert.match(home,/id="how-it-works"/);assert.match(home,/class="doge-accent"/);
 for(const absent of ['<img','<input','id="simulator"','id="intelligence"','id="risk"','<video'])assert.ok(!home.includes(absent),absent);
 for(const anchor of [...home.matchAll(/href="(?:\/)?#([^"]+)"/g)].map(m=>m[1]))assert.ok(home.includes(`id="${anchor}"`),anchor);
 assert.match(portal,/Welcome to VELA/);assert.match(portal,/MetaMask/);
 for(const html of [home,portal]){for(const [id,label] of [['overview','Overview'],['vault','Vault'],['borrow','Borrow'],['trading','AI Trading'],['repay','Repay &amp; Manage']]){assert.ok(html.includes(`href="/${id}"`),id);assert.ok(html.includes(label),label);}assert.match(html,/aria-label="VELA application sections"/);}
 assert.ok(home.indexOf('public-app-tabs')<home.indexOf('hero-editorial'));
 assert.ok(portal.indexOf('public-app-tabs')<portal.indexOf('Welcome to VELA'));
 for(const html of [home,portal]){
  for(const privateControl of ['role="tablist"','class="guided-app"','class="overview-stats"','Open positions','Today · realized','Withdraw DOGE','Authorize AI','Trading equity','id="simulator"'])assert.ok(!html.includes(privateControl),privateControl);
  assert.ok(!/<button[^>]*>Stop &amp; Settle/.test(html));
  assert.ok(!html.includes('AutonomousDeFi')&&!html.includes('VERA'));
 }
 // Authenticated presentation fixtures, never a mocked sign-in or live wallet claim.
 const props={address:'0x1111111111111111111111111111111111111111',markets:[],nativeBalance:'123',busy:false,actions:{},config:{factory:'configured',attestorConfigured:true,modelConfigured:true,network:{blockExplorerUrls:['https://dogeos-testnet.l2scan.co']},risk:{maxLtv:'.4',dogePrice:'.20',annualInterest:'.05',liquidationThreshold:'.65',reduceHealthBelow:1.4,blockHealthBelow:1.2}},data:{vault:{address:'0x2222222222222222222222222222222222222222',loanState:2,collateral:'10000000000000000000000',trading:false,paused:false,limits:{allocation:'800000000',expiresAt:'9999999999'}},health:{collateral:'10000',price:'.20',collateralValue:'2000',principal:'800',interest:'2',debt:'802',borrowCapacity:'0',withdrawableDoge:'0',healthFactor:'1.62',ltv:'.401',liquidationThreshold:'.65',loan:{state:'ACTIVE',paid:'0',seized:'0'}},paper:{equity:'790',available:'790',balance:'790',pnl:'-10',preferences:{autonomous:false},today:{realized:'-10',trades:2,wins:1,losses:1},totals:{trades:3,wins:2,losses:1},positions:[],trades:[],activity:[]},events:[],transactions:[],demoSurplus:'0'}};
 const {NetworkSelector}=await import(pathToFileURL(path.resolve(dir+'/network-selector.mjs')));
 const chains=JSON.parse(await new Promise((resolve,reject)=>{const child=spawn('../.venv/bin/python',['-c','import sys,json;sys.path.insert(0,"../backend");from autodefi.adapters import CHAINS;from autodefi.config import Config;print(json.dumps([c.public(Config()) for c in CHAINS.values()]))']);let output='',error='';child.stdout.on('data',data=>output+=data);child.stderr.on('data',data=>error+=data);child.on('error',reject);child.on('close',code=>code===0?resolve(output):reject(new Error(`Registry fixture failed (${code}): ${error}`)));}));
 for(const chain of chains){
  const config={...props.config,multichain:true,chain,chains,network:chain.network};
  const selector=render(NetworkSelector,{config,busy:false,onChange:()=>{}});
  assert.ok(selector.includes(`data-network-selector="${chain.id}"`));assert.match(selector,/aria-haspopup="listbox"/);assert.ok(!selector.includes("<select"));
  const welcome=render(Welcome,{config,busy:false});assert.match(welcome,/Welcome to VELA/);
  assert.ok(welcome.includes(chain.family==='solana'?'Phantom — Connect':'MetaMask — Connect'));if(chain.id==='dogeos'){assert.match(welcome,/MyDoge/);assert.match(welcome,/disabled="">Coming Soon/);assert.ok(!welcome.includes('mydoge.com'));}
  for(const tab of ['overview','vault','borrow','trading','agent']){
   const data=structuredClone(props.data);data.vault.asset=chain.collateral;data.vault.simulated=chain.id!=='dogeos';
   const html=render(GuidedDashboard,{...props,config,data,initialTab:tab});assert.match(html,/role="tabpanel"/);assert.ok(html.includes(chain.collateral));
  }
 }
 props.config={...props.config,chain:chains.find(c=>c.id==='base')};props.data.vault={...props.data.vault,asset:'ETH',simulated:true};
 const pages={};
 for(const tab of ['overview','vault','borrow','trading','agent']){
  const html=render(GuidedDashboard,{...props,initialTab:tab});pages[tab]=html;
  assert.equal((html.match(/role="tab"/g)||[]).length,5);
  assert.equal((html.match(/role="tabpanel"/g)||[]).length,1);
  assert.ok(html.indexOf('guided-tabs')<html.indexOf('guided-heading'));
  assert.match(html,new RegExp(`id="panel-${tab}"`));
  assert.match(html,/Interactive walkthrough/);assert.match(html,/Open walkthrough:/);
  assert.ok(!html.includes('<video')&&!html.includes('<img'));
  // No advanced panel is expanded by default.
  assert.ok(!/<details[^>]*\sopen/.test(html));
  const tutorial=render(WalkthroughPlayer,{tutorial:tutorials[tab],onClose:()=>{}});
  assert.match(tutorial,/aria-modal="true"/);assert.match(tutorial,/Skip tutorial/);assert.match(tutorial,/no transactions or account changes/);assert.match(tutorial,/Auto-play/);assert.match(tutorial,/Next/);
 }
 assert.match(pages.overview,/YOUR NEXT STEP/);assert.match(pages.overview,/All-time trading P&amp;L/);
 assert.match(pages.vault,/Agent permissions/);assert.match(pages.vault,/Emergency pause/);assert.match(pages.vault,/Revoke agent/);
 assert.match(pages.borrow,/id="simulator"/);assert.match(pages.borrow,/\$800\.00/);assert.match(pages.borrow,/aria-label="Amount to borrow"/);assert.match(pages.borrow,/Review partial repayment/);assert.match(pages.borrow,/Repay full debt/);
 for(const tab of ['overview','vault','trading','agent'])assert.ok(!pages[tab].includes('id="simulator"'));
 assert.match(pages.trading,/Start AI Trading/);assert.match(pages.trading,/Stop AI Trading/);assert.match(pages.trading,/Today · realized P&amp;L \(UTC\)/);assert.match(pages.trading,/2 closed fills · 1 wins \/ 1 losses/);assert.match(pages.trading,/3<\/strong><span>2 wins \/ 1 losses/);assert.match(pages.trading,/UNAVAILABLE \/ STALE/);assert.ok(!pages.trading.includes('>LIVE<'));
 assert.match(pages.agent,/Stop &amp; Settle/);assert.match(pages.agent,/Estimated shortfall<\/dt><dd>\$12\.00/);
 assert.match(pages.agent,/<button class="primary" disabled="">Withdraw ETH/);
 const newUser=structuredClone(props);newUser.data.vault=null;newUser.data.health=null;
 assert.match(render(GuidedDashboard,{...newUser,initialTab:'vault'}),/Create Vault/);
 const pending=structuredClone(props);pending.data.vault.loanState=1;
 const recovery=render(GuidedDashboard,{...pending,initialTab:'borrow'});
 assert.match(recovery,/Resume pending demo loan/);assert.match(recovery,/Cancel uncredited request/);
 const short=structuredClone(props);short.data.health.loan.state='SHORTFALL';
 assert.match(render(GuidedDashboard,{...short,initialTab:'agent'}),/Review simulated liquidation/);
 short.data.health.loan.state='AWAITING_CHAIN';
 assert.ok(!render(GuidedDashboard,{...short,initialTab:'agent'}).includes('Confirm testnet settlement'));
 for(const tab of ['overview','vault','agent'])assert.ok(!pages[tab].includes('class="simulation-notice"'));
 for(const tab of ['borrow','trading'])assert.equal((pages[tab].match(/class="simulation-notice"/g)||[]).length,1);
 assert.match(pages.borrow,/USDC loans are simulated. No real stablecoins are issued/);assert.match(pages.trading,/Trades use live market data and simulated funds. No real orders are placed/);
 assert.ok(home.includes('home-caption'));assert.ok(!home.includes('DEMO BORROWING'));assert.ok(!portal.includes('TESTNET + DEMO'));
 assert.match(pages.agent,/Profit Sweep percentage/);assert.match(pages.agent,/Scheduled|scheduled/);assert.match(pages.agent,/Revoke automation/);
 assert.match(pages.trading,/Allocate demo capital/);assert.match(pages.trading,/Save trading limits/);
 assert.match(render(GuidedDashboard,{...newUser,initialTab:'trading'}),/A vault and loan are optional/);
 {const html=render(GuidedDashboard,{...newUser,initialTab:'trading'});assert.match(html,/How much do you want to trade\?/);assert.match(html,/\$1,000/);assert.match(html,/Fee-inclusive stop-trigger budget \$2\.00 per trade, fees included/);assert.equal((html.match(/>Start AI Trading</g)||[]).length,1);
  const running=structuredClone(newUser);running.data.paper.preferences={autonomous:true};running.data.tradingPermission={positionSize:'100000000',maxLoss:'2000000',leverage:'1',expiresAt:String(Math.floor(Date.now()/1000)+3600)};
  const live=render(GuidedDashboard,{...running,initialTab:'trading'});assert.match(live,/AI is running/);assert.match(live,/>Stop AI Trading</);assert.match(live,/No open trade right now/);assert.doesNotMatch(live,/How much do you want to trade/);}
 {const pages=Object.fromEntries(['overview','vault','borrow','agent'].map(t=>[t,render(GuidedDashboard,{...props,initialTab:t})]));
  assert.match(pages.overview,/In your vault/);assert.match(pages.overview,/Profit \/ loss \(all time\)/);
  assert.match(pages.vault,/How much do you want to add\?/);assert.match(pages.vault,/>Add to vault</);
  assert.match(pages.borrow,/You owe/);assert.match(pages.agent,/How much do you want to repay\?/);assert.match(pages.agent,/Take back my/);
  for(const t of ['overview','vault','borrow','agent'])assert.match(pages[t],/<summary>Advanced<\/summary>/);
  const fresh=structuredClone(props);fresh.data.health.loan=null;fresh.data.health.debt='0';fresh.data.health.borrowCapacity='300';
  assert.match(render(GuidedDashboard,{...fresh,initialTab:'borrow'}),/How much do you want to borrow\?/);}
 const stopped=structuredClone(props);stopped.data.paper.entryStop={reason:'A new demo loan needs trading authorization.',action:'Select Start AI Trading to review and authorize trading with the borrowed demo funds.'};
 const stoppedHtml=render(GuidedDashboard,{...stopped,initialTab:'trading'});assert.match(stoppedHtml,/A new demo loan needs trading authorization/);assert.match(stoppedHtml,/Select Start AI Trading to review/);
 const connecting=render(Welcome,{config:props.config,busy:true,connecting:true});assert.match(connecting,/45 seconds/);assert.match(connecting,/>Cancel connection</);assert.match(connecting,/disabled="">Check your wallet/);
 assert.match(render(Welcome,{config:props.config,busy:false,retryAvailable:true}),/>Retry connection</);
 const disconnected=structuredClone(props);disconnected.config.modelConfigured=false;assert.match(render(GuidedDashboard,{...disconnected,initialTab:'trading'}),/AI disconnected/);assert.match(render(GuidedDashboard,{...disconnected,initialTab:'trading'}),/AI is disconnected/);
 console.log('PASS: modular homepage, public top destinations, 3D illustration, wallet-first privacy, five fixture-rendered top tabs, tutorial dialogs, simulator relocation, trading scopes, pending loan and settlement controls. SSR only; no browser or genuine wallet verification.');
}finally{await rm(dir,{recursive:true,force:true});}
