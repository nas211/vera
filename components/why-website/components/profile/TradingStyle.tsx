"use client";

export type TradingStyleId = "swing" | "scalp";

export function TradingStyle({ value, busy, paused = false, blockedReason, onChange, onDiscovery, controlId = "live-trading-style" }: {
  controlId?: string;
  onDiscovery: () => void;
  value: TradingStyleId | undefined;
  busy: boolean;
  paused?: boolean;
  blockedReason?: string;
  onChange: (style: TradingStyleId) => void;
}) {
  return <div style={{ margin: "16px 0", maxWidth: 440 }} aria-label="Live strategy selection">
    {paused && value && <p style={{ marginBottom: 10, fontSize: 13 }}>Current trading strategy: <strong>{value === "scalp" ? "Scalp Trading — Coming soon" : "Swing"}</strong> · Paused</p>}
    <label htmlFor={controlId} style={{ display: "block", marginBottom: 8, fontSize: 13, fontWeight: 600 }}>Trading strategy</label>
    <select id={controlId} value={value ?? ""} disabled={busy} aria-describedby={`${controlId}-help`}
      onChange={event => {
        const style = event.target.value;
        if (busy) return;
        if (style === "why-discovery-paper") {
          event.target.value = value ?? "";
          onDiscovery();
          return;
        }
        if (style === "swing" && value && !blockedReason) onChange(style);
      }}
      style={{ width: "100%", padding: "12px", borderRadius: 8, border: "1px solid var(--steel)", color: "var(--platinum)", background: "var(--carbon-2)", font: "inherit" }}>
      {!value && <option value="" disabled>Loading your saved style…</option>}
      <option value="swing" disabled={!value || !!blockedReason}>Swing</option>
      <option value="scalp" disabled>Scalp — Coming soon</option>
      <option value="why-discovery-paper">AI Agents Trading · Paper</option>
    </select>
    <p id={`${controlId}-help`} style={{ marginTop: 8, color: "var(--ash)", fontSize: 12, lineHeight: 1.5 }}>
      AI Agents Trading shows your paper experiments here in your profile.
      {" "}{blockedReason || (paused ? "Live strategy changes require all positions and pending orders to be cleared." : "Pause live trading before changing its strategy.")}
      {" "}Live changes require confirmation. Choosing a strategy does not start trading.
    </p>
  </div>;
}
