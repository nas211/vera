"use client";
import {useEffect,useState} from "react";
import type {PaperAccount} from "../client";
import s from "./dashboard.module.css";
type Settings=PaperAccount["settings"];
export function PaperSizing({value,busy,onSave,onDiscovery,discoveryActive=false,onCloseDiscovery}:{discoveryActive?:boolean;onCloseDiscovery?:()=>void;onDiscovery:()=>void;value:Settings;busy:boolean;onSave:(value:Settings)=>void}){
 const [leverage,setLeverage]=useState(value.leverage),[size,setSize]=useState(value.position_size_pct);
 useEffect(()=>{setLeverage(value.leverage);setSize(value.position_size_pct);},[value.leverage,value.position_size_pct]);
 const dirty=leverage!==value.leverage||size!==value.position_size_pct;
 return <section className={`${s.panel} ${s.paperSizing}`} aria-label="Paper sizing settings"><div className={s.cardHead}><h3>Paper sizing</h3><span className={s.modeBadge}>Simulated</span></div><div className={s.sizingGrid}>
 <div><label htmlFor="paper-strategy-choice">Paper strategy</label><select id="paper-strategy-choice" value={discoveryActive?"why-discovery-paper":value.strategy} onChange={e=>{if(e.target.value==="why-discovery-paper")onDiscovery();else onCloseDiscovery?.();}} style={{width:"100%",padding:"10px",border:"1px solid #46503e",borderRadius:6,background:"#171c17",color:"#e7e6e0"}}><option value={value.strategy}>Swing</option><option value="scalp" disabled>Scalp — Coming soon</option><option value="why-discovery-paper">AI Agents Trading · Paper</option></select></div>
 <div><label>Position size</label>{discoveryActive ? <p>Managed by AI</p> : <div className={s.sizeOptions} role="group" aria-label="Paper position size">{[1,2,5,10,15,20].map(n=><button type="button" key={n} aria-pressed={size===n} disabled={busy} onClick={()=>setSize(n)}>{n}%</button>)}</div>}</div>
 <div><label>Leverage</label>{discoveryActive ? <p>Per strategy</p> : <div className={s.sizeOptions} role="group" aria-label="Paper leverage">{[2,5,10,15,20].map(n=><button type="button" key={n} aria-pressed={leverage===n} disabled={busy} onClick={()=>setLeverage(n)}>{n}x</button>)}</div>}</div>
 </div>{!discoveryActive && <div className={s.sizingFooter}><small>Paper account only. Existing risk limits still apply.</small><button type="button" className={s.headingButton} disabled={!dirty||busy} onClick={()=>onSave({...value,leverage,position_size_pct:size,risk_preset:"custom"})}>{busy?"Saving…":"Save Paper settings"}</button></div>}</section>;
}
