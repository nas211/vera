/** Validate the user's own-funds budget; leveraged exposure is never buying power. */
export function marginSelection(percentage: string, amount: string | null, available: number | null | undefined) {
  const money = (value: number) => `$${value.toFixed(2)}`;
  const invalid = (error: string) => ({ percentage: null, budget: null, error });
  if (available == null || !Number.isFinite(available)) {
    return invalid("Available balance is unavailable. Re-check your exchange connection before saving.");
  }
  if (available <= 0) return invalid("Insufficient available balance. No funds are available for a new trade.");
  let pct: number;
  if (amount !== null) {
    const requested = Number(amount);
    if (!amount.trim() || !Number.isFinite(requested) || requested <= 0) return invalid("Enter an amount greater than $0.");
    if (requested > available) return invalid(`Insufficient available balance. You entered ${money(requested)}; only ${money(available)} is available.`);
    // The existing account setting uses whole percentages. Round down, never
    // allocate more of the user's funds than the dollar amount they entered.
    const ratio = requested / available * 100;
    pct = Math.min(100, Math.floor(ratio + Number.EPSILON * ratio));
    while (pct > 0 && available * pct / 100 > requested) pct -= 1;
    if (pct < 1) return invalid(`The minimum selection is 1% of available funds (${money(available / 100)}).`);
  } else {
    pct = Number(percentage);
    if (!percentage.trim() || !Number.isInteger(pct) || pct < 1) return invalid("Enter a whole percentage from 1 to 100.");
    if (pct > 100) return invalid(`Insufficient available balance. ${pct}% exceeds your ${money(available)} available balance. Choose 100% or less.`);
  }
  return { percentage: pct, budget: available * pct / 100, error: null };
}
