"""Per-customer execution engines (one ledger, engine, venue monitor and transport per
tenant) and the registry that fans canonical strategy signals out to them.

Isolation rules:
  * every tenant has its own SQLite ledger under state/customer/tenants/<account_id>/;
  * the engine account id is ctx.namespace = "yellow:<account_id>", so client_order_ids
    (sha256(account|signal|purpose|market|direction)) are unique per (signal, tenant, venue);
  * every registry loop wraps each tenant in try/except — one tenant's exception is
    recorded on that tenant and never touches another;
  * canonical signals are re-sized per tenant; an optional account-scoped live
    filter can reject or defer core entries without changing position exits."""
from __future__ import annotations
import os
import json
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from decimal import Decimal, ROUND_CEILING
from typing import Any, Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from execution.engine import EngineConfig, ExecutionEngine
from execution.ledger import Ledger
from execution.state_machine import ORDER_AMBIGUOUS, ORDER_LIVE
from execution.types import EntrySignal, PositionState as P
from execution.venue_state import VenueMonitor
from .context import CustomerExecutionContext
from .log import get_logger, safe_exc

log = get_logger("tenants")
OPEN_STATES = [P.PROTECTION_PENDING, P.PROTECTED, P.CLOSE_PENDING, P.RECONCILIATION_REQUIRED]



def confirmed_taker_fee(tenant_dir: str, account_id: str) -> Optional[Decimal]:
    """Load an operator-confirmed fee scoped to one account; otherwise use public fees."""
    try:
        with open(os.path.join(tenant_dir, "fee_profile.json")) as f:
            profile = json.load(f)
        if profile["account_id"] != account_id or profile["confirmed"] is not True:
            return None
        fee = Decimal(str(profile["taker_fee_rate"]))
        if not fee.is_finite() or not Decimal(0) <= fee <= Decimal("0.01"):
            return None
        return fee
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
        return None

def margin_take_profit(tenant_dir: str, account_id: str):
    """Opt-in net percentage of each position's entry margin, never coin price."""
    try:
        with open(os.path.join(tenant_dir, "take_profit_margin.json")) as f:
            setting = json.load(f)
    except FileNotFoundError:
        return None, None
    if setting.get("account_id") != account_id or setting.get("scope") != "individual_positions":
        raise ValueError("margin take-profit account/scope mismatch")
    pct = Decimal(str(setting["return_on_margin_pct"]))
    fee = Decimal(str(setting["estimated_exit_taker_fee_rate"]))
    if (not pct.is_finite() or not Decimal(0) < pct <= Decimal(100)
            or not fee.is_finite() or not Decimal(0) <= fee < Decimal('.01')):
        raise ValueError("invalid margin take-profit setting")
    return pct, fee


def minimum_lot_notional(filters, ref_price: Decimal, strategy=None, direction=None) -> Decimal:
    """Smallest valid venue lot at the signal price, measured in USD notional."""
    px = Decimal(ref_price)
    if not px.is_finite() or px <= 0 or filters.step <= 0 or filters.contract_multiplier <= 0:
        raise ValueError("invalid minimum-lot inputs")
    protected_px = px
    if strategy is not None and direction is not None:
        from execution.geometry import protection_prices
        tp, sl = protection_prices(strategy, direction, px)
        tp = filters.round_price(tp, up=direction == "long")
        sl = filters.round_price(sl, up=direction != "long")
        protected_px = min(px, tp, sl)
        if protected_px <= 0:
            raise ValueError("invalid protection price")
    need_qty = max(filters.min_qty, filters.min_notional / (protected_px * filters.contract_multiplier))
    qty = (need_qty / filters.step).to_integral_value(rounding=ROUND_CEILING) * filters.step
    return qty * px * filters.contract_multiplier


@dataclass(frozen=True)
class CanonicalSignal:
    """One strategy decision, produced once, fanned out to every active tenant."""
    signal_id: str
    market: str
    direction: str
    strategy: str
    ref_price: Decimal
    ts: float
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CloseSignal:
    market: str
    direction: str
    reason: str
    ts: float


class TenantEngine:
    def __init__(self, ctx: CustomerExecutionContext, transport, metadata, freshness, state_root: str,
                 clock: Optional[Callable[[], float]] = None, engine_config: Optional[EngineConfig] = None):
        self.ctx = ctx
        self.clock = clock or time.time
        self.dir = os.path.join(state_root, "tenants", ctx.account_id)
        os.makedirs(self.dir, exist_ok=True)
        self.ledger = Ledger(os.path.join(self.dir, "ledger.sqlite"), switches_mirror=os.path.join(self.dir, "exec_switches.json"), clock=self.clock)
        self.transport = transport
        self.venue = VenueMonitor(clock=self.clock)
        cfg = engine_config or EngineConfig()
        cfg.trade_margin_pct = ctx.trade_margin_pct
        cfg.confirmed_taker_fee = confirmed_taker_fee(self.dir, ctx.account_id)
        cfg.take_profit_margin_pct, cfg.take_profit_taker_fee = margin_take_profit(self.dir, ctx.account_id)
        cfg.leverage = ctx.leverage
        cfg.limits = ctx.limits()
        cfg.accounts = [ctx.namespace]
        self._apply_risk_policy(cfg, ctx)
        self.eng = ExecutionEngine(self.ledger, transport, metadata, freshness, self.venue, cfg, clock=self.clock)
        self.started = False
        self.failures = 0
        self.last_error: Optional[str] = None
        self.last_reconcile_ok: Optional[bool] = None
        self.last_signal: Optional[dict] = None
        self._sync_switches()

    # ---- context
    def apply_context(self, ctx: CustomerExecutionContext) -> None:
        if ctx.account_id != self.ctx.account_id or ctx.user_id != self.ctx.user_id:
            raise ValueError("context belongs to a different tenant")
        self.ctx = ctx
        self.eng.cfg.limits = ctx.limits()
        self.eng.cfg.leverage = ctx.leverage
        self.eng.cfg.trade_margin_pct = ctx.trade_margin_pct
        self.eng.cfg.confirmed_taker_fee = confirmed_taker_fee(self.dir, ctx.account_id)
        self.eng.cfg.take_profit_margin_pct, self.eng.cfg.take_profit_taker_fee = margin_take_profit(self.dir, ctx.account_id)
        self._apply_risk_policy(self.eng.cfg, ctx)
        self._sync_switches()

    def _apply_risk_policy(self, cfg, ctx):
        # Owner authorized JP live entries on 2026-10-19. Retain the daily
        # loss brake, one-position limit, and all protection-failure handling.
        if ctx.account_id == '04d10bf8-6614-4c11-9ab9-240e26fce66c':
            cfg.loss_halt_timezone = 'Europe/Berlin'
            cfg.limits = replace(cfg.limits, max_open_positions=min(1, cfg.limits.max_open_positions))
        path = os.path.join(self.dir, 'risk_policy.json')
        self.risk_policy_error = True
        try:
            with open(path) as handle: policy = json.load(handle)
        except FileNotFoundError:
            self.risk_policy = None
            self.risk_policy_error = False
            cfg.risk_plan_required = False
            return
        base=dict(account_id=ctx.account_id, version=1, mode='volatility_risk', max_open_positions=1)
        hybrid=dict(base,version=2,signal_mix='swing_and_discovery')
        if policy not in (base,hybrid):
            raise ValueError('invalid account risk policy')
        self.risk_policy = policy
        self.risk_policy_error = False
        cfg.risk_plan_required = True
        cfg.trade_margin_pct = None
        cfg.limits = replace(cfg.limits, max_open_positions=min(1,cfg.limits.max_open_positions),
                             max_notional_per_order=min(Decimal(500),cfg.limits.max_notional_per_order),
                             max_total_notional=min(Decimal(500),cfg.limits.max_total_notional))

    def _sync_switches(self) -> None:
        sw = self.ledger.get_switches()
        want = self.ctx.entries_wanted
        if sw.allow_new_entries != want or not sw.manage_existing_positions:
            self.eng.set_switches(allow_new_entries=want, manage_existing_positions=True, reason=f"account_state={self.ctx.state}")

    # ---- lifecycle
    def start(self):
        rep = self.eng.start()
        self.started = bool(rep.ok)
        self.last_reconcile_ok = bool(rep.ok)
        self.last_error = None if rep.ok else rep.error
        return rep

    def shutdown(self) -> None:
        try:
            if hasattr(self.transport, "forget_credentials"):
                self.transport.forget_credentials()
        finally:
            self.ledger.close()

    def fixed_entry_notional(self):
        """Opt-in, account-scoped fixed USD exposure for future core entries."""
        path = os.path.join(self.dir, "fixed_entry_notional.json")
        try:
            with open(path) as f:
                setting = json.load(f)
        except FileNotFoundError:
            return None
        if setting.get("account_id") != self.ctx.account_id:
            raise ValueError("fixed sizing account mismatch")
        value = Decimal(str(setting["notional_usd"]))
        if not value.is_finite() or value <= 0:
            raise ValueError("invalid fixed entry notional")
        return value

    # ---- execution
    def submit(self, sig: CanonicalSignal):
        from execution.ai_policy import parse, PENDING
        from execution.engine import SubmitResult
        ai = parse(sig.strategy)
        if self.risk_policy_error:
            return SubmitResult('BLOCKED',None,'risk_policy_invalid')
        entry_risk = self.eng.entry_risk_status(self.ctx.namespace)
        if entry_risk['blocked']:
            return SubmitResult('BLOCKED', None, 'entry_risk:' + entry_risk['reason'])
        if self.risk_policy:
            hybrid=self.risk_policy.get('signal_mix')=='swing_and_discovery'
            allowed=sig.strategy in (('WY-PULLBACK','WY-REVERT') if hybrid else ('WY-PULLBACK',))
            proof=sig.detail.get('discovery',{})
            if ai and hybrid:
                allowed=(proof.get('source')=='discovery_agents' and proof.get('account_id')==self.ctx.account_id
                    and proof.get('user_id')==self.ctx.user_id and proof.get('fingerprint')==ai['id']
                    and 0<=self.clock()-proof.get('reviewed_at',0)<=90)
            if not allowed or not sig.detail.get('live_entry_filter'):
                return SubmitResult('BLOCKED', None, 'risk_plan_requires_confirmed_pullback')
            try:
                from execution.trade_plan import build_plan, observed_exit_slippage
                evidence = dict(sig.detail['risk_evidence'])
                evidence['confirmed_fee'] = str(self.eng.cfg.confirmed_taker_fee or 0)
                evidence['exit_slippage_fraction'] = str(observed_exit_slippage(
                    self.ledger, self.ctx.namespace, sig.market, self.clock()))
                if ai:
                    from execution.trade_plan import build_agent_plan
                    plan=build_agent_plan(account=self.ctx.namespace,direction=sig.direction,price=sig.ref_price,
                        filters=self.eng.meta.filters(sig.market),ai=ai,equity=self.transport.get_account(self.ctx.namespace).equity,
                        now=self.clock(),configured=self.eng.cfg.max_planned_loss_usd,confirmed_fee=self.eng.cfg.confirmed_taker_fee,
                        exit_slippage_fraction=evidence['exit_slippage_fraction'])
                else:
                    plan = build_plan(account=self.ctx.namespace, direction=sig.direction, price=sig.ref_price,
                                      filters=self.eng.meta.filters(sig.market), evidence=evidence,
                                      now=self.clock(), configured=self.eng.cfg.max_planned_loss_usd)
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                return SubmitResult('BLOCKED', None, str(exc) if isinstance(exc,ValueError) else 'risk_plan_unavailable')
            es = EntrySignal(signal_id=sig.signal_id,account_id=self.ctx.namespace,market=sig.market,
                direction=sig.direction,strategy=sig.strategy,notional_usd=Decimal(plan['notional_usd']),
                ref_price=Decimal(sig.ref_price),ts=sig.ts,detail=dict(sig.detail,risk_trade_plan=plan))
            return self.eng.submit_entry(es)
        with self.eng.lock:
            if not ai:
                # Core may cancel unfilled AI entries. A filled position retains
                # ownership; a core signal never liquidates it merely to replace it.
                orders = self.ledger.orders_where(PENDING, account_id=self.ctx.namespace, market=sig.market, purpose='ENTRY')
                for order in orders:
                    if parse(order['strategy']):
                        result = self.eng.cancel_order(order['client_order_id'])
                        if result not in ('CANCELLED', 'PARTIALLY_FILLED_CANCELLED'):
                            return SubmitResult('BLOCKED', None, 'ai_entry_cancellation_unconfirmed')
        try:
            minimum = minimum_lot_notional(self.eng.meta.filters(sig.market), Decimal(sig.ref_price), sig.strategy, sig.direction)
        except (KeyError, ValueError, ArithmeticError):
            minimum = Decimal(1)  # Engine reports invalid markets and prices.
        if ai:
            # Start at the venue's smallest valid lot. After twenty profitable
            # live closes, one size step is allowed, still under the shared cap.
            from execution.ai_policy import live_performance
            performance = live_performance(self.eng, sig.strategy)
            if performance['closed'] >= 20 and performance['pnl'] > 0 and not performance['paused']:
                minimum *= 2
        detail = dict(sig.detail)
        if not ai and self.ctx.trade_margin_pct is None:
            try:
                fixed = self.fixed_entry_notional()
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
                return SubmitResult("BLOCKED", None, "invalid_fixed_entry_notional")
            if fixed is not None:
                if fixed < minimum:
                    return SubmitResult("REJECTED", None, "fixed_entry_below_coin_minimum")
                minimum = fixed
                detail["fixed_entry_notional_usd"] = str(fixed)
        es = EntrySignal(signal_id=sig.signal_id, account_id=self.ctx.namespace, market=sig.market, direction=sig.direction,
                         strategy=sig.strategy, notional_usd=minimum,
                         ref_price=Decimal(sig.ref_price),
                         ts=sig.ts, detail=detail)
        return self.eng.submit_entry(es)

    def close(self, market: str, direction: str, reason: str) -> str:
        pos = self.ledger.open_position_for(self.ctx.namespace, market, direction)
        if pos is None:
            return "NO_POSITION"
        from execution.ai_policy import parse
        if parse(pos['strategy']):
            return 'AI_POSITION_NOT_OWNED_BY_CORE_CLOSE'
        return self.eng.close_position(pos["position_id"], reason)

    def manage(self) -> dict:
        out = self.eng.manage_existing()
        from execution.ai_policy import parse
        for pos in self.open_positions():
            ai = parse(pos['strategy'])
            if ai and self.clock() - pos['opened_ts'] >= ai['hold_bars'] * 300:
                self.eng.close_position(pos['position_id'], 'AI_TIMEOUT')
            elif ai:
                from execution.ai_policy import live_performance
                live_performance(self.eng, pos['strategy'])
        from execution.trade_plan import stored_plan
        for pos in self.open_positions():
            order = self.ledger.get_order(pos['entry_coid'])
            plan = stored_plan(order) if order else None
            if plan and self.clock()-pos['opened_ts'] >= plan['max_hold_seconds']:
                self.eng.close_position(pos['position_id'], 'RISK_PLAN_TIMEOUT')
        self.last_reconcile_ok = out.get("reconcile_ok")
        if self.last_reconcile_ok:
            # A later successful exchange check supersedes an earlier failure.
            self.last_error = None
        return out

    def on_mark(self, market: str, price, ts: Optional[float] = None) -> List[str]:
        # A flat venue-stop account has no stop to evaluate. Do not wait behind
        # slow private reconciliation just to take the engine lock and find none.
        # Any existing position (including pending protection) retains the normal
        # engine path; software-stop-only venues always retain it as well.
        if self.transport.capabilities.venue_stop_orders and not self.open_positions():
            self.eng.fresh.on_mark(market, float(price), ts)
            return []
        return self.eng.on_mark(market, price, ts)

    def on_disconnect(self, reason: str = "feed_disconnect") -> None:
        self.eng.on_disconnect(reason)

    # ---- views
    def open_positions(self) -> List[dict]:
        return self.ledger.positions_where(OPEN_STATES, account_id=self.ctx.namespace)

    def ambiguous_orders(self) -> List[dict]:
        return self.ledger.orders_where(list(ORDER_AMBIGUOUS), account_id=self.ctx.namespace)

    def is_flat(self) -> bool:
        return not self.open_positions() and not self.ambiguous_orders()

    def runtime_info(self) -> dict:
        pos = self.open_positions()
        entry_risk = self.eng.entry_risk_status(self.ctx.namespace)
        fixed = None
        fixed_error = None
        if self.ctx.trade_margin_pct is None and not self.risk_policy:
            try:
                value = self.fixed_entry_notional()
                fixed = str(value) if value is not None else None
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
                fixed_error = "invalid_fixed_entry_notional"
        return dict(risk_policy=self.risk_policy, max_effective_positions=self.eng.cfg.limits.max_open_positions,
                    target_net_usd=('2.50' if self.risk_policy else None),
                    take_profit_margin_pct=(str(self.eng.cfg.take_profit_margin_pct) if self.eng.cfg.take_profit_margin_pct is not None and not self.risk_policy else None),
                    take_profit_scope=("individual_positions" if self.eng.cfg.take_profit_margin_pct is not None else None),
                    fixed_entry_notional_usd=fixed, fixed_entry_sizing_error=fixed_error, loaded=True, started=self.started, open_positions=len(pos), ambiguous_orders=len(self.ambiguous_orders()),
                    venue=self.venue.state.value, failures=self.failures, last_error=self.last_error,
                    pending_entries=len(self.ledger.orders_where(list(ORDER_LIVE), account_id=self.ctx.namespace, purpose='ENTRY')),
                    last_signal=self.last_signal, size_overrides={},
                    confirmed_taker_fee_rate=(str(self.eng.cfg.confirmed_taker_fee)
                                              if self.eng.cfg.confirmed_taker_fee is not None else None),
                    entries_allowed=self.ledger.get_switches().allow_new_entries and not entry_risk['blocked'],
                    entry_risk=entry_risk,
                    positions=[self._position_view(p) for p in pos])

    def _position_view(self, p: dict) -> dict:
        """Read-only dashboard view: the ledger's current protection levels and entry leverage."""
        prot = self.ledger.get_protection(p["position_id"]) or {}
        entry = self.ledger.get_order(p["entry_coid"]) if p.get("entry_coid") else None
        return dict(position_id=p["position_id"], market=p["market"], direction=p["direction"], qty=p["qty"], entry_price=p["entry_price"],
                    state=p["state"], strategy=p["strategy"], opened_ts=p["opened_ts"],
                    stop_loss=prot.get("stop_price"), take_profit=prot.get("tp_price"),
                    protection_state=prot.get("state"), protection_updated_ts=prot.get("updated_ts"),
                    leverage=(str(entry["leverage"]) if entry and entry.get("leverage") is not None else None))


class TenantRegistry:
    """Owns all TenantEngines. Every per-tenant call is isolated."""

    def __init__(self, accounts, transport_factory: Callable[[CustomerExecutionContext], Any], metadata, freshness, state_root: str,
                 clock: Optional[Callable[[], float]] = None, engine_config_factory: Optional[Callable[[], EngineConfig]] = None,
                 entry_filter=None):
        self.accounts = accounts
        self.entry_filter = entry_filter
        self.transport_factory = transport_factory
        self.metadata, self.freshness = metadata, freshness
        self.state_root = state_root
        self.clock = clock or time.time
        self.engine_config_factory = engine_config_factory or EngineConfig
        self.tenants: Dict[str, TenantEngine] = {}
        self.lock = threading.RLock()
        self.errors: List[dict] = []
        self.last_refresh: float = 0.0
        self.discovery_status = dict(checked_at=None, last_error=None, by_account={})

    def _entry_gates(self):
        if self.entry_filter is None:
            return ()
        if hasattr(self.entry_filter, 'filters'):
            return tuple(self.entry_filter.filters.values())
        return (self.entry_filter,)

    def _entry_gate(self, ctx):
        gate=next((gate for gate in self._entry_gates() if gate.applies(ctx)),None)
        tenant=self.tenants.get(ctx.account_id)
        policy=getattr(tenant,'risk_policy',None)
        hybrid=bool(policy and policy.get('signal_mix')=='swing_and_discovery')
        if gate is not None:gate.allow_revert=hybrid
        return gate

    def discovery_contexts(self):
        with self.lock:
            return [t.ctx for t in self.tenants.values() if t.ctx.entries_wanted and t.risk_policy
                and t.risk_policy.get('signal_mix')=='swing_and_discovery' and self._entry_gate(t.ctx)]

    def dispatch_discovery(self,sig):
        with self.lock:
            proof=sig.detail.get('discovery',{});t=self.tenants.get(proof.get('account_id'))
            if t is None or proof.get('user_id')!=t.ctx.user_id or not t.ctx.entries_wanted or not t.risk_policy or t.risk_policy.get('signal_mix')!='swing_and_discovery':return 'BLOCKED:discovery_account_not_enabled'
            gate=self._entry_gate(t.ctx)
            confirmed=gate.confirm_agent(sig,self.freshness) if gate else None
            if confirmed is None:return 'FILTERED:discovery_trend_or_recovery_unconfirmed'
            result=t.submit(confirmed)
            t.last_signal=dict(at=sig.ts,market=sig.market,result=result.status,reason=result.reason,source='discovery_agents')
            return result.status+(':'+result.reason if result.reason else '')


    def _record(self, tenant: Optional[TenantEngine], account_id: str, where: str, e: BaseException) -> None:
        msg = safe_exc(e)
        if tenant is not None:
            tenant.failures += 1
            tenant.last_error = f"{where}: {msg}"
        self.errors.append(dict(ts=self.clock(), account_id=account_id, where=where, error=msg))
        del self.errors[:-200]
        log.warning("tenant %s %s failed: %s", account_id, where, msg)

    # ---- membership
    def refresh(self) -> dict:
        added, updated, removed, failed = [], [], [], []
        try:
            ctxs = self.accounts.list_execution_contexts()
        except Exception as e:
            self._record(None, "*", "list_execution_contexts", e)
            return dict(error=safe_exc(e))
        wanted = {c.account_id: c for c in ctxs if c.trading_style == "swing"}
        with self.lock:
            for aid in list(self.tenants):
                if aid not in wanted:
                    t = self.tenants.pop(aid)
                    try:
                        t.shutdown()
                    except Exception as e:
                        self._record(None, aid, "shutdown", e)
                    removed.append(aid)
            for aid, ctx in wanted.items():
                t = self.tenants.get(aid)
                if t is None:
                    try:
                        t = TenantEngine(ctx, self.transport_factory(ctx), self.metadata, self.freshness, self.state_root,
                                         clock=self.clock, engine_config=self.engine_config_factory())
                        self.tenants[aid] = t
                        added.append(aid)
                    except Exception as e:
                        self._record(None, aid, "build", e)
                        failed.append(aid)
                        continue
                    try:
                        t.start()
                    except Exception as e:
                        self._record(t, aid, "start", e)
                else:
                    if t.ctx != ctx:
                        try:
                            t.apply_context(ctx)
                            updated.append(aid)
                        except Exception as e:
                            self._record(t, aid, "apply_context", e)
        self.last_refresh = self.clock()
        return dict(added=added, updated=updated, removed=removed, failed=failed, total=len(self.tenants))

    # ---- fan-out
    def dispatch(self, sig: CanonicalSignal) -> Dict[str, str]:
        out: Dict[str, str] = {}
        with self.lock:
            items = list(self.tenants.items())
        for aid, t in items:
            if not t.ctx.entries_wanted:
                out[aid] = "SKIPPED:entries_not_wanted"
                continue
            try:
                gate = self._entry_gate(t.ctx)
                if gate:
                    reason = gate.offer(sig)
                    if reason is not None:
                        out[aid] = "FILTERED:" + reason
                        t.last_signal = dict(at=sig.ts, market=sig.market, result="FILTERED", reason=reason)
                        continue
                r = t.submit(sig)
                out[aid] = f"{r.status}:{r.reason}" if r.reason else r.status
                t.last_signal = dict(at=sig.ts, market=sig.market, result=r.status, reason=r.reason)
            except Exception as e:
                self._record(t, aid, "submit", e)
                out[aid] = "ERROR"
                t.last_signal = dict(at=sig.ts, market=sig.market, result="ERROR", reason=safe_exc(e))
        return out

    def dispatch_close(self, cs: CloseSignal) -> Dict[str, str]:
        if self.entry_filter:
            self.entry_filter.cancel(cs.market)
        out: Dict[str, str] = {}
        with self.lock:
            items = list(self.tenants.items())
        for aid, t in items:
            try:
                out[aid] = t.close(cs.market, cs.direction, cs.reason)
            except Exception as e:
                self._record(t, aid, "close", e)
                out[aid] = "ERROR"
        return out

    def close_for_user(self, ident, position_id: str) -> str:
        """Close exactly one owned ledger position, through the reduce-only engine."""
        from .accounts import AccountError
        from execution.state_machine import ORDER_LIVE
        from execution.types import OrderState as O
        with self.lock:
            tenant = next((t for t in self.tenants.values() if str(t.ctx.user_id) == str(ident.user_id)), None)
        if tenant is None:
            raise AccountError("execution_unavailable", 503)
        # Do not queue a close behind a slow venue call or another close request.
        if not tenant.eng.lock.acquire(blocking=False):
            raise AccountError("position_busy", 409)
        try:
            pos = tenant.ledger.get_position(position_id)
            if pos is None or pos["account_id"] != tenant.ctx.namespace:
                raise AccountError("position_not_found", 404)
            if pos["state"] == P.CLOSED.value:
                return "ALREADY_CLOSED"
            pending = tenant.ledger.orders_where(list(ORDER_AMBIGUOUS | ORDER_LIVE), account_id=tenant.ctx.namespace)
            if any(o["position_id"] == position_id and o["purpose"] == "CLOSE" for o in pending):
                return "CLOSE_PENDING"
            if any(o["position_id"] == position_id and O(o["state"]) in ORDER_AMBIGUOUS for o in pending):
                raise AccountError("position_reconciliation_required", 409)
            try:
                return tenant.eng.close_position(position_id, "USER_MARKET_CLOSE")
            except Exception as e:
                self._record(tenant, tenant.ctx.account_id, "manual_close", e)
                raise AccountError("venue_close_unavailable", 503) from e
        finally:
            tenant.eng.lock.release()

    # ---- periodic
    def manage_all(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        with self.lock:
            items = list(self.tenants.items())
        for aid, t in items:
            try:
                if not t.started:
                    t.start()
                out[aid] = t.manage()
                if t.ctx.state == "DRAINING" and t.is_flat():
                    new_ctx = self.accounts.complete_drain(aid)
                    if new_ctx is not None:
                        t.apply_context(new_ctx)
                        out[aid]["drain_completed"] = str(new_ctx.position_size_usd)
            except Exception as e:
                self._record(t, aid, "manage", e)
                out[aid] = dict(error=safe_exc(e))
        return out

    def seed_candles(self, market, interval, rows):
        if self.entry_filter:
            return self.entry_filter.seed(market, interval, rows)

    def on_candle(self, market, interval, row):
        if self.entry_filter:
            self.entry_filter.on_bar(market, interval, row)

    def dispatch_confirmed(self):
        out = {}
        with self.lock:
            gates = self._entry_gates()
            for gate in gates:
                t = self.tenants.get(gate.account_id)
                if t is None or not gate.applies(t.ctx) or not t.ctx.entries_wanted:
                    gate.cancel()
                    continue
                for sig in gate.ready(self.freshness):
                    key = sig.market if len(gates) == 1 else gate.account_id + ':' + sig.market
                    try:
                        result = t.submit(sig)
                        out[key] = result.status
                        t.last_signal = dict(at=sig.ts, market=sig.market, result=result.status, reason=result.reason)
                    except Exception as e:
                        self._record(t, t.ctx.account_id, "confirmed_submit", e)
                        out[key] = "ERROR"
        return out

    def on_mark(self, market: str, price, ts: Optional[float] = None) -> None:
        with self.lock:
            items = list(self.tenants.items())
        for aid, t in items:
            try:
                t.on_mark(market, price, ts)
            except Exception as e:
                self._record(t, aid, "on_mark", e)

    def on_disconnect(self, reason: str = "feed_disconnect") -> None:
        if self.entry_filter:
            self.entry_filter.disconnect()
        with self.lock:
            items = list(self.tenants.items())
        for aid, t in items:
            try:
                t.on_disconnect(reason)
            except Exception as e:
                self._record(t, aid, "on_disconnect", e)

    # ---- views
    def runtime_info(self, account_id: str) -> dict:
        with self.lock:
            t = self.tenants.get(str(account_id))
        if t is None:
            return dict(loaded=False, open_positions=0, ambiguous_orders=0, venue=None, positions=[])
        try:
            info = t.runtime_info()
            info["discovery"] = self.discovery_status["by_account"].get(str(account_id))
            gate = self._entry_gate(t.ctx)
            if gate:
                info["live_entry_filter"] = gate.status()
            return info
        except Exception as e:
            self._record(t, account_id, "runtime_info", e)
            return dict(loaded=True, open_positions=1, ambiguous_orders=0, venue=None, positions=[], error=safe_exc(e))

    def status(self) -> dict:
        with self.lock:
            items = list(self.tenants.items())
        return dict(tenants=len(items), last_refresh=self.last_refresh, discovery=self.discovery_status,
                    live_entry_filter=(self.entry_filter.status() if self.entry_filter else None),
                    by_state={aid: dict(state=t.ctx.state, venue=t.venue.state.value, started=t.started, failures=t.failures,
                                        open_positions=len(t.open_positions()), last_signal=t.last_signal, entries_wanted=t.ctx.entries_wanted,
                                        entry_risk=t.eng.entry_risk_status(t.ctx.namespace),
                                        take_profit_margin_pct=(str(t.eng.cfg.take_profit_margin_pct) if t.eng.cfg.take_profit_margin_pct is not None and not t.risk_policy else None),
                                        risk_policy=t.risk_policy, max_effective_positions=t.eng.cfg.limits.max_open_positions,
                                        target_net_usd=('2.50' if t.risk_policy else None),
                                        take_profit_scope=("individual_positions" if t.eng.cfg.take_profit_margin_pct is not None else None)) for aid, t in items},
                    recent_errors=self.errors[-10:])
