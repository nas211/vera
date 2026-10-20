-- Paper accounts are denominated as simulated USD, with the same $25,000
-- starting balance for every plan. Preserve every account's existing P&L,
-- open positions and margin while increasing the active session baseline.
begin;

with balances as (
  select user_id, session_id, (25000::numeric - starting_balance) delta
  from public.why_paper_accounts
  where starting_balance < 25000
)
update public.why_paper_accounts a
set starting_balance = 25000,
    cash_balance = a.cash_balance + balances.delta,
    equity = a.equity + balances.delta,
    peak_equity = a.peak_equity + balances.delta,
    updated_at = now()
from balances
where a.user_id = balances.user_id;

update public.why_paper_sessions s
set starting_balance = 25000
from public.why_paper_accounts a
where s.id = a.session_id
  and s.starting_balance < 25000;

commit;
