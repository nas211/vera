begin;
set local lock_timeout = '5s';
alter table public.yellow_bot_configs add column if not exists trading_style text not null default 'swing'
  check (trading_style in ('swing', 'scalp'));
grant select (trading_style) on public.yellow_bot_configs to authenticated;
commit;
