"""ExecutionEngine: the safe execution core.

Guarantees (each backed by tests/execution):
  * one intent -> at most one venue order (deterministic client_order_id, intent row
    written before submit, atomic claim, no order-POST retries);
  * ambiguous transport -> UNKNOWN -> RECONCILIATION_REQUIRED; resubmission only after a
    reconciliation proved venue absence;
  * fills open a position that enters PROTECTION_PENDING and must reach PROTECTED;
  * entry switch never blocks management; venue READY only after reconciliation;
  * strict validation + sizing before any transport call; stale data blocks entries.
Strategy logic is NOT here: signals arrive as EntrySignal; sleeve geometry is read
from execution.geometry (a mirror of yellow_paper.CFG defaults)."""
from __future__ import annotations
import json
import hashlib
import threading
import time
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional
from . import geometry
from .ledger import Ledger, make_coid
from .market_data import FreshnessMonitor
from .metadata import MarketFilters, MetadataSource
from .sizing import ExposureLimits, SizingError, size_order, margin_percentage_notional, loss_budget_notional, entry_loss_limit
from .loss_stop import stop_price as dollar_stop_price, loss_limit as dollar_loss_limit
from .state_machine import IllegalTransition, ORDER_AMBIGUOUS, ORDER_LIVE, ORDER_TERMINAL
from .transport import iter_pages, TransportAmbiguous, VenueRejected, VenueUnavailable, all_pages
from .types import (EntrySignal, OrderPurpose as PU, OrderRequest, OrderState as O, PositionState as P, VenueOrder,
                    VenueState as V, VenueTrade, VENUE_CANCELLED, VENUE_FILLED, VENUE_OPEN, VENUE_REJECTED, D)
from .validation import ValidationError, validate_market_known, validate_order
from .venue_state import VenueMonitor


def protection_coid(position_id, kind, version):
    """Preserve existing short IDs; bound future revisions to Yellow's 36 chars."""
    legacy = f"{position_id}-{kind}{version}"
    if len(legacy) <= 36:
        return legacy
    return 'why-' + hashlib.sha256(legacy.encode()).hexdigest()[:32]


@dataclass
class EngineConfig:
    leverage: int = field(default_factory=geometry.leverage)
    limits: ExposureLimits = field(default_factory=lambda: ExposureLimits(Decimal("2000"), Decimal("16000"), 8))
    unknown_grace_s: float = 5.0            # UNKNOWN orders younger than this are not declared absent
    protection_timeout_s: float = 60.0
    protection_max_attempts: int = 3
    protection_failure_policy: str = "flatten"   # "flatten" | "flag"
    cancel_max_attempts: int = 3
    trades_lookback_s: float = 6 * 3600
    accounts: List[str] = field(default_factory=list)   # accounts to reconcile even when the ledger is empty
    trade_margin_pct: Optional[int] = None
    confirmed_taker_fee: Optional[Decimal] = None
    max_planned_loss_usd: Optional[Decimal] = None
    take_profit_margin_pct: Optional[Decimal] = None
    take_profit_taker_fee: Optional[Decimal] = None
    risk_plan_required: bool = False
    loss_halt_timezone: Optional[str] = None
    entry_hold_reason: Optional[str] = None


@dataclass
class SubmitResult:
    status: str            # ACKNOWLEDGED | REJECTED | UNKNOWN | BLOCKED | DUPLICATE | FAILED
    client_order_id: Optional[str]
    reason: str = ""


@dataclass
class ReconcileReport:
    ok: bool
    reconcile_id: str
    mismatches: List[dict] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    error: str = ""


class ExecutionEngine:
    def __init__(self, ledger: Ledger, transport, metadata: MetadataSource, freshness: FreshnessMonitor,
                 venue: VenueMonitor, config: Optional[EngineConfig] = None, clock: Callable[[], float] = None):
        self.ledger, self.t, self.meta, self.fresh, self.venue = ledger, transport, metadata, freshness, venue
        self.cfg = config or EngineConfig()
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.blocked_markets: Dict[str, set] = {}       # account -> markets blocked by orphan/side mismatch
        self._reconcile_seq = 0
        self.accounts: set = set(self.cfg.accounts)
        self._pass_new_orders: Optional[set] = None      # coids created inside the running reconcile pass
        self._pass_new_positions: Optional[set] = None

    def register_account(self, account_id: str) -> None:
        self.accounts.add(account_id)

    def _note_new_order(self, coid: str) -> None:
        if self._pass_new_orders is not None:
            self._pass_new_orders.add(coid)

    def _note_new_position(self, pid: str) -> None:
        if self._pass_new_positions is not None:
            self._pass_new_positions.add(pid)

    @staticmethod
    def _submitted_ts(o: dict) -> float:
        return float(o["submitted_ts"] if o.get("submitted_ts") is not None else o["state_ts"])

    # ================================================================ lifecycle
    def start(self) -> ReconcileReport:
        """Restart recovery: nothing is resubmitted; ambiguous orders are marked for
        reconciliation, then the venue is reconciled before READY."""
        with self.lock:
            now = self.clock()
            for o in self.ledger.orders_where([O.SUBMITTING]):
                self.ledger.transition_order(o["client_order_id"], O.UNKNOWN, f"restart_unknown:{o['client_order_id']}:{now}", reason="restart_during_submit")
            for o in self.ledger.orders_where([O.UNKNOWN]):
                self.ledger.transition_order(o["client_order_id"], O.RECONCILIATION_REQUIRED, f"restart_recon:{o['client_order_id']}:{now}", reason="restart")
            for pos in self.ledger.positions_where([P.CLOSE_PENDING, P.PROTECTION_PENDING, P.PROTECTED]):
                pass  # resolved by reconcile() below
            self.venue.on_reconnect()
            return self.reconcile()

    def set_switches(self, *, allow_new_entries: Optional[bool] = None, manage_existing_positions: Optional[bool] = None, reason: str = ""):
        return self.ledger.set_switches(allow_new_entries=allow_new_entries, manage_existing_positions=manage_existing_positions, reason=reason)

    def on_disconnect(self, reason: str = "ws_disconnect"):
        self.venue.on_disconnect(reason)
        self.fresh.on_disconnect()

    def on_reconnect(self) -> ReconcileReport:
        self.venue.on_reconnect()
        return self.reconcile()

    # ================================================================ entries
    def submit_entry(self, sig: EntrySignal) -> SubmitResult:
        with self.lock:
            self.accounts.add(sig.account_id)
            # 1. identity first: a duplicate is a duplicate whatever the venue is doing
            if sig.direction not in ("long", "short"):
                return SubmitResult("REJECTED", None, "invalid_direction")
            coid = make_coid(sig.account_id, sig.signal_id, PU.ENTRY.value, sig.market, sig.direction)
            existing = self.ledger.get_order(coid)
            if existing is not None and existing["state"] != O.VALIDATED.value:
                return SubmitResult("DUPLICATE", coid, f"intent_exists:{existing['state']}")
            if self.cfg.risk_plan_required:
                from .trade_plan import stored_plan
                try:
                    plan = stored_plan(dict(account_id=sig.account_id,direction=sig.direction,
                                            meta=json.dumps(dict(detail=sig.detail))))
                    if plan is None or sig.notional_usd != D(plan['notional_usd']):
                        raise ValueError('missing risk plan')
                except (ValueError,KeyError,TypeError,ArithmeticError):
                    return SubmitResult('BLOCKED',None,'risk_plan_required')
            # 2. gates that block (not reject) new risk
            sw = self.ledger.get_switches()
            if not sw.allow_new_entries:
                return SubmitResult("BLOCKED", None, "entries_disabled")
            if not self.venue.entries_allowed:
                return SubmitResult("BLOCKED", None, f"venue_not_ready:{self.venue.state.value}")
            if sig.market in self.blocked_markets.get(sig.account_id, set()):
                return SubmitResult("BLOCKED", None, "market_blocked_by_reconciliation")
            risk = self.entry_risk_status(sig.account_id)
            if risk['blocked']:
                return SubmitResult("BLOCKED", None, 'entry_risk:' + risk['reason'])
            if self.t.capabilities.venue_stop_orders and any(
                self.ledger.get_position(p["position_id"])["account_id"] == sig.account_id
                for p in self.find_unprotected()
            ):
                return SubmitResult("BLOCKED", None, "position_protection_unverified")
            # 3. signal validity (rejections)
            try:
                geometry.geometry_for(sig.strategy)
            except KeyError as e:
                return SubmitResult("REJECTED", None, f"unknown_strategy:{e.args[0]}")
            try:
                filters = validate_market_known(self.meta, sig.market)
            except ValidationError as e:
                return SubmitResult("REJECTED", None, e.code)
            if not filters.tradeable:
                return SubmitResult("REJECTED", None, "validation:market_not_trading")
            # 4. market data
            ok, why = self.fresh.entry_allowed(sig.market, self.clock())
            if not ok:
                return SubmitResult("BLOCKED", None, f"market_data:{why}")
            if self.ledger.open_position_for(sig.account_id, sig.market, sig.direction):
                return SubmitResult("BLOCKED", None, "position_already_open")
            # ---- sizing against live balance
            try:
                acct = self.t.get_account(sig.account_id)
            except (VenueUnavailable, TransportAmbiguous) as e:
                self.venue.on_transport_error(f"get_account:{e}")
                return SubmitResult("BLOCKED", None, "balance_unavailable")
            open_pos = self.ledger.positions_where([P.PROTECTION_PENDING, P.PROTECTED, P.CLOSE_PENDING, P.RECONCILIATION_REQUIRED], account_id=sig.account_id)
            # Reserve a slot for entries still awaiting their final venue state.
            # Partially filled entries already represented by a position count once.
            occupied = {(p["market"], p["direction"]) for p in open_pos}
            pending_entries = self.ledger.orders_where(
                [O.INTENT, O.VALIDATED, O.SUBMITTING, O.UNKNOWN,
                 O.RECONCILIATION_REQUIRED, O.ACKNOWLEDGED,
                 O.PARTIALLY_FILLED, O.CANCEL_PENDING],
                account_id=sig.account_id, purpose=PU.ENTRY.value)
            occupied.update((o["market"], o["direction"]) for o in pending_entries
                            if o["client_order_id"] != coid)
            from .ai_policy import parse
            if self.cfg.trade_margin_pct is not None and not parse(sig.strategy):
                try:
                    # Conservative reservation: do not reuse funds for unresolved entries,
                    # even when the venue's available balance already reflects a lock.
                    reserved = Decimal(0)
                    for order in pending_entries:
                        if order["client_order_id"] == coid:
                            continue
                        meta = json.loads(order["meta"] or "{}")
                        margin = D(meta["required_margin"])
                        notional = D(meta["notional"])
                        if not margin.is_finite() or margin < 0 or not notional.is_finite() or notional < 0:
                            raise ValueError("invalid reservation")
                        from .sizing import percentage_order_reservation
                        reserved += percentage_order_reservation(margin, notional, Decimal("0.0004"))
                    notional = margin_percentage_notional(acct.available_balance - reserved,
                        self.cfg.trade_margin_pct, self.cfg.leverage, filters.taker_fee, sig.notional_usd,
                        confirmed_taker_fee=self.cfg.confirmed_taker_fee)
                    sig = replace(sig, notional_usd=notional,
                                  detail=dict(sig.detail, trade_margin_pct=self.cfg.trade_margin_pct))
                except SizingError as e:
                    return SubmitResult("REJECTED", None, f"sizing:{e.code}")
                except (KeyError, ValueError, ArithmeticError, TypeError):
                    return SubmitResult("BLOCKED", None, "pending_margin_unconfirmed")
            try:
                loss_limit = entry_loss_limit(sig.account_id, self.cfg.max_planned_loss_usd)
            except (SizingError, ValueError, TypeError, ArithmeticError):
                return SubmitResult("REJECTED", None, "sizing:invalid_loss_budget_inputs")
            if loss_limit is not None and not self.cfg.risk_plan_required:
                try:
                    sleeve = geometry.geometry_for(sig.strategy)
                    risk_fee = filters.taker_fee
                    if self.cfg.confirmed_taker_fee is not None:
                        confirmed_fee = D(self.cfg.confirmed_taker_fee)
                        if not confirmed_fee.is_finite() or not Decimal(0) <= confirmed_fee <= Decimal('.01'):
                            raise SizingError('invalid_loss_budget_inputs')
                        risk_fee = max(risk_fee, confirmed_fee)
                    notional, risk_detail = loss_budget_notional(
                        sig.notional_usd, sig.ref_price, sleeve.sl_pct, sleeve.tp_pct,
                        sig.direction, filters, loss_limit, risk_fee)
                    sig = replace(sig, notional_usd=notional,
                                  detail=dict(sig.detail, loss_budget=risk_detail))
                except (SizingError, ValueError, TypeError, ArithmeticError) as e:
                    return SubmitResult("REJECTED", None, f"sizing:{getattr(e, 'code', 'invalid_loss_budget_inputs')}")
            open_notional = sum((D(p["qty"]) * D(p["entry_price"]) for p in open_pos), Decimal(0))
            from .ai_policy import risk_check
            ai_block = risk_check(self, sig, acct, coid)
            if ai_block:
                return SubmitResult("BLOCKED", None, ai_block)
            try:
                sized = size_order(sig.notional_usd, sig.ref_price, self.cfg.leverage, filters, acct, self.cfg.limits,
                                   open_notional=open_notional, open_positions=len(occupied))
            except SizingError as e:
                return SubmitResult("REJECTED", None, f"sizing:{e.code}")
            # Reject entries whose estimated fees alone consume the dollar
            # budget. Fixed-size settings cannot silently bypass stop economics.
            fee = max(filters.taker_fee, D(self.cfg.confirmed_taker_fee or 0), Decimal('.0004'))
            budget = dollar_loss_limit(self.cfg.max_planned_loss_usd)
            if sized.notional * 2 * fee + sized.qty * filters.tick * filters.contract_multiplier >= budget:
                return SubmitResult("REJECTED", None, "sizing:fees_exceed_dollar_loss_budget")
            req = OrderRequest(account_id=sig.account_id, market=sig.market, side="buy" if sig.direction == "long" else "sell",
                               direction=sig.direction, order_type="market", qty=sized.qty, leverage=self.cfg.leverage,
                               client_order_id=coid, price=None, reduce_only=False, time_in_force="ioc")
            try:
                validate_order(req, filters, ref_price=sized.ref_price, max_leverage_policy=self.cfg.leverage)
            except ValidationError as e:
                return SubmitResult("REJECTED", None, f"validation:{e.code}")
            if existing is not None and loss_limit is not None and D(existing['qty']) != req.qty:
                # A pre-cap VALIDATED intent must never send a resized request
                # while its durable ledger still records the old larger size.
                return SubmitResult("REJECTED", coid, "sizing:existing_intent_requires_new_signal")
            # ---- durable intent BEFORE transport
            row, created = self.ledger.create_intent(signal_id=sig.signal_id, account_id=sig.account_id, market=sig.market, side=req.side,
                                                     direction=sig.direction, order_type="market", purpose=PU.ENTRY.value, strategy=sig.strategy,
                                                     qty=sized.qty, leverage=self.cfg.leverage, reduce_only=False, tif="ioc",
                                                     meta=dict(signal_ts=sig.ts, ref_price=str(sized.ref_price), notional=str(sized.notional),
                                                               required_margin=str(sized.required_margin), detail=sig.detail))
            if created:
                self.ledger.transition_order(coid, O.VALIDATED, f"validated:{coid}")
                self._note_new_order(coid)
            elif row["state"] != O.VALIDATED.value:
                return SubmitResult("DUPLICATE", coid, f"intent_exists:{row['state']}")
        return self._submit(coid, req, filters)

    def _submit(self, coid: str, req: OrderRequest, filters: MarketFilters) -> SubmitResult:
        # The claim is atomic in the ledger; the transport call runs outside the engine
        # lock so a slow venue cannot block management, but exactly one claimant exists.
        with self.lock:
            if not req.reduce_only:
                risk = self.entry_risk_status(req.account_id)
                if risk['blocked']:
                    # Release the reservation only for an intent that has not
                    # been claimed. An in-flight duplicate stays reconciliation-owned.
                    order = self.ledger.get_order(coid)
                    if order is not None and order['state'] == O.VALIDATED.value:
                        self.ledger.transition_order(coid, O.REJECTED,
                            f"entry_risk_rejected:{coid}", reason='entry_risk:' + risk['reason'])
                    return SubmitResult("BLOCKED", coid, 'entry_risk:' + risk['reason'])
            if not self.ledger.claim_for_submission(coid):
                return SubmitResult("DUPLICATE", coid, "already_claimed")
        try:
            ack = self.t.place_order(req)
        except VenueRejected as e:
            self.ledger.add_flag("P1", "ORDER_REJECTED_DETAIL", str(e), req.account_id, req.market)
            self.ledger.transition_order(coid, O.REJECTED, f"rejected:{coid}:{self.ledger.get_order(coid)['attempts']}", reason=e.code)
            return SubmitResult("REJECTED", coid, e.code)
        except VenueUnavailable as e:
            # definitive not-processed (429). No retry here: the caller may re-signal later
            # and the same intent will be resubmitted through reconciliation-approved path.
            self.venue.on_transport_error(f"submit:{e.status}")
            self.ledger.transition_order(coid, O.FAILED, f"unavailable:{coid}:{self.ledger.get_order(coid)['attempts']}", reason=f"venue_unavailable:{e.status}")
            return SubmitResult("FAILED", coid, f"venue_unavailable:{e.status}")
        except TransportAmbiguous as e:
            n = self.ledger.get_order(coid)["attempts"]
            self.ledger.transition_order(coid, O.UNKNOWN, f"unknown:{coid}:{n}", reason=str(e))
            self.ledger.transition_order(coid, O.RECONCILIATION_REQUIRED, f"recon_req:{coid}:{n}", reason="ambiguous_transport")
            self.venue.on_ambiguous_submission(str(e))
            self.ledger.add_flag("P1", "UNKNOWN_ORDER_STATUS", str(e), req.account_id, req.market)
            return SubmitResult("UNKNOWN", coid, "reconciliation_required")
        self.ledger.transition_order(coid, O.ACKNOWLEDGED, f"ack:{coid}:{ack.venue_order_id}", venue_order_id=ack.venue_order_id)
        return SubmitResult("ACKNOWLEDGED", coid, ack.venue_order_id)

    # ================================================================ fills
    def on_fill(self, trade: VenueTrade) -> bool:
        """Idempotent by trade_id. Accepts venue trades from WS or reconciliation."""
        with self.lock:
            order = None
            if trade.client_order_id:
                order = self.ledger.get_order(trade.client_order_id)
            if order is None:
                order = self.ledger.find_order_by_venue_id(trade.venue_order_id)
            if order is None:
                if self.ledger.record_fill(trade, None):
                    self.ledger.add_flag("P2", "ORPHAN_TRADE", f"trade {trade.trade_id} order {trade.venue_order_id}", None, trade.market)
                return False
            coid = order["client_order_id"]
            if not self.ledger.record_fill(trade, coid):
                return False   # duplicate delivery
            order = self.ledger.get_order(coid)
            filled, qty = D(order["filled_qty"]), D(order["qty"])
            new_state = O.FILLED if filled >= qty else O.PARTIALLY_FILLED
            try:
                self.ledger.transition_order(coid, new_state, f"fill:{trade.trade_id}", venue_order_id=order["venue_order_id"] or trade.venue_order_id)
            except IllegalTransition:
                # e.g. fill reported for an order we already marked CANCELLED/FILLED: keep the
                # fill (position accounting) but do not corrupt the order state.
                self.ledger.add_flag("P2", "FILL_AFTER_TERMINAL", f"{coid} state {order['state']} trade {trade.trade_id}", order["account_id"], order["market"])
            self._apply_fill_to_position(order, trade)
            return True

    def _apply_fill_to_position(self, order: dict, trade: VenueTrade) -> None:
        purpose = order["purpose"]
        if purpose == PU.ENTRY.value:
            pid = order["client_order_id"]
            pos = self.ledger.get_position(pid)
            o = self.ledger.get_order(pid)
            if pos is None:
                pos = self.ledger.open_position(position_id=pid, account_id=order["account_id"], market=order["market"], direction=order["direction"],
                                                strategy=order["strategy"], entry_coid=pid, qty=D(o["filled_qty"]), entry_price=D(o["avg_price"]),
                                                state=P.PROTECTION_PENDING, origin="bot")
                self._note_new_position(pid)
            else:
                self.ledger.update_position(pid, qty=D(o["filled_qty"]), entry_price=D(o["avg_price"]))
                if pos["state"] == P.PROTECTED.value:
                    prot = self.ledger.get_protection(pid)
                    if prot is None or D(prot["qty"]) != D(o["filled_qty"]):
                        self.ledger.transition_position(pid, P.PROTECTION_PENDING, f"more_fills:{trade.trade_id}", reason="quantity_changed")
            self.ensure_protection(pid)
        else:
            pid = order["position_id"]
            pos = self.ledger.get_position(pid) if pid else None
            if pos is None:
                return
            remaining = D(pos["qty"]) - trade.qty
            if remaining <= 0:
                self.ledger.update_position(pid, qty=Decimal(0))
                if pos["state"] != P.CLOSED.value:
                    self.ledger.transition_position(pid, P.CLOSED, f"closed_by:{trade.trade_id}", reason=purpose)
                self._cancel_protection_orders(pid, keep=order["client_order_id"])
            else:
                self.ledger.update_position(pid, qty=remaining)

    def _dollar_stop(self, pos, filters, strategy_stop):
        entry = self.ledger.get_order(pos['entry_coid']) if pos.get('entry_coid') else None
        if entry is None or entry['account_id'] != pos['account_id']:
            raise ValueError('dollar stop requires the owned entry order')
        orders = {o['client_order_id']: o for o in self.ledger.orders_for_position(pos['position_id'])}
        orders[entry['client_order_id']] = entry
        paid, realized = Decimal(0), Decimal(0)
        sign = Decimal(1) if pos['direction'] == 'long' else Decimal(-1)
        for order in orders.values():
            fills = self.ledger.fills_for(order['client_order_id'])
            paid += max(D(order['fees']), sum((D(f['fee']) for f in fills), Decimal(0)))
            if order['purpose'] != PU.ENTRY.value:
                realized += sign * (D(order['avg_price']) - D(pos['entry_price'])) * D(order['filled_qty']) * filters.contract_multiplier
        prot = self.ledger.get_protection(pos['position_id'])
        fee = max(filters.taker_fee, D(self.cfg.confirmed_taker_fee or 0),
                  D(entry['fees']) / (D(entry['filled_qty']) * D(pos['entry_price']) * filters.contract_multiplier))
        from .trade_plan import stored_plan
        plan = stored_plan(entry)
        cap = min(dollar_loss_limit(self.cfg.max_planned_loss_usd), D(plan['risk_usd']),
                  D(plan.get('trigger_budget_usd', plan['risk_usd']))) if plan else self.cfg.max_planned_loss_usd
        return dollar_stop_price(entry=pos['entry_price'], qty=pos['qty'],
            multiplier=filters.contract_multiplier, direction=pos['direction'],
            paid_fees=paid, realized_gross=realized, exit_fee=fee,
            strategy_stop=strategy_stop, tick=filters.tick,
            configured=cap,
            previous_stop=prot['stop_price'] if prot else None)

    def protection_prices(self, pos: dict, filters):
        """An account override targets net return on entry margin, per position."""
        entry = D(pos["entry_price"])
        tp, sl = geometry.protection_prices(pos["strategy"], pos["direction"], entry)
        order = self.ledger.get_order(pos['entry_coid']) if pos.get('entry_coid') else None
        from .trade_plan import stored_plan
        plan = stored_plan(order) if order else None
        if plan:
            sl = entry*(1-D(plan['stop_fraction']) if pos['direction']=='long' else 1+D(plan['stop_fraction']))
        sl = self._dollar_stop(pos, filters, sl)
        if plan:
            orders = {o['client_order_id']:o for o in self.ledger.orders_for_position(pos['position_id'])}
            orders[order['client_order_id']] = order
            paid, realized = D(0), D(0); sign = 1 if pos['direction']=='long' else -1
            for o in orders.values():
                paid += max(D(o['fees']),sum((D(f['fee']) for f in self.ledger.fills_for(o['client_order_id'])),D(0)))
                if o['purpose']!='ENTRY':
                    realized += (D(o['avg_price'])-entry)*D(o['filled_qty'])*filters.contract_multiplier*sign
            fee = max(D('.0004'),filters.taker_fee,D(self.cfg.confirmed_taker_fee or 0),
                      D(order['fees'])/(D(order['filled_qty'])*entry*filters.contract_multiplier))
            goal = D(plan['target_net_usd'])*D(order['filled_qty'])/D(plan['planned_qty'])
            gain = (goal-realized+paid)/(D(pos['qty'])*filters.contract_multiplier)
            tp = (entry+gain)/(1-fee) if sign==1 else (entry-gain)/(1+fee)
            if not tp.is_finite() or tp<=0:raise ValueError('invalid risk target')
            return tp, sl
        pct = self.cfg.take_profit_margin_pct
        if pct is None:
            return tp, sl
        pct = D(pct)
        order = self.ledger.get_order(pos["entry_coid"]) if pos.get("entry_coid") else None
        if order is None or order["account_id"] != pos["account_id"]:
            raise ValueError("margin take-profit requires the owned entry order")
        leverage = D(order["leverage"])
        filled = D(order["filled_qty"])
        if (not pct.is_finite() or not Decimal(0) < pct <= Decimal(100)
                or leverage <= 0 or entry <= 0 or filled <= 0):
            raise ValueError("invalid margin take-profit inputs")
        fills = self.ledger.fills_for(order["client_order_id"])
        # Fees per filled unit work for partial entries and remaining partial exits.
        paid = max(D(order["fees"]), sum((D(f["fee"]) for f in fills), Decimal(0)))
        entry_fee_per_unit = paid / filled / filters.contract_multiplier
        fee = max(filters.taker_fee, D(self.cfg.take_profit_taker_fee or 0),
                  D(self.cfg.confirmed_taker_fee or 0), entry_fee_per_unit / entry)
        if not fee.is_finite() or not Decimal(0) <= fee < Decimal('.01'):
            raise ValueError("invalid take-profit fee rate")
        gain_per_unit = entry / leverage * pct / Decimal(100)
        if pos["direction"] == "long":
            tp = (entry + entry_fee_per_unit + gain_per_unit) / (1 - fee)
        else:
            tp = (entry - entry_fee_per_unit - gain_per_unit) / (1 + fee)
        if not tp.is_finite() or tp <= 0:
            raise ValueError("invalid margin take-profit price")
        return tp, sl

    # ================================================================ protection
    def ensure_protection(self, position_id: str) -> str:
        """Idempotent. Drives PROTECTION_PENDING -> PROTECTED. Returns the position state."""
        with self.lock:
            pos = self.ledger.get_position(position_id)
            if pos is None or pos["state"] != P.PROTECTION_PENDING.value:
                return pos["state"] if pos else "MISSING"
            if D(pos["qty"]) <= 0:
                self.ledger.transition_position(position_id, P.CLOSED, f"zero_qty:{position_id}:{self.clock()}", reason="zero_quantity")
                return P.CLOSED.value
            if self.t.capabilities.venue_stop_orders:
                return self._ensure_venue_protection(pos)
            filters = self.meta.filters(pos["market"])
            tp_raw, sl_raw = self.protection_prices(pos, filters)
            long = pos["direction"] == "long"
            tp_px = filters.round_price(tp_raw, up=long)        # never worse than the sleeve's target
            sl_px = filters.round_price(sl_raw, up=long)
            qty = D(pos["qty"])
            prot = self.ledger.get_protection(position_id) or dict(version=0, target_coid=None, attempts=0)
            # existing target still valid?
            if prot.get("target_coid"):
                t = self.ledger.get_order(prot["target_coid"])
                if t and O(t["state"]) in ORDER_LIVE and D(t["qty"]) == qty and D(t["price"]) == tp_px:
                    self.ledger.set_protection(position_id, state="ACTIVE", qty=qty, tp_price=tp_px, stop_price=sl_px)
                    self.ledger.transition_position(position_id, P.PROTECTED, f"protected:{position_id}:{prot['version']}")
                    return P.PROTECTED.value
                if t and O(t["state"]) in ORDER_LIVE:
                    res = self.cancel_order(prot["target_coid"])
                    if res in ("FILLED",):
                        return self.ledger.get_position(position_id)["state"]
                    if res in ("UNKNOWN", "STILL_OPEN"):
                        self.ledger.add_flag("P1", "PROTECTION_REPLACE_BLOCKED", f"{position_id} old target {res}", pos["account_id"], pos["market"])
                        return P.PROTECTION_PENDING.value
                if t and O(t["state"]) in ORDER_AMBIGUOUS:
                    return P.PROTECTION_PENDING.value   # reconciliation must resolve the old target first
            version = int(prot.get("version", 0)) + 1
            coid = protection_coid(position_id, "tp", version)
            req = OrderRequest(account_id=pos["account_id"], market=pos["market"], side="sell" if long else "buy", direction=pos["direction"],
                               order_type="limit", qty=qty, leverage=self.cfg.leverage, client_order_id=coid, price=tp_px, reduce_only=True, time_in_force="gtc")
            try:
                validate_order(req, filters, ref_price=tp_px, max_leverage_policy=self.cfg.leverage)
            except ValidationError as e:
                self.ledger.add_flag("P0", "PROTECTION_UNPLACEABLE", f"{position_id}: {e}", pos["account_id"], pos["market"])
                return self._protection_failed(position_id, f"validation:{e.code}")
            row, created = self.ledger.create_intent(signal_id=f"{position_id}:tp{version}", account_id=pos["account_id"], market=pos["market"], side=req.side,
                                                     direction=pos["direction"], order_type="limit", purpose=PU.TARGET.value, strategy=pos["strategy"], qty=qty,
                                                     leverage=self.cfg.leverage, reduce_only=True, tif="gtc", price=tp_px, position_id=position_id, coid=coid)
            if created:
                self.ledger.transition_order(coid, O.VALIDATED, f"validated:{coid}")
                self._note_new_order(coid)
            self.ledger.set_protection(position_id, version=version, target_coid=coid, stop_kind="software",
                                       tp_price=tp_px, stop_price=sl_px, qty=qty, state="PENDING")
            res = self._submit(coid, req, filters)
            if res.status == "ACKNOWLEDGED":
                self.ledger.set_protection(position_id, state="ACTIVE", attempts=0)
                self.ledger.transition_position(position_id, P.PROTECTED, f"protected:{position_id}:{version}")
                return P.PROTECTED.value
            if res.status == "DUPLICATE":
                return P.PROTECTION_PENDING.value
            if res.status == "UNKNOWN":
                self.ledger.add_flag("P1", "PROTECTION_AMBIGUOUS", f"{position_id} target {coid}", pos["account_id"], pos["market"])
                return P.PROTECTION_PENDING.value
            attempts = int(prot.get("attempts", 0)) + 1
            self.ledger.set_protection(position_id, attempts=attempts, state="PENDING")
            if attempts >= self.cfg.protection_max_attempts:
                return self._protection_failed(position_id, f"{res.status}:{res.reason}")
            return P.PROTECTION_PENDING.value

    def _ensure_venue_protection(self, pos: dict) -> str:
        """Install the exchange stop first. Both legs require authoritative readback;
        an acknowledgement or a software stop never establishes outage protection."""
        pid = pos["position_id"]
        if self.t.capabilities.conditional_take_profit:
            # Legacy Yellow targets are resting reduce-only limits. They reserve
            # the position and can prevent both a triggered stop and its repair
            # from closing it. Release that target BEFORE repairing a stop.
            prot = self.ledger.get_protection(pid)
            target = self.ledger.get_order(prot["target_coid"]) if prot and prot.get("target_coid") else None
            if target and (target["order_type"] != "trigger_market"
                           or json.loads(target["meta"] or "{}").get("trigger_type") != "take_profit"):
                if O(target["state"]) in ORDER_AMBIGUOUS:
                    return P.PROTECTION_PENDING.value
                if O(target["state"]) in ORDER_LIVE:
                    result = self.cancel_order(target["client_order_id"])
                    if result in ("UNKNOWN", "STILL_OPEN"):
                        self.ledger.add_flag("P1", "PROTECTION_REPLACE_BLOCKED",
                                             f"{pid} legacy target {result}", pos["account_id"], pos["market"])
                        return P.PROTECTION_PENDING.value
                    # Cancellation can discover full or partial fills. Work only
                    # from the resulting position, never the pre-cancel quantity.
                    pos = self.ledger.get_position(pid)
                    if pos["state"] != P.PROTECTION_PENDING.value:
                        return pos["state"]
        filters = self.meta.filters(pos["market"])
        tp, sl = self.protection_prices(pos, filters)
        long = pos["direction"] == "long"
        tp, sl = filters.round_price(tp, up=long), filters.round_price(sl, up=long)
        qty = D(pos["qty"])
        self.ledger.set_protection(pid, stop_kind="venue", tp_price=tp, stop_price=sl, qty=qty, state="PENDING")
        # The stop must be on the exchange before we work on the target.
        for purpose, px in ((PU.STOP, sl), (PU.TARGET, tp)):
            if not self._ensure_venue_leg(pos, purpose, px, filters):
                current = self.ledger.get_position(pid)
                if current["state"] != P.PROTECTION_PENDING.value:
                    return current["state"]
                prot = self.ledger.get_protection(pid)
                if int(prot["attempts"]) >= self.cfg.protection_max_attempts:
                    return self._protection_failed(pid, "venue_protection_failed")
                return P.PROTECTION_PENDING.value
        self.ledger.set_protection(pid, state="ACTIVE", attempts=0)
        self.ledger.transition_position(pid, P.PROTECTED, f"venue_protected:{pid}:{self.ledger.get_protection(pid)['version']}:{self.clock()}")
        return P.PROTECTED.value

    def _ensure_venue_leg(self, pos: dict, purpose: PU, px: Decimal, filters: MarketFilters) -> bool:
        pid = pos["position_id"]
        stop = purpose == PU.STOP
        conditional = stop or self.t.capabilities.conditional_take_profit
        order_type = "trigger_market" if conditional else "limit"
        trigger_type = ("stop_loss" if stop else "take_profit") if conditional else None
        key = "stop_coid" if stop else "target_coid"
        prot = self.ledger.get_protection(pid)
        o = self.ledger.get_order(prot[key]) if prot.get(key) else None
        qty = D(pos["qty"])
        if o is not None:
            state = O(o["state"])
            if state in ORDER_AMBIGUOUS:
                return False  # reconcile the existing intent; never submit a second stop
            if state in ORDER_LIVE:
                meta = json.loads(o["meta"] or "{}")
                old_px = meta.get("trigger_price") if conditional else o["price"]
                if (o["order_type"] == order_type and meta.get("trigger_type") == trigger_type
                        and D(o["qty"]) == qty and old_px is not None and D(old_px) == px):
                    return self._venue_leg_confirmed(pos, o, purpose, px)
                result = self.cancel_order(o["client_order_id"])
                if result in ("UNKNOWN", "STILL_OPEN", "FILLED"):
                    return False
                current = self.ledger.get_position(pid)
                if current["state"] == P.CLOSED.value or D(current["qty"]) != qty:
                    return False
        version = int(prot["version"]) + 1
        coid = protection_coid(pid, "sl" if stop else "tp", version)
        req = OrderRequest(account_id=pos["account_id"], market=pos["market"], side="sell" if pos["direction"] == "long" else "buy",
                           direction=pos["direction"], order_type=order_type, qty=qty,
                           leverage=self.cfg.leverage, client_order_id=coid, reduce_only=True,
                           time_in_force="ioc" if conditional else "gtc", price=None if conditional else px,
                           trigger_price=px if conditional else None, trigger_type=trigger_type)
        try:
            validate_order(req, filters, ref_price=px, max_leverage_policy=self.cfg.leverage)
        except ValidationError as e:
            self._protection_failed(pid, f"validation:{e.code}")
            return False
        row, created = self.ledger.create_intent(
            signal_id=f"{pid}:{purpose.value}:{version}", account_id=pos["account_id"], market=pos["market"],
            side=req.side, direction=req.direction, order_type=req.order_type, purpose=purpose.value,
            strategy=pos["strategy"], qty=qty, leverage=req.leverage, reduce_only=True, tif=req.time_in_force,
            price=req.price, position_id=pid, coid=coid,
            meta={"trigger_price": str(px), "trigger_type": trigger_type} if conditional else {})
        if created:
            self.ledger.transition_order(coid, O.VALIDATED, f"validated:{coid}")
            self._note_new_order(coid)
        # Persist the linkage BEFORE the network call so a crash can recover it.
        self.ledger.set_protection(pid, version=version, **{key: coid})
        result = self._submit(coid, req, filters)
        if result.status == "ACKNOWLEDGED":
            return self._venue_leg_confirmed(pos, self.ledger.get_order(coid), purpose, px)
        if result.status in ("FAILED", "REJECTED"):
            self.ledger.set_protection(pid, attempts=int(prot["attempts"]) + 1)
        return False

    def _venue_leg_confirmed(self, pos: dict, o: dict, purpose: PU, px: Decimal) -> bool:
        try:
            vo = self._find_venue_order(pos["account_id"], pos["market"], o["client_order_id"], o["venue_order_id"])
        except (VenueUnavailable, TransportAmbiguous, VenueRejected) as e:
            self.venue.on_transport_error("protection_readback_failed")
            self.ledger.add_flag("P1", "PROTECTION_READBACK_FAILED", str(e), pos["account_id"], pos["market"])
            return False
        stop = purpose == PU.STOP
        conditional = stop or self.t.capabilities.conditional_take_profit
        trigger_type = "stop_loss" if stop else "take_profit"
        ok = bool(vo and vo.state == VENUE_OPEN and vo.market == pos["market"] and vo.direction == pos["direction"]
                  and vo.venue_order_id == o["venue_order_id"]
                  and vo.client_order_id in (None, o["client_order_id"])
                  and vo.side == ("sell" if pos["direction"] == "long" else "buy") and vo.reduce_only
                  and vo.qty - vo.filled_qty == D(pos["qty"])
                  and (vo.order_type == "trigger_market" and vo.trigger_price == px and vo.trigger_type == trigger_type
                       if conditional else vo.order_type == "limit" and vo.price == px))
        if not ok:
            self.ledger.add_flag("P0", "PROTECTION_NOT_CONFIRMED", f"{purpose.value}:{o['client_order_id']}", pos["account_id"], pos["market"])
        return ok

    def _protection_failed(self, position_id: str, reason: str) -> str:
        pos = self.ledger.get_position(position_id)
        from .entry_brake import halt_after_protection_failure
        halt_after_protection_failure(self, pos, reason)
        self.ledger.add_flag("P0", "POSITION_WITHOUT_PROTECTION", f"{position_id}: {reason}", pos["account_id"], pos["market"])
        if self.cfg.protection_failure_policy == "flatten":
            self.close_position(position_id, "PROTECTION_FAILED")
            return self.ledger.get_position(position_id)["state"]
        return P.PROTECTION_PENDING.value

    def _cancel_protection_orders(self, position_id: str, keep: Optional[str] = None) -> None:
        for o in self.ledger.orders_for_position(position_id):
            if o["client_order_id"] != keep and O(o["state"]) in ORDER_LIVE and o["purpose"] in (PU.TARGET.value, PU.STOP.value):
                self.cancel_order(o["client_order_id"])

    def check_protection(self, now: Optional[float] = None) -> List[str]:
        """Detect and act on positions lacking protection. Returns ids still unprotected."""
        now = self.clock() if now is None else now
        unprotected: List[str] = []
        with self.lock:
            for pos in self.ledger.positions_where([P.PROTECTED]):
                prot = self.ledger.get_protection(pos["position_id"])
                if prot:
                    filters = self.meta.filters(pos["market"])
                    desired, desired_stop = self.protection_prices(pos, filters)
                    desired = filters.round_price(desired, up=pos["direction"] == "long")
                    if D(prot["tp_price"]) != desired or D(prot['stop_price']) != desired_stop:
                        self.ledger.transition_position(pos["position_id"], P.PROTECTION_PENDING,
                            f"protection_policy_changed:{pos['position_id']}:{desired}:{desired_stop}:{now}",
                            reason="universal_dollar_stop_or_margin_target")
                        continue
                t = self.ledger.get_order(prot["target_coid"]) if prot and prot.get("target_coid") else None
                target_ok = bool(t and O(t["state"]) in ORDER_LIVE)
                stop_ok = True
                if self.t.capabilities.venue_stop_orders:
                    target_ok = bool(target_ok and self._venue_leg_confirmed(pos, t, PU.TARGET, D(prot["tp_price"])))
                    s = self.ledger.get_order(prot["stop_coid"]) if prot and prot.get("stop_coid") else None
                    stop_ok = bool(s and O(s["state"]) in ORDER_LIVE and prot["stop_kind"] == "venue"
                                   and self._venue_leg_confirmed(pos, s, PU.STOP, D(prot["stop_price"])))
                if not target_ok or not stop_ok:
                    self.ledger.add_flag("P0", "PROTECTION_LOST", f"{pos['position_id']} target {t['state'] if t else 'missing'}", pos["account_id"], pos["market"])
                    self.ledger.transition_position(pos["position_id"], P.PROTECTION_PENDING, f"protection_lost:{pos['position_id']}:{now}", reason="target_or_stop_not_verified")
            for pos in self.ledger.positions_where([P.PROTECTION_PENDING]):
                state = self.ensure_protection(pos["position_id"])
                if state == P.PROTECTION_PENDING.value:
                    age = now - float(pos["state_ts"])
                    if age > self.cfg.protection_timeout_s:
                        state = self._protection_failed(pos["position_id"], f"timeout:{age:.0f}s")
                    if state == P.PROTECTION_PENDING.value:
                        unprotected.append(pos["position_id"])
        return unprotected

    def find_unprotected(self, now: Optional[float] = None) -> List[dict]:
        now = self.clock() if now is None else now
        out = []
        for pos in self.ledger.positions_where([P.PROTECTION_PENDING, P.RECONCILIATION_REQUIRED]):
            out.append(dict(position_id=pos["position_id"], state=pos["state"], age=now - float(pos["state_ts"])))
        for pos in self.ledger.positions_where([P.PROTECTED]):
            prot = self.ledger.get_protection(pos["position_id"])
            t = self.ledger.get_order(prot["target_coid"]) if prot and prot.get("target_coid") else None
            if t is None or O(t["state"]) not in ORDER_LIVE:
                out.append(dict(position_id=pos["position_id"], state="PROTECTED_BUT_TARGET_NOT_LIVE", age=0.0))
            elif self.t.capabilities.venue_stop_orders:
                s = self.ledger.get_order(prot["stop_coid"]) if prot and prot.get("stop_coid") else None
                if prot["stop_kind"] != "venue" or s is None or O(s["state"]) not in ORDER_LIVE:
                    out.append(dict(position_id=pos["position_id"], state="PROTECTED_BUT_STOP_NOT_LIVE", age=0.0))
        return out

    # ================================================================ marks / software stops
    def on_mark(self, market: str, price, ts: Optional[float] = None) -> List[str]:
        """Feeds freshness; evaluates software stops on FRESH marks only. Returns
        position ids for which a stop close was initiated."""
        self.fresh.on_mark(market, float(price), ts)
        closed: List[str] = []
        with self.lock:
            if not self.ledger.get_switches().manage_existing_positions:
                return closed
            fresh = self.fresh.mark_fresh(market, self.clock())
            px = D(price)
            for pos in self.ledger.positions_where([P.PROTECTED], market=market):
                prot = self.ledger.get_protection(pos["position_id"])
                if not prot or prot["stop_kind"] != "software":
                    continue
                stop = D(prot["stop_price"])
                crossed = px <= stop if pos["direction"] == "long" else px >= stop
                if not crossed:
                    continue
                if not fresh:
                    self.ledger.add_flag("P1", "STOP_UNEVALUATED_STALE_DATA", f"{pos['position_id']} mark age {self.fresh.mark_age(market, self.clock())}", pos["account_id"], market)
                    continue
                self.close_position(pos["position_id"], "STOP")
                closed.append(pos["position_id"])
        return closed

    # ================================================================ close
    def close_position(self, position_id: str, reason: str) -> str:
        with self.lock:
            pos = self.ledger.get_position(position_id)
            if pos is None:
                return "MISSING"
            if reason == 'PROTECTION_FAILED':
                from .entry_brake import halt_after_protection_failure
                halt_after_protection_failure(self, pos, reason)
            if pos["state"] == P.CLOSED.value:
                return "ALREADY_CLOSED"
            if pos["state"] == P.CLOSE_PENDING.value:
                return "CLOSE_PENDING"
            try:
                legs = self.t.get_positions(pos["account_id"])
            except (VenueUnavailable, TransportAmbiguous, VenueRejected) as e:
                self.venue.on_transport_error(f"get_positions:{e}")
                self.ledger.add_flag("P1", "CLOSE_BLOCKED_VENUE_UNAVAILABLE", f"{position_id}: {e}", pos["account_id"], pos["market"])
                return "VENUE_UNAVAILABLE"
            leg = next((l for l in legs if l.market == pos["market"] and l.direction == pos["direction"]), None)
            if leg is None or leg.qty <= 0:
                self.ledger.update_position(position_id, qty=Decimal(0))
                self.ledger.transition_position(position_id, P.CLOSED, f"close_venue_flat:{position_id}:{self.clock()}", reason=f"VENUE_FLAT:{reason}")
                self._cancel_protection_orders(position_id)
                return "CLOSED"
            qty = min(D(pos["qty"]), leg.qty)
            self.ledger.transition_position(position_id, P.CLOSE_PENDING, f"close_pending:{position_id}:{reason}:{self.clock()}", reason=reason)
            # cancel resting protection first so target + market close cannot both execute
            prot = self.ledger.get_protection(position_id)
            for coid_key in ("target_coid", "stop_coid"):
                c = prot.get(coid_key) if prot else None
                t = self.ledger.get_order(c) if c else None
                if t and O(t["state"]) in ORDER_AMBIGUOUS:
                    resolved = self._resolve_ambiguous(c)
                    if resolved == "UNKNOWN":
                        self.ledger.transition_position(position_id, P.RECONCILIATION_REQUIRED, f"close_prot_ambiguous:{position_id}:{self.clock()}", reason="protection_order_ambiguous")
                        return "RECONCILIATION_REQUIRED"
                    t = self.ledger.get_order(c)
                if t and O(t["state"]) in ORDER_LIVE:
                    res = self.cancel_order(c)
                    if res == "FILLED":
                        return self.ledger.get_position(position_id)["state"]
                    if res in ("UNKNOWN", "STILL_OPEN"):
                        self.ledger.add_flag("P1", "CLOSE_BLOCKED_PROTECTION_CANCEL", f"{position_id} {c} {res}", pos["account_id"], pos["market"])
                        self.ledger.transition_position(position_id, P.RECONCILIATION_REQUIRED, f"close_cancel_unknown:{position_id}:{self.clock()}", reason=res)
                        return "RECONCILIATION_REQUIRED"
            n = int(self.ledger.kv_get(f"closes:{position_id}", 0)) + 1
            self.ledger.kv_set(f"closes:{position_id}", n)
            coid = make_coid(pos["account_id"], f"{position_id}:close{n}", PU.CLOSE.value, pos["market"], pos["direction"])
            filters = self.meta.filters(pos["market"])
            long = pos["direction"] == "long"
            req = OrderRequest(account_id=pos["account_id"], market=pos["market"], side="sell" if long else "buy", direction=pos["direction"],
                               order_type="market", qty=qty, leverage=self.cfg.leverage, client_order_id=coid, reduce_only=True, time_in_force="ioc")
            try:
                validate_order(req, filters, ref_price=leg.entry_price if leg.entry_price > 0 else D(pos["entry_price"]), max_leverage_policy=self.cfg.leverage)
            except ValidationError as e:
                self.ledger.add_flag("P0", "CLOSE_UNPLACEABLE", f"{position_id}: {e}", pos["account_id"], pos["market"])
                self.ledger.transition_position(position_id, P.RECONCILIATION_REQUIRED, f"close_invalid:{position_id}:{n}", reason=e.code)
                return "RECONCILIATION_REQUIRED"
            row, created = self.ledger.create_intent(signal_id=f"{position_id}:close{n}", account_id=pos["account_id"], market=pos["market"], side=req.side,
                                                     direction=pos["direction"], order_type="market", purpose=PU.CLOSE.value, strategy=pos["strategy"], qty=qty,
                                                     leverage=self.cfg.leverage, reduce_only=True, tif="ioc", position_id=position_id, coid=coid, meta=dict(reason=reason))
            if created:
                self.ledger.transition_order(coid, O.VALIDATED, f"validated:{coid}")
                self._note_new_order(coid)
            res = self._submit(coid, req, filters)
            if res.status == "ACKNOWLEDGED":
                return "CLOSE_SUBMITTED"
            if res.status == "REJECTED":
                # e.g. no_position_to_close / insufficient_position: venue disagrees with us
                self.ledger.transition_position(position_id, P.RECONCILIATION_REQUIRED, f"close_rejected:{coid}", reason=res.reason)
                return "RECONCILIATION_REQUIRED"
            if res.status == "UNKNOWN":
                self.ledger.transition_position(position_id, P.RECONCILIATION_REQUIRED, f"close_unknown:{coid}", reason="ambiguous_close")
                return "RECONCILIATION_REQUIRED"
            self.ledger.transition_position(position_id, P.PROTECTION_PENDING, f"close_failed:{coid}", reason=res.reason)
            return "CLOSE_FAILED"

    # ================================================================ cancel
    def _find_venue_order(self, account_id: str, market: str, coid: str, venue_order_id: Optional[str]) -> Optional[VenueOrder]:
        for fn in (self.t.get_open_orders, self.t.get_orders):
            kwargs = {"market": market}
            if fn == self.t.get_orders:
                owned = self.ledger.get_order(coid)
                kwargs["since"] = min(self.clock() - self.cfg.trades_lookback_s,
                                      owned['created_ts'] - 60 if owned else self.clock())
            for vo in iter_pages(fn, account_id, **kwargs):
                if (venue_order_id and vo.venue_order_id == venue_order_id) or vo.client_order_id == coid:
                    return vo
        return None

    def _sync_fills_from_venue(self, order: dict, vo: VenueOrder) -> None:
        if vo.filled_qty > D(order["filled_qty"]):
            for tr in all_pages(self.t.get_trades, order["account_id"], market=order["market"], since=min(self.clock() - self.cfg.trades_lookback_s, order['created_ts'] - 60)):
                if tr.venue_order_id == vo.venue_order_id or tr.client_order_id == order["client_order_id"]:
                    self.on_fill(tr)

    def _resolve_ambiguous(self, coid: str) -> str:
        """Resolve a SUBMITTING/UNKNOWN/RECONCILIATION_REQUIRED order by asking the venue.
        Returns the resulting local state name or 'UNKNOWN' if the venue could not answer."""
        o = self.ledger.get_order(coid)
        if o is None:
            return "MISSING"
        st = O(o["state"])
        if st not in ORDER_AMBIGUOUS:
            return st.value
        now = self.clock()
        if st == O.SUBMITTING:
            self.ledger.transition_order(coid, O.UNKNOWN, f"resolve_unknown:{coid}:{now}", reason="resolve")
            st = O.UNKNOWN
        if st == O.UNKNOWN:
            self.ledger.transition_order(coid, O.RECONCILIATION_REQUIRED, f"resolve_recon:{coid}:{now}", reason="resolve")
        try:
            vo = self._find_venue_order(o["account_id"], o["market"], coid, o["venue_order_id"])
        except (VenueUnavailable, TransportAmbiguous) as e:
            self.venue.on_transport_error(f"resolve:{e}")
            return "UNKNOWN"
        rep = ReconcileReport(ok=True, reconcile_id=f"reconcile:resolve:{now}")
        if vo is not None:
            self._sync_order_from_venue(self.ledger.get_order(coid), vo, rep)
            return self.ledger.get_order(coid)["state"]
        if now - self._submitted_ts(self.ledger.get_order(coid)) < self.cfg.unknown_grace_s:
            return "UNKNOWN"
        self.ledger.transition_order(coid, O.FAILED, f"resolve_absent:{coid}:{now}", reason="not_on_venue")
        return O.FAILED.value

    def cancel_order(self, coid: str) -> str:
        """CANCELLED | FILLED | PARTIALLY_FILLED_CANCELLED | STILL_OPEN | UNKNOWN. A cancel
        acknowledgement is never trusted on its own; the venue's order list decides."""
        with self.lock:
            o = self.ledger.get_order(coid)
            if o is None:
                return "MISSING"
            st = O(o["state"])
            if st in ORDER_TERMINAL:
                return "FILLED" if st == O.FILLED else "CANCELLED"
            if st in ORDER_AMBIGUOUS:
                return "UNKNOWN"
            if st != O.CANCEL_PENDING:
                self.ledger.transition_order(coid, O.CANCEL_PENDING, f"cancel_req:{coid}:{self.clock()}")
            for attempt in range(self.cfg.cancel_max_attempts):
                try:
                    self.t.cancel_order(o["account_id"], o["market"], o["venue_order_id"])
                except TransportAmbiguous as e:
                    self.venue.on_transport_error(f"cancel:{e}")
                except VenueRejected:
                    pass   # not found: already filled or cancelled -> the lookup below decides
                except VenueUnavailable as e:
                    self.venue.on_transport_error(f"cancel:{e.status}")
                try:
                    vo = self._find_venue_order(o["account_id"], o["market"], coid, o["venue_order_id"])
                except (VenueUnavailable, TransportAmbiguous) as e:
                    self.venue.on_transport_error(f"cancel_lookup:{e}")
                    self.ledger.transition_order(coid, O.RECONCILIATION_REQUIRED, f"cancel_lookup_failed:{coid}:{attempt}:{self.clock()}", reason=str(e))
                    return "UNKNOWN"
                if vo is None:
                    self.ledger.transition_order(coid, O.RECONCILIATION_REQUIRED, f"cancel_not_found:{coid}:{attempt}:{self.clock()}", reason="not_on_venue")
                    return "UNKNOWN"
                self._sync_fills_from_venue(o, vo)
                cur = O(self.ledger.get_order(coid)["state"])
                if vo.state == VENUE_FILLED or cur == O.FILLED:
                    if cur != O.FILLED:
                        self.ledger.transition_order(coid, O.FILLED, f"cancel_found_filled:{coid}", venue_order_id=vo.venue_order_id)
                    return "FILLED"
                if vo.state in (VENUE_CANCELLED, VENUE_REJECTED):
                    self.ledger.transition_order(coid, O.CANCELLED, f"cancel_confirmed:{coid}:{vo.state}", venue_order_id=vo.venue_order_id)
                    return "PARTIALLY_FILLED_CANCELLED" if vo.filled_qty > 0 else "CANCELLED"
                # still open -> try again
            self.ledger.transition_order(coid, O.ACKNOWLEDGED, f"cancel_gave_up:{coid}:{self.clock()}", reason="still_open_after_cancel_attempts")
            self.ledger.add_flag("P1", "CANCEL_NOT_EFFECTIVE", coid, o["account_id"], o["market"])
            return "STILL_OPEN"

    # ================================================================ reconciliation
    def reconcile(self, account_id: Optional[str] = None) -> ReconcileReport:
        with self.lock:
            self._reconcile_seq += 1
            rid = f"reconcile:{self._reconcile_seq}:{self.clock()}"
            rep = ReconcileReport(ok=False, reconcile_id=rid)
            if not self.ledger.get_switches().manage_existing_positions:
                rep.error = "management_disabled"
                return rep
            self.venue.begin_reconcile()
            known = self.accounts | {r["account_id"] for r in self.ledger.orders_where(list(O))} | {r["account_id"] for r in self.ledger.positions_where(list(P))}
            accounts = [account_id] if account_id else sorted(known)
            self._pass_new_orders, self._pass_new_positions = set(), set()
            try:
                for acct in accounts:
                    self._reconcile_account(acct, rep)
            except (VenueUnavailable, TransportAmbiguous, VenueRejected) as e:
                self.venue.reconcile_failed(str(e))
                rep.error = str(e)
                self.ledger.add_flag("P1", "RECONCILE_FAILED", str(e), account_id)
                return rep
            finally:
                self._pass_new_orders, self._pass_new_positions = None, None
            if self.venue.state != V.RECONCILING:
                rep.error = self.venue.last_error or "venue_degraded_during_protection_check"
                return rep
            rep.ok = True
            self.venue.reconcile_ok()
            return rep

    def _reconcile_account(self, acct: str, rep: ReconcileReport) -> None:
        now = self.clock()
        since = now - self.cfg.trades_lookback_s
        trades = all_pages(self.t.get_trades, acct, since=since)
        venue_orders: Dict[str, VenueOrder] = {}
        for vo in all_pages(self.t.get_open_orders, acct):
            venue_orders[vo.venue_order_id] = vo
        # Historical orders are only needed to resolve a local in-flight order.
        # Reading the full venue history for an empty/settled ledger can exceed
        # Yellow's five-second signed-request window. Open orders, trades and
        # positions are still reconciled on every pass.
        pending = self.ledger.orders_where([O.SUBMITTING, O.UNKNOWN, O.RECONCILIATION_REQUIRED,
                                             O.ACKNOWLEDGED, O.PARTIALLY_FILLED, O.CANCEL_PENDING], account_id=acct)
        observed_coids = {o.client_order_id for o in venue_orders.values() if o.client_order_id}
        observed_ids = set(venue_orders)
        needed = {}
        for o in pending:
            fill_qty = sum((t.qty for t in trades if t.client_order_id == o["client_order_id"]
                            or (o["venue_order_id"] and t.venue_order_id == o["venue_order_id"])), Decimal(0))
            open_observed = o["client_order_id"] in observed_coids or o["venue_order_id"] in observed_ids
            if (not open_observed and fill_qty < D(o["qty"])) or o["state"] in (O.PARTIALLY_FILLED.value, O.CANCEL_PENDING.value):
                needed[o["client_order_id"]] = o["venue_order_id"]
        if needed:
            for vo in iter_pages(self.t.get_orders, acct, since=since):
                venue_orders.setdefault(vo.venue_order_id, vo)
                for coid, vid in list(needed.items()):
                    if vo.client_order_id == coid or (vid and vo.venue_order_id == vid):
                        del needed[coid]
                if not needed:
                    break  # Every relevant order was positively identified.
        venue_pos = {(l.market, l.direction): l for l in self.t.get_positions(acct) if l.qty > 0}
        by_coid = {vo.client_order_id: vo for vo in venue_orders.values() if vo.client_order_id}
        # 1. fills first (idempotent) so order/position quantities are current
        for tr in trades:
            self.on_fill(tr)
        # 2. local orders in non-terminal states
        for o in self.ledger.orders_where([O.SUBMITTING, O.UNKNOWN, O.RECONCILIATION_REQUIRED,
                                           O.ACKNOWLEDGED, O.PARTIALLY_FILLED, O.CANCEL_PENDING], account_id=acct):
            coid = o["client_order_id"]
            if self._pass_new_orders is not None and coid in self._pass_new_orders:
                continue   # created after the venue snapshot was taken; next pass will see it
            st = O(o["state"])
            if st == O.SUBMITTING:
                self.ledger.transition_order(coid, O.UNKNOWN, f"recon_unknown:{coid}:{now}", reason="submitting_at_reconcile")
                self.ledger.transition_order(coid, O.RECONCILIATION_REQUIRED, f"recon_req:{coid}:{now}", reason="submitting_at_reconcile")
                st = O.RECONCILIATION_REQUIRED
            elif st == O.UNKNOWN:
                self.ledger.transition_order(coid, O.RECONCILIATION_REQUIRED, f"recon_req:{coid}:{now}", reason="unknown_at_reconcile")
                st = O.RECONCILIATION_REQUIRED
            vo = venue_orders.get(o["venue_order_id"]) if o["venue_order_id"] else None
            if vo is None:
                vo = by_coid.get(coid)
            if vo is not None:
                self._sync_order_from_venue(o, vo, rep)
                continue
            # not on venue
            age = now - self._submitted_ts(o)
            if st == O.RECONCILIATION_REQUIRED:
                has_trade = any(t.client_order_id == coid for t in trades)
                if has_trade:
                    rep.mismatches.append(dict(kind="UNKNOWN_ORDER_HAS_TRADES", coid=coid))
                    continue
                if age < self.cfg.unknown_grace_s:
                    rep.mismatches.append(dict(kind="UNKNOWN_ORDER_IN_GRACE", coid=coid, age=age))
                    continue
                self.ledger.transition_order(coid, O.FAILED, f"recon_absent:{coid}:{rep.reconcile_id}", reason="not_on_venue_after_reconciliation")
                rep.actions.append(f"{coid}: UNKNOWN -> FAILED (absent from venue); resubmission allowed via approve_resubmission")
                if o["purpose"] == PU.ENTRY.value:
                    self.ledger.approve_resubmission(coid, rep.reconcile_id, "venue_absent")
                    rep.actions.append(f"{coid}: FAILED -> VALIDATED (eligible for one resubmission)")
                elif o["position_id"]:
                    self.ledger.transition_position(o["position_id"], P.PROTECTION_PENDING, f"recon_order_absent:{coid}", reason="protection_order_absent") \
                        if self.ledger.get_position(o["position_id"])["state"] != P.CLOSED.value else None
            elif st in ORDER_LIVE:
                self.ledger.transition_order(coid, O.RECONCILIATION_REQUIRED, f"recon_lost:{coid}:{now}", reason="live_order_missing_on_venue")
                self.ledger.add_flag("P1", "LIVE_ORDER_MISSING_ON_VENUE", coid, acct, o["market"])
                rep.mismatches.append(dict(kind="LIVE_ORDER_MISSING", coid=coid))
        # 3. positions: venue is authoritative
        local_open = self.ledger.positions_where([P.PROTECTION_PENDING, P.PROTECTED, P.CLOSE_PENDING, P.RECONCILIATION_REQUIRED], account_id=acct)
        seen_keys = set()
        for pos in local_open:
            key = (pos["market"], pos["direction"])
            seen_keys.add(key)
            leg = venue_pos.get(key)
            pid = pos["position_id"]
            if self._pass_new_positions is not None and pid in self._pass_new_positions and leg is None:
                continue   # opened by a fill applied in this pass; the leg snapshot may predate it
            if leg is None:
                opposite = venue_pos.get((pos["market"], "short" if pos["direction"] == "long" else "long"))
                if opposite is not None and self.ledger.open_position_for(acct, pos["market"], opposite.direction) is None:
                    self.ledger.add_flag("P0", "SIDE_MISMATCH", f"local {pos['direction']} vs venue {opposite.direction} {pos['market']}", acct, pos["market"])
                    self.blocked_markets.setdefault(acct, set()).add(pos["market"])
                    rep.mismatches.append(dict(kind="SIDE_MISMATCH", position_id=pid))
                self.ledger.update_position(pid, qty=Decimal(0))
                self.ledger.transition_position(pid, P.CLOSED, f"recon_venue_flat:{pid}:{rep.reconcile_id}", reason="VENUE_CLOSED")
                self._cancel_protection_orders(pid)
                rep.mismatches.append(dict(kind="LOCAL_OPEN_VENUE_CLOSED", position_id=pid))
                rep.actions.append(f"{pid}: CLOSED (venue flat); protection orders cancelled")
                continue
            if leg.qty != D(pos["qty"]):
                rep.mismatches.append(dict(kind="QTY_MISMATCH", position_id=pid, local=str(pos["qty"]), venue=str(leg.qty)))
                self.ledger.update_position(pid, qty=leg.qty)
                self.ledger.add_flag("P1", "QTY_MISMATCH", f"{pid} local {pos['qty']} venue {leg.qty}", acct, pos["market"])
                if pos["state"] in (P.PROTECTED.value, P.RECONCILIATION_REQUIRED.value, P.CLOSE_PENDING.value):
                    self.ledger.transition_position(pid, P.PROTECTION_PENDING, f"recon_qty:{pid}:{rep.reconcile_id}", reason="qty_mismatch")
            elif pos["state"] == P.RECONCILIATION_REQUIRED.value:
                self.ledger.transition_position(pid, P.PROTECTION_PENDING, f"recon_resolved:{pid}:{rep.reconcile_id}", reason="venue_confirmed")
            elif pos["state"] == P.CLOSE_PENDING.value:
                close_live = any(O(x["state"]) in ORDER_LIVE and x["purpose"] == PU.CLOSE.value for x in self.ledger.orders_for_position(pid))
                if not close_live:
                    self.ledger.transition_position(pid, P.PROTECTION_PENDING, f"recon_close_gone:{pid}:{rep.reconcile_id}", reason="close_order_not_live")
                    rep.actions.append(f"{pid}: CLOSE_PENDING but no live close order -> re-protect")
        for key, leg in venue_pos.items():
            if key in seen_keys:
                continue
            pid = f"venue:{acct}:{key[0]}:{key[1]}:{int(now)}"
            if self.ledger.open_position_for(acct, key[0], key[1]) is None:
                self.ledger.open_position(position_id=pid, account_id=acct, market=key[0], direction=key[1], strategy="UNMANAGED",
                                          entry_coid=None, qty=leg.qty, entry_price=leg.entry_price, state=P.RECONCILIATION_REQUIRED,
                                          origin="venue", reason="orphan_position")
                self.ledger.add_flag("P1", "ORPHAN_POSITION", f"{key[0]} {key[1]} qty {leg.qty}", acct, key[0])
                self.blocked_markets.setdefault(acct, set()).add(key[0])
                rep.mismatches.append(dict(kind="LOCAL_CLOSED_VENUE_OPEN", position_id=pid))
        # 4. orphan venue orders carrying our prefix -> cancel; foreign orders -> record only
        for vo in venue_orders.values():
            if vo.state != VENUE_OPEN:
                continue
            if vo.client_order_id and self.ledger.get_order(vo.client_order_id) is None and vo.client_order_id.startswith("why-"):
                rep.mismatches.append(dict(kind="ORPHAN_ORDER", venue_order_id=vo.venue_order_id))
                self.ledger.add_flag("P1", "ORPHAN_ORDER", f"{vo.venue_order_id} {vo.client_order_id}", acct, vo.market)
                try:
                    self.t.cancel_order(acct, vo.market, vo.venue_order_id)
                    rep.actions.append(f"orphan order {vo.venue_order_id}: cancel requested")
                except (VenueRejected, TransportAmbiguous, VenueUnavailable) as e:
                    rep.actions.append(f"orphan order {vo.venue_order_id}: cancel failed {e}")
            elif not vo.client_order_id:
                rep.mismatches.append(dict(kind="FOREIGN_OPEN_ORDER", venue_order_id=vo.venue_order_id))
        # 5. protection health
        self.check_protection(now)

    def _sync_order_from_venue(self, o: dict, vo: VenueOrder, rep: ReconcileReport) -> None:
        coid = o["client_order_id"]
        self._sync_fills_from_venue(o, vo)
        cur = O(self.ledger.get_order(coid)["state"])
        ev = f"recon_sync:{coid}:{vo.state}:{vo.filled_qty}:{rep.reconcile_id}"
        if vo.state == VENUE_OPEN:
            if cur in (O.RECONCILIATION_REQUIRED, O.CANCEL_PENDING) or cur == O.SUBMITTING:
                target = O.PARTIALLY_FILLED if vo.filled_qty > 0 else O.ACKNOWLEDGED
                self.ledger.transition_order(coid, target, ev, venue_order_id=vo.venue_order_id)
                rep.actions.append(f"{coid}: {cur.value} -> {target.value} (found open on venue)")
            elif not o["venue_order_id"]:
                self.ledger.transition_order(coid, cur, ev, venue_order_id=vo.venue_order_id)
        elif vo.state == VENUE_FILLED:
            if cur != O.FILLED:
                self.ledger.transition_order(coid, O.FILLED, ev, venue_order_id=vo.venue_order_id)
                rep.actions.append(f"{coid}: {cur.value} -> FILLED (venue)")
        elif vo.state in (VENUE_CANCELLED, VENUE_REJECTED):
            if cur not in ORDER_TERMINAL:
                target = O.REJECTED if (vo.state == VENUE_REJECTED and vo.filled_qty == 0) else O.CANCELLED
                # An accepted trigger may later be rejected by the venue.
                # Resolve it through reconciliation rather than weakening the
                # generic live-order transition rules or resubmitting the intent.
                if target == O.REJECTED and cur in (O.ACKNOWLEDGED, O.CANCEL_PENDING):
                    self.ledger.transition_order(coid, O.RECONCILIATION_REQUIRED,
                                                 ev + ":confirmed_rejection", reason="venue_confirmed_rejection")
                self.ledger.transition_order(coid, target, ev, venue_order_id=vo.venue_order_id)
                rep.actions.append(f"{coid}: {cur.value} -> {target.value} (venue)")
                if o["purpose"] == PU.ENTRY.value and vo.filled_qty == 0:
                    pass   # no position; the sleeve may re-signal with a new signal id
                if o["purpose"] in (PU.TARGET.value, PU.STOP.value) and o["position_id"]:
                    pos = self.ledger.get_position(o["position_id"])
                    if pos and pos["state"] == P.PROTECTED.value:
                        self.ledger.transition_position(o["position_id"], P.PROTECTION_PENDING, f"recon_prot_gone:{coid}", reason="protection_order_cancelled_on_venue")

    # ================================================================ periodic management
    def manage_existing(self, now: Optional[float] = None) -> dict:
        """Runs regardless of the entry switch. Reconciles, re-protects, closes what must
        close. Returns a summary."""
        now = self.clock() if now is None else now
        sw = self.ledger.get_switches()
        if not sw.manage_existing_positions:
            return dict(managed=False, reason="management_disabled")
        rep = self.reconcile()
        unprotected = self.check_protection(now)
        # Stop resting entry remainders during the wait, while keeping every
        # protective/exit order managed. Unknown sends stay reconciliation-owned.
        for account in list(self.accounts):
            if self.entry_risk_status(account)['blocked']:
                for order in self.ledger.orders_where([O.ACKNOWLEDGED, O.PARTIALLY_FILLED],
                                                      account_id=account, purpose=PU.ENTRY.value):
                    self.cancel_order(order['client_order_id'])
        return dict(managed=True, reconcile_ok=rep.ok, mismatches=len(rep.mismatches), unprotected=unprotected)

    # ================================================================ views
    def entry_risk_status(self, account_id):
        from .entry_brake import status
        with self.lock:
            return status(self, account_id)

    def status(self) -> dict:
        sw = self.ledger.get_switches()
        return dict(venue=self.venue.state.value, allow_new_entries=sw.allow_new_entries, manage_existing_positions=sw.manage_existing_positions,
                    open_positions=[p["position_id"] for p in self.ledger.positions_where([P.PROTECTION_PENDING, P.PROTECTED, P.CLOSE_PENDING, P.RECONCILIATION_REQUIRED])],
                    ambiguous_orders=[o["client_order_id"] for o in self.ledger.orders_where(list(ORDER_AMBIGUOUS))],
                    blocked_markets={k: sorted(v) for k, v in self.blocked_markets.items()}, counts=self.ledger.counts())
