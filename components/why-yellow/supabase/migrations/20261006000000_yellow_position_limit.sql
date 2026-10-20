-- Allow user-selected caps without changing any existing account values.
begin;
set local lock_timeout = '5s';
alter table public.yellow_bot_configs
  drop constraint if exists yellow_bot_configs_max_open_positions_check;
alter table public.yellow_bot_configs
  add constraint yellow_bot_configs_max_open_positions_check
  check (max_open_positions between 1 and 8);
commit;
