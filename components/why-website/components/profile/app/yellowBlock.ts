import type { Me } from "../client";
// Plain-language reason for a Yellow entry block, from the runner's own state.
export function yellowBlock(r: Me["runtime"]): { title: string; detail: string; serious: boolean; checkPositions: boolean } {
  const e = r.last_error ?? "", open = r.open_positions > 0 || r.positions.length > 0;
  const pending = r.positions.find(x => x.state === "PROTECTION_PENDING"), unmatched = r.positions.find(x => x.state === "RECONCILIATION_REQUIRED");
  if (unmatched) return { title: "Position needs checking.", detail: `Your ${unmatched.market} ${unmatched.direction} position on Yellow doesn't match the bot's records yet.`, serious: true, checkPositions: true };
  if (pending) return { title: "Waiting for position protection.", detail: `Yellow hasn't confirmed the stop-loss and take-profit for ${pending.market} yet.`, serious: true, checkPositions: true };
  if (e.startsWith("scalp_emergency_close_requires_attention")) return { title: "Emergency close needs attention.", detail: "The $1 emergency close was tried 3 times without being confirmed by Yellow.", serious: true, checkPositions: true };
  if (e.startsWith("scalp_emergency")) return { title: "Emergency close in progress.", detail: "A position hit the $1 loss limit and the bot is closing it.", serious: true, checkPositions: true };
  if (e.includes("invalid_timestamp")) return { title: "Yellow is rejecting requests.", detail: "Signed requests are expiring before Yellow processes them, so the bot is retrying.", serious: true, checkPositions: open };
  if (e === "submission_reconciliation_required") return { title: "Confirming an order.", detail: "Yellow didn't answer an order request clearly, so the bot is checking its outcome before placing anything else.", serious: false, checkPositions: open };
  if (/ConnectionReset|BrokenPipe|feed_closed|feed_error|feed_backlog|Disconnect/i.test(e)) return { title: "Reconnecting to Yellow.", detail: "The connection to Yellow dropped and the bot is reconnecting.", serious: false, checkPositions: open };
  if (r.started === false) return { title: "Starting up.", detail: e ? `The first exchange check hasn't succeeded yet (${e}).` : "The bot is running its first exchange check.", serious: !!e, checkPositions: open };
  if (r.venue === "RECONCILING" || r.venue === "RECOVERING") return { title: "Syncing with Yellow.", detail: e === "scalp_private_data_stale" ? "Yellow is slow to answer the latest account check." : "The bot is matching recent orders and fills with Yellow.", serious: false, checkPositions: false };
  if (r.venue === "DEGRADED" || r.venue === "DISCONNECTED") return { title: "Yellow connection problem.", detail: e ? `The last exchange check failed (${e}).` : "Yellow isn't responding to account checks.", serious: true, checkPositions: open };
  return { title: "Waiting for Yellow.", detail: e ? `The last exchange check failed (${e}).` : "Waiting for a successful exchange check.", serious: !!e, checkPositions: open };
}
