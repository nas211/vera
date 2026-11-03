import React,{useEffect,useState} from 'react';
import {TABS,validTab} from './guided-state.js';
import {ROUTES,tabForPath} from './navigation.js';
import {ProtocolVisual} from './protocol-visual.jsx';
import {NetworkIcon} from './network-selector.jsx';
import './landing.css';

export const Launch=()=> <a className="vella-launch" href="/vault">START TRADING <span aria-hidden="true">↗</span></a>;
// Presentation only; wallet capabilities continue to come from /api/config.
export function NetworkStrip(){
 return <div className="home-networks"><p className="home-kicker">SEVEN NETWORKS. ONE INTERFACE.</p><ul aria-label="Seven network experiences">{[['dogeos','DogeOS','Featured · testnet'],['solana','Solana','Demo'],['base','Base','Demo'],['ethereum','Ethereum','Demo'],['bnb','BNB Chain','Demo'],['arbitrum','Arbitrum','Demo'],['robinhood','Robinhood Chain Testnet','Testnet · demo']].map(([id,name,status])=><li key={id}><NetworkIcon network={id}/><span>{name}<small>{status}</small></span></li>)}</ul><p className="home-caption">All trading is simulated. Six networks offer demo DeFi; DogeOS retains its separate testnet workflow. No real deposits, borrowing or exchange orders.</p></div>;
}
export function AppSectionLinks({className=''}){
 const [selected,setSelected]=useState(()=>typeof window!=='undefined'?tabForPath(window.location.pathname):null);
 useEffect(()=>{const update=()=>setSelected(tabForPath(window.location.pathname));const events=['hashchange','popstate','vela:navigate'];events.forEach(e=>window.addEventListener(e,update));return()=>events.forEach(e=>window.removeEventListener(e,update));},[]);
 return <nav className={`public-app-tabs ${className}`} aria-label="VELA application sections">{TABS.map(([id,label])=><a key={id} href={ROUTES[id]} aria-current={selected===id?'page':undefined}>{label}</a>)}</nav>;
}
export function HomeNav(){
 return <header className="vella-nav"><a className="vella-skip" href="#main-content">Skip to content</a><div className="vella-nav-main"><a className="vella-brand" href="/" aria-label="VELA homepage"><span aria-hidden="true">V</span>VELA</a><AppSectionLinks/><Launch/></div></header>;
}
export default function Landing(){
 return <div className="vella-home" id="main-content">
  <section className="vella-hero" aria-labelledby="home-title"><div className="hero-editorial"><div className="hero-eyebrow"><span className="doge-accent" aria-hidden="true">Ð</span><span>DOGEOS · FEATURED ECOSYSTEM<small>MULTICHAIN AI-POWERED DEFI TRADING</small></span></div><h1 id="home-title">DeFi, <br/><em>Reimagined.</em></h1><p className="hero-subheadline">Intelligent Trading. Limitless Possibilities.</p><p className="hero-copy">Put your crypto to work with AI-powered trading. Unlock liquidity by using your assets as collateral, access opportunities across multiple blockchains, and earn rewards as the ecosystem grows—all from one seamless platform.</p><p className="home-caption hero-availability"><strong>Upcoming:</strong> collateral-backed liquidity and ecosystem rewards. Available now: demo funds and paper trading.</p><div className="home-actions"><Launch/><a className="how-link" href="/how-it-works">How It Works <span aria-hidden="true">↓</span></a></div><NetworkStrip/></div><ProtocolVisual/></section>
  <section className="vella-process modular-intro" id="how-it-works" aria-labelledby="how-title"><p className="home-kicker">ONE PROTOCOL. FOUR PRODUCTS.</p><h2 id="how-title">Choose your starting point.</h2><p>Explore today's demo products independently or together. Collateral-backed liquidity and ecosystem rewards are upcoming features.</p><nav className="module-strip" aria-label="Explore VELA products">{[['vault','Vault','Explore demo collateral and read-only DogeOS testnet vaults.'],['borrow','Borrow','Explore simulated USDC borrowing. Collateral-backed liquidity is upcoming.'],['trading','AI Trading','Automate paper-trading strategies.'],['agent','Repay & Manage','Explore simulated vault, borrowing, trading and repayment.']].map(([id,title,copy],i)=><a key={id} href={ROUTES[id]}><small>0{i+1} ↗</small><strong>{title}</strong><span>{copy}</span></a>)}</nav></section>
  <section className="vella-final" aria-labelledby="final-title"><div><p className="home-kicker">YOU SET THE BOUNDARIES.</p><h2 id="final-title">Your network. Your rules.<br/><span>Your AI.</span></h2></div><div className="final-invitation"><Launch/></div></section>
  <footer className="home-footer"><span>VELA</span><p>DogeOS featured · Lighter market data · Simulated trading</p><a href="#main-content">Back to top ↑</a></footer>
 </div>;
}
