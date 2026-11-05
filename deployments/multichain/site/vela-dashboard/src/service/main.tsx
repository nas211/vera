import '../polyfills';
import '../index.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import { INTEGRATION } from '@/lib/vela/integrationConfig';
import { DashboardFrame } from '@/components/layout/DashboardFrame';
import { AccountDashboard } from './approved/AccountDashboard';
import { ConnectedService } from './ConnectedService';
import { OwnerSetup } from '../owner/OwnerSetup';
declare const __VELA_TESTNET_RELEASE__: boolean;

const root = document.getElementById('root');
if (!root) throw new Error('Missing dashboard root');
createRoot(root).render(<StrictMode>{__VELA_TESTNET_RELEASE__ && new URLSearchParams(location.search).get('setup')==='rewards' ? <OwnerSetup /> : <HashRouter>
  {(__VELA_TESTNET_RELEASE__ || INTEGRATION.serviceEnabled) ? <ConnectedService testnetRelease={__VELA_TESTNET_RELEASE__} /> : <DashboardFrame
    banner={<div className="fixture-stripe py-2 text-center text-xs">Prepared review · no external account connection · blockchain writes OFF</div>}
    wallet={<span className="text-xs text-muted-foreground">Wallet connection disabled</span>}
    footer="No deployment or verified live protection. Fixture rehearsals use a separate entry and ledger."
  ><AccountDashboard service={null} /></DashboardFrame>}
</HashRouter>}</StrictMode>);
