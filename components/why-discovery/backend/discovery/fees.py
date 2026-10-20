"""Conservative paper fee assumption: 4bp per fill on entry and exit."""
FEE_RATE = .0004
MODEL_VERSION = 2


def effective_net(trade):
    # Preserve recorded historical economics. Qualification can compare legacy
    # zero-fee trials with new costed trials without rewriting the audit ledger.
    if trade.get('fee_model_version') == MODEL_VERSION:
        return trade['net']
    if trade.get('fees') == 0 and all(k in trade for k in ('qty','entry','exit')):
        return trade['net'] - trade['qty']*(trade['entry']+trade['exit'])*FEE_RATE
    return trade['net']
