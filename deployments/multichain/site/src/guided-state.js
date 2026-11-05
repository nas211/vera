// Presentation preferences only. Never store authentication, balances or account data.
import {ROUTES,tabForPath,navigate} from './navigation.js';
export const TABS=[['overview','Overview'],['vault','Vault'],['borrow','Borrow'],['trading','AI Trading'],['agent','Repay & Manage']];
export const validTab=value=>value==='settle'?'agent':TABS.some(([id])=>id===value)?value:null;
export const preferenceKey=(owner,suffix)=>`vella:ui:${(String(owner).startsWith('solana:')?String(owner):String(owner).toLowerCase())}:${suffix}`;
export function readTab(owner,hash='',storage){
 const requested=validTab(hash.replace(/^#/,''));
 if(requested)return requested;
 try{return validTab(storage?.getItem(preferenceKey(owner,'tab')))||'overview';}catch{return 'overview';}
}
export function saveTab(owner,tab,storage){
 if(!validTab(tab))return;
 try{storage?.setItem(preferenceKey(owner,'tab'),tab);}catch{/* Private browsing must not block navigation. */}
}
export function bindTabHistory(host,initialTab,onTab){
 if(host.location.pathname){
  const update=()=>onTab(validTab(host.location.hash.slice(1))||tabForPath(host.location.pathname)||'overview');
  update();host.addEventListener('popstate',update);host.addEventListener('hashchange',update);host.addEventListener('vela:navigate',update);
  return()=>{host.removeEventListener('popstate',update);host.removeEventListener('hashchange',update);host.removeEventListener('vela:navigate',update);};
 }
 // Canonicalize the initial entry before pushing others. Back must restore that
 // entry rather than reading the most recently persisted tab preference.
 if(!validTab(host.location.hash.slice(1)))host.history.replaceState(host.history.state,'',`#${validTab(initialTab)||'overview'}`);
 const update=()=>onTab(validTab(host.location.hash.slice(1))||'overview');
 host.addEventListener('popstate',update);host.addEventListener('hashchange',update);
 return()=>{host.removeEventListener('popstate',update);host.removeEventListener('hashchange',update);};
}
export function selectTab(tab,host,onTab){
 if(!validTab(tab))return;
 onTab(tab);
 if(host.location.pathname){navigate(ROUTES[validTab(tab)],host);return;}
 if(host.location.hash!==`#${tab}`)host.history.pushState(null,'',`#${tab}`);
}
export function nextStep(data,config,now=Date.now()){
 const v=data.vault,h=data.health,l=h?.loan,p=data.paper;
 if(config.chain?.id==='dogeos')return Number(p.equity)>0?{tab:'trading',title:'Your paper trading account.',text:'Demo USD is separate from native test DOGE. Valid trading permission continues after disconnect until expiry or Stop.',label:'Open paper trading'}:{tab:'vault',title:'Your native test DOGE vault.',text:'Review confirmed vault balances. USDC borrowing is unavailable; paper trading has separate demo funds.',label:'Open test DOGE vault'};
 if(config.chain?.capabilities.simulatedVault){const asset=v?.asset||config.chain.collateral;if(!v)return {tab:'vault',title:'A home for your demo '+asset+'.',text:'Create a simulated vault for this wallet and network. No real funds move.',label:'Create simulated vault'};if(Number(h?.collateral)<=0)return {tab:'vault',title:'Allocate demo '+asset+'.',text:'Demo collateral is not redeemable. Your real wallet balance stays separate.',label:'Allocate demo collateral'};if(l?.state==='REPAID')return {tab:'agent',title:'Eligible demo collateral is unlocked.',text:'Review the simulation ledger and remove eligible collateral.',label:'Review withdrawal'};}
 if(!v&&Number(p.equity)>0)return {tab:'trading',title:'Your trading capital is ready.',text:'Set your risk limits and start AI Trade. No vault or borrowing is required.',label:'Open AI Trade'};
 if(!v)return {tab:'vault',title:'A home for your DOGE.',text:config.factory?'Create a wallet-owned vault, or open AI Trade and allocate capital independently.':'Testnet vault deployment is pending. AI Trade can be used independently when its model and market data are configured.',label:'Go to your vault'};
 if(v.paused)return {tab:'vault',title:'Your vault is paused.',text:'Review vault controls before taking the next step.',label:'Review vault'};
 if(v.loanState===1)return {tab:'borrow',title:'Finish your borrowing request.',text:'Resume or cancel the existing request before submitting another.',label:'Review pending request'};
 if(['CLOSING','SHORTFALL','AWAITING_CHAIN','DEFAULT_PENDING'].includes(l?.state))return {tab:'agent',title:'Let’s settle up.',text:'Review outstanding debt and the next settlement confirmation. Collateral remains locked until eligible.',label:'Review settlement'};
 if(l?.state==='DEFAULTED')return {tab:'agent',title:'Review the settled shortfall.',text:v.simulated?'Review recorded simulated bad debt. Exhausted collateral cannot be withdrawn.':'Review recorded bad debt and contract-confirmed withdrawal availability.',label:'Review settlement'};
 if(l?.state==='REPAID'&&Number(h.withdrawableDoge)>0)return {tab:'agent',title:'Your eligible DOGE is ready.',text:'Your wallet approves the withdrawal of unlocked test DOGE.',label:'Review withdrawal'};
 if(Number(h?.collateral)<=0)return {tab:'vault',title:'Give your vault some DOGE.',text:'Deposit faucet DOGE with a testnet transaction approved in your wallet.',label:'Deposit test DOGE'};
 if(l?.state!=='ACTIVE')return {tab:'borrow',title:'Your vault is ready.',text:'Keep using your vault independently, or explore optional borrowing. You choose which products to use.',label:'Explore borrowing'};
 if(Number(v.limits?.expiresAt||0)*1000<=now)return {tab:'vault',title:'Set your AI’s boundaries.',text:'Review allocation and authorize an agent before starting simulated trading.',label:'Review agent permissions'};
 return {tab:'trading',title:p.preferences.autonomous?'Your AI is watching the markets.':'Ready when you are.',text:p.preferences.autonomous?'Monitor positions and loan health. The model may choose to wait.':config.modelConfigured?'Review your trading allocation, then start AI trading. Risk checks still apply.':'A dedicated model connection is required before AI trading can start.',label:p.preferences.autonomous?'Monitor AI trading':'Review AI trading'};
}
