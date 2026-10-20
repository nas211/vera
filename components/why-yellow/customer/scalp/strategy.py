"""Load exact strategy source in an isolated module namespace.
Signal and exit rules are inherited; live entries use exchange minimum sizes.
"""
import builtins
import copy
import hashlib
import json
from pathlib import Path
import types

ROOT = Path(__file__).with_name('original')
NAMES = ('runner', 'v2', 'overnight', 'maker_only', 'profit_capture')


def load(account_sizing=False):
    expected = json.loads((ROOT / 'sha256.json').read_text())
    modules = {}
    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        if name in modules and not level:
            return modules[name]
        return builtins.__import__(name, globals, locals, fromlist, level)
    for name in NAMES:
        path = ROOT / (name + '.py')
        code = path.read_bytes()
        if hashlib.sha256(code).hexdigest() != expected[path.name]:
            raise RuntimeError('microscalp_strategy_hash_mismatch:' + name)
        module = types.ModuleType('why_live_scalp_' + name)
        module.__file__ = str(path)
        module.__dict__['__builtins__'] = dict(vars(builtins), __import__=importing)
        exec(compile(code, str(path), 'exec'), module.__dict__)
        if account_sizing and name == 'runner':
            from .sized_runner import AccountSizedRunner
            module.Paper = type('AccountSizedPaper', (AccountSizedRunner, module.Paper), {})
        modules[name] = module
    modules['profit_capture'].configure()
    return modules


MODULES = load()
BASE = MODULES['profit_capture'].ProfitCapturePaper
CONFIG = MODULES['runner'].CONFIG
RULES = MODULES['v2'].RULES
MARKETS = MODULES['runner'].MARKETS
# Worst price a stop/time exit IOC may take beyond the touch; unfilled
# remainder retries on the next frame at the fresh touch.
STOP_SLIPPAGE_BPS = 30
# A triggered stop first rests a free maker close at the touch, re-posted
# whenever the touch moves. It turns taker only after this long unfilled or
# after this much further adverse movement from the trigger.
STOP_MAKER_SECONDS = 5
STOP_MAKER_BPS = 3
STOP_REPRICE_SECONDS = 1
STATE_FIELDS = ('started', 'orders', 'lots', 'stats', 'next_id', 'halted', 'finished_at', 'last_mid', 'last_quote', 'entry_cursor', 'waiting_entry_market')
LIVE_MODULES = load(account_sizing=True)
LIVE_BASE = LIVE_MODULES['profit_capture'].ProfitCapturePaper


class LiveStrategy(LIVE_BASE):
    def __init__(self, filters, clock):
        self.intents = []
        self.pending_exits = set()
        self.allow_entries = True
        self.coin_entry_policy = False
        self.entry_cooldowns = {}
        self.entry_cursor = 0
        self.waiting_entry_market = None
        self.strategy_config = CONFIG
        self.strategy_markets = MARKETS
        self.set_equity(None)
        super().__init__(filters, clock=clock, record=False)

    def set_equity(self, equity):
        from .sizing import budget_for_equity
        self.budget = budget_for_equity(equity)

    def event(self, kind, **fields):
        if kind == 'POST_ONLY_SUBMIT':
            self.intents.append(dict(kind='maker', **fields['order']))

    def trade(self, market, trade):
        # Public prints inform the market feed, never an account's real fills.
        self.stats['public_trades'] += 1

    def place(self, market, buy, price, qty, lot=None):
        from .availability import SCALP_LIVE_AVAILABLE
        if not lot and not SCALP_LIVE_AVAILABLE:
            return None
        if not lot and not self.allow_entries:
            return None
        if not lot and self.coin_entry_policy:
            if self.clock()<self.entry_cooldowns.get(market,0):
                return None
        if lot and (lot in self.pending_exits or lot in self.profit_requests):
            return None
        if lot:
            price, reason = self.exit_price(self.lots[lot])
        else:
            reason = self.entry_reason(market, buy)
            if reason:
                self.blocked[reason] += 1
                return None
            if self.occupied_entry_slots() >= CONFIG['max_entry_slots']:
                self.blocked['entry_slot_limit'] += 1
                return None
            price = self.rounded(market, price, buy)
            from .sizing import minimum_quantity
            qty = float(minimum_quantity(price, self.filters[market]))
            room = float(self.budget['per_side_notional_cap_usd']) - self.side_committed(market, buy)
            if qty <= 0 or qty * price > room:
                self.blocked['side_inventory_limit'] += 1
                return None
        oid = LIVE_MODULES['runner'].Paper.place(self, market, buy, price, qty, lot)
        if oid and lot:
            self.orders[oid]['exit_reason'] = reason
        return oid

    def maintain(self):
        self.enforce_stop_exits()
        super().maintain()

    def enforce_stop_exits(self):
        # Loss and time exits: a maker close chases the touch for a few
        # seconds ($0 fee), then a bounded taker IOC. The original re-posts
        # only every 5s while price runs away: live, 188 of 265 exits outlived
        # the 120s hold and lost $2.55 of the $2.84 total (down to -102 bps).
        if not self.connected:
            return
        now = self.clock()
        for lid, lot in list(self.lots.items()):
            m = lot['market']
            if lid in self.pending_exits or lid in self.profit_requests:
                continue
            if not self.top(m) or now-self.books[m]['received'] > 15:
                continue
            maker_price, reason = self.exit_price(lot)
            if reason == 'target':
                lot.pop('stop_at', None)
                lot.pop('stop_mid', None)
                continue
            bid, ask = self.top(m)
            mid = (bid+ask)/2
            if 'stop_at' not in lot:
                lot['stop_at'], lot['stop_mid'] = now, mid
            further = (mid/lot['stop_mid']-1)*10000*(1 if lot['buy'] else -1)
            resting = [(oid, o) for oid, o in self.orders.items() if o['lot'] == lid]
            if now-lot['stop_at'] < STOP_MAKER_SECONDS and further > -STOP_MAKER_BPS:
                # Withdraw a stale maker close; the strategy re-posts it at the
                # current touch within this same maintain pass.
                touch = self.rounded(m, maker_price, not lot['buy'])
                for oid, o in resting:
                    if (o['cancel_at'] is None and now-o['requested'] >= STOP_REPRICE_SECONDS
                            and abs(o['price']-touch) >= self.filters[m]['tick']*.99):
                        self.orders.pop(oid)
                        self.stats['cancelled'] += 1
                        lot['exit_id'] = None
                continue
            for oid, o in resting:
                self.orders.pop(oid)
                self.stats['cancelled'] += 1
            lot['exit_id'] = None
            buy = not lot['buy']
            slip = STOP_SLIPPAGE_BPS/10000
            price = self.rounded(m, ask*(1+slip) if buy else bid*(1-slip), not buy)
            self.next_id += 1
            self.intents.append(dict(kind='ioc', id=str(self.next_id), market=m, buy=buy, price=price,
                                     qty=lot['qty'], lot=lid, requested=now, reason='STOP_EXIT'))
            self.pending_exits.add(lid)

    def request_profit(self, lid):
        if lid not in self.pending_exits:
            return super().request_profit(lid)

    def execute_profit(self, lid):
        if lid in self.pending_exits:
            return
        # Run the ORIGINAL decision/calculation on a disposable projection.
        # The resulting liquidity/price decision becomes an IOC request. Its
        # synthetic fill/accounting is discarded; only private fills book P&L.
        projection = copy.copy(self)
        projection.lots = copy.deepcopy(self.lots)
        projection.stats = dict(self.stats)
        projection.used_depth = dict(self.used_depth)
        events = []
        projection.event = lambda kind, **data: events.append((kind, data))
        BASE.execute_profit(projection, lid)
        for kind, data in events:
            if kind != 'TAKER_EXIT_FILL':
                continue
            lot = self.lots[lid]
            self.next_id += 1
            self.intents.append(dict(kind='ioc', id=str(self.next_id), market=lot['market'], buy=not lot['buy'],
                                     price=data['depth_fills'][-1]['price'], qty=data['qty'], lot=lid,
                                     requested=self.clock()))
            self.pending_exits.add(lid)
            self.used_depth = projection.used_depth

    def checkpoint(self):
        return {key: copy.deepcopy(getattr(self, key)) for key in STATE_FIELDS}

    def restore_checkpoint(self, state):
        for key in STATE_FIELDS:
            if key in state:
                setattr(self, key, state[key])
        self.intents = []
        self.connected = False
