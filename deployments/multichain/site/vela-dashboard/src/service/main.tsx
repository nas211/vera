import '../polyfills';
import '../index.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter, BrowserRouter } from 'react-router-dom';
import { INTEGRATION } from '@/lib/vela/integrationConfig';
import { DashboardFrame } from '@/components/layout/DashboardFrame';
import { AccountDashboard } from './approved/AccountDashboard';
import { ConnectedService } from './ConnectedService';
import { OwnerSetup } from '../owner/OwnerSetup';
import { DogeLendingSetup } from '../owner/DogeLendingSetup';
import { DemoRepaymentReserve } from '../owner/DemoRepaymentReserve';
declare const __VELA_TESTNET_RELEASE__: boolean;

if (__VELA_TESTNET_RELEASE__) {
  let theme = 'day';
  try { if (localStorage.getItem('vela:theme') === 'night') theme = 'night'; } catch { /* Use the default when storage is unavailable. */ }
  document.documentElement.dataset.theme = theme;
  document.documentElement.classList.toggle('dark', theme === 'night');
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing dashboard root');
// Preserve existing bookmarks while the released app gains direct /settings URLs.
if(__VELA_TESTNET_RELEASE__ && /^\/app\/?$/.test(location.pathname) && /^#\/(vault|borrow|trading|risk|settings)?$/.test(location.hash)) {
  const route=location.hash.slice(1);
  history.replaceState(null,'',(route==='/settings'?route:'/app'+route)+location.search);
}
if(__VELA_TESTNET_RELEASE__ && /^\/app\/?$/.test(location.pathname) && location.hash==='#trade') history.replaceState(null,'','/app/trading'+location.search);
const Router=__VELA_TESTNET_RELEASE__?BrowserRouter:HashRouter;
createRoot(root).render(<StrictMode>{__VELA_TESTNET_RELEASE__ && new URLSearchParams(location.search).get('setup')==='repayments' ? <DemoRepaymentReserve /> : __VELA_TESTNET_RELEASE__ && new URLSearchParams(location.search).get('setup')==='lending' ? <DogeLendingSetup /> : __VELA_TESTNET_RELEASE__ && new URLSearchParams(location.search).get('setup')==='rewards' ? <OwnerSetup /> : <Router>
  {(__VELA_TESTNET_RELEASE__ || INTEGRATION.serviceEnabled) ? <ConnectedService testnetRelease={__VELA_TESTNET_RELEASE__} /> : <DashboardFrame
    banner={<div className="fixture-stripe py-2 text-center text-xs">Prepared review · no external account connection · blockchain writes OFF</div>}
    wallet={<span className="text-xs text-muted-foreground">Wallet connection disabled</span>}
    footer="No deployment or verified live protection. Fixture rehearsals use a separate entry and ledger."
  ><AccountDashboard service={null} /></DashboardFrame>}
</Router>}</StrictMode>);
