import type { AiCandidate, AiController } from "./useDiscovery";
import { money } from "./model";
import s from "./dashboard.module.css";
const duration = (n: number) => n >= 3600 ? `${Math.round(n / 360) / 10}h` : n >= 60 ? `${Math.round(n / 60)}m` : `${n}s`;
const words = (s: string) => s.toLowerCase().replaceAll("_", " ");
const reviewReason = (reason: string) => {
  if (reason.startsWith("CONTEXT_") || reason.startsWith("NEWS_") || reason.startsWith("MARKET_CONTEXT_")) return "Fresh news, calendar or market-risk evidence is unavailable. The review will retry.";
  if (reason === "MARKET_DATA_STALE") return "Waiting for fresh market prices.";
  if (reason === "AI_RATE_LIMIT") return "The model request budget is exhausted. The review will retry.";
  if (reason === "AI_REQUEST_FAILED" || reason === "REVIEW_EVIDENCE_OR_AI_UNAVAILABLE") return "Required review evidence or the model response is unavailable. The review will retry.";
  if (reason === "DIRECTION_NOT_ALIGNED_NEW_VERSION_REQUIRED") return "The direction review does not support this strategy's side.";
  return words(reason);
};
export function AiStrategies({ candidates, controller, archivedVersions = 0 }: { candidates: AiCandidate[]; controller?: AiController; archivedVersions?: number }) {
  return <details className={s.panel} style={{ padding: 18, marginTop: 20 }}>
    <summary style={{ cursor: "pointer" }}>Strategies · {candidates.length}</summary>
    <p className={s.muted}>Screening and forward results are separate. Forward qualification requires at least 50 valid trades over 24 hours, positive profit, a profit factor of at least 1.2 and no more than $10 drawdown. It is paper evidence, not a profitability guarantee.</p>
    <div className={s.tablePanel}><div className={s.tableScroll} tabIndex={0} role="region" aria-label="AI strategies"><table><thead><tr><th>Strategy / market</th><th>Status</th><th>Screening</th><th>Screen P&amp;L</th><th>Forward paper</th><th>Max hold</th></tr></thead><tbody>
      {candidates.map(c => <tr key={c.id}>
        <td><strong>{c.spec.name}</strong><span>{c.spec.market} · {c.spec.side}</span></td>
        <td>{c.state === "APPROVED_PAPER" ? "Forward paper testing" : words(c.state)}
          {c.state === "REVIEW_DELAYED" && c.review?.reason && <p>Review blocked: {reviewReason(c.review.reason)}</p>}
          {c.expiry && <p>Expired: no completed valid trial progress.</p>}
          {c.revision && <details><summary>{c.revision.status === "WAITING_SLOT" ? "Revision queued" : words(c.revision.status)}</summary>{c.revision.reason && <p>{c.revision.reason}</p>}{c.revision.proposal && <p>Stop {c.revision.proposal.stop_bps}bp · target {c.revision.proposal.target_bps}bp · hold {duration(c.revision.proposal.max_hold_seconds)}</p>}</details>}
        </td>
        <td>{c.score.closed}/10 · {c.score.wins} wins</td><td className={c.score.net < 0 ? s.negative : s.positive}>{money(c.score.net)}</td>
        <td>{c.forward_score && c.forward_score.closed > 0 ? <><span>{c.forward_score.closed} trades · {c.forward_score.wins} wins</span><span className={c.forward_score.net < 0 ? s.negative : s.positive}>{money(c.forward_score.net)}</span></> : "No forward trades"}
          {c.validation && c.validation.closed > 0 && <details><summary>{words(c.validation.status)} · {c.validation.closed}/{c.validation.minimum_trades} trades</summary><p>Observed window: {duration(c.validation.elapsed_seconds)} / {duration(c.validation.minimum_seconds)} minimum</p><p>Average result {money(c.validation.expectancy)} · max drawdown {money(c.validation.max_drawdown)}</p><p>Profit factor: {c.validation.profit_factor == null ? "No realized losses in sample" : c.validation.profit_factor.toFixed(2)}</p></details>}
        </td><td>{duration(c.spec.max_hold_seconds)}</td>
      </tr>)}
    </tbody></table>{!candidates.length && <p className={s.muted}>No strategies yet.</p>}</div></div>
    {archivedVersions > 0 && controller && <details><summary>Archived strategies · {archivedVersions}</summary>
      <p>Original versions and trade records are retained. Archived results remain included in account totals.</p>
      {controller.archive?.items.map(({summary: a, candidate: c}) => <details key={a.id}><summary>{a.spec.name} · {words(a.state)} · {money(a.screen.net + a.forward.net)} valid-trial P&amp;L</summary>
        <p>{a.spec.market} · {a.spec.side} · {a.screen.closed} screening and {a.forward.closed} forward trades</p>
        <div className={s.tableScroll}><table><thead><tr><th>Closed</th><th>P&amp;L</th><th>Exit / validity</th></tr></thead><tbody>{c.trades.map(t => <tr key={t.id}><td>{new Date(t.closed * 1000).toISOString()}</td><td>{money(t.net)}</td><td>{words(t.reason)}{t.valid === false ? ` · excluded: ${words(t.invalid_reason || "invalid trial")}` : ""}</td></tr>)}</tbody></table></div>
      </details>)}
      {controller.archiveError && <p role="alert">{controller.archiveError}</p>}
      {controller.archive?.next !== null && <button type="button" disabled={controller.archiveBusy} onClick={() => void controller.loadArchive()}>{controller.archiveBusy ? "Loading history…" : controller.archive ? "Load more archived strategies" : "Load archived strategies"}</button>}
    </details>}
  </details>;
}
