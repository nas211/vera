/**
 * Pure controller for the DEMO borrow review dialog. BorrowPanel delegates its Confirm
 * decision here so the open/close/error/idempotency outcome is unit-testable without a DOM.
 *
 * Rules:
 *  - success → dialog closes, error cleared, request id consumed
 *  - failure → dialog stays OPEN, engine error shown, request id kept (same attempt may retry)
 *  - repeated Confirm for an already-completed request id → act is NOT called again
 */
import type { Action } from './vela/fixtureEngine';

export type BorrowDialogState = {
  open: boolean;
  error: string | null;
  requestId: string | null;
  completedId: string | null;
};

export type BorrowConfirmResult = {
  next: BorrowDialogState;
  recorded: boolean;
  skipped: null | 'invalid-amount' | 'no-request' | 'already-completed';
};

export function confirmBorrowDialog(
  d: BorrowDialogState,
  amountUsd6: bigint | null,
  act: (a: Action) => string | null,
): BorrowConfirmResult {
  if (amountUsd6 === null) return { next: d, recorded: false, skipped: 'invalid-amount' };
  if (!d.requestId) return { next: d, recorded: false, skipped: 'no-request' };
  if (d.completedId === d.requestId) return { next: d, recorded: false, skipped: 'already-completed' };
  const error = act({ type: 'BORROW', usd6: amountUsd6, requestId: d.requestId });
  if (error) return { next: { ...d, open: true, error }, recorded: false, skipped: null };
  return { next: { open: false, error: null, requestId: null, completedId: d.requestId }, recorded: true, skipped: null };
}
