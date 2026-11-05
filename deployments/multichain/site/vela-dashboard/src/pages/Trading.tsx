import { TradingStatusCard } from '@/components/trading/TradingStatusCard';
import { PositionsPanel } from '@/components/trading/PositionsPanel';
import { FillsPanel } from '@/components/trading/FillsPanel';
import { ActualResultPanel } from '@/components/trading/ActualResultPanel';

export default function Trading() {
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <div className="space-y-4 lg:col-span-5">
        <TradingStatusCard />
        <ActualResultPanel />
      </div>
      <div className="space-y-4 lg:col-span-7">
        <PositionsPanel />
        <FillsPanel />
      </div>
    </div>
  );
}
