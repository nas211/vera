import { FlaskConical } from 'lucide-react';
import { SourceTag } from '@/components/common/SourceTag';
import { usePreviewMode } from '@/state/PreviewMode';
import { BUILD_ID } from '@/lib/buildInfo';

/** Always-visible statement of what is and isn't live. */
export function ModeBanner() {
  const preview = usePreviewMode();
  return (
    <div className="fixture-stripe border-b border-fixture/20">
      <div className="container flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-[11px] text-fixture">
        <FlaskConical className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="font-semibold">{preview ? 'Noah UI preview — sample balances and actions.' : 'Fixture mode — deterministic rehearsal data.'}</span>
        <span className="text-fixture/80">{preview ? 'Wallets off · no transactions.' : 'Blockchain writes OFF · sample data only.'}</span>
        {preview && <span className="num" title={BUILD_ID}>Build {BUILD_ID.slice(0, 12)}</span>}
        <SourceTag kind="off" className="ml-auto" />
      </div>
    </div>
  );
}
