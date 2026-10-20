from abc import ABC, abstractmethod
from decimal import Decimal as D, ROUND_DOWN, ROUND_CEILING
import time
import uuid
from .reuse import loss_stop
from .prices import quote, entries_allowed
from .db import transaction, enqueue, get, put, log

MICRO = D(".000001")
QTY_STEP = D(".00000001")
TICKS = {"DOGE/USD": D(".00000001"), "BTC/USD": D(".01"), "ETH/USD": D(".01"),
         "SOL/USD": D(".0001"), "XAU/USD": D(".01")}


def money(x):
    return D(x).quantize(MICRO, rounding=ROUND_DOWN)


def up_money(x):
    return D(x).quantize(MICRO, rounding=ROUND_CEILING)


class ExecutionAdapter(ABC):
    @abstractmethod
    def open(self, user, signal): ...
    @abstractmethod
    def close(self, position_id, reason, fraction=D(1)): ...
    @abstractmethod
    def getPosition(self, user): ...
    @abstractmethod
    def getPrice(self, symbol): ...


class OrderlyAdapter(ExecutionAdapter):
    """TODO: separate design, audit and authorization. Never usable by Vela."""
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Live execution is not implemented; TESTNET + PAPER ONLY")
    def open(self, *args): raise NotImplementedError
    def close(self, *args): raise NotImplementedError
    def getPosition(self, *args): raise NotImplementedError
    def getPrice(self, *args): raise NotImplementedError


class PaperAdapter(ExecutionAdapter):
    def __init__(self, db, settings, on_chain=True):
        self.db, self.settings = db, settings
        self.on_chain = on_chain

    def getPrice(self, symbol):
        return quote(self.db, symbol, self.settings.max_price_age)

    def getPosition(self, user):
        return [dict(r) for r in self.db.execute("SELECT * FROM positions WHERE user=?", (user.lower(),))]

    def protect(self, p):
        return loss_stop.stop_price(entry=p["entry"], qty=p["qty"], multiplier=1, direction=p["side"],
            paid_fees=p["paid_fees"], realized_gross=p["realized_gross"], exit_fee=self.settings.fee,
            strategy_stop=p["stop"], tick=TICKS[p["symbol"]], configured=self.settings.loss_budget,
            previous_stop=p["stop"])

    def open(self, address, signal):
        address = address.lower()
        symbol, side = signal["symbol"], signal["side"]
        if side not in ("long", "short") or symbol not in TICKS:
            raise ValueError("Invalid signal")
        if not entries_allowed(self.db, symbol, self.settings.max_price_age):
            raise ValueError("New entries paused: price health")
        with transaction(self.db):
            user = self.db.execute("SELECT * FROM users WHERE address=?", (address,)).fetchone()
            if not user or user["status"] != "active":
                raise ValueError("User is not active")
            if self.getPosition(address):
                raise ValueError("One position per user; wait for close")
            signal_key = "signal:" + user["session"] + ":" + str(user["strategy"]) + ":" + signal["signal_id"]
            if get(self.db, signal_key):
                raise ValueError("Signal already consumed")
            sign = D(1) if side == "long" else D(-1)
            mid = self.getPrice(symbol)
            entry = mid * (1 + sign * self.settings.slippage)
            stop_bps, target_bps = D(signal["stop_bps"]), D(signal["target_bps"])
            if not stop_bps.is_finite() or not target_bps.is_finite() or not 0 < stop_bps < 10000 or target_bps <= 0:
                raise ValueError("Invalid signal geometry")
            stop = entry * (1 - sign * stop_bps / 10000)
            target = entry * (1 + sign * target_bps / 10000)
            balance = D(user["balance"])
            leverage = self.settings.max_leverage
            # Size down before calculating the universal stop. Include both fees and
            # a slippage reserve; the trigger budget is still not a fill guarantee.
            costs = abs(entry - stop) + (entry + stop) * self.settings.fee + entry * self.settings.slippage
            risk = min(D(2), self.settings.loss_budget) - MICRO * 2
            qty = min(balance * self.settings.size_fraction * leverage / entry,
                      max(D(0), risk) / costs, balance / (entry / leverage + entry * self.settings.fee))
            qty = qty.quantize(QTY_STEP, rounding=ROUND_DOWN)
            if qty <= 0:
                raise ValueError("Insufficient paper balance")
            fee = up_money(entry * qty * self.settings.fee)
            p = dict(id=uuid.uuid4().hex, user=address, session=user["session"], symbol=symbol, side=side,
                     qty=str(qty), entry=str(entry), stop=str(stop), target=str(target),
                     entry_fee_left=str(fee), paid_fees=str(fee), realized_gross="0",
                     opened=time.time(), expires=time.time() + min(21600, max(10, float(signal["hold"]))),
                     leverage=str(leverage))
            p["stop"] = str(self.protect(p))
            if (entry - D(p["stop"])) * sign <= 0:
                raise ValueError("Fees leave no valid protected position")
            self.db.execute("INSERT INTO positions VALUES (" + ",".join("?" for _ in p) + ")", tuple(p.values()))
            put(self.db, signal_key, True)
            log(self.db, "engine", "paper_open", position=p["id"], symbol=symbol, side=side)
            return p

    def close(self, position_id, reason, fraction=D(1)):
        fraction = D(fraction)
        if not fraction.is_finite() or not 0 < fraction <= 1:
            raise ValueError("Invalid partial close")
        with transaction(self.db):
            row = self.db.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
            if not row:
                return None
            p = dict(row)
            old_qty = D(p["qty"])
            qty = old_qty if fraction == 1 else (old_qty * fraction).quantize(QTY_STEP, rounding=ROUND_DOWN)
            if qty <= 0:
                raise ValueError("Partial close too small")
            sign = D(1) if p["side"] == "long" else D(-1)
            exit_price = self.getPrice(p["symbol"]) * (1 - sign * self.settings.slippage)
            entry = D(p["entry"])
            gross = money((exit_price - entry) * qty * sign)
            exit_fee = up_money(exit_price * qty * self.settings.fee)
            entry_fee = D(p["entry_fee_left"]) if qty == old_qty else money(D(p["entry_fee_left"]) * qty / old_qty)
            fees = entry_fee + exit_fee
            pnl = gross - fees
            user = self.db.execute("SELECT * FROM users WHERE address=?", (p["user"],)).fetchone()
            trade_id = uuid.uuid4().hex
            t = dict(id=trade_id, user=p["user"], session=p["session"], strategy=user["strategy"],
                     position_id=position_id, symbol=p["symbol"], side=p["side"], qty=str(qty),
                     entry=p["entry"], exit=str(exit_price), gross=str(gross), fees=str(fees), pnl=str(pnl),
                     pnl_micro=int(pnl * 1_000_000), opened=p["opened"], closed=time.time(), reason=reason)
            self.db.execute("INSERT INTO trades(" + ",".join(t) + ") VALUES (" + ",".join("?" for _ in t) + ")", tuple(t.values()))
            self.db.execute("UPDATE users SET balance=? WHERE address=?", (str(D(user["balance"]) + pnl), p["user"]))
            if qty == old_qty:
                self.db.execute("DELETE FROM positions WHERE id=?", (position_id,))
            else:
                p.update(qty=str(old_qty - qty), entry_fee_left=str(D(p["entry_fee_left"]) - entry_fee),
                         paid_fees=str(D(p["paid_fees"]) + exit_fee),
                         realized_gross=str(D(p["realized_gross"]) + gross))
                p["stop"] = str(self.protect(p))
                self.db.execute("""UPDATE positions SET qty=?,entry_fee_left=?,paid_fees=?,
                    realized_gross=?,stop=? WHERE id=?""",
                    (p["qty"], p["entry_fee_left"], p["paid_fees"], p["realized_gross"], p["stop"], position_id))
            if self.on_chain:
                enqueue(self.db, "trade:" + trade_id, "logTrade", {"trade": trade_id})
            else:
                self.db.execute("UPDATE trades SET chain_status='offchain' WHERE id=?", (trade_id,))
            log(self.db, "engine", "paper_close", trade=trade_id, pnl=str(pnl), reason=reason)
            return t

    def reconcile(self):
        """Reapply the universal budget to persisted positions on every tick/restart."""
        for row in self.db.execute("SELECT * FROM positions").fetchall():
            p = dict(row)
            try:
                new_stop = self.protect(p)
                self.db.execute("UPDATE positions SET stop=? WHERE id=?", (str(new_stop), p["id"]))
            except ValueError:
                self.close(p["id"], "invalid_persisted_risk")

    def equity(self, user):
        value = D(user["balance"])
        for p in self.getPosition(user["address"]):
            sign = D(1) if p["side"] == "long" else D(-1)
            px = self.getPrice(p["symbol"]) * (1 - sign * self.settings.slippage)
            value += (px - D(p["entry"])) * D(p["qty"]) * sign - D(p["entry_fee_left"]) - up_money(px * D(p["qty"]) * self.settings.fee)
        return money(value)

    def tick(self, agents, allow_entries=True):
        self.reconcile()
        for row in self.db.execute("SELECT * FROM users WHERE status IN ('active','stopping')").fetchall():
            user = dict(row)
            for p in self.getPosition(user["address"]):
                try:
                    mid = self.getPrice(p["symbol"])
                    long = p["side"] == "long"
                    hit_stop = mid <= D(p["stop"]) if long else mid >= D(p["stop"])
                    hit_target = mid >= D(p["target"]) if long else mid <= D(p["target"])
                    reason = "owner_stop" if user["status"] == "stopping" else "stop" if hit_stop else "target" if hit_target else "timeout" if time.time() >= p["expires"] else None
                    if reason:
                        self.close(p["id"], reason)
                except ValueError:
                    # Never invent a market fill on a stale/divergent quote.
                    continue
            if user["status"] == "active" and allow_entries and not self.getPosition(user["address"]):
                signal = agents.signal(user)
                if signal:
                    try:
                        self.open(user["address"], signal)
                    except ValueError:
                        pass
            elif user["status"] == "stopping" and not self.getPosition(user["address"]):
                if not self.on_chain:
                    current = self.db.execute("SELECT balance FROM users WHERE address=?", (user["address"],)).fetchone()[0]
                    self.db.execute("UPDATE users SET status='settled',final_pnl=? WHERE address=?",
                                    (str(D(current)-D(user["initial"])),user["address"]))
                    continue
                pending = self.db.execute("SELECT 1 FROM trades WHERE session=? AND chain_status!='confirmed'", (user["session"],)).fetchone()
                if not pending:
                    pnl_micro = self.db.execute("SELECT coalesce(sum(pnl_micro),0) FROM trades WHERE session=?", (user["session"],)).fetchone()[0]
                    enqueue(self.db, "settle:" + user["session"], "settle", {"user": user["address"], "session": user["session"], "pnl_micro": pnl_micro})
            refreshed = dict(self.db.execute("SELECT * FROM users WHERE address=?", (user["address"],)).fetchone())
            try:
                equity = self.equity(refreshed)
                self.db.execute("INSERT INTO equity(user,session,ts,equity,pnl) VALUES (?,?,?,?,?)",
                    (user["address"], user["session"], time.time(), str(equity), str(equity - D(user["initial"]))))
            except ValueError:
                pass
