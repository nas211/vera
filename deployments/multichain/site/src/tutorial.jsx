import React,{useEffect,useRef,useState,useMemo} from 'react';
import {networkTutorial,duration} from './tutorials.js';
import {preferenceKey} from './guided-state.js';

export function WalkthroughPlayer({tutorial,onClose}){
 const [index,setIndex]=useState(0),[playing,setPlaying]=useState(false),[complete,setComplete]=useState(false);
 const dialog=useRef(null),previous=useRef(null),step=tutorial.steps[index];
 useEffect(()=>{
  previous.current=document.activeElement;const before=document.body.style.overflow;document.body.style.overflow='hidden';
  dialog.current?.querySelector('button')?.focus();
  return()=>{document.body.style.overflow=before;previous.current?.focus();};
 },[]);
 useEffect(()=>{
  if(!playing||complete||tutorial.media?.src)return;
  const timer=setTimeout(()=>{if(index===tutorial.steps.length-1){setPlaying(false);setComplete(true);}else setIndex(i=>i+1);},step.seconds*1000);
  return()=>clearTimeout(timer);
 },[playing,complete,index,step.seconds,tutorial]);
 const keydown=e=>{
  if(e.key==='Escape'){e.stopPropagation();onClose();}
  if(e.key==='Tab'){
   const nodes=[...dialog.current.querySelectorAll('button:not(:disabled),a[href],video[controls],[tabindex="0"]')];
   const first=nodes[0],last=nodes.at(-1);
   if(!dialog.current.contains(document.activeElement)){e.preventDefault();first?.focus();return;}
   if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
   else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
  }
 };
 useEffect(()=>{document.addEventListener('keydown',keydown);return()=>document.removeEventListener('keydown',keydown);});
 return <div className="modal-backdrop tutorial-backdrop" onClick={onClose}><section className="modal panel tutorial-player" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="walkthrough-title" onClick={e=>e.stopPropagation()}>
  <button className="close" aria-label="Close walkthrough" onClick={onClose}>×</button><p className="eyebrow">LEARN AT YOUR PACE</p><h2 id="walkthrough-title">{tutorial.title}</h2>
  {tutorial.media?.src?<video controls preload="metadata" aria-label={tutorial.title}><source src={tutorial.media.src} type="video/mp4"/>{tutorial.media.captions&&<track kind="captions" src={tutorial.media.captions} srcLang="en" label="English" default/>}Your browser cannot play this tutorial.</video>:<>
   <div className={`walkthrough-scene scene-${step.visual}`} key={index} aria-label="Illustrative process, not your account"><span className="walkthrough-doge" aria-hidden="true">Ð</span><div>{step.labels.map((label,i)=><React.Fragment key={label}>{i>0&&<span className="walkthrough-arrow" aria-hidden="true">→</span>}<span className="walkthrough-node">{label}</span></React.Fragment>)}</div></div>
   <div className="walkthrough-progress" aria-label={`Step ${index+1} of ${tutorial.steps.length}`}>{tutorial.steps.map((s,i)=><button key={s.title} aria-label={`Step ${i+1}: ${s.title}`} aria-current={index===i?'step':undefined} onClick={()=>{setIndex(i);setComplete(false);}}><span/></button>)}</div>
   <div className="walkthrough-script" aria-live="polite"><small>STEP {index+1} / {tutorial.steps.length}{complete?' · COMPLETE':''}</small><h3>{step.title}</h3><p>{step.narration}</p></div>
   <div className="walkthrough-controls"><button disabled={index===0} onClick={()=>{setIndex(i=>i-1);setComplete(false);}}>Back</button><button onClick={()=>{if(complete){setIndex(0);setComplete(false);}setPlaying(!playing);}}>{complete?'Replay':playing?'Pause':'Auto-play'}</button><button className="primary" onClick={()=>index===tutorial.steps.length-1?onClose():(setIndex(i=>i+1),setComplete(false))}>{index===tutorial.steps.length-1?'Got it':'Next'}</button></div>
  </>}
  {tutorial.videoPage&&<a className="tx-link" href={tutorial.videoPage} target="_blank" rel="noreferrer">Watch generated tutorial video ↗</a>}
  <div className="walkthrough-end"><span>Interactive guide · no transactions or account changes</span><button className="text-button" onClick={onClose}>Skip tutorial</button></div>
 </section></div>;
}
export function Tutorial({tab,owner,chain,asset}){
 const tutorial=useMemo(()=>networkTutorial(tab,chain,asset),[tab,chain,asset]),key=preferenceKey(owner,`tutorial:${tab}`);
 const [open,setOpen]=useState(false),[dismissed,setDismissed]=useState(()=>{try{return localStorage.getItem(key)==='dismissed';}catch{return false;}});
 const dismiss=()=>{setDismissed(true);try{localStorage.setItem(key,'dismissed');}catch{}};
 return <>{dismissed?<button className="tutorial-restore text-button" onClick={()=>setOpen(true)}>↗ {tutorial.title} · View walkthrough</button>:<div className="tutorial-tile"><button className="tutorial-launch" onClick={()=>setOpen(true)} aria-label={`Open walkthrough: ${tutorial.title}`}><span className="tutorial-preview" aria-hidden="true"><span>Ð</span></span><span><strong>{tutorial.title}</strong><small>~{duration(tutorial)} sec · {tutorial.media?.src?'Video tutorial':'Interactive walkthrough'}</small></span><span className="tutorial-open" aria-hidden="true">↗</span></button><button className="tutorial-dismiss" aria-label="Dismiss tutorial" onClick={dismiss}>×</button></div>}{open&&<WalkthroughPlayer tutorial={tutorial} onClose={()=>setOpen(false)}/>}</>;
}
