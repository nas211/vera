import React,{useEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import './networks.css';
export function NetworkIcon({network,logo=false}){
 const color={dogeos:'#cba44b',solana:'#aa7aff',base:'#3975ff',ethereum:'#9faeee',bnb:'#f0ba31',arbitrum:'#54a8e8',robinhood:'#a4f64e'}[network]||'#cbef8b';
 if(logo&&network==='bnb')return <svg className="network-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill={color}/><g fill="#101410"><path d="m12 4-4 4 1.8 1.8L12 7.6l2.2 2.2L16 8zm-8 8 2.2-2.2L8.4 12l-2.2 2.2zm8-2.2 2.2 2.2-2.2 2.2L9.8 12zm3.6 2.2 2.2-2.2L20 12l-2.2 2.2zM8 16l4 4 4-4-1.8-1.8-2.2 2.2-2.2-2.2z"/></g></svg>;
 if(logo&&network==='arbitrum')return <svg className="network-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 1.5 9 5.2v10.6l-9 5.2-9-5.2V6.7z" fill="#213147" stroke="#9caec3" strokeWidth="1.4"/><path d="M5 16.5 10 6.8h3.2L7 18zm4 2.2 6-11.9h3.2L12 20.3z" fill="white"/><path d="m15.4 13.6 3.5 6.1 2.1-1.3-4.1-7.1zm-2.1 4.1 1.6 3 2.4-1.4-2.5-4.3z" fill="#28a0f0"/></svg>;
 if(logo&&network==='robinhood')return <svg className="network-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill={color}/><path d="M7 19c.8-3.2 3-6.2 6.5-9.3M8.2 15.7C6.5 8.4 11.7 4.5 18.8 4.2l-1.4 5-3.4 1.2 2.9.3-2.6 3.6-3.2.4-2.9 1z" fill="#101410" stroke="#101410" strokeWidth="1.2" strokeLinecap="round"/><path d="m9.2 14.5 6.9-7" stroke={color} strokeWidth=".8"/></svg>;
 return <svg className="network-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill={color}/>{network==='solana'?<path d="M6 7h12l-2 3H4zm2 4h12l-2 3H6zm-2 4h12l-2 3H4z" fill="#101410"/>:network==='ethereum'?<path d="m12 3-6 9 6 4 6-4zm-6 11 6 7 6-7-6 4z" fill="#101410"/>:network==='base'?<path d="M5 12h14" stroke="white" strokeWidth="3"/>:<text x="12" y="17" textAnchor="middle" fontSize="14" fontWeight="700" fill="#101410">{{dogeos:'Ð',bnb:'B',arbitrum:'A',robinhood:'R'}[network]}</text>}</svg>;
}
export function NetworkSelector({config,busy,onChange}){
 const [open,setOpen]=useState(false),[closing,setClosing]=useState(false),[active,setActive]=useState(0),[position,setPosition]=useState({});
 const trigger=useRef(null),menu=useRef(null),closeTimer=useRef(null),id=useId();
 const chains=config?.chains||[],selected=chains.findIndex(c=>c.id===config?.chain?.id);
 const close=()=>{if(!open)return;setOpen(false);setClosing(true);clearTimeout(closeTimer.current);closeTimer.current=setTimeout(()=>setClosing(false),120);};
 const show=()=>{if(!busy){clearTimeout(closeTimer.current);setClosing(false);setActive(Math.max(0,selected));setOpen(true);}};
 useEffect(()=>()=>clearTimeout(closeTimer.current),[]);
 useEffect(()=>{if(busy)close();},[busy]);
 useEffect(()=>{
  if(!open)return;
  const place=()=>{const box=trigger.current.getBoundingClientRect(),width=Math.min(340,window.innerWidth-24),below=window.innerHeight-box.bottom-16,above=box.top-16;
   setPosition({width,left:Math.max(12,Math.min(box.left,window.innerWidth-width-12)),...(below>=Math.min(440,above)?{top:box.bottom+8,maxHeight:Math.max(120,below)}:{bottom:window.innerHeight-box.top+8,maxHeight:Math.max(120,above)})});};
  const outside=e=>{if(!trigger.current?.contains(e.target)&&!menu.current?.contains(e.target))close();};
  place();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);document.addEventListener('pointerdown',outside);
  menu.current?.focus();
  return()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);document.removeEventListener('pointerdown',outside);};
 },[open]);
 useEffect(()=>{menu.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({block:'nearest'});},[active,open]);
 const select=index=>{const c=chains[index];if(c?.enabled&&!busy){close();trigger.current?.focus();if(c.id!==config.chain.id)onChange(c.id);}};
 function keys(e){
  if(e.key==='Escape'){e.preventDefault();close();trigger.current?.focus();return;}
  if(e.key==='Tab'){close();trigger.current?.focus();return;}
  if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();if(!open){show();return;}const enabled=chains.map((c,i)=>c.enabled?i:-1).filter(i=>i>=0),at=enabled.indexOf(active);setActive(e.key==='Home'?enabled[0]:e.key==='End'?enabled.at(-1):enabled[(at+(e.key==='ArrowDown'?1:-1)+enabled.length)%enabled.length]);}
  else if(e.key==='Enter'||e.key===' '){e.preventDefault();open?select(active):show();}
  else if(e.key.length===1){const index=chains.findIndex(c=>c.enabled&&c.name.toLowerCase().startsWith(e.key.toLowerCase()));if(index>=0)setActive(index);}
 }
 if(!config?.multichain)return null;
 const chain=config.chain;
 const name=c=>c.id==='bnb'?'BNB Chain':c.name;
 return <div className="network-switch"><button type="button" ref={trigger} className="network-trigger" aria-label={`Network: ${name(chain)}`} aria-haspopup="listbox" aria-expanded={open} aria-controls={open?id:undefined} data-network-selector={chain.id} disabled={busy} onClick={()=>open?close():show()} onKeyDown={keys}><NetworkIcon network={chain.id} logo/><span>{name(chain)}<small>{chain.id==='dogeos'?'Featured · Chikyū Testnet · Demo':chain.testnet?'Testnet · Demo':'Demo'}</small></span><span className="network-chevron" aria-hidden="true">⌄</span></button>{(open||closing)&&createPortal(<div ref={menu} id={id} className={`network-menu ${closing?'is-closing':''}`} aria-hidden={!open} role="listbox" aria-label="Choose blockchain" tabIndex={0} aria-activedescendant={`${id}-${active}`} style={position} onKeyDown={keys} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget)&&e.relatedTarget!==trigger.current)close();}}><div className="network-menu-heading">CHOOSE YOUR NETWORK</div>{chains.map((c,i)=><div role="option" id={`${id}-${i}`} key={c.id} data-index={i} aria-selected={chain.id===c.id} aria-disabled={!c.enabled} className={`network-option ${active===i?'is-focused':''}`} onPointerMove={()=>setActive(i)} onMouseDown={e=>e.preventDefault()} onClick={()=>select(i)}><NetworkIcon network={c.id} logo/><span>{name(c)}<small>{c.id==='dogeos'?'Featured · Chikyū Testnet · Demo':c.testnet?'Testnet · Demo':'Demo'} · {c.collateral}</small></span>{chain.id===c.id&&<span className="network-check" aria-hidden="true">✓</span>}</div>)}</div>,document.body)}</div>;
}
