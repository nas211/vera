import React,{useEffect,useRef,useState} from 'react';
import './protocol-visual.css';

const chapters=[
 {name:'Lock & earn APY',title:'Planned: lock collateral and earn APY.',copy:'Lock crypto in your vault as collateral and earn APY while it is locked. Vault yield is planned; no live vault APY or funded rewards are available today.',out:'LOCK COLLATERAL → VAULT + APY (PLANNED)',className:'deposit'},
 {name:'Borrow USDC',title:'Take a USDC loan against your vault.',copy:'Use your locked collateral to borrow a USDC loan, then put the borrowed funds to work with AI trading. Current loans are simulated; real collateral-backed borrowing is planned.',out:'VAULT → BORROW → USDC LOAN',className:'borrow'},
 {name:'Let AI trade',title:'Put the loan to work with AI trading.',copy:'AI analyzes live market data and trades simulated loan funds within your risk limits. Any net trading profits can go toward repaying your loan.',out:'AI TRADING → POTENTIAL PROFITS',className:'trade'},
 {name:'Profits repay loan',title:'Repay with profits. Unlock your collateral.',copy:'Use any trading profits to repay your loan and interest. Once the full debt is settled, unlock your collateral. Profits are not guaranteed; any shortfall must be covered. This flow is simulated today.',out:'PROFITS → REPAY LOAN → UNLOCK COLLATERAL',className:'repay'},
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
   <div className="vault-yield-label"><span>LOCK &amp; EARN APY</span><small>PLANNED</small></div>
   <div className="vault-plinth"/>
   <div className="protocol-vault"><div className="vault-face vault-side"/><div className="vault-face vault-roof"/><div className="vault-face vault-front"><div className="vault-inset"><span className="vault-engraving">VELA</span><div className="vault-dial"><i/><i/><i/><span/></div><span className="vault-latch"/></div><span className="vault-light"/><span className="vault-caption">WALLET-OWNED</span></div></div>
   <div className="capital-anchor"><span>BORROW USDC</span><small>COLLATERAL LOAN</small><Token kind="usdc"/></div>
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
