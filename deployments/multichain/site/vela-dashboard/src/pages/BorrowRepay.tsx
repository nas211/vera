import { LoanStatusPanel } from '@/components/borrow/LoanStatusPanel';
import { BorrowPanel } from '@/components/borrow/BorrowPanel';
import { RepayPanel } from '@/components/borrow/RepayPanel';
import { SweepPermissionPanel } from '@/components/borrow/SweepPermissionPanel';
import { DemoCashPanel } from '@/components/borrow/DemoCashPanel';
import { RepaymentHistory } from '@/components/risk/RepaymentHistory';

export default function BorrowRepay() {
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <div className="space-y-4 lg:col-span-5">
        <LoanStatusPanel />
        <DemoCashPanel />
      </div>
      <div className="space-y-4 lg:col-span-7">
        <div className="grid gap-4 md:grid-cols-2">
          <BorrowPanel />
          <RepayPanel />
        </div>
        <SweepPermissionPanel />
        <RepaymentHistory />
      </div>
    </div>
  );
}
