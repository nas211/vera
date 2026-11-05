import { Outlet } from 'react-router-dom';
import { DashboardFrame } from './DashboardFrame';
import { ModeBanner } from './ModeBanner';
import { WalletControl } from './WalletControl';
import { FixtureControls } from './FixtureControls';
import { usePreviewMode } from '@/state/PreviewMode';
import { Button } from '@/components/ui/button';
import { Details } from '@/components/common/Details';

export function AppShell() {
  const preview = usePreviewMode();
  return <DashboardFrame banner={<ModeBanner />} wallet={preview ? <Button size="sm" disabled>Preview · no wallet</Button> : <WalletControl />} footer={<>
    Sample data · Demo USD · No wallet transactions.
    <Details label="About this preview">Testnet demo incentives funded by the project; no production yield promised. DOGE is not a staking-yield source. Risk parameters are illustrative, not validated production values. Refresh resets sample data. Live protection and payouts are unverified.</Details>
  </>}>
    <Outlet /><Details label="Sample scenarios"><FixtureControls /></Details>
  </DashboardFrame>;
}
