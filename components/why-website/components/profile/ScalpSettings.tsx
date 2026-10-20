import type { ScalpProfile } from "./exchanges";

export function ScalpSettings({ profile }: { profile?: ScalpProfile }) {
  if (!profile) return <p className="pf-note" role="status">Scalp settings are unavailable. Refresh to load the current strategy defaults.</p>;
  const pct = (value: number) => `${Number(value.toFixed(4))}%`;
  const dollars = (value: number) => `$${value.toLocaleString("en-US", { maximumFractionDigits: 4 })}`;
  const markets = (values: string[]) => values.map(value => value.replace("USDT-PERP", "")).join(", ");
  return <section className="pf-field" aria-label="Scalp default settings">
    <h3 className="pf-label">Scalp defaults</h3>
    <p className="pf-dim">These settings apply automatically when Scalp is selected. Your Swing trade-size settings are saved separately and return when you switch back.</p>
    <dl className="pf-kv2">
      <div><dt className="pf-k">Margin per entry</dt><dd className="pf-v">{profile.sizing_mode === "exchange_minimum" ? "Coin minimum" : `$${profile.entry_margin_usd}`} at {profile.leverage}×</dd><p className="pf-dim">{profile.sizing_mode === "exchange_minimum" ? "The smallest quantity that meets the coin’s lot-size and minimum order-value rules. Eligible markets take turns; a larger minimum waits for available margin." : `Up to $${profile.entry_notional_usd} position value, rounded down to the exchange lot size.`}</p></div>
      <div><dt className="pf-k">Trades per day</dt><dd className="pf-v">No fixed daily count</dd><p className="pf-dim">Signal-driven. Actual fills depend on market conditions, available funds and account limits; some days may have no trades.</p></div>
      <div><dt className="pf-k">Entry quote refresh</dt><dd className="pf-v">Every {profile.quote_refresh_seconds} seconds</dd><p className="pf-dim">After a {profile.warmup_seconds}-second signal warmup. Quote refreshes are not completed trades.</p></div>
      <div><dt className="pf-k">Profit target</dt><dd className="pf-v">{pct(profile.target_pct)}</dd><p className="pf-dim">Maker exits or profit-protected IOC exits when executable after the exit fee. Funding excluded; fills and profits are not guaranteed.</p></div>
      <div><dt className="pf-k">Passive exit rule</dt><dd className="pf-v">{profile.passive_exit_after_seconds} seconds or {pct(profile.adverse_exit_pct)} adverse move</dd><p className="pf-dim">Passive orders can be repriced every {profile.exit_refresh_seconds} seconds. This is not a guaranteed close time or hard stop loss.</p></div>
      <div><dt className="pf-k">Scalp entry slots</dt><dd className="pf-v">Up to {profile.strategy_entry_slots} simultaneous entries</dd><p className="pf-dim">Includes open entries and pending entry orders. Coin minimums, available funds and signals determine how many can trade.</p></div>
      <div><dt className="pf-k">Total exposure limit</dt><dd className="pf-v">{dollars(profile.gross_notional_cap_usd)} notional</dd><p className="pf-dim">Available margin and pending entry reservations also limit new orders.</p></div>
      <div><dt className="pf-k">Per-market exposure limit</dt><dd className="pf-v">{dollars(profile.per_market_notional_cap_usd)} notional</dd><p className="pf-dim">Up to {dollars(profile.per_side_notional_cap_usd)} per side in each market.</p></div>
      <div><dt className="pf-k">Entry grid</dt><dd className="pf-v">{profile.levels_per_side} levels per side</dd><p className="pf-dim">Post-only maker entries, subject to signal and inventory checks.</p></div>
      {profile.emergency_stop_usd != null && <div><dt className="pf-k">Emergency loss trigger</dt><dd className="pf-v">{dollars(profile.emergency_stop_usd)} per entry</dd><p className="pf-dim">The bot cancels resting orders, verifies cancellation, then submits a market close. Includes recorded trading fees and estimated exit fees; funding excluded. Requires the bot and exchange connection. Slippage can increase the final loss. Ordinary trade sizes and exit rules stay unchanged.</p></div>}
      <div><dt className="pf-k">Configured {profile.fee_tier} fees</dt><dd className="pf-v">{pct(profile.maker_fee_pct)} maker · {pct(profile.taker_fee_pct)} taker</dd></div>
    </dl>
    {profile.minimum_entries && profile.minimum_entries.length > 0 && <div className="pf-field"><h4 className="pf-label">Estimated minimum margin now</h4><p className="pf-dim">Changes with price and the exchange’s lot size.</p><dl className="pf-kv2">{profile.minimum_entries.map(entry => <div key={entry.market}><dt className="pf-k">{entry.market.replace("USDT-PERP", "")}</dt><dd className="pf-v">{dollars(entry.margin_usd)}</dd></div>)}</dl></div>}
    {profile.waiting_for_market && <p className="pf-note" role="status">Waiting for available margin for {profile.waiting_for_market.replace("USDT-PERP", "")}’s minimum order. Existing exits remain active.</p>}
    <p className="pf-dim">Entry markets: {markets(profile.active_markets)}.</p>
    <p className="pf-dim">Entries paused on {markets(profile.exit_only_markets)}; existing positions can still exit.</p>
    {profile.activation_blocked_reason && <p className="pf-note pf-note-amber" role="alert">{profile.activation_blocked_reason}</p>}
  </section>;
}
