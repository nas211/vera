import { Panel } from '@/components/common/Panel';
import { Details } from '@/components/common/Details';
import { SourceTag } from '@/components/common/SourceTag';
import { PLACEHOLDERS } from '@/lib/vela/integrationConfig';

/** Unresolved integration requirements — shown, never invented. */
export function PlaceholderPanel() {
  return (
    <Panel eyebrow="Configuration" title="Connections" aside={<SourceTag kind="off" label="OFF" />}>
      <p className="text-sm text-muted-foreground">Sample data only. Live accounts are not connected.</p>
      <Details label="Connection details">
      <ul className="divide-y divide-border/60 text-sm">
        {PLACEHOLDERS.map((p) => (
          <li key={p.key} className="flex flex-col gap-0.5 py-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p>{p.label}</p>
              <p className="font-mono text-[11px] text-muted-foreground">{p.key} · {p.stage}</p>
            </div>
            <span className={`num text-xs ${p.value ? 'text-primary' : 'text-muted-foreground'}`}>{p.value ?? 'placeholder — unset'}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] text-muted-foreground">
        Noah preview and Vela run on different origins. Cookies or tokens are never copied across; live Vela data needs a reviewed same-origin deployment or a narrowly scoped integration API.
      </p>
      </Details>
    </Panel>
  );
}
