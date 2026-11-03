import React,{useEffect,useRef,useState} from 'react';
import './protocol-visual.css';

const chapters=[
 {name:'Lock DOGE',title:'Upcoming: put your collateral to work.',copy:'Collateral-backed liquidity is upcoming. Today, explore read-only DogeOS testnet vaults and separate demo funds; customer deposits are disabled.',out:'DOGE → VAULT',className:'deposit'},
 {name:'Unlock capital',title:'Upcoming: collateral-backed trading capital.',copy:'Unlocking liquidity against your assets is upcoming. Current USDC borrowing experiences are simulations; no real borrowing or stablecoin transfer is available.',out:'VAULT → USDC CAPITAL',className:'borrow'},
 {name:'Let AI trade',title:'Give the capital a job. Give the AI limits.',copy:'AI analyzes live market data and proposes paper trades using simulated funds. Permission and risk checks govern execution. Ecosystem rewards are upcoming.',out:'USDC → TRADING ENGINE',className:'trade'},
 {name:'Repay & unlock',title:'Upcoming: repay and unlock your collateral.',copy:'Collateral-backed repayment and withdrawals are upcoming. Current demo repayments are simulated; customer contract settlement and withdrawals remain disabled.',out:'REPAYMENT → ELIGIBLE DOGE',className:'repay'},
];
function Token({kind='doge',className=''}){return <span className={`protocol-token ${kind} ${className}`}><span>{kind==='doge'?'Ð':'$'}</span></span>;}
export function ProtocolVisual(){
 const [phase,setPhase]=useState(0),[playing,setPlaying]=useState(false),[visible,setVisible]=useState(false),[reduced,setReduced]=useState(false);
 const host=useRef(null);
 useEffect(()=>{
  const media=window.matchMedia('(prefers-reduced-motion: reduce)');
  const motion=()=>{setReduced(media.matches);setPlaying(!media.matches);};motion();media.addEventListener('change',motion);
  const observer=typeof IntersectionObserver==='undefined'?null:new IntersectionObserver(entries=>setVisible(entries[0].isIntersecting),{threshold:.2});
  if(observer)observer.observe(host.current);else setVisible(true);
  return()=>{media.removeEventListener('change',motion);observer?.disconnect();};
 },[]);
 useEffect(()=>{
  if(!playing||!visible||reduced)return;
  const timer=setInterval(()=>{if(!document.hidden)setPhase(p=>(p+1)%chapters.length);},6500);
  return()=>clearInterval(timer);
 },[playing,visible,reduced]);
 const chapter=chapters[phase];
 return <figure className={`protocol-visual phase-${chapter.className} ${playing&&visible&&!reduced?'is-playing':''}`} ref={host} aria-labelledby="visual-title">
  <div className="visual-overline"><span>THE VELA ENGINE</span><span>ILLUSTRATIVE</span></div>
  <div className="protocol-stage" aria-hidden="true">
   <div className="stage-halo"/><div className="stage-floor"/>
   <svg className="capital-pipes" viewBox="0 0 520 350" fill="none"><path className="doge-pipe" d="M52 160C100 160 103 213 153 213"/><path className="usdc-pipe" d="M299 207C360 207 335 105 431 105"/><path className="engine-pipe" d="M431 127C487 163 486 246 428 260"/><path className="repay-pipe" d="M411 283C360 345 129 320 68 214"/></svg>
   <div className="wallet-anchor"><span>YOUR WALLET</span><div className="wallet-outline"><i/></div></div>
   <Token className="deposit-token"/>
   <div className="vault-plinth"/>
   <div className="protocol-vault"><div className="vault-face vault-side"/><div className="vault-face vault-roof"/><div className="vault-face vault-front"><div className="vault-inset"><span className="vault-engraving">VELA</span><div className="vault-dial"><i/><i/><i/><span/></div><span className="vault-latch"/></div><span className="vault-light"/><span className="vault-caption">WALLET-OWNED</span></div></div>
   <div className="capital-anchor"><span>USDC CAPITAL</span><Token kind="usdc"/></div>
   <Token kind="usdc" className="capital-token"/>
   <div className="ai-anchor"><span>AI TRADING ENGINE</span><div className="ai-orbit"><i/><div className="ai-core">AI</div></div></div>
   <Token kind="usdc" className="repay-token"/>
   <Token className="return-token"/>
   <div className="stage-label"><span>{chapter.out}</span><i>0{phase+1} / 04</i></div>
  </div>
  <div className="visual-chapters" role="group" aria-label="Explore how VELA works">{chapters.map((c,i)=><button key={c.name} aria-pressed={phase===i} onClick={()=>{setPhase(i);setPlaying(false);}}><span>0{i+1}</span>{c.name}</button>)}</div>
  <figcaption><h2 id="visual-title">{chapter.title}</h2><p aria-live={playing?'off':'polite'}>{chapter.copy}</p><div><small>Protocol animation · not account activity</small><button className="visual-play" disabled={reduced} onClick={()=>setPlaying(p=>!p)} aria-label={playing?'Pause protocol animation':'Play protocol animation'}>{reduced?'Reduced motion':playing?'Ⅱ Pause':'▷ Play'}</button></div></figcaption>
 </figure>;
}
