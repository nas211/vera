"""Public strategy settings derived from the same configuration execution uses."""


def strategy_profile(account_id, config, equity=None, runtime=None):
    if config['trading_style'] != 'scalp':
        return dict(id='swing', name='AI Agents — Swing', leverage=config['leverage'],
                    max_open_positions=config['max_open_positions'])

    # Lazy import keeps Swing reads independent of the Scalp strategy loader.
    from .scalp.strategy import CONFIG, RULES, MODULES
    from .scalp.policy import software_emergency_authorized, protected_execution_required
    namespace = 'yellow:' + str(account_id)
    software_guard = software_emergency_authorized(namespace)
    from .scalp.emergency import EMERGENCY_LOSS_USD
    from .scalp.sizing import budget_for_equity
    budget=budget_for_equity(equity)
    runtime=runtime or {}
    return dict(
        id='scalp', name='AI Agents — Scalp', version=CONFIG['strategy_version'],
        sizing_mode='exchange_minimum',
        entry_margin_usd=None, leverage=CONFIG['leverage'], entry_notional_usd=None,
        minimum_entries=runtime.get('minimum_entries',[]),
        waiting_for_market=runtime.get('waiting_for_market'),
        strategy_entry_slots=CONFIG['max_entry_slots'],
        max_open_positions=CONFIG['max_entry_slots'],
        gross_notional_cap_usd=float(budget['gross_notional_cap_usd']),
        per_market_notional_cap_usd=float(budget['per_market_notional_cap_usd']),
        per_side_notional_cap_usd=float(budget['per_side_notional_cap_usd']),
        target_pct=CONFIG['target_bps'] / 100,
        quote_refresh_seconds=CONFIG['refresh_seconds'],
        warmup_seconds=RULES['warmup_seconds'],
        passive_exit_after_seconds=RULES['max_hold_seconds'],
        adverse_exit_pct=RULES['adverse_exit_bps'] / 100,
        exit_refresh_seconds=RULES['exit_refresh_seconds'],
        levels_per_side=CONFIG['levels_per_side'],
        fee_tier=CONFIG['fee_tier'], maker_fee_pct=CONFIG['maker_fee'] * 100,
        taker_fee_pct=CONFIG['taker_fee'] * 100,
        active_markets=list(MODULES['v2'].ACTIVE_MARKETS),
        exit_only_markets=sorted(MODULES['v2'].EXCLUDED_ENTRIES),
        daily_trade_target=None, frequency_mode='signal_driven',
        emergency_stop_usd=float(EMERGENCY_LOSS_USD) if software_guard else None,
        emergency_stop_mode='bot_managed' if software_guard else None,
        availability='coming_soon',
        activation_blocked_reason='Scalp Live — Coming soon. New entries are disabled; existing positions keep being managed.',
    )
