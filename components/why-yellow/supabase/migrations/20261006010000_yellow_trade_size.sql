-- NULL preserves the existing coin-minimum policy until the user saves a percentage.
begin;
set local lock_timeout = '5s';
alter table public.yellow_bot_configs add column if not exists trade_margin_pct integer
  check (trade_margin_pct between 1 and 100);
grant select (trade_margin_pct) on public.yellow_bot_configs to authenticated;
commit;
