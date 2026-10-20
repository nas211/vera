"use client";
export type BulkResearch = {
  status: string; evaluated: number; planned: number; training_passed?: number; shortlisted?: number;
  completed?: number; heartbeat?: number; source?: string;
  forward_simulations?: { status: string; configurations: number; as_of?: number; completed_simulated_trades?: number; open_simulated_positions?: number };
};
const count = (n?: number) => (n ?? 0).toLocaleString("en-US");
export function ResearchProgress({ research }: { research?: BulkResearch }) {
  if (!research) return null;
  const f = research.forward_simulations;
  const stale = !f?.as_of || Date.now() / 1000 - f.as_of > 900;
  const metrics = [
    ["Historical configurations evaluated", `${count(research.evaluated)} / ${count(research.planned)}`],
    ["Passed training checks", count(research.training_passed)],
    ["Finalists passing both later periods", count(research.shortlisted)],
    ["Configurations in forward simulation", count(f?.configurations)],
    ["Completed simulated trades", count(f?.completed_simulated_trades)],
    ["Open simulated positions", count(f?.open_simulated_positions)],
  ];
  return <section aria-label="Broad strategy research" style={{ padding: "20px 0", borderBottom: "1px solid #ffffff20", marginBottom: 20 }}>
    <h3 style={{ margin: "0 0 8px" }}>Broad strategy research</h3>
    <p style={{ fontSize: 13, opacity: .75 }}>Historical screen: {research.status.toLowerCase().replaceAll("_", " ")} · Forward simulation: {stale ? "awaiting fresh update" : f?.status.toLowerCase()}</p>
    <dl style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, margin: "16px 0" }}>
      {metrics.map(([label, value]) => <div key={label}><dt style={{ fontSize: 12, opacity: .7 }}>{label}</dt><dd style={{ fontSize: 22, margin: "4px 0", fontVariantNumeric: "tabular-nums" }}>{value}</dd></div>)}
    </dl>
    <p style={{ fontSize: 12, opacity: .7, lineHeight: 1.6, margin: 0 }}>Counts are parameter configurations, not independent strategies. Historical results use Binance candles as a proxy. Finalists are selected using the training period before later-period checks. Forward simulations use fresh Yellow candles with estimated costs; they are separate from order-book paper fills and account P&amp;L. Passing research checks does not enable live trading.</p>
    {!!f?.as_of && <p style={{ fontSize: 12, opacity: .6, marginBottom: 0 }}>Forward update: {new Date(f.as_of * 1000).toLocaleString()}</p>}
  </section>;
}
